import { useCallback, useEffect, useMemo, useRef, type RefObject } from "react";
import { useI18n } from "../../../i18n";
import type { ChatMessage } from "../../../types/app";
import {
  deleteVoicePeerConnection,
  getDefaultLegacyVoiceProvider,
} from "../api/realtimeVoiceClient";
import type { LegacyVoiceProvider, VoiceSessionResponse } from "../types";
import { createOpenAIRealtimeEventHandler } from "../runtime/openaiRealtimeEventHandler";
import { createVoiceRuntimeEventHandler } from "../runtime/voiceRuntimeEventHandler";
import { startVoiceSession, withTimeout } from "../runtime/startVoiceSession";
import { VOICE_SIGNALING_TIMEOUT_MS } from "../utils/voiceTelemetry";
import { useVoiceConversationState } from "./useVoiceConversationState";

type UseRealtimeVoiceInterviewArgs = {
  recordId?: string;
  provider?: LegacyVoiceProvider;
  hasQuestions: boolean;
  remoteAudioRef: RefObject<HTMLAudioElement>;
  onMessage: (message: ChatMessage) => void;
  onInterviewStateChanged: () => void;
  onCompleted: () => void;
};

export function useRealtimeVoiceInterview(args: UseRealtimeVoiceInterviewArgs) {
  const {
    recordId,
    provider = getDefaultLegacyVoiceProvider(),
    hasQuestions,
    remoteAudioRef,
    onMessage,
    onInterviewStateChanged,
    onCompleted,
  } = args;
  const { t } = useI18n();
  const {
    status,
    setStatus,
    message,
    setMessage,
    partialTranscript,
    setPartialTranscript,
    connectionState,
    setConnectionState,
    requiresManualPlayback,
    setRequiresManualPlayback,
    initialReplyActive,
    setInitialReplyActive,
    stats,
    setStats,
    voiceSessionRef,
    peerRef,
    microphoneStreamRef,
    startingRef,
    stoppingRef,
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
    resetConversationTracking,
  } = useVoiceConversationState();
  const onMessageRef = useRef(onMessage);
  const onInterviewStateChangedRef = useRef(onInterviewStateChanged);
  const onCompletedRef = useRef(onCompleted);

  const cleanupVoiceTransport = useCallback(async (reason: string) => {
    const voiceSessionId = voiceSessionRef.current?.id;
    console.info("openai_realtime_duplicate_trace", {
      voice_session_id: voiceSessionId,
      transcript_final_count: openAITranscriptFinalCountRef.current,
      unique_item_ids: openAITranscriptItemIdsRef.current.size,
      user_message_insert_count: openAIUserMessageInsertCountRef.current,
      assistant_response_ids: [...openAIAssistantResponseIdsRef.current],
      cleanup_reason: reason,
    });
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
    setInitialReplyActive(false);
    resetConversationTracking();
    voiceSessionRef.current = null;
    if (voiceSessionId) {
      await withTimeout(
        (signal) => deleteVoicePeerConnection(voiceSessionId, reason, signal),
        VOICE_SIGNALING_TIMEOUT_MS,
      ).catch(() => undefined);
    }
  }, [microphoneStreamRef, openAIAssistantResponseIdsRef, openAITranscriptFinalCountRef, openAITranscriptItemIdsRef, openAIUserMessageInsertCountRef, peerRef, remoteAudioRef, resetConversationTracking, setInitialReplyActive, setPartialTranscript, setStats, voiceSessionRef]);

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
  }, [cleanupVoiceTransport, setConnectionState, setStatus, startingRef, stoppingRef]);

  const handleOpenAIEvent = useMemo(() => createOpenAIRealtimeEventHandler({
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
  }), [
    finalizedMessageKeysRef,
    frontendTraceRef,
    onCompletedRef,
    onInterviewStateChangedRef,
    onMessageRef,
    openAIAssistantResponseIdsRef,
    openAIAssistantTranscriptRef,
    openAIActiveResponseRef,
    openAIResponseMetadataRef,
    openAITranscriptFinalCountRef,
    openAITranscriptItemIdsRef,
    openAIUserMessageInsertCountRef,
    openAIUserTranscriptRef,
    setInitialReplyActive,
    setMessage,
    setPartialTranscript,
    setStatus,
    t,
    voiceSessionRef,
  ]);

  const handleEvent = useMemo(() => createVoiceRuntimeEventHandler({
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
  }), [
    cleanupVoiceTransport,
    finalizedMessageKeysRef,
    frontendTraceRef,
    microphoneStreamRef,
    onCompletedRef,
    onInterviewStateChangedRef,
    onMessageRef,
    setConnectionState,
    setMessage,
    setPartialTranscript,
    setRequiresManualPlayback,
    setStatus,
    t,
    voiceSessionRef,
  ]);

  const start = useCallback(async () => {
    if (startingRef.current || peerRef.current) {
      return;
    }
    if (!recordId) {
      setStatus("error");
      setMessage(t("interview.selectedRecordNone"));
      return;
    }
    await startVoiceSession({
      recordId,
      provider,
      hasQuestions,
      remoteAudioRef,
      voiceSessionRef,
      peerRef,
      microphoneStreamRef,
      startingRef,
      resetConversationTracking,
      setStatus,
      setMessage,
      setInitialReplyActive,
      setRequiresManualPlayback,
      setConnectionState,
      setStats,
      onMessageRef,
      handleEvent,
      handleOpenAIEvent,
      hasPendingInitialReply,
      t,
    });
  }, [
    handleEvent,
    handleOpenAIEvent,
    hasQuestions,
    microphoneStreamRef,
    peerRef,
    provider,
    recordId,
    remoteAudioRef,
    resetConversationTracking,
    setConnectionState,
    setInitialReplyActive,
    setMessage,
    setRequiresManualPlayback,
    setStats,
    setStatus,
    startingRef,
    t,
    voiceSessionRef,
  ]);

  useEffect(() => {
    const onBeforeUnload = () => {
      const voiceSessionId = voiceSessionRef.current?.id;
      microphoneStreamRef.current?.getTracks().forEach((track) => track.stop());
      peerRef.current?.stop();
      if (voiceSessionId) {
        void deleteVoicePeerConnection(voiceSessionId, "browser_unload", undefined, true).catch(() => undefined);
      }
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      void stop("component_unmounted");
    };
  }, [microphoneStreamRef, peerRef, stop, voiceSessionRef]);

  const isActive = !["idle", "completed", "error", "disconnected"].includes(status);

  return {
    status,
    message,
    partialTranscript,
    connectionState,
    stats,
    requiresManualPlayback,
    initialReplyActive,
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

function hasPendingInitialReply(session: VoiceSessionResponse | null): boolean {
  if (!session?.initialReplyText?.trim()) {
    return false;
  }
  return session.initialReplyStatus === "pending" || session.initialReplyStatus === "sending";
}
