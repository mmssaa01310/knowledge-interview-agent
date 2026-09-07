import type { OpenAIRealtimeEvent, VoiceConnectionStats } from "../types";
import { waitForIceGatheringComplete } from "./voicePeerConnection";

type OpenAIRealtimePeerConnectionOptions = {
  voiceSessionId: string;
  microphoneStream: MediaStream;
  remoteAudioElement: HTMLAudioElement | null;
  onEvent: (event: OpenAIRealtimeEvent) => void;
  onConnectionStateChange: (state: string) => void;
  onStatsChange?: (stats: VoiceConnectionStats) => void;
};

export type OpenAIRealtimePeerConnectionHandle = {
  peerConnection: RTCPeerConnection;
  dataChannel: RTCDataChannel;
  offer: RTCSessionDescriptionInit;
  stop: () => void;
};

const ICE_GATHERING_TIMEOUT_MS = Number.parseInt(
  import.meta.env.VITE_VOICE_ICE_GATHERING_TIMEOUT_MS ?? "1000",
  10,
);

export async function createOpenAIRealtimePeerConnection(
  options: OpenAIRealtimePeerConnectionOptions,
): Promise<OpenAIRealtimePeerConnectionHandle> {
  const startedAt = performance.now();
  const peerConnection = new RTCPeerConnection();
  const dataChannel = peerConnection.createDataChannel("oai-events", { ordered: true });
  let remoteStream: MediaStream | null = null;
  let playbackInitialized = false;

  dataChannel.onopen = () => {
    options.onEvent({ type: "kikiori.data_channel.open" });
  };
  dataChannel.onclose = () => {
    options.onEvent({ type: "kikiori.data_channel.close" });
  };
  dataChannel.onerror = () => {
    options.onEvent({ type: "error", error: { code: "data_channel_failed" } });
  };
  dataChannel.onmessage = (messageEvent) => {
    if (typeof messageEvent.data !== "string") {
      return;
    }
    try {
      const event = JSON.parse(messageEvent.data) as OpenAIRealtimeEvent;
      options.onEvent(event);
    } catch {
      options.onEvent({ type: "error", error: { code: "realtime_event_parse_failed" } });
    }
  };

  peerConnection.onconnectionstatechange = () => {
    options.onConnectionStateChange(peerConnection.connectionState);
  };
  peerConnection.oniceconnectionstatechange = () => {
    options.onConnectionStateChange(peerConnection.iceConnectionState);
  };
  peerConnection.ontrack = (event) => {
    const incomingStream = event.streams[0] ?? new MediaStream([event.track]);
    if (options.remoteAudioElement) {
      if (remoteStream === null) {
        remoteStream = incomingStream;
      } else if (!remoteStream.getTracks().some((track) => track.id === event.track.id)) {
        remoteStream.addTrack(event.track);
      }
      if (options.remoteAudioElement.srcObject !== remoteStream) {
        options.remoteAudioElement.srcObject = remoteStream;
      }
      if (!playbackInitialized) {
        playbackInitialized = true;
        const remoteTrackReceivedAt = performance.now();
        options.remoteAudioElement.onplaying = () => {
          const playbackFirstAudioAt = performance.now();
          console.info("openai_realtime_latency", {
            event: "playback_first_audio",
            timestamp_ms: Math.round(playbackFirstAudioAt),
            remote_track_to_playing_ms: Math.round(playbackFirstAudioAt - remoteTrackReceivedAt),
          });
        };
        void options.remoteAudioElement.play().catch(() => {
          options.onEvent({ type: "error", error: { code: "audio_playback_failed" } });
        });
      }
    }
    options.onStatsChange?.({
      microphoneTrackLive: options.microphoneStream.getAudioTracks().some((track) => track.readyState === "live"),
      remoteAudioTrackReceived: true,
    });
  };

  for (const track of options.microphoneStream.getAudioTracks()) {
    peerConnection.addTrack(track, options.microphoneStream);
  }
  const offer = await peerConnection.createOffer();
  await peerConnection.setLocalDescription(offer);
  const iceStartedAt = performance.now();
  const iceCompleted = await waitForIceGatheringComplete(peerConnection, ICE_GATHERING_TIMEOUT_MS);
  console.info("openai_realtime_connection_latency", {
    stage: "browser_offer_ready",
    voice_session_id: options.voiceSessionId,
    ice_completed: iceCompleted,
    browser_ice_gathering_ms: Math.round(performance.now() - iceStartedAt),
    browser_peer_setup_ms: Math.round(performance.now() - startedAt),
  });

  return {
    peerConnection,
    dataChannel,
    offer: peerConnection.localDescription?.toJSON() ?? offer,
    stop: () => {
      if (options.remoteAudioElement !== null) {
        options.remoteAudioElement.onplaying = null;
        options.remoteAudioElement.pause();
        options.remoteAudioElement.srcObject = null;
      }
      for (const track of remoteStream?.getTracks() ?? []) {
        track.stop();
      }
      dataChannel.close();
      peerConnection.close();
    },
  };
}
