import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { ApiError } from "../../../lib/api";
import { useI18n, type Translate } from "../../../i18n";
import type { ChatMessage } from "../../../types/app";
import {
  createVoiceSession,
  deleteVoicePeerConnection,
  getVoiceIceConfig,
  sendVoiceOffer,
} from "../api/realtimeVoiceClient";
import type {
  VoiceConnectionStats,
  VoiceConversationStatus,
  VoiceDataChannelEvent,
  VoiceSessionResponse,
} from "../types";
import { createVoicePeerConnection, type VoicePeerConnectionHandle } from "../webrtc/voicePeerConnection";

const VOICE_SIGNALING_TIMEOUT_MS = Number.parseInt(
  import.meta.env.VITE_VOICE_SIGNALING_TIMEOUT_MS ?? "8000",
  10,
);

type UseRealtimeVoiceInterviewArgs = {
  recordId?: string;
  hasQuestions: boolean;
  remoteAudioRef: RefObject<HTMLAudioElement>;
  onMessage: (message: ChatMessage) => void;
  onInterviewStateChanged: () => void;
  onCompleted: () => void;
};

export function useRealtimeVoiceInterview(args: UseRealtimeVoiceInterviewArgs) {
  const {
    recordId,
    hasQuestions,
    remoteAudioRef,
    onMessage,
    onInterviewStateChanged,
    onCompleted,
  } = args;
  const { t } = useI18n();
  const [status, setStatus] = useState<VoiceConversationStatus>("idle");
  const [message, setMessage] = useState("");
  const [partialTranscript, setPartialTranscript] = useState("");
  const [connectionState, setConnectionState] = useState("");
  const [requiresManualPlayback, setRequiresManualPlayback] = useState(false);
  const [stats, setStats] = useState<VoiceConnectionStats>({
    microphoneTrackLive: false,
    remoteAudioTrackReceived: false,
  });
  const voiceSessionRef = useRef<VoiceSessionResponse | null>(null);
  const peerRef = useRef<VoicePeerConnectionHandle | null>(null);
  const microphoneStreamRef = useRef<MediaStream | null>(null);
  const onMessageRef = useRef(onMessage);
  const onInterviewStateChangedRef = useRef(onInterviewStateChanged);
  const onCompletedRef = useRef(onCompleted);
  const startingRef = useRef(false);
  const stoppingRef = useRef(false);
  const finalizedMessageKeysRef = useRef(new Set<string>());
  const frontendTraceRef = useRef<{
    userTranscriptFinalAt?: number;
    processingStartedAt?: number;
    assistantSpeechStartedAt?: number;
    remoteAudioTrackReceivedAt?: number;
    audioPlayEventAt?: number;
  }>({});

  const cleanupVoiceTransport = useCallback(async (reason: string) => {
    const voiceSessionId = voiceSessionRef.current?.id;
    microphoneStreamRef.current?.getTracks().forEach((track) => track.stop());
    microphoneStreamRef.current = null;
    const peer = peerRef.current;
    peerRef.current = null;
    peer?.stop();
    if (remoteAudioRef.current) {
      remoteAudioRef.current.srcObject = null;
    }
    setStats({ microphoneTrackLive: false, remoteAudioTrackReceived: false });
    setPartialTranscript("");
    voiceSessionRef.current = null;
    if (voiceSessionId) {
      await withTimeout(
        (signal) => deleteVoicePeerConnection(voiceSessionId, reason, signal),
        VOICE_SIGNALING_TIMEOUT_MS,
      ).catch(() => undefined);
    }
  }, [remoteAudioRef]);

  useEffect(() => {
    onMessageRef.current = onMessage;
    onInterviewStateChangedRef.current = onInterviewStateChanged;
    onCompletedRef.current = onCompleted;
  }, [onCompleted, onInterviewStateChanged, onMessage]);

  const stop = useCallback(async (reason = "user_requested") => {
    if (stoppingRef.current) {
      return;
    }
    stoppingRef.current = true;
    setStatus((current) => current === "completed" ? current : "stopping");
    try {
      await cleanupVoiceTransport(reason);
      setConnectionState("closed");
      setStatus((current) => current === "completed" ? "completed" : "idle");
      onInterviewStateChangedRef.current();
    } finally {
      stoppingRef.current = false;
      startingRef.current = false;
    }
  }, [cleanupVoiceTransport]);

  const handleEvent = useCallback((event: VoiceDataChannelEvent) => {
    switch (event.type) {
      case "connection_state":
        setConnectionState(event.state);
        if (event.state === "connected") {
          setStatus((current) => current === "connecting" ? "listening" : current);
        }
        if (event.state === "failed" || event.state === "closed") {
            setMessage(event.state === "failed" ? t("errors.webrtcFailed") : t("errors.connectionFailed"));
          setStatus((current) => current === "completed" ? current : "error");
        }
        return;
      case "runtime_ready":
        setStatus(hasPendingInitialReply(voiceSessionRef.current) ? "preparing_initial_reply" : "listening");
        return;
      case "runtime_reconnecting":
        setStatus("connecting");
        setMessage(t("interview.voice.reconnecting"));
        return;
      case "input_state_changed":
        switch (event.inputState) {
          case "ANSWER_LISTENING":
          case "CONFIRMATION_LISTENING":
            setStatus("listening");
            break;
          case "ANSWER_PROCESSING":
            setStatus("processing_interview");
            break;
          case "ASSISTANT_SPEAKING":
            setStatus("speaking");
            break;
          case "INTERVIEW_COMPLETED":
            microphoneStreamRef.current?.getTracks().forEach((track) => track.stop());
            microphoneStreamRef.current = null;
            setStatus("completed");
            onCompletedRef.current();
            break;
          case "INPUT_UNAVAILABLE":
            setMessage(t("interview.voice.continueText"));
            setStatus("error");
            void cleanupVoiceTransport("transcribe_unavailable");
            break;
          default:
            break;
        }
        return;
      case "user_speech_started":
        setPartialTranscript("");
        setStatus("listening");
        return;
      case "user_speech_ended":
        setStatus("finalizing_transcript");
        return;
      case "user_transcript_partial":
        setPartialTranscript(event.text);
        return;
      case "user_transcript_final": {
        setPartialTranscript("");
        const key = event.turnId ?? `voice-user-${event.questionId ?? "unknown"}-${event.stateVersion ?? "unknown"}-${event.text}`;
        if (!finalizedMessageKeysRef.current.has(key)) {
          finalizedMessageKeysRef.current.add(key);
          onMessageRef.current({
            id: key,
            role: "user",
            text: event.text,
            turnType: event.turnType ?? "ANSWER",
            answerToQuestionId: event.questionId ?? undefined,
            voiceSessionId: event.voiceSessionId,
            voiceTurnId: event.turnId ?? undefined,
          });
        }
        frontendTraceRef.current.userTranscriptFinalAt = performance.now();
        frontendTraceRef.current.processingStartedAt = performance.now();
        setStatus("processing_interview");
        return;
      }
      case "assistant_response_preparing":
        setStatus("preparing_audio");
        return;
      case "assistant_speech_started":
        frontendTraceRef.current.assistantSpeechStartedAt = performance.now();
        if (frontendTraceRef.current.userTranscriptFinalAt !== undefined) {
          console.info("realtime_voice_frontend_latency", {
            frontend_transcript_to_speech_started_ms: Math.round(
              frontendTraceRef.current.assistantSpeechStartedAt - frontendTraceRef.current.userTranscriptFinalAt,
            ),
          });
        }
        setStatus("speaking");
        return;
      case "assistant_transcript_final": {
        const key = event.responseId ?? `voice-assistant-${event.generation ?? "unknown"}-${event.text}`;
        if (!finalizedMessageKeysRef.current.has(key)) {
          finalizedMessageKeysRef.current.add(key);
          onMessageRef.current({
            id: key,
            role: "assistant",
            text: event.text,
            questionId: event.questionId ?? undefined,
            voiceSessionId: event.voiceSessionId,
            voiceResponseId: event.responseId ?? undefined,
          });
        }
        return;
      }
      case "assistant_speech_ended":
        if (
          frontendTraceRef.current.assistantSpeechStartedAt !== undefined
          && frontendTraceRef.current.audioPlayEventAt !== undefined
        ) {
          console.info("realtime_voice_frontend_latency", {
            frontend_speech_started_to_audio_play_ms: Math.round(
              frontendTraceRef.current.audioPlayEventAt - frontendTraceRef.current.assistantSpeechStartedAt,
            ),
          });
        }
        onInterviewStateChangedRef.current();
        return;
      case "assistant_interrupted":
        setStatus("processing");
        return;
      case "assistant_backchannel":
        return;
      case "interview_state":
        onInterviewStateChangedRef.current();
        return;
      case "interview_completed":
        setStatus("completed");
        onCompletedRef.current();
        return;
      case "initial_reply_sent":
        setStatus(hasPendingInitialReply(voiceSessionRef.current) ? "preparing_initial_reply" : "listening");
        return;
      case "error":
        if (event.message === "audio_playback_failed") {
          setRequiresManualPlayback(true);
          setMessage(toUserFacingError(event.message, t));
          return;
        }
        if (event.fatal === false) {
          setMessage(toUserFacingError(event.message, t));
          return;
        }
        setStatus("error");
        setMessage(toUserFacingError(event.message, t));
        return;
      default:
        return;
    }
  }, [cleanupVoiceTransport, hasQuestions, t]);

  const start = useCallback(async () => {
    if (startingRef.current || peerRef.current) {
      return;
    }
    if (!recordId) {
      setStatus("error");
      setMessage(t("interview.selectedRecordNone"));
      return;
    }
    if (!hasQuestions) {
      setStatus("error");
      setMessage(t("errors.voiceMissingQuestions"));
      return;
    }

    startingRef.current = true;
    setRequiresManualPlayback(false);
    setMessage("");
    setStatus("checking");
    let failedStage = "voice_session";
    const startStartedAt = performance.now();
    const trace = {
      voice_session_ms: 0,
      microphone_ms: 0,
      ice_config_ms: 0,
      peer_connection_ms: 0,
      offer_ms: 0,
      answer_ms: 0,
      total_ms: 0,
    };
    let microphonePromise: Promise<MediaStream> | null = null;
    let microphoneStreamForStart: MediaStream | null = null;
    let stopMicrophoneWhenReady = false;
    try {
      // The API may need to generate the first structured-interview question.
      // Start the browser microphone handshake at the same time so this latency
      // is not added to the server-side question-generation latency.
      const microphoneStartedAt = performance.now();
      microphonePromise = navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      }).then((stream) => {
        microphoneStreamForStart = stream;
        trace.microphone_ms = Math.round(performance.now() - microphoneStartedAt);
        if (stopMicrophoneWhenReady) {
          stream.getTracks().forEach((track) => track.stop());
        }
        return stream;
      });

      let stageStartedAt = performance.now();
      const voiceSession = await withTimeout(
        (signal) => createVoiceSession(recordId, signal),
        VOICE_SIGNALING_TIMEOUT_MS,
      );
      trace.voice_session_ms = Math.round(performance.now() - stageStartedAt);
      voiceSessionRef.current = voiceSession;

      failedStage = "microphone_or_ice_config";
      const iceStartedAt = performance.now();
      setStatus("connecting");
      const iceConfigPromise = withTimeout(
        (signal) => getVoiceIceConfig(voiceSession.id, signal),
        VOICE_SIGNALING_TIMEOUT_MS,
      ).then((config) => {
        trace.ice_config_ms = Math.round(performance.now() - iceStartedAt);
        return config;
      });
      const [microphoneResult, iceConfigResult] = await Promise.allSettled([
        microphonePromise,
        iceConfigPromise,
      ]);
      if (microphoneResult.status === "rejected") {
        failedStage = "microphone";
        throw microphoneResult.reason;
      }
      if (iceConfigResult.status === "rejected") {
        failedStage = "ice_config";
        throw iceConfigResult.reason;
      }
      const microphoneStream = microphoneResult.value;
      const iceConfig = iceConfigResult.value;
      microphoneStreamForStart = microphoneStream;
      microphoneStreamRef.current = microphoneStream;
      setStats((current) => ({
        ...current,
        microphoneTrackLive: microphoneStream.getAudioTracks().some((track) => track.readyState === "live"),
      }));

      failedStage = "peer_connection";
      stageStartedAt = performance.now();
      const peerHandle = await createVoicePeerConnection({
        voiceSessionId: voiceSession.id,
        iceServers: iceConfig.iceServers,
        microphoneStream,
        remoteAudioElement: remoteAudioRef.current,
        onEvent: handleEvent,
        onConnectionStateChange: (state) => {
          setConnectionState(state);
          if (state === "connected" || state === "completed") {
            setStatus((current) => current === "connecting" ? "listening" : current);
          }
          if (state === "failed" || state === "closed") {
            setMessage(state === "failed" ? t("errors.webrtcFailed") : t("errors.connectionFailed"));
            setStatus((current) => current === "completed" ? current : "error");
          }
        },
        onStatsChange: setStats,
      });
      trace.peer_connection_ms = Math.round(performance.now() - stageStartedAt);
      peerRef.current = peerHandle;
      failedStage = "offer";
      stageStartedAt = performance.now();
      const answer = await withTimeout(
        (signal) => sendVoiceOffer(voiceSession.id, peerHandle.offer, signal),
        VOICE_SIGNALING_TIMEOUT_MS,
      );
      trace.offer_ms = Math.round(performance.now() - stageStartedAt);
      failedStage = "answer";
      stageStartedAt = performance.now();
      await peerHandle.peerConnection.setRemoteDescription(answer);
      trace.answer_ms = Math.round(performance.now() - stageStartedAt);
      failedStage = "playback";
      await remoteAudioRef.current?.play().catch(() => undefined);
      trace.total_ms = Math.round(performance.now() - startStartedAt);
      console.info("realtime_voice_connection_latency", trace);
    } catch (error) {
      stopMicrophoneWhenReady = true;
      microphoneStreamForStart?.getTracks().forEach((track) => track.stop());
      // If the Voice Session request fails while the permission prompt is still
      // open, consume the eventual rejection and stop a late-arriving stream.
      void microphonePromise?.catch(() => undefined);
      console.warn("realtime_voice_start_failed", {
        stage: failedStage,
        errorName: error instanceof Error ? error.name : "unknown",
        status: error instanceof ApiError ? error.status : undefined,
        detail: error instanceof ApiError ? error.detail : undefined,
      });
      microphoneStreamRef.current?.getTracks().forEach((track) => track.stop());
      microphoneStreamRef.current = null;
      peerRef.current?.stop();
      peerRef.current = null;
      const voiceSessionId = voiceSessionRef.current?.id;
      if (voiceSessionId) {
        await withTimeout(
          (signal) => deleteVoicePeerConnection(voiceSessionId, `start_failed_${failedStage}`, signal),
          VOICE_SIGNALING_TIMEOUT_MS,
        ).catch(() => undefined);
      }
      setStatus("error");
      setMessage(toStartErrorMessage(error, failedStage, t));
    } finally {
      startingRef.current = false;
    }
  }, [recordId, hasQuestions, handleEvent, remoteAudioRef, t]);

  useEffect(() => {
    const onBeforeUnload = () => {
      microphoneStreamRef.current?.getTracks().forEach((track) => track.stop());
      peerRef.current?.stop();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      void stop("component_unmounted");
    };
  }, [stop]);

  const isActive = !["idle", "completed", "error"].includes(status);

  return {
    status,
    message,
    partialTranscript,
    connectionState,
    stats,
    requiresManualPlayback,
    isActive,
    start,
    stop: () => stop("user_requested"),
    playRemoteAudio: async () => {
      try {
        await remoteAudioRef.current?.play();
        setRequiresManualPlayback(false);
      } catch {
        setRequiresManualPlayback(true);
      }
    },
  };
}

