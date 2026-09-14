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
  console.info("openai_realtime_peer_connection_created", {
    voice_session_id: options.voiceSessionId,
  });
  console.info("openai_realtime_data_channel_created", {
    voice_session_id: options.voiceSessionId,
    label: dataChannel.label,
  });
  let remoteStream: MediaStream | null = null;
  let playbackInitialized = false;
  let microphoneStatsTimer: number | null = null;
  let microphoneStatsInFlight = false;
  let previousMicrophoneBytesSent: number | undefined;
  let previousMicrophonePacketsSent: number | undefined;

  const sampleMicrophoneRtp = async () => {
    if (microphoneStatsInFlight) return;
    microphoneStatsInFlight = true;
    try {
      const senders = peerConnection
        .getSenders()
        .filter((sender) => sender.track?.kind === "audio");
      const reports = await Promise.all(senders.map((sender) => sender.getStats()));
      let bytesSent = 0;
      let packetsSent = 0;
      let audioLevel: number | undefined;
      let outboundAudioReportCount = 0;
      for (const report of reports) {
        report.forEach((stat) => {
          const metric = stat as RTCStats & {
            audioLevel?: number;
            bytesSent?: number;
            kind?: string;
            mediaType?: string;
            packetsSent?: number;
          };
          if (
            metric.type === "outbound-rtp"
            && (metric.kind === "audio" || metric.mediaType === "audio")
          ) {
            bytesSent += metric.bytesSent ?? 0;
            packetsSent += metric.packetsSent ?? 0;
            outboundAudioReportCount += 1;
          }
          if (
            metric.type === "media-source"
            && (metric.kind === "audio" || metric.mediaType === "audio")
            && typeof metric.audioLevel === "number"
          ) {
            audioLevel = metric.audioLevel;
          }
        });
      }
      const bytesSentDelta = previousMicrophoneBytesSent === undefined
        ? undefined
        : bytesSent - previousMicrophoneBytesSent;
      const packetsSentDelta = previousMicrophonePacketsSent === undefined
        ? undefined
        : packetsSent - previousMicrophonePacketsSent;
      previousMicrophoneBytesSent = bytesSent;
      previousMicrophonePacketsSent = packetsSent;
      console.info("openai_realtime_microphone_rtp", {
        voice_session_id: options.voiceSessionId,
        timestamp_ms: Math.round(performance.now()),
        connection_state: peerConnection.connectionState,
        microphone_track_live: options.microphoneStream
          .getAudioTracks()
          .some((track) => track.readyState === "live"),
        outbound_audio_report_count: outboundAudioReportCount,
        bytes_sent_delta: bytesSentDelta,
        packets_sent_delta: packetsSentDelta,
        audio_level: audioLevel,
      });
    } catch (error) {
      console.debug("openai_realtime_microphone_rtp_unavailable", {
        voice_session_id: options.voiceSessionId,
        error_name: error instanceof Error ? error.name : "unknown",
      });
    } finally {
      microphoneStatsInFlight = false;
    }
  };

  const stopMicrophoneRtpSampling = () => {
    if (microphoneStatsTimer === null) return;
    window.clearInterval(microphoneStatsTimer);
    microphoneStatsTimer = null;
  };

  dataChannel.onopen = () => {
    console.info("openai_realtime_data_channel_open", {
      voice_session_id: options.voiceSessionId,
      label: dataChannel.label,
      ready_state: dataChannel.readyState,
      connection_state: peerConnection.connectionState,
      ice_connection_state: peerConnection.iceConnectionState,
      signaling_state: peerConnection.signalingState,
    });
    options.onEvent({ type: "kikiori.data_channel.open" });
  };
  dataChannel.onclose = () => {
    console.info("openai_realtime_data_channel_close", {
      voice_session_id: options.voiceSessionId,
      label: dataChannel.label,
      ready_state: dataChannel.readyState,
      connection_state: peerConnection.connectionState,
      ice_connection_state: peerConnection.iceConnectionState,
      signaling_state: peerConnection.signalingState,
    });
    options.onEvent({ type: "kikiori.data_channel.close" });
  };
  dataChannel.onerror = (event) => {
    const rtcErrorEvent = event as RTCErrorEvent;
    const nativeError = rtcErrorEvent.error;
    const errorDetail = (
      rtcErrorEvent as RTCErrorEvent & { errorDetail?: string }
    ).errorDetail;
    console.warn("openai_realtime_data_channel_error", {
      voice_session_id: options.voiceSessionId,
      label: dataChannel.label,
      ready_state: dataChannel.readyState,
      error_name: nativeError?.name,
      error_message: nativeError?.message,
      error_detail: errorDetail,
      connection_state: peerConnection.connectionState,
      ice_connection_state: peerConnection.iceConnectionState,
      signaling_state: peerConnection.signalingState,
    });
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
    if (peerConnection.connectionState === "connected" && microphoneStatsTimer === null) {
      void sampleMicrophoneRtp();
      microphoneStatsTimer = window.setInterval(() => {
        void sampleMicrophoneRtp();
      }, 1000);
    } else if (["failed", "closed"].includes(peerConnection.connectionState)) {
      stopMicrophoneRtpSampling();
    }
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
      stopMicrophoneRtpSampling();
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
