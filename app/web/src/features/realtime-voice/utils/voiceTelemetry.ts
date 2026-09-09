export const VOICE_SIGNALING_TIMEOUT_MS = Number.parseInt(
  import.meta.env.VITE_VOICE_SIGNALING_TIMEOUT_MS ?? "8000",
  10,
);

export type VoiceStartupTrace = {
  voice_session_ms: number;
  microphone_ms: number;
  ice_config_ms: number;
  peer_connection_ms: number;
  offer_ms: number;
  answer_ms: number;
  total_ms: number;
};

export type VoiceFrontendTrace = {
  userSpeechEndedAt?: number;
  userTranscriptFinalAt?: number;
  processingStartedAt?: number;
  assistantSpeechStartedAt?: number;
  remoteAudioTrackReceivedAt?: number;
  audioPlayEventAt?: number;
};

export function createVoiceStartupTrace(): VoiceStartupTrace {
  return {
    voice_session_ms: 0,
    microphone_ms: 0,
    ice_config_ms: 0,
    peer_connection_ms: 0,
    offer_ms: 0,
    answer_ms: 0,
    total_ms: 0,
  };
}

export function logVoiceStartupEvent({
  event,
  provider,
  voiceSessionId,
  startStartedAt,
}: {
  event: string;
  provider: string;
  voiceSessionId?: string;
  startStartedAt: number;
}): void {
  const monotonicMs = performance.now();
  console.info("voice_startup_latency", {
    event,
    provider,
    voice_session_id: voiceSessionId,
    monotonic_ms: monotonicMs,
    timestamp_ms: Date.now(),
    start_elapsed_ms: Math.round(monotonicMs - startStartedAt),
  });
}
