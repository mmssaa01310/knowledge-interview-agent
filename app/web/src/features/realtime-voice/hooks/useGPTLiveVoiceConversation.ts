import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { useI18n } from "../../../i18n";
import {
  submitGPTLiveCapture,
  getGPTLiveCaptureStatus,
  submitGPTLiveStateUpdate,
} from "../api/realtimeVoiceClient";
import type { VoiceConnectionStats, VoiceConversationStatus } from "../types";
import {
  createGPTLivePeerConnection,
  type GPTLiveEvent,
  type GPTLivePeerConnectionHandle,
} from "../webrtc/gptLivePeerConnection";
import { createLiveTranscriptCapture } from "../utils/liveTranscriptCapture";
import { toStartErrorMessage } from "../utils/voiceErrors";
import { logVoiceStartupEvent } from "../utils/voiceTelemetry";

type UseGPTLiveVoiceConversationArgs = {
  enabled: boolean;
  remoteAudioRef: RefObject<HTMLAudioElement>;
  recordId?: string;
  onInterviewStateChanged?: () => void | Promise<void>;
};

export type GPTLiveTranscriptLine = {
  id: string;
  role: "user" | "assistant";
  text: string;
};

type DelegationTranscriptSnapshot = {
  transcript: string;
  sealed: boolean;
};

const EMPTY_STATS: VoiceConnectionStats = {
  microphoneTrackLive: false,
  remoteAudioTrackReceived: false,
};
const GPT_LIVE_START_TIMEOUT_MS = 30000;
const GPT_LIVE_RESPONSE_START_TIMEOUT_MS = 5000;
const GPT_LIVE_WAIT_NOTICE_TIMEOUT_MS = 2500;

