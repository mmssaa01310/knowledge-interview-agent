import type { Dispatch, MutableRefObject, SetStateAction } from "react";
import type { ChatMessage } from "../../../types/app";
import { toUserFacingError } from "../utils/voiceErrors";
import type { Translate } from "../../../i18n";
import type {
  VoiceConversationStatus,
  VoiceDataChannelEvent,
  VoiceSessionResponse,
} from "../types";
import type { VoiceFrontendTrace as VoiceFrontendTraceTelemetry } from "../utils/voiceTelemetry";

export type VoiceRuntimeEventHandlerContext = {
  voiceSessionRef: MutableRefObject<VoiceSessionResponse | null>;
  microphoneStreamRef: MutableRefObject<MediaStream | null>;
  finalizedMessageKeysRef: MutableRefObject<Set<string>>;
  frontendTraceRef: MutableRefObject<VoiceFrontendTraceTelemetry>;
  setConnectionState: Dispatch<SetStateAction<string>>;
  setStatus: Dispatch<SetStateAction<VoiceConversationStatus>>;
  setMessage: Dispatch<SetStateAction<string>>;
  setPartialTranscript: Dispatch<SetStateAction<string>>;
  setRequiresManualPlayback: Dispatch<SetStateAction<boolean>>;
  onMessageRef: MutableRefObject<(message: ChatMessage) => void>;
  onInterviewStateChangedRef: MutableRefObject<() => void>;
  onCompletedRef: MutableRefObject<() => void>;
  cleanupVoiceTransport: (reason: string) => Promise<void>;
  hasPendingInitialReply: (session: VoiceSessionResponse | null) => boolean;
  t: Translate;
};

export function createVoiceRuntimeEventHandler(
  context: VoiceRuntimeEventHandlerContext,
): (event: VoiceDataChannelEvent) => void {
  const {
    voiceSessionRef,
    microphoneStreamRef,
    finalizedMessageKeysRef,
    frontendTraceRef,
    setConnectionState,
    setStatus,
    setMessage,
    setPartialTranscript,
    setRequiresManualPlayback,
    onMessageRef,
    onInterviewStateChangedRef,
    onCompletedRef,
    cleanupVoiceTransport,
    hasPendingInitialReply,
    t,
  } = context;

  return (event: VoiceDataChannelEvent) => {
    switch (event.type) {
      case "connection_state":
        setConnectionState(event.state);
        if (event.state === "connected") {
          setStatus((current) => current === "connecting" ? "listening" : current);
        }
        if (event.state === "failed" || event.state === "closed") {
          setMessage(event.state === "failed" ? t("errors.webrtcFailed") : t("errors.connectionFailed"));
          setStatus((current) => current === "completed" ? current : event.state === "closed" ? "disconnected" : "error");
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
        frontendTraceRef.current.userSpeechEndedAt = performance.now();
        setStatus("finalizing_transcript");
        return;
      case "user_transcript_partial":
        setPartialTranscript(event.text);
        return;
      case "user_transcript_final": {
        setPartialTranscript("");
        const clientTurnId = event.clientTurnId;
        const key = event.turnId ?? clientTurnId ?? `voice-user-${event.questionId ?? "unknown"}-${event.stateVersion ?? "unknown"}-${event.text}`;
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
            voiceClientTurnId: clientTurnId,
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
            retrievedSources: event.retrievedSources,
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
        setStatus("interrupted");
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
        if (event.code === "PROCESS_TIMEOUT") {
          setStatus("processing_interview");
          setMessage(t("interview.voice.processingDelayed"));
          return;
        }
        if (event.message === "audio_playback_failed") {
          setRequiresManualPlayback(true);
          setMessage(toUserFacingError(event.message, t));
          return;
        }
        if (event.fatal === false) {
          setMessage(toUserFacingError(event.message, t, event.code));
          return;
        }
        setStatus("error");
        setMessage(toUserFacingError(event.message, t, event.code));
        return;
      default:
        return;
    }
  };
}
