/*
Role:
  browser側のRealtime Voice用RTCPeerConnection実装。

Summary:
  microphone track、remote audio、data channelイベントを管理し、
  assistant segmentの再生開始・drain通知をbackendへ返す。

Relations:
  Uses realtime-voice/types contracts.
  Used by frontend hooks that start and control voice interviews.
*/

import type { IceServerConfig, VoiceConnectionStats, VoiceDataChannelEvent } from "../types";
import { AssistantPlaybackTracker } from "./assistantPlaybackTracker";

type VoicePeerConnectionOptions = {
  voiceSessionId: string;
  iceServers: IceServerConfig[];
  microphoneStream: MediaStream;
  remoteAudioElement: HTMLAudioElement | null;
  onEvent: (event: VoiceDataChannelEvent) => void;
  onConnectionStateChange: (state: string) => void;
  onStatsChange?: (stats: VoiceConnectionStats) => void;
};

const ICE_GATHERING_TIMEOUT_MS = Number.parseInt(
  import.meta.env.VITE_VOICE_ICE_GATHERING_TIMEOUT_MS ?? "1000",
  10,
);

export type VoicePeerConnectionHandle = {
  peerConnection: RTCPeerConnection;
  dataChannel: RTCDataChannel;
  offer: RTCSessionDescriptionInit;
  stop: () => void;
};

export async function createVoicePeerConnection(
  options: VoicePeerConnectionOptions,
): Promise<VoicePeerConnectionHandle> {
  const startedAt = performance.now();
  const peerConnection = new RTCPeerConnection({
    iceServers: options.iceServers.map((server) => ({
      urls: server.urls,
      username: server.username ?? undefined,
      credential: server.credential ?? undefined,
    })),
    iceTransportPolicy: "all",
  });
  const dataChannel = peerConnection.createDataChannel("voice-events", { ordered: true });
  let remoteStream: MediaStream | null = null;
  let remoteTrackId: string | null = null;
  let playbackInitialized = false;
  const playbackTracker = new AssistantPlaybackTracker({
    voiceSessionId: options.voiceSessionId,
    sendDataChannelMessage: (message) => {
      if (dataChannel.readyState !== "open") {
        return false;
      }
      dataChannel.send(message);
      return true;
    },
    onEvent: options.onEvent,
  });

  dataChannel.onmessage = (messageEvent) => {
    if (typeof messageEvent.data !== "string") {
      return;
    }
    try {
      const event = JSON.parse(messageEvent.data) as VoiceDataChannelEvent;
      playbackTracker.handleDataChannelEvent(event, {
        playbackInitialized,
        remoteAudioPaused: options.remoteAudioElement?.paused ?? true,
      });
      options.onEvent(event);
    } catch (error) {
      console.warn("voice_event_parse_failed", {
        name: error instanceof Error ? error.name : "unknown",
      });
      options.onEvent({
        type: "error",
        voiceSessionId: options.voiceSessionId,
        message: "voice_event_parse_failed",
      });
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
    if (options.remoteAudioElement && incomingStream) {
      const remoteAudioTrackReceivedAt = performance.now();
      if (remoteStream === null) {
        remoteStream = incomingStream;
      } else if (!remoteStream.getTracks().some((track) => track.id === event.track.id)) {
        remoteStream.addTrack(event.track);
      }
      if (playbackInitialized && remoteTrackId === event.track.id && options.remoteAudioElement.srcObject === remoteStream) {
        return;
      }
      remoteTrackId = event.track.id;
      if (options.remoteAudioElement.srcObject !== remoteStream) {
        options.remoteAudioElement.srcObject = remoteStream;
      }
      if (!playbackInitialized) {
        playbackInitialized = true;
        console.info("realtime_voice_frontend_audio", {
          remote_track_playback_initialized_at: Math.round(remoteAudioTrackReceivedAt),
        });
        options.remoteAudioElement.onplaying = () => {
          playbackTracker.handleAudioElementPlaying();
          console.info("realtime_voice_frontend_audio", {
            event: "remote_track_playing",
            frontend_audio_playing_event_at: Math.round(performance.now()),
            remote_track_received_to_playing_ms: Math.max(
              0,
              Math.round(performance.now() - remoteAudioTrackReceivedAt),
            ),
          });
        };
        console.info("frontend_audio_play_called");
        options.remoteAudioElement.play().then(() => {
          console.info("frontend_audio_play_succeeded");
        }).catch((error) => {
          playbackInitialized = false;
          console.warn("frontend_audio_play_failed", {
            name: error instanceof Error ? error.name : "unknown",
          });
          options.onEvent({
            type: "error",
            voiceSessionId: options.voiceSessionId,
            message: "audio_playback_failed",
          });
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
  console.info("realtime_voice_connection_latency", {
    stage: "browser_offer_ready",
    ice_completed: iceCompleted,
    ice_gathering_state: peerConnection.iceGatheringState,
    browser_ice_gathering_ms: Math.round(performance.now() - iceStartedAt),
    browser_peer_setup_ms: Math.round(performance.now() - startedAt),
  });

  return {
    peerConnection,
    dataChannel,
    offer: peerConnection.localDescription?.toJSON() ?? offer,
    stop: () => {
      playbackTracker.stop();
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

export async function waitForIceGatheringComplete(
  peerConnection: RTCPeerConnection,
  timeoutMs = 10000,
): Promise<boolean> {
  if (peerConnection.iceGatheringState === "complete") {
    return true;
  }
  return await new Promise<boolean>((resolve) => {
    const onStateChange = () => {
      if (peerConnection.iceGatheringState === "complete") {
        peerConnection.removeEventListener("icegatheringstatechange", onStateChange);
        window.clearTimeout(timeoutId);
        resolve(true);
      }
    };
    const timeoutId = window.setTimeout(() => {
      peerConnection.removeEventListener("icegatheringstatechange", onStateChange);
      resolve(false);
    }, timeoutMs);
    peerConnection.addEventListener("icegatheringstatechange", onStateChange);
  });
}