export function useGPTLiveVoiceConversation(args: UseGPTLiveVoiceConversationArgs) {
  const {
    enabled,
    onInterviewStateChanged,
    recordId,
    remoteAudioRef,
  } = args;
  const { t } = useI18n();
  const [status, setStatus] = useState<VoiceConversationStatus>("idle");
  const [message, setMessage] = useState("");
  const [connectionState, setConnectionState] = useState("new");
  const [requiresManualPlayback, setRequiresManualPlayback] = useState(false);
  const [stats, setStats] = useState<VoiceConnectionStats>(EMPTY_STATS);
  const [transcriptLines, setTranscriptLines] = useState<GPTLiveTranscriptLine[]>([]);
  const peerRef = useRef<GPTLivePeerConnectionHandle | null>(null);
  const startingRef = useRef(false);
  const stoppingRef = useRef(false);
  const startAbortRef = useRef<AbortController | null>(null);
  const connectionGenerationRef = useRef(0);
  const sessionIdRef = useRef<string | null>(null);
  const sessionStartedRef = useRef(false);
  const initialGreetingInstructionIdRef = useRef<string | null>(null);
  const startupStartedAtRef = useRef<number | null>(null);
  const transcriptLineSequenceRef = useRef(0);
  const captureRef = useRef<ReturnType<typeof createLiveTranscriptCapture> | null>(null);
  const latestUserTranscriptRef = useRef("");
  const completedUserTranscriptRef = useRef("");
  const userSpeechActiveRef = useRef(false);
  const activeTurnRef = useRef<{
    turnId: string;
    speechEndedAt: number;
    responseStarted: boolean;
    firstAudio: boolean;
    waitNoticeSent: boolean;
    continuationSent: boolean;
    responseStartTimeoutId?: number;
    waitNoticeTimeoutId?: number;
  } | null>(null);
  const turnSequenceRef = useRef(0);
  const handledToolCallIdsRef = useRef(new Set<string>());
  const delegatedResponseIdsRef = useRef(new Map<string, string>());
  const pendingDelegationIdsRef = useRef(new Set<string>());
  const delegationTranscriptSnapshotsRef = useRef(new Map<string, DelegationTranscriptSnapshot>());
  const currentRecordRef = useRef(recordId);
  currentRecordRef.current = recordId;
  const onInterviewStateChangedRef = useRef(onInterviewStateChanged);

  useEffect(() => {
    onInterviewStateChangedRef.current = onInterviewStateChanged;
  }, [onInterviewStateChanged]);

  const clearActiveTurnTimers = useCallback(() => {
    const turn = activeTurnRef.current;
    if (!turn) {
      return;
    }
    if (turn.responseStartTimeoutId !== undefined) {
      window.clearTimeout(turn.responseStartTimeoutId);
    }
    if (turn.waitNoticeTimeoutId !== undefined) {
      window.clearTimeout(turn.waitNoticeTimeoutId);
    }
    turn.responseStartTimeoutId = undefined;
    turn.waitNoticeTimeoutId = undefined;
  }, []);

  const appendTranscriptDelta = useCallback((role: GPTLiveTranscriptLine["role"], delta: string) => {
    if (!delta) {
      return;
    }
    setTranscriptLines((current) => {
      const last = current[current.length - 1];
      if (last?.role === role) {
        return [...current.slice(0, -1), { ...last, text: `${last.text}${delta}` }];
      }
      transcriptLineSequenceRef.current += 1;
      return [
        ...current,
        {
          id: `gpt-live-transcript-${transcriptLineSequenceRef.current}`,
          role,
          text: delta,
        },
      ];
    });

  }, []);

  const cleanup = useCallback(() => {
    clearActiveTurnTimers();
    activeTurnRef.current = null;
    latestUserTranscriptRef.current = "";
    completedUserTranscriptRef.current = "";
    userSpeechActiveRef.current = false;
    handledToolCallIdsRef.current.clear();
    delegatedResponseIdsRef.current.clear();
    pendingDelegationIdsRef.current.clear();
    delegationTranscriptSnapshotsRef.current.clear();
    captureRef.current?.stop();
    captureRef.current = null;
    connectionGenerationRef.current += 1;
    startAbortRef.current?.abort();
    startAbortRef.current = null;
    peerRef.current?.stop();
    peerRef.current = null;
    sessionIdRef.current = null;
    sessionStartedRef.current = false;
    initialGreetingInstructionIdRef.current = null;
    if (remoteAudioRef.current) {
      remoteAudioRef.current.srcObject = null;
    }
    setStats(EMPTY_STATS);
    setRequiresManualPlayback(false);
    startupStartedAtRef.current = null;
  }, [clearActiveTurnTimers, remoteAudioRef]);

  const markResponseStarted = useCallback((source: string) => {
    const turn = activeTurnRef.current;
    if (!turn || turn.responseStarted) {
      return;
    }
    turn.responseStarted = true;
    if (turn.responseStartTimeoutId !== undefined) {
      window.clearTimeout(turn.responseStartTimeoutId);
      turn.responseStartTimeoutId = undefined;
    }
    console.info("gpt_live_response_started", {
      session_id: sessionIdRef.current,
      turn_id: turn.turnId,
      source,
      speech_end_to_response_start_ms: Math.round(performance.now() - turn.speechEndedAt),
    });
  }, []);

  const markFirstAudio = useCallback(() => {
    const turn = activeTurnRef.current;
    if (!turn || turn.firstAudio) {
      return;
    }
    turn.firstAudio = true;
    if (turn.waitNoticeTimeoutId !== undefined) {
      window.clearTimeout(turn.waitNoticeTimeoutId);
      turn.waitNoticeTimeoutId = undefined;
    }
    console.info("gpt_live_first_audio", {
      session_id: sessionIdRef.current,
      turn_id: turn.turnId,
      speech_end_to_first_audio_ms: Math.round(performance.now() - turn.speechEndedAt),
    });
  }, []);

  const scheduleResponseStartWatchdog = useCallback((turn: NonNullable<typeof activeTurnRef.current>) => {
    turn.responseStartTimeoutId = window.setTimeout(() => {
      const active = activeTurnRef.current;
      if (!active || active.turnId !== turn.turnId || active.responseStarted
        || active.continuationSent) {
        return;
      }
      if (pendingDelegationIdsRef.current.size > 0) {
        console.info("gpt_live_continuation_deferred", {
          session_id: sessionIdRef.current,
          turn_id: active.turnId,
          pending_delegations: pendingDelegationIdsRef.current.size,
        });
        scheduleResponseStartWatchdog(active);
        return;
      }
      active.continuationSent = true;
      const sent = peerRef.current?.sendEvent({
        type: "response.create",
        event_id: `gpt_live_continuation_${active.turnId}`,
      });
      console.info("gpt_live_continuation_trigger", {
        session_id: sessionIdRef.current,
        turn_id: active.turnId,
        sent: Boolean(sent),
        speech_end_to_continuation_ms: Math.round(performance.now() - active.speechEndedAt),
      });
    }, GPT_LIVE_RESPONSE_START_TIMEOUT_MS);
  }, []);

  const beginUserTurnWatchdog = useCallback(() => {
    if (!peerRef.current || !sessionStartedRef.current) {
      return;
    }
    const existingTurn = activeTurnRef.current;
    if (existingTurn && !existingTurn.responseStarted) {
      return;
    }
    clearActiveTurnTimers();
    turnSequenceRef.current += 1;
    const turn: NonNullable<typeof activeTurnRef.current> = {
      turnId: `gpt-live-turn-${connectionGenerationRef.current}-${turnSequenceRef.current}`,
      speechEndedAt: performance.now(),
      responseStarted: false,
      firstAudio: false,
      waitNoticeSent: false,
      continuationSent: false,
    };
    activeTurnRef.current = turn;
    console.info("gpt_live_user_speech_end", {
      session_id: sessionIdRef.current,
      turn_id: turn.turnId,
    });
    turn.waitNoticeTimeoutId = window.setTimeout(() => {
      const active = activeTurnRef.current;
      if (!active || active.turnId !== turn.turnId || active.firstAudio || active.waitNoticeSent) {
        return;
      }
      active.waitNoticeSent = true;
      const sent = peerRef.current?.sendEvent({
        type: "session.commentary.append",
        event_id: `gpt_live_wait_notice_${active.turnId}`,
        delegation_id: null,
        content: "少々お待ちください。",
      });
      console.info("gpt_live_wait_notice", {
        session_id: sessionIdRef.current,
        turn_id: active.turnId,
        sent: Boolean(sent),
        speech_end_to_wait_notice_ms: Math.round(performance.now() - active.speechEndedAt),
      });
    }, GPT_LIVE_WAIT_NOTICE_TIMEOUT_MS);
    scheduleResponseStartWatchdog(turn);
  }, [clearActiveTurnTimers, scheduleResponseStartWatchdog]);

  const handleDelegatedFunctionCall = useCallback(async (
    event: GPTLiveEvent,
    item: Record<string, unknown>,
  ) => {
    const callId = typeof item.call_id === "string" ? item.call_id : "";
    if (!callId || handledToolCallIdsRef.current.has(callId)) {
      return;
    }
    handledToolCallIdsRef.current.add(callId);
    const delegationId = readDelegationId(event) ?? `delegation-${callId}`;
    const responseId = readResponseId(event)
      ?? delegatedResponseIdsRef.current.get(delegationId);
    const transcriptSnapshot = delegationTranscriptSnapshotsRef.current.get(delegationId);
    const delegatedTranscript = transcriptSnapshot?.transcript.trim()
      || latestUserTranscriptRef.current.trim();
    console.info("gpt_live_responses_function_call", {
      session_id: sessionIdRef.current,
      delegation_id: delegationId,
      call_id: callId,
      response_id: responseId,
    });
    let argumentsValue: Record<string, unknown> = {};
    try {
      const parsed = JSON.parse(typeof item.arguments === "string" ? item.arguments : "{}");
      if (parsed && typeof parsed === "object") {
        argumentsValue = parsed as Record<string, unknown>;
      }
    } catch {
      console.warn("gpt_live_state_tool_arguments_invalid", {
        session_id: sessionIdRef.current,
        call_id: callId,
      });
    }
    let output: unknown;
    try {
      if (!currentRecordRef.current) {
        throw new Error("gpt_live_record_id_missing");
      }
      output = await submitGPTLiveStateUpdate(
        currentRecordRef.current,
        {
          delegation_id: delegationId,
          call_id: callId,
          turn_id: typeof argumentsValue.turn_id === "string"
            ? argumentsValue.turn_id
            : activeTurnRef.current?.turnId ?? `gpt-live-${delegationId}-${callId}`,
          base_state_version: typeof argumentsValue.base_state_version === "number"
            ? argumentsValue.base_state_version
            : 0,
          question_id: typeof argumentsValue.question_id === "string"
            ? argumentsValue.question_id
            : "",
          answer_status: argumentsValue.answer_status as "ANSWERED" | "PARTIAL" | "NOT_ANSWERED",
          covered_aspects: Array.isArray(argumentsValue.covered_aspects)
            ? argumentsValue.covered_aspects as { aspect_id: string; evidence: string }[]
            : [],
          missing_aspects: Array.isArray(argumentsValue.missing_aspects)
            ? argumentsValue.missing_aspects as string[]
            : [],
          evidence: Array.isArray(argumentsValue.evidence)
            ? argumentsValue.evidence as { aspect_id: string; text: string }[]
            : [],
        },
        delegatedTranscript,
      );
      console.info("gpt_live_state_persisted", {
        session_id: sessionIdRef.current,
        delegation_id: delegationId,
        call_id: callId,
        status: (output as { status?: string })?.status,
      });
      void onInterviewStateChangedRef.current?.();
    } catch (error) {
      output = {
        status: "error",
        code: error instanceof Error ? error.message : "state_update_failed",
      };
      console.warn("gpt_live_state_persist_failed", {
        session_id: sessionIdRef.current,
        delegation_id: delegationId,
        call_id: callId,
      });
    }
    delegationTranscriptSnapshotsRef.current.delete(delegationId);
    pendingDelegationIdsRef.current.delete(delegationId);
    const peer = peerRef.current;
    if (!peer) {
      return;
    }
    const outputSent = peer.sendEvent({
      type: "response.item.create",
      event_id: `gpt_live_state_tool_result_${callId}`,
      item: {
        type: "function_call_output",
        call_id: callId,
        output: JSON.stringify(output),
      },
    });
    const responseRequested = outputSent && peer.sendEvent({
      type: "response.create",
      event_id: `gpt_live_state_tool_continue_${callId}`,
    });
    console.info("gpt_live_delegation_completed", {
      session_id: sessionIdRef.current,
      delegation_id: delegationId,
      call_id: callId,
      response_id: responseId,
      function_output_sent: outputSent,
      response_requested: Boolean(responseRequested),
    });
  }, []);

  const handleEvent = useCallback((event: GPTLiveEvent) => {
    const eventType = typeof event.type === "string" ? event.type : "unknown";
    const eventSessionId = typeof event.session?.id === "string"
      ? event.session.id
      : sessionIdRef.current;
    console.info("gpt_live_event", {
      type: eventType,
      event_id: typeof event.event_id === "string" ? event.event_id : undefined,
      session_id: eventSessionId,
    });
    const startupStartedAt = startupStartedAtRef.current;
    const markStartup = (event: string, details?: Record<string, unknown>) => {
      if (startupStartedAt === null) {
        return;
      }
      logVoiceStartupEvent({
        event,
        provider: "gpt_live",
        voiceSessionId: eventSessionId ?? undefined,
        startStartedAt: startupStartedAt,
        details,
      });
    };

    const nestedResponseEvent = readNestedResponseEvent(event);
    const nestedType = typeof nestedResponseEvent?.type === "string"
      ? nestedResponseEvent.type
      : undefined;
    if (isResponseAudioEvent(eventType) || isResponseAudioEvent(nestedType)) {
      markFirstAudio();
    }
    if (nestedType === "response.created") {
      const delegationId = readDelegationId(event);
      const responseId = readResponseId(event);
      if (delegationId && responseId) {
        delegatedResponseIdsRef.current.set(delegationId, responseId);
      }
      console.info("gpt_live_responses_backend_started", {
        session_id: eventSessionId,
        delegation_id: delegationId,
        response_id: responseId,
      });
    }
    if (["response.failed", "response.incomplete", "response.cancelled"].includes(nestedType ?? "")) {
      const delegationId = readDelegationId(event);
      if (delegationId) {
        pendingDelegationIdsRef.current.delete(delegationId);
      }
    }
    if (isResponseStartEvent(eventType)) {
      markResponseStarted(eventType);
    }
    if (isResponseDoneEvent(eventType)) {
      clearActiveTurnTimers();
      console.info("gpt_live_response_done", {
        session_id: eventSessionId,
        turn_id: activeTurnRef.current?.turnId,
        type: eventType,
      });
    }
    const functionCall = eventType === "response.output_item.done"
      || nestedType === "response.output_item.done"
      ? readResponseFunctionCall(event)
      : undefined;
    if (functionCall) {
      void handleDelegatedFunctionCall(event, functionCall);
      return;
    }

    if (eventType === "session.started") {
      sessionStartedRef.current = true;
      sessionIdRef.current = eventSessionId ?? sessionIdRef.current;
      setConnectionState("connected");
      setStatus("listening");
      const connectionGeneration = connectionGenerationRef.current;
      const playbackPromise = remoteAudioRef.current?.play();
      if (playbackPromise) {
        void playbackPromise.then(
          () => {
            if (connectionGenerationRef.current === connectionGeneration) {
              setRequiresManualPlayback(false);
            }
          },
          () => {
            if (connectionGenerationRef.current === connectionGeneration) {
              setRequiresManualPlayback(true);
            }
          },
        );
      }
      return;
    }

    if (eventType === "session.closed") {
      console.info("gpt_live_session_closed", { session_id: eventSessionId });
      markStartup("session_closed");
      cleanup();
      setConnectionState("closed");
      setStatus((current) => current === "stopping" ? current : "disconnected");
      return;
    }

    if (eventType === "session.instructions.appended") {
      const acknowledgedEventId = readClientEventId(event);
      const initialGreetingInstructionId = initialGreetingInstructionIdRef.current;
      if (initialGreetingInstructionId && acknowledgedEventId === initialGreetingInstructionId) {
        markStartup("initial_response_request_started", {
          request_type: "session.commentary.append",
        });
        const sent = peerRef.current?.sendEvent({
          type: "session.commentary.append",
          event_id: `gpt_live_greeting_begin_${connectionGenerationRef.current}`,
          delegation_id: null,
          content: "Begin the conversation now. Greet the user in Japanese and ask the first missing checklist question, then pause and listen.",
        });
        if (!sent) {
          console.warn("gpt_live_initial_greeting_not_sent", {
            session_id: eventSessionId,
          });
        }
        console.info("gpt_live_initial_greeting_requested", {
          session_id: eventSessionId,
        });
        if (sent) {
          markStartup("initial_response_request_sent", {
            request_type: "session.commentary.append",
          });
        }
        initialGreetingInstructionIdRef.current = null;
      }
      return;
    }

    if (eventType === "transport.closed") {
      console.info("gpt_live_transport_closed", { session_id: eventSessionId });
      cleanup();
      setConnectionState("closed");
      setStatus((current) => current === "stopping" ? current : "disconnected");
      return;
    }

    if (isUserSpeechStartEvent(eventType)) {
      for (const snapshot of delegationTranscriptSnapshotsRef.current.values()) {
        snapshot.sealed = true;
      }
      if (activeTurnRef.current && !activeTurnRef.current.responseStarted) {
        clearActiveTurnTimers();
        activeTurnRef.current = null;
      }
      userSpeechActiveRef.current = true;
      latestUserTranscriptRef.current = "";
      return;
    }

    if (isInputTranscriptDelta(eventType)) {
      const delta = typeof event.delta === "string" ? event.delta : "";
      console.info("gpt_live_input_transcript_delta", {
        session_id: eventSessionId,
        delta_length: delta.length,
      });
      const wasSpeechActive = userSpeechActiveRef.current;
      if (!wasSpeechActive) {
        latestUserTranscriptRef.current = "";
        if (activeTurnRef.current && !activeTurnRef.current.responseStarted) {
          completedUserTranscriptRef.current += delta;
        }
      }
      userSpeechActiveRef.current = true;
      appendTranscriptDelta("user", delta);
      latestUserTranscriptRef.current += delta;
      // A delegation can be created before the final transcript fragment of
      // the same utterance arrives. Accept those late fragments only while
      // no new speech segment has started; once speech_started is received,
      // the previous delegation snapshot is sealed.
      if (!wasSpeechActive && pendingDelegationIdsRef.current.size > 0) {
        for (const snapshot of delegationTranscriptSnapshotsRef.current.values()) {
          if (!snapshot.sealed) {
            snapshot.transcript += delta;
          }
        }
      }
      captureRef.current?.append({ role: "user", text: delta, ...transcriptTiming(event) });
      return;
    }

    if (isOutputTranscriptDelta(eventType)) {
      const delta = typeof event.delta === "string" ? event.delta : "";
      console.info("gpt_live_output_transcript_delta", {
        session_id: eventSessionId,
        delta_length: delta.length,
      });
      appendTranscriptDelta("assistant", delta);
      captureRef.current?.append({ role: "assistant", text: delta, ...transcriptTiming(event) });
      return;
    }

    if (isUserSpeechEndEvent(eventType)) {
      userSpeechActiveRef.current = false;
      completedUserTranscriptRef.current = latestUserTranscriptRef.current;
      beginUserTurnWatchdog();
      return;
    }

    if (eventType === "session.delegation.created") {
      const delegationId = readDelegationId(event);
      const responseId = readResponseId(event);
      if (delegationId && responseId) {
        delegatedResponseIdsRef.current.set(delegationId, responseId);
      }
      if (delegationId) {
        pendingDelegationIdsRef.current.add(delegationId);
        delegationTranscriptSnapshotsRef.current.set(delegationId, {
          transcript: completedUserTranscriptRef.current || latestUserTranscriptRef.current,
          sealed: false,
        });
      }
      console.info("gpt_live_delegation_created", {
        session_id: eventSessionId,
        delegation_id: delegationId,
        response_id: responseId,
      });
      void captureRef.current?.flush();
      return;
    }

    if (isErrorEvent(eventType)) {
      console.warn("gpt_live_error_event", {
        session_id: eventSessionId,
        type: eventType,
        code: readErrorField(event.error, "code"),
        message: readErrorField(event.error, "message"),
      });
      cleanup();
      setConnectionState("closed");
      setMessage(t("errors.voiceConnectFailed"));
      setStatus("error");
    }
    // Observation never controls speech scheduling.
  }, [
    appendTranscriptDelta,
    beginUserTurnWatchdog,
    clearActiveTurnTimers,
    cleanup,
    handleDelegatedFunctionCall,
    markFirstAudio,
    markResponseStarted,
    remoteAudioRef,
    t,
  ]);

  const start = useCallback(async () => {
    if (!enabled || startingRef.current || peerRef.current) {
      return;
    }
    startingRef.current = true;
    const startupStartedAt = performance.now();
    startupStartedAtRef.current = startupStartedAt;
    logVoiceStartupEvent({
      event: "start_clicked",
      provider: "gpt_live",
      startStartedAt: startupStartedAt,
    });
    setMessage("");
    setRequiresManualPlayback(false);
    setStats(EMPTY_STATS);
    setTranscriptLines([]);
    latestUserTranscriptRef.current = "";
    completedUserTranscriptRef.current = "";
    userSpeechActiveRef.current = false;
    turnSequenceRef.current = 0;
    activeTurnRef.current = null;
    handledToolCallIdsRef.current.clear();
    delegatedResponseIdsRef.current.clear();
    pendingDelegationIdsRef.current.clear();
    delegationTranscriptSnapshotsRef.current.clear();
    setConnectionState("connecting");
    setStatus("connecting");
    const startAbortController = new AbortController();
    const connectionGeneration = connectionGenerationRef.current + 1;
    connectionGenerationRef.current = connectionGeneration;
    startAbortRef.current = startAbortController;
    if (recordId) {
      const captureId = crypto.randomUUID();
      captureRef.current = createLiveTranscriptCapture({
        submit: (revision, fragments) => submitGPTLiveCapture(recordId, captureId, revision, fragments),
        poll: () => getGPTLiveCaptureStatus(recordId, captureId),
        onSaved: (result) => {
          if (currentRecordRef.current !== recordId) return;
          void Promise.resolve().then(() => {
            if (currentRecordRef.current === recordId) return onInterviewStateChangedRef.current?.();
          })
            .catch(() => console.warn("gpt_live_state_refresh_failed"));
          if (connectionGenerationRef.current === connectionGeneration
            || (connectionGenerationRef.current === connectionGeneration + 1 && !peerRef.current)) {
            setMessage("");
          }
          const isCurrent = connectionGenerationRef.current === connectionGeneration;
          const isStopped = connectionGenerationRef.current === connectionGeneration + 1 && !peerRef.current;
          if (!isCurrent && !isStopped) return;
          if (!result.processing && result.interviewState?.status === "completed") {
            if (isCurrent) cleanup();
            setConnectionState("closed");
            setStatus("completed");
            return;
          }
          if (isStopped) {
            setStatus(result.processing ? "processing" : "idle");
            return;
          }
          const completion = result.completion;
          if (result.processing && completion && !completion.closingRequired
            && !completion.missingRequiredTargets.length && !completion.pendingConfirmationTargets.length
            && !completion.unknownApplicabilityTopics.length && !completion.unresolvedContradictionIds.length) {
            setStatus("processing");
          } else if (sessionStartedRef.current) {
            setStatus("listening");
          }
        },
        onError: () => {
          console.warn("gpt_live_capture_failed_retrying");
          if (currentRecordRef.current === recordId
            && (connectionGenerationRef.current === connectionGeneration
              || (connectionGenerationRef.current === connectionGeneration + 1 && !peerRef.current))) {
            setMessage("回答の記録に失敗しました。音声会話を継続しながら再試行しています。ページを閉じないでください。");
          }
        },
      });
    }
    let startTimedOut = false;
    const startTimeoutId = window.setTimeout(() => {
      startTimedOut = true;
      startAbortController.abort();
    }, GPT_LIVE_START_TIMEOUT_MS);
    try {
      const peer = await createGPTLivePeerConnection({
        remoteAudioElement: remoteAudioRef.current,
        recordId,
        startupStartedAt,
        onEvent: (event) => {
          if (connectionGenerationRef.current === connectionGeneration) handleEvent(event);
        },
        onRemoteAudioPlaying: () => {
          if (connectionGenerationRef.current === connectionGeneration) {
            markFirstAudio();
          }
        },
        signal: startAbortController.signal,
        onConnectionStateChange: (state) => {
          if (connectionGenerationRef.current !== connectionGeneration) {
            return;
          }
          // ICE/DTLS can become connected before Live has announced the
          // session. Treat session.started as the application-level ready
          // signal required by the Live API contract.
          setConnectionState(
            state === "connected" && !sessionStartedRef.current ? "connecting" : state,
          );
          if (state === "failed") {
            cleanup();
            setMessage(t("errors.webrtcFailed"));
            setStatus("error");
          } else if (state === "closed" && !stoppingRef.current) {
            cleanup();
            setStatus((current) => current === "error" ? current : "disconnected");
          }
        },
        onStatsChange: setStats,
      });
      if (startAbortController.signal.aborted) {
        peer.stop();
        if (startTimedOut) {
          throw new Error("gpt_live_start_timeout");
        }
        return;
      }
      peerRef.current = peer;
      sessionIdRef.current = peer.sessionId;
      const initialGreetingEventId = `gpt_live_greeting_instructions_${connectionGeneration}`;
      initialGreetingInstructionIdRef.current = initialGreetingEventId;
      logVoiceStartupEvent({
        event: "initial_instructions_send_started",
        provider: "gpt_live",
        voiceSessionId: peer.sessionId,
        startStartedAt: startupStartedAt,
      });
      const initialGreetingSent = peer.sendEvent({
        type: "session.instructions.append",
        event_id: initialGreetingEventId,
        delegation_id: null,
        content: "会話を今すぐ開始してください。日本語で短く自然に挨拶し、これからインタビューを始めることを伝えてください。アプリケーションのチェックリストで最初に不足している項目について、一度に一つだけ具体的な質問をしてください。ユーザーが先に話し始めるのを待たず、その後は回答を遮らずに聞いてください。",
      });
      if (initialGreetingSent) {
        logVoiceStartupEvent({
          event: "initial_instructions_sent",
          provider: "gpt_live",
          voiceSessionId: peer.sessionId,
          startStartedAt: startupStartedAt,
        });
      }
      if (!initialGreetingSent) {
        initialGreetingInstructionIdRef.current = null;
        console.warn("gpt_live_initial_greeting_instructions_not_sent", {
          session_id: peer.sessionId,
        });
      }
    } catch (error) {
      const wasCancelled = startAbortController.signal.aborted;
      cleanup();
      if (!wasCancelled || startTimedOut) {
        setConnectionState("closed");
        setStatus("error");
        setMessage(toStartErrorMessage(error, "offer", t));
      }
    } finally {
      window.clearTimeout(startTimeoutId);
      if (startAbortRef.current === startAbortController) {
        startAbortRef.current = null;
      }
      startingRef.current = false;
    }
  }, [cleanup, enabled, handleEvent, markFirstAudio, recordId, remoteAudioRef, t]);

  const stop = useCallback(() => {
    if (stoppingRef.current) {
      return;
    }
    stoppingRef.current = true;
    const processing = captureRef.current?.isProcessing();
    setStatus("stopping");
    cleanup();
    setConnectionState("closed");
    setStatus(processing ? "processing" : "idle");
    stoppingRef.current = false;
  }, [cleanup]);

  useEffect(() => {
    if (enabled || (!startingRef.current && !peerRef.current)) {
      return;
    }
    stop();
  }, [enabled, stop]);

  useEffect(() => () => stop(), [stop, recordId]);

  const playRemoteAudio = useCallback(async () => {
    const connectionGeneration = connectionGenerationRef.current;
    try {
      await remoteAudioRef.current?.play();
      if (connectionGenerationRef.current === connectionGeneration) {
        setRequiresManualPlayback(false);
      }
    } catch {
      if (connectionGenerationRef.current === connectionGeneration) {
        setRequiresManualPlayback(true);
      }
    }
  }, [remoteAudioRef]);

  return {
    status,
    message,
    partialTranscript: transcriptLines[transcriptLines.length - 1]?.text ?? "",
    transcriptLines,
    connectionState,
    stats,
    requiresManualPlayback,
    initialReplyActive: false,
    isActive: !["idle", "completed", "error", "disconnected"].includes(status),
    start,
    stop,
    playRemoteAudio,
  };
}

