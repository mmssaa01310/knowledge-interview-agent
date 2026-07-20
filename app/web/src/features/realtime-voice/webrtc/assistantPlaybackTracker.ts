/*
Role:
  Assistant音声segmentの再生開始・drain通知を管理する。

Summary:
  assistant_speech_started / ended と audio element の再生状態から、
  backendへ返す playback started / drained を1segment単位で整列する。

Relations:
  Uses realtime-voice/types event contracts.
  Used by voicePeerConnection.ts for remote audio playback tracking.
*/

import type { VoiceDataChannelEvent } from "../types";

type AssistantPlaybackPayload = {
  responseId?: string | null;
  generation?: number | null;
};

type ActiveAssistantSegment = {
  key: string;
  payload: AssistantPlaybackPayload;
  announcedAtMs: number;
  playbackStartedAtMs: number | null;
  startedNotified: boolean;
};

type AssistantPlaybackTrackerOptions = {
  voiceSessionId: string;
  sendDataChannelMessage: (message: string) => boolean;
  onEvent: (event: VoiceDataChannelEvent) => void;
};

const PLAYBACK_DRAIN_GUARD_MS = 300;
const PLAYBACK_DRAIN_FALLBACK_MS = 3000;

export class AssistantPlaybackTracker {
  private activeAssistantSegment: ActiveAssistantSegment | null = null;
  private pendingDrainTimer: number | null = null;

  constructor(private readonly options: AssistantPlaybackTrackerOptions) {}

  handleDataChannelEvent(
    event: VoiceDataChannelEvent,
    {
      playbackInitialized,
      remoteAudioPaused,
    }: {
      playbackInitialized: boolean;
      remoteAudioPaused: boolean;
    },
  ): void {
    if (event.type === "assistant_speech_started") {
      this.clearPendingDrain();
      const payload: AssistantPlaybackPayload = {
        responseId: event.responseId ?? null,
        generation: event.generation ?? null,
      };
      const announcedAtMs = performance.now();
      this.activeAssistantSegment = {
        key: this.playbackKeyOf(payload),
        payload,
        announcedAtMs,
        playbackStartedAtMs: null,
        startedNotified: false,
      };
      console.info("realtime_voice_frontend_audio", {
        event: "assistant_segment_announced",
        response_id: payload.responseId ?? null,
        generation: payload.generation ?? null,
        at_ms: Math.round(announcedAtMs),
      });
      if (playbackInitialized && !remoteAudioPaused) {
        this.notifyAssistantSegmentPlaybackStarted(this.activeAssistantSegment, "assistant_speech_started");
      }
      return;
    }

    if (event.type !== "assistant_speech_ended") {
      return;
    }

    const payload: AssistantPlaybackPayload = {
      responseId: event.responseId ?? this.activeAssistantSegment?.payload.responseId ?? null,
      generation: event.generation ?? this.activeAssistantSegment?.payload.generation ?? null,
    };
    const endedKey = this.playbackKeyOf(payload);
    if (this.activeAssistantSegment === null || this.activeAssistantSegment.key !== endedKey) {
      console.warn("assistant_speech_ended_ignored", {
        ended_key: endedKey,
        active_key: this.activeAssistantSegment?.key ?? null,
        response_id: payload.responseId ?? null,
        generation: payload.generation ?? null,
      });
      this.options.onEvent(event);
      return;
    }
    const audioDurationMs = Number.isFinite(event.audioDurationMs) && event.audioDurationMs >= 0
      ? event.audioDurationMs
      : null;
    this.scheduleAssistantSegmentDrain(this.activeAssistantSegment, audioDurationMs);
  }

  handleAudioElementPlaying(): void {
    this.notifyAssistantSegmentPlaybackStarted(
      this.activeAssistantSegment,
      "audio_element_onplaying",
    );
  }

  stop(): void {
    this.clearPendingDrain();
    this.activeAssistantSegment = null;
  }

  private clearPendingDrain(): void {
    if (this.pendingDrainTimer !== null) {
      window.clearTimeout(this.pendingDrainTimer);
      this.pendingDrainTimer = null;
    }
  }

  private sendPlaybackEvent(
    type: "assistant_playback_started" | "assistant_playback_drained",
    payload: AssistantPlaybackPayload,
  ): void {
    const sent = this.options.sendDataChannelMessage(
      JSON.stringify({
        type,
        voiceSessionId: this.options.voiceSessionId,
        responseId: payload.responseId ?? null,
        generation: payload.generation ?? null,
      }),
    );
    if (!sent) {
      console.warn("voice_playback_event_not_sent", {
        type,
        response_id: payload.responseId ?? null,
        generation: payload.generation ?? null,
      });
    }
  }

  private playbackKeyOf(payload: AssistantPlaybackPayload): string {
    return `${payload.responseId ?? "null"}:${payload.generation ?? "null"}`;
  }

  private notifyAssistantSegmentPlaybackStarted(
    segment: ActiveAssistantSegment | null,
    reason: "assistant_speech_started" | "audio_element_onplaying",
  ): void {
    if (segment === null || segment.startedNotified) {
      return;
    }
    segment.startedNotified = true;
    segment.playbackStartedAtMs = performance.now();
    console.info("realtime_voice_frontend_audio", {
      event: "assistant_segment_playback_started",
      reason,
      response_id: segment.payload.responseId ?? null,
      generation: segment.payload.generation ?? null,
      announced_to_started_ms: Math.max(
        0,
        Math.round(segment.playbackStartedAtMs - segment.announcedAtMs),
      ),
      at_ms: Math.round(segment.playbackStartedAtMs),
    });
    this.sendPlaybackEvent("assistant_playback_started", segment.payload);
  }

  private scheduleAssistantSegmentDrain(
    segment: ActiveAssistantSegment,
    audioDurationMs: number | null,
  ): void {
    const now = performance.now();
    const playbackStartedAtMs = segment.playbackStartedAtMs ?? segment.announcedAtMs;
    const elapsedPlaybackMs = Math.max(0, now - playbackStartedAtMs);
    const remainingPlaybackMs = audioDurationMs !== null
      ? Math.max(0, audioDurationMs - elapsedPlaybackMs)
      : PLAYBACK_DRAIN_FALLBACK_MS;
    const drainDelayMs = Math.ceil(remainingPlaybackMs + PLAYBACK_DRAIN_GUARD_MS);

    this.clearPendingDrain();

    console.info("realtime_voice_frontend_audio", {
      event: "assistant_segment_drain_scheduled",
      response_id: segment.payload.responseId ?? null,
      generation: segment.payload.generation ?? null,
      audio_duration_ms: audioDurationMs,
      elapsed_playback_ms: Math.round(elapsedPlaybackMs),
      remaining_playback_ms: Math.round(remainingPlaybackMs),
      drain_delay_ms: drainDelayMs,
      degraded: audioDurationMs === null,
      at_ms: Math.round(now),
    });

    this.pendingDrainTimer = window.setTimeout(() => {
      if (this.activeAssistantSegment?.key !== segment.key) {
        this.pendingDrainTimer = null;
        return;
      }

      this.sendPlaybackEvent("assistant_playback_drained", segment.payload);
      console.info("realtime_voice_frontend_audio", {
        event: "assistant_segment_playback_drained",
        response_id: segment.payload.responseId ?? null,
        generation: segment.payload.generation ?? null,
        at_ms: Math.round(performance.now()),
      });

      this.activeAssistantSegment = null;
      this.pendingDrainTimer = null;
    }, drainDelayMs);
  }
}