function toStartErrorMessage(error: unknown, stage: string, t: Translate): string {
  if (error instanceof DOMException && error.name === "NotAllowedError") {
    return t("errors.microphoneDenied");
  }
  if (error instanceof DOMException && error.name === "NotFoundError") {
    return t("errors.microphoneNotFound");
  }
  if (error instanceof ApiError && error.status === 401) {
    return t("errors.voiceUnauthorized");
  }
  if (error instanceof ApiError && error.status === 403) {
    return t("errors.voiceForbidden");
  }
  if (error instanceof ApiError && error.status === 409 && error.detail === "voice_session_missing_questions") {
    return t("errors.voiceMissingQuestions");
  }
  if (
    error instanceof ApiError
    && error.status === 409
    && (error.detail === "voice_session_missing_current_question" || error.detail === "voice_session_completed")
  ) {
    return t("errors.voiceNoQuestion");
  }
  if (error instanceof ApiError && error.status === 409 && error.detail === "voice_session_already_connected") {
    return t("errors.voiceAlreadyConnected");
  }
  if (stage === "voice_session") {
    return t("errors.voiceSessionFailed");
  }
  if (stage === "microphone" || stage === "microphone_or_ice_config") {
    return t("errors.microphonePrepareFailed");
  }
  if (stage === "ice_config") {
    return t("errors.iceConfigFailed");
  }
  if (stage === "offer" || stage === "answer" || stage === "peer_connection") {
    return t("errors.webrtcFailed");
  }
  return t("errors.voiceConnectFailed");
}

function toUserFacingError(message: string | undefined, t: Translate): string {
  if (message === "audio_playback_failed") {
    return t("errors.audioPlaybackFailed");
  }
  if (message === "transcribe_stream_failed") {
    return t("errors.transcribeFailed");
  }
  if (message === "polly_synthesis_failed") {
    return t("errors.pollyFailed");
  }
  if (message) {
    return t("errors.voiceErrorWithMessage", { message });
  }
  return t("errors.voiceError");
}

function hasPendingInitialReply(session: VoiceSessionResponse | null) {
  if (!session?.initialReplyText?.trim()) {
    return false;
  }
  return session.initialReplyStatus === "pending" || session.initialReplyStatus === "sending";
}

async function withTimeout<T>(
  operation: (signal: AbortSignal) => Promise<T>,
  timeoutMs: number,
): Promise<T> {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await operation(controller.signal);
  } finally {
    window.clearTimeout(timeoutId);
  }
}