function isInputTranscriptDelta(type: string): boolean {
  return type === "session.input_transcript.delta"
    || type === "input_transcript.delta"
    || type === "conversation.item.input_audio_transcription.delta";
}

function isOutputTranscriptDelta(type: string): boolean {
  return type === "session.output_transcript.delta"
    || type === "output_transcript.delta"
    || type === "response.output_transcript.delta"
    || type === "response.output_audio_transcript.delta";
}

function isUserSpeechEndEvent(type: string): boolean {
  return [
    "session.input_transcript.done",
    "input_transcript.done",
    "session.input_transcript.completed",
    "conversation.item.input_audio_transcription.completed",
    "session.input_audio_buffer.speech_stopped",
    "input_audio_buffer.speech_stopped",
  ].includes(type);
}

function isUserSpeechStartEvent(type: string): boolean {
  return [
    "session.input_audio_buffer.speech_started",
    "input_audio_buffer.speech_started",
    "session.input_speech_started",
    "input_speech_started",
  ].includes(type);
}

function isResponseStartEvent(type: string | undefined): boolean {
  return type === "response.created"
    || type === "response.output_audio.delta"
    || type === "session.output_audio.delta"
    || type === "response.output_transcript.delta"
    || type === "response.output_audio_transcript.delta"
    || type === "session.output_transcript.delta";
}

