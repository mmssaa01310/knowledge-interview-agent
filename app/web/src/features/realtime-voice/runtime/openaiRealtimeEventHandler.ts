import type { Dispatch, MutableRefObject, SetStateAction } from "react";
import { realtimeAssistantMessage } from "../realtimeAssistantMessage";
import type { ChatMessage } from "../../../types/app";
import { toUserFacingError } from "../utils/voiceErrors";
import type { Translate } from "../../../i18n";
import type {
  OpenAIRealtimeEvent,
  VoiceConversationStatus,
  VoiceSessionResponse,
} from "../types";
import type { VoiceFrontendTrace } from "../utils/voiceTelemetry";

export type OpenAIRealtimeEventHandlerContext = {
  voiceSessionRef: MutableRefObject<VoiceSessionResponse | null>;
  finalizedMessageKeysRef: MutableRefObject<Set<string>>;
  openAIUserTranscriptRef: MutableRefObject<Map<string, string>>;
  openAIAssistantTranscriptRef: MutableRefObject<Map<string, string>>;
  openAIResponseMetadataRef: MutableRefObject<Map<string, Record<string, string>>>;
  openAITranscriptFinalCountRef: MutableRefObject<number>;
  openAITranscriptItemIdsRef: MutableRefObject<Set<string>>;
  openAIUserMessageInsertCountRef: MutableRefObject<number>;
  openAIAssistantResponseIdsRef: MutableRefObject<Set<string>>;
  openAIAssistantFirstAudioResponseIdsRef: MutableRefObject<Set<string>>;
  openAIActiveResponseRef: MutableRefObject<string | null>;
  frontendTraceRef: MutableRefObject<VoiceFrontendTrace>;
  setStatus: Dispatch<SetStateAction<VoiceConversationStatus>>;
  setMessage: Dispatch<SetStateAction<string>>;
  setPartialTranscript: Dispatch<SetStateAction<string>>;
  setInitialReplyActive: Dispatch<SetStateAction<boolean>>;
  onMessageRef: MutableRefObject<(message: ChatMessage) => void>;
  onInterviewStateChangedRef: MutableRefObject<() => void>;
  onCompletedRef: MutableRefObject<() => void>;
  hasPendingInitialReply: (session: VoiceSessionResponse | null) => boolean;
  t: Translate;
};