function isResponseAudioEvent(type: string | undefined): boolean {
  return type === "response.output_audio.delta"
    || type === "session.output_audio.delta"
    || type === "response.audio.delta";
}

function isResponseDoneEvent(type: string | undefined): boolean {
  return type === "response.done"
    || type === "response.output_audio.done"
    || type === "session.output_audio.done"
    || type === "response.audio.done";
}

function readNestedResponseEvent(event: GPTLiveEvent): Record<string, unknown> | undefined {
  const nested = event.event;
  return nested && typeof nested === "object"
    ? nested as Record<string, unknown>
    : undefined;
}

function readResponseFunctionCall(event: GPTLiveEvent): Record<string, unknown> | undefined {
  const candidates = [event, readNestedResponseEvent(event)].filter(
    (candidate): candidate is Record<string, unknown> => Boolean(candidate),
  );
  for (const candidate of candidates) {
    const item = candidate.item;
    if (!item || typeof item !== "object") {
      continue;
    }
    const itemRecord = item as Record<string, unknown>;
    if (itemRecord.type === "function_call") {
      return itemRecord;
    }
  }
  return undefined;
}

function readDelegationId(event: GPTLiveEvent): string | undefined {
  const delegation = event.delegation;
  if (delegation && typeof delegation === "object" && "id" in delegation) {
    const id = (delegation as Record<string, unknown>).id;
    if (typeof id === "string" && id.trim()) {
      return id;
    }
  }
  const delegationId = event.delegation_id;
  return typeof delegationId === "string" && delegationId.trim()
    ? delegationId
    : undefined;
}