export function createOpenAIRealtimeEventHandler(
  context: OpenAIRealtimeEventHandlerContext,
): (event: OpenAIRealtimeEvent) => void {
  const {
    voiceSessionRef,
    finalizedMessageKeysRef,
    openAIUserTranscriptRef,
    openAIAssistantTranscriptRef,
    openAIResponseMetadataRef,
    openAITranscriptFinalCountRef,
    openAITranscriptItemIdsRef,
    openAIUserMessageInsertCountRef,
    openAIAssistantResponseIdsRef,
    openAIAssistantFirstAudioResponseIdsRef,
    openAIActiveResponseRef,
    frontendTraceRef,
    setStatus,
    setMessage,
    setPartialTranscript,
    setInitialReplyActive,
    onMessageRef,
    onInterviewStateChangedRef,
    onCompletedRef,
    hasPendingInitialReply,
    t,
  } = context;

  return (event: OpenAIRealtimeEvent) => {
    const eventType = stringValue(event.type);
    const voiceSessionId = voiceSessionRef.current?.id;
    const emitOpenAIAssistantMessage = ({
      responseId,
      text,
      metadata,
    }: {
      responseId: string;
      text: string;
      voiceSessionId?: string;
      metadata?: Record<string, string>;
    }) => {
      const message = realtimeAssistantMessage(voiceSessionRef.current, metadata, text);
      if (message) onMessageRef.current(message);
    };
    switch (eventType) {
      case "kikiori.data_channel.open":
      case "session.created":
      case "session.updated":
        setStatus(hasPendingInitialReply(voiceSessionRef.current) ? "preparing_initial_reply" : "listening");
        return;
      case "input_audio_buffer.speech_started":
        if (openAIActiveResponseRef.current) {
          console.info("openai_realtime_latency", {
            event: "barge_in_detected",
            response_id: openAIActiveResponseRef.current,
            timestamp_ms: Math.round(performance.now()),
          });
          setStatus("interrupted");
        } else {
          setStatus("listening");
        }
        setPartialTranscript("");
        return;
      case "input_audio_buffer.speech_stopped":
        frontendTraceRef.current.userSpeechEndedAt = performance.now();
        setStatus("finalizing_transcript");
        return;
      case "conversation.item.input_audio_transcription.delta": {
        const itemId = stringValue(event.item_id);
        const delta = stringValue(event.delta);
        if (!itemId || !delta) return;
        const transcript = `${openAIUserTranscriptRef.current.get(itemId) ?? ""}${delta}`;
        openAIUserTranscriptRef.current.set(itemId, transcript);
        setPartialTranscript(transcript);
        return;
      }
      case "conversation.item.input_audio_transcription.completed": {
        const itemId = stringValue(event.item_id);
        const transcript = stringValue(event.transcript).trim();
        if (!transcript) return;
        openAITranscriptFinalCountRef.current += 1;
        if (itemId) {
          openAITranscriptItemIdsRef.current.add(itemId);
        }
        openAIUserTranscriptRef.current.delete(itemId);
        setPartialTranscript("");
        if (!itemId) {
          console.warn("openai_realtime_transcript_final_missing_item_id", {
            voice_session_id: voiceSessionId,
            transcript_chars: transcript.length,
          });
          return;
        }
        const key = `openai-user-${itemId}`;
        const duplicate = finalizedMessageKeysRef.current.has(key);
        console.info("openai_realtime_transcript_final", {
          voice_session_id: voiceSessionId,
          item_id: itemId,
          voice_client_turn_id: `openai-${itemId}`,
          message_key: key,
          action: duplicate ? "duplicate_ignored" : "insert",
        });
        if (duplicate) {
          return;
        }
        finalizedMessageKeysRef.current.add(key);
        openAIUserMessageInsertCountRef.current += 1;
        onMessageRef.current({
          id: key,
          role: "user",
          text: transcript,
          answerToQuestionId: voiceSessionRef.current?.currentQuestionId ?? undefined,
          voiceSessionId,
          voiceClientTurnId: `openai-${itemId}`,
        });
        frontendTraceRef.current.userTranscriptFinalAt = performance.now();
        frontendTraceRef.current.processingStartedAt = performance.now();
        setStatus("processing_interview");
        return;
      }
      case "response.created": {
        const response = objectValue(event.response);
        const responseId = stringValue(response?.id) || stringValue(event.response_id);
        const metadata = objectValue(response?.metadata);
        const isInitialResponse = metadata?.kikiori_kind === "initial";
        if (responseId) {
          openAIAssistantResponseIdsRef.current.add(responseId);
          openAIActiveResponseRef.current = responseId;
          const responseMetadata = metadata ? stringRecord(metadata) : {};
          if (isInitialResponse) {
            openAIResponseMetadataRef.current.set(responseId, {
              ...responseMetadata,
              kikiori_kind: "initial",
              kikiori_response_id: `initial-response-${voiceSessionId ?? ""}`,
              kikiori_turn_id: `initial-${voiceSessionId ?? ""}`,
              ...(voiceSessionRef.current?.initialQuestionId || voiceSessionRef.current?.currentQuestionId
                ? {
                    kikiori_question_id:
                      voiceSessionRef.current.initialQuestionId
                      || voiceSessionRef.current.currentQuestionId
                      || "",
                  }
                : {}),
            });
          } else if (metadata) {
            openAIResponseMetadataRef.current.set(responseId, responseMetadata);
          }
        }
        if (isInitialResponse) {
          setInitialReplyActive(true);
          setStatus("preparing_initial_reply");
        } else {
          setStatus("preparing_audio");
        }
        return;
      }
      case "response.output_audio_transcript.delta": {
        const responseId = stringValue(event.response_id);
        const delta = stringValue(event.delta);
        if (!responseId || !delta) return;
        const transcript = `${openAIAssistantTranscriptRef.current.get(responseId) ?? ""}${delta}`;
        openAIAssistantTranscriptRef.current.set(responseId, transcript);
        console.info("openai_realtime_latency", {
          event: "assistant_transcript_delta",
          response_id: responseId,
          timestamp_ms: Math.round(performance.now()),
        });
        emitOpenAIAssistantMessage({
          responseId,
          text: transcript,
          voiceSessionId,
          metadata: openAIResponseMetadataRef.current.get(responseId),
        });
        return;
      }
      case "response.output_audio.delta": {
        const responseId = stringValue(event.response_id);
        const firstAudioKey = responseId || "unknown-response";
        const isFirstAudio = !openAIAssistantFirstAudioResponseIdsRef.current.has(firstAudioKey);
        if (!isFirstAudio) {
          setStatus("speaking");
          return;
        }
        openAIAssistantFirstAudioResponseIdsRef.current.add(firstAudioKey);
        const firstAudioAt = performance.now();
        frontendTraceRef.current.audioPlayEventAt = firstAudioAt;
        console.info("openai_realtime_latency", {
          event: "assistant_first_audio",
          response_id: responseId || undefined,
          timestamp_ms: Math.round(firstAudioAt),
          speech_end_to_first_audio_ms:
            frontendTraceRef.current.userSpeechEndedAt === undefined
              ? undefined
              : Math.round(firstAudioAt - frontendTraceRef.current.userSpeechEndedAt),
        });
        setStatus("speaking");
        return;
      }
      case "response.output_audio_transcript.done": {
        const responseId = stringValue(event.response_id);
        const transcript = stringValue(event.transcript).trim();
        if (!responseId || !transcript) return;
        openAIAssistantTranscriptRef.current.set(responseId, transcript);
        emitOpenAIAssistantMessage({
          responseId,
          text: transcript,
          voiceSessionId,
          metadata: openAIResponseMetadataRef.current.get(responseId),
        });
        return;
      }
      case "response.output_audio.done":
        return;
      case "response.cancelled":
      case "response.canceled":
        {
          const responseId = stringValue(event.response_id);
          const metadata = responseId
            ? openAIResponseMetadataRef.current.get(responseId)
            : undefined;
          if (metadata?.kikiori_kind === "initial") {
            setInitialReplyActive(false);
          }
        }
        console.info("openai_realtime_latency", {
          event: "response_cancelled",
          response_id: stringValue(event.response_id) || undefined,
          timestamp_ms: Math.round(performance.now()),
        });
        openAIActiveResponseRef.current = null;
        setStatus("listening");
        return;
      case "response.done": {
        const response = objectValue(event.response);
        const responseId = stringValue(response?.id) || stringValue(event.response_id);
        const responseStatus = stringValue(response?.status);
        const completedMetadata = objectValue(response?.metadata);
        const metadata = completedMetadata
          ? stringRecord(completedMetadata)
          : openAIResponseMetadataRef.current.get(responseId);
        if (metadata) openAIResponseMetadataRef.current.set(responseId, metadata);
        const transcript = openAIAssistantTranscriptRef.current.get(responseId);
        if (transcript) emitOpenAIAssistantMessage({ responseId, text: transcript, voiceSessionId, metadata });
        if (metadata?.kikiori_kind === "initial") {
          setInitialReplyActive(false);
          if (voiceSessionRef.current && responseStatus === "completed") {
            voiceSessionRef.current.initialReplyStatus = "sent";
          }
        }
        if (responseId) {
          openAIAssistantResponseIdsRef.current.add(responseId);
        }
        if (responseStatus === "cancelled" || responseStatus === "canceled" || responseStatus === "incomplete") {
          console.info("openai_realtime_latency", {
            event: "response_cancelled",
            response_id: responseId || undefined,
            timestamp_ms: Math.round(performance.now()),
          });
        }
        openAIActiveResponseRef.current = null;
        if (responseStatus === "completed" && metadata?.kikiori_interview_status === "completed") {
          setStatus("completed");
          onCompletedRef.current();
        } else if (responseStatus === "completed") {
          onInterviewStateChangedRef.current();
          setStatus((current) => current === "completed" ? current : "listening");
        }
        if (metadata?.kikiori_kind !== "interview" || metadata?.kikiori_interview_status !== "completed") {
          setStatus((current) => current === "completed" ? current : "listening");
        }
        return;
      }
      case "error": {
        const error = objectValue(event.error);
        const code = stringValue(error?.code);
        setStatus("error");
        setMessage(toUserFacingError(code || "openai_realtime_error", t));
        return;
      }
      default:
        return;
    }
  };
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function stringRecord(value: Record<string, unknown>): Record<string, string> {
  return Object.fromEntries(
    Object.entries(value).filter((entry): entry is [string, string] => typeof entry[1] === "string"),
  );
}