function readResponseId(event: GPTLiveEvent): string | undefined {
  const nested = readNestedResponseEvent(event);
  const response = nested?.response;
  if (response && typeof response === "object" && "id" in response) {
    const id = (response as Record<string, unknown>).id;
    if (typeof id === "string" && id.trim()) {
      return id;
    }
  }
  const delegation = event.delegation;
  const delegationRecord = delegation && typeof delegation === "object"
    ? delegation as Record<string, unknown>
    : undefined;
  for (const candidate of [event, nested, delegationRecord]) {
    if (!candidate) {
      continue;
    }
    for (const key of ["response_id", "responseId"]) {
      const value = candidate[key];
      if (typeof value === "string" && value.trim()) {
        return value;
      }
    }
  }
  return undefined;
}

function readClientEventId(event: GPTLiveEvent): string | undefined {
  const clientEventId = event.client_event_id;
  if (typeof clientEventId === "string" && clientEventId.trim()) {
    return clientEventId;
  }
  return typeof event.event_id === "string" && event.event_id.trim()
    ? event.event_id
    : undefined;
}

function isErrorEvent(type: string): boolean {
  return type === "error" || type.endsWith(".error");
}

function readErrorField(error: unknown, field: "code" | "message"): string | undefined {
  if (!error || typeof error !== "object" || !(field in error)) {
    return undefined;
  }
  const value = (error as Record<string, unknown>)[field];
  return typeof value === "string" ? value : undefined;
}

function transcriptTiming(event: GPTLiveEvent): { start_ms?: number; end_ms?: number } {
  return {
    ...(typeof event.start_ms === "number" ? { start_ms: event.start_ms } : {}),
    ...(typeof event.end_ms === "number" ? { end_ms: event.end_ms } : {}),
  };
}
