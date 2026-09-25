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
import {
  isDelegatedResponseTerminalEvent,
  isErrorEvent,
  isInputTranscriptDelta,
  isOutputTranscriptDelta,
  isResponseAudioEvent,
  isResponseDoneEvent,
  isResponseFunctionCallEvent,
  isResponseStartEvent,
  isUserSpeechEndEvent,
  isUserSpeechStartEvent,
  readClientEventId,
  readDelegationId,
  readErrorField,
  readNestedResponseEvent,
  readResponseFunctionCall,
  readResponseId,
  resolveDelegationId,
  transcriptTiming,
} from "../runtime/gptLiveEventUtils";
import { createLiveTranscriptCapture } from "../utils/liveTranscriptCapture";
import { toStartErrorMessage } from "../utils/voiceErrors";
import { logVoiceStartupEvent } from "../utils/voiceTelemetry";

type UseGPTLiveVoiceConversationArgs = {
  enabled: boolean;
  remoteAudioRef: RefObject<HTMLAudioElement>;
  recordId?: string;
  onInterviewStateChanged?: (stateVersion?: number) => void | Promise<void>;
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
// Live transcript deltas do not guarantee a corresponding speech-stopped or
// transcript.done event. This is only a transport fallback boundary; it does
// not infer utterance completeness from punctuation or specific words.
const GPT_LIVE_TRANSCRIPT_IDLE_TIMEOUT_MS = 2500;

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
  const transcriptRecordIdRef = useRef(recordId);
  const userTranscriptRevisionRef = useRef(0);
  const handledSpeechEndRevisionRef = useRef(-1);
  const finalizedTranscriptRevisionRef = useRef(-1);
  const userSpeechActiveRef = useRef(false);
  const transcriptIdleTimeoutIdRef = useRef<number | undefined>();
  const responseObservedRevisionRef = useRef(-1);
  const activeTurnRef = useRef<{
    turnId: string;
    speechEndedAt: number;
    responseStarted: boolean;
    responseObserved: boolean;
    firstAudio: boolean;
    waitNoticeSent: boolean;
    continuationSent: boolean;
    responseStartTimeoutId?: number;
    waitNoticeTimeoutId?: number;
  } | null>(null);
  const turnSequenceRef = useRef(0);
  const handledToolCallIdsRef = useRef(new Set<string>());
  const delegationToolCallSeenRef = useRef(new Set<string>());
  const delegatedResponseIdsRef = useRef(new Map<string, string>());
  const pendingDelegationIdsRef = useRef(new Set<string>());
  const delegationTranscriptSnapshotsRef = useRef(new Map<string, DelegationTranscriptSnapshot>());
  const stateUpdateQueueRef = useRef(Promise.resolve());
  const stateUpdateAbortControllersRef = useRef(new Set<AbortController>());
  const currentRecordRef = useRef(recordId);
  currentRecordRef.current = recordId;
  const onInterviewStateChangedRef = useRef(onInterviewStateChanged);

  useEffect(() => {
    onInterviewStateChangedRef.current = onInterviewStateChanged;
  }, [onInterviewStateChanged]);

  useEffect(() => {
    if (transcriptRecordIdRef.current === recordId) {
      return;
    }
    transcriptRecordIdRef.current = recordId;
    setTranscriptLines([]);
  }, [recordId]);

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

  const clearTranscriptIdleTimer = useCallback(() => {
    if (transcriptIdleTimeoutIdRef.current !== undefined) {
      window.clearTimeout(transcriptIdleTimeoutIdRef.current);
      transcriptIdleTimeoutIdRef.current = undefined;
    }
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
    clearTranscriptIdleTimer();
    activeTurnRef.current = null;
    latestUserTranscriptRef.current = "";
    completedUserTranscriptRef.current = "";
    userTranscriptRevisionRef.current = 0;
    handledSpeechEndRevisionRef.current = -1;
    finalizedTranscriptRevisionRef.current = -1;
    userSpeechActiveRef.current = false;
    responseObservedRevisionRef.current = -1;
    handledToolCallIdsRef.current.clear();
    delegationToolCallSeenRef.current.clear();
    delegatedResponseIdsRef.current.clear();
    pendingDelegationIdsRef.current.clear();
    delegationTranscriptSnapshotsRef.current.clear();
    for (const controller of stateUpdateAbortControllersRef.current) {
      controller.abort();
    }
    stateUpdateAbortControllersRef.current.clear();
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
  }, [clearActiveTurnTimers, clearTranscriptIdleTimer, remoteAudioRef]);

  const markResponseStarted = useCallback((source: string) => {
    if (userTranscriptRevisionRef.current > 0) {
      responseObservedRevisionRef.current = userTranscriptRevisionRef.current;
    }
    const turn = activeTurnRef.current;
    if (!turn || turn.responseStarted) {
      return;
    }
    turn.responseStarted = true;
    turn.responseObserved = true;
    if (turn.responseStartTimeoutId !== undefined) {
      window.clearTimeout(turn.responseStartTimeoutId);
      turn.responseStartTimeoutId = undefined;
    }
    if (turn.waitNoticeTimeoutId !== undefined) {
      window.clearTimeout(turn.waitNoticeTimeoutId);
      turn.waitNoticeTimeoutId = undefined;
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

  const markUserTranscriptFinal = useCallback((source: string) => {
    const transcript = latestUserTranscriptRef.current;
    const transcriptRevision = userTranscriptRevisionRef.current;
    if (!transcript.trim() || finalizedTranscriptRevisionRef.current === transcriptRevision) {
      return;
    }
    completedUserTranscriptRef.current = transcript;
    finalizedTranscriptRevisionRef.current = transcriptRevision;
    // GPT-Live supplies transcript deltas but does not guarantee a completed
    // turn event. This is the application's transport-level grouping marker,
    // not a punctuation or keyword based completeness guess.
    console.info("gpt_live_input_transcript_final", {
      session_id: sessionIdRef.current,
      source,
      transcript_revision: transcriptRevision,
      transcript_length: transcript.length,
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

  const beginUserTurnWatchdog = useCallback((source = "provider_event") => {
    if (!peerRef.current || !sessionStartedRef.current) {
      return;
    }
    const transcriptRevision = userTranscriptRevisionRef.current;
    if (handledSpeechEndRevisionRef.current === transcriptRevision) {
      return;
    }
    const existingTurn = activeTurnRef.current;
    if (existingTurn && !existingTurn.responseStarted) {
      return;
    }
    handledSpeechEndRevisionRef.current = transcriptRevision;
    clearActiveTurnTimers();
    turnSequenceRef.current += 1;
    const turn: NonNullable<typeof activeTurnRef.current> = {
      turnId: `gpt-live-turn-${connectionGenerationRef.current}-${turnSequenceRef.current}`,
      speechEndedAt: performance.now(),
      responseStarted: false,
      responseObserved: responseObservedRevisionRef.current === transcriptRevision,
      firstAudio: false,
      waitNoticeSent: false,
      continuationSent: false,
    };
    activeTurnRef.current = turn;
    console.info("gpt_live_user_speech_end", {
      session_id: sessionIdRef.current,
      turn_id: turn.turnId,
      source,
    });
    turn.waitNoticeTimeoutId = window.setTimeout(() => {
      const active = activeTurnRef.current;
      if (!active || active.turnId !== turn.turnId || active.responseStarted
        || active.responseObserved || active.firstAudio || active.waitNoticeSent) {
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

  const scheduleTranscriptIdleBoundary = useCallback(() => {
    clearTranscriptIdleTimer();
    const transcriptRevision = userTranscriptRevisionRef.current;
    transcriptIdleTimeoutIdRef.current = window.setTimeout(() => {
      transcriptIdleTimeoutIdRef.current = undefined;
      if (userTranscriptRevisionRef.current !== transcriptRevision
        || !latestUserTranscriptRef.current.trim()) {
        return;
      }
      userSpeechActiveRef.current = false;
      markUserTranscriptFinal("transcript_idle");
      console.info("gpt_live_transcript_idle_boundary", {
        session_id: sessionIdRef.current,
        transcript_revision: transcriptRevision,
        idle_timeout_ms: GPT_LIVE_TRANSCRIPT_IDLE_TIMEOUT_MS,
      });
      beginUserTurnWatchdog("transcript_idle");
    }, GPT_LIVE_TRANSCRIPT_IDLE_TIMEOUT_MS);
  }, [beginUserTurnWatchdog, clearTranscriptIdleTimer, markUserTranscriptFinal]);

  const handleDelegatedFunctionCall = useCallback(async (
    event: GPTLiveEvent,
    item: Record<string, unknown>,
    context: {
      connectionGeneration: number;
      sessionId: string | null;
      recordId?: string;
    },
  ) => {
    const isCurrentLiveCall = () => (
      connectionGenerationRef.current === context.connectionGeneration
      && currentRecordRef.current === context.recordId
      && (!context.sessionId || sessionIdRef.current === context.sessionId)
      && peerRef.current !== null
    );
    const eventDelegationId = resolveDelegationId(
      event,
      delegatedResponseIdsRef.current,
    );
    const callId = typeof item.call_id === "string" ? item.call_id : "";
    if (!callId) {
      if (eventDelegationId) {
        pendingDelegationIdsRef.current.delete(eventDelegationId);
        delegationTranscriptSnapshotsRef.current.delete(eventDelegationId);
        delegationToolCallSeenRef.current.delete(eventDelegationId);
      }
      console.warn("gpt_live_function_call_id_missing", {
        session_id: sessionIdRef.current,
        delegation_id: eventDelegationId,
      });
      return;
    }
    if (handledToolCallIdsRef.current.has(callId)) {
      return;
    }
    if (typeof item.name === "string" && item.name !== "update_interview_state") {
      if (eventDelegationId) {
        pendingDelegationIdsRef.current.delete(eventDelegationId);
        delegationTranscriptSnapshotsRef.current.delete(eventDelegationId);
        delegationToolCallSeenRef.current.delete(eventDelegationId);
      }
      console.warn("gpt_live_unknown_function_call", {
        session_id: sessionIdRef.current,
        delegation_id: eventDelegationId,
        call_id: callId,
        name: item.name,
      });
      return;
    }
    handledToolCallIdsRef.current.add(callId);
    const delegationId = eventDelegationId ?? `delegation-${callId}`;
    delegationToolCallSeenRef.current.add(delegationId);
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
    let stateUpdateAbortController: AbortController | undefined;
    try {
      if (!isCurrentLiveCall()) {
        console.info("gpt_live_state_update_discarded_stale_session", {
          delegation_id: delegationId,
          call_id: callId,
        });
        return;
      }
      const stateUpdateRecordId = context.recordId;
      if (!stateUpdateRecordId) {
        throw new Error("gpt_live_record_id_missing");
      }
      const requestedBaseStateVersion = typeof argumentsValue.base_state_version === "number"
        ? argumentsValue.base_state_version
        : 0;
      const stateUpdatePayload: GPTLiveStateUpdatePayload = {
        delegation_id: delegationId,
        call_id: callId,
        turn_id: typeof argumentsValue.turn_id === "string"
          ? argumentsValue.turn_id
          : activeTurnRef.current?.turnId ?? `gpt-live-${delegationId}-${callId}`,
        // Keep the evaluator's version as the concurrency boundary. Rebasing
        // an old semantic assessment onto a newer state can apply evidence to
        // the wrong turn; the backend returns the current state instead.
        base_state_version: requestedBaseStateVersion,
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
      };
      stateUpdateAbortController = new AbortController();
      stateUpdateAbortControllersRef.current.add(stateUpdateAbortController);
      const persist = async () => {
        if (!isCurrentLiveCall()) {
          return;
        }
        output = await submitGPTLiveStateUpdateWithRetry(
          stateUpdateRecordId,
          stateUpdatePayload,
          delegatedTranscript,
          stateUpdateAbortController?.signal,
        );
      };
      const queued = stateUpdateQueueRef.current.catch(() => undefined).then(persist);
      stateUpdateQueueRef.current = queued.then(() => undefined, () => undefined);
      await queued;
      if (!isCurrentLiveCall()) {
        console.info("gpt_live_state_update_discarded_after_session_change", {
          delegation_id: delegationId,
          call_id: callId,
        });
        return;
      }
      const acknowledgedStateVersion = (output as { stateVersion?: unknown })?.stateVersion;
      console.info("gpt_live_state_persisted", {
        session_id: sessionIdRef.current,
        delegation_id: delegationId,
        call_id: callId,
        status: (output as { status?: string })?.status,
      });
      if ((output as { status?: unknown })?.status !== "error") {
        void onInterviewStateChangedRef.current?.(
          typeof acknowledgedStateVersion === "number" && Number.isFinite(acknowledgedStateVersion)
            ? acknowledgedStateVersion
            : undefined,
        );
      }
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
    } finally {
      if (stateUpdateAbortController) {
        stateUpdateAbortControllersRef.current.delete(stateUpdateAbortController);
      }
    }
    if (!isCurrentLiveCall()) {
      return;
    }
    delegationTranscriptSnapshotsRef.current.delete(delegationId);
    delegationToolCallSeenRef.current.delete(delegationId);
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
    const functionCall = isResponseFunctionCallEvent(eventType)
      || isResponseFunctionCallEvent(nestedType)
      ? readResponseFunctionCall(event)
      : undefined;
    if (functionCall) {
      const delegationId = resolveDelegationId(
        event,
        delegatedResponseIdsRef.current,
      );
      if (delegationId) {
        delegationToolCallSeenRef.current.add(delegationId);
      }
      void handleDelegatedFunctionCall(event, functionCall, {
        connectionGeneration: connectionGenerationRef.current,
        sessionId: eventSessionId,
        recordId: currentRecordRef.current,
      });
      return;
    }

    if (isDelegatedResponseTerminalEvent(nestedType)) {
      const delegationId = readDelegationId(event);
      if (delegationId && !delegationToolCallSeenRef.current.has(delegationId)) {
        pendingDelegationIdsRef.current.delete(delegationId);
        delegationTranscriptSnapshotsRef.current.delete(delegationId);
        console.warn("gpt_live_delegation_finished_without_state_tool", {
          session_id: eventSessionId,
          delegation_id: delegationId,
          response_event: nestedType,
        });
      }
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
      clearTranscriptIdleTimer();
      for (const snapshot of delegationTranscriptSnapshotsRef.current.values()) {
        snapshot.sealed = true;
      }
      if (activeTurnRef.current && !activeTurnRef.current.responseStarted) {
        clearActiveTurnTimers();
        activeTurnRef.current = null;
      }
      userSpeechActiveRef.current = true;
      latestUserTranscriptRef.current = "";
      responseObservedRevisionRef.current = -1;
      return;
    }

    if (isInputTranscriptDelta(eventType)) {
      const delta = typeof event.delta === "string" ? event.delta : "";
      if (!delta) {
        return;
      }
      console.info("gpt_live_input_transcript_delta", {
        session_id: eventSessionId,
        delta_length: delta.length,
      });
      const wasSpeechActive = userSpeechActiveRef.current;
      if (!wasSpeechActive) {
        clearTranscriptIdleTimer();
        for (const snapshot of delegationTranscriptSnapshotsRef.current.values()) {
          snapshot.sealed = true;
        }
        latestUserTranscriptRef.current = "";
        if (activeTurnRef.current && !activeTurnRef.current.responseStarted) {
          clearActiveTurnTimers();
          activeTurnRef.current = null;
        }
        responseObservedRevisionRef.current = -1;
      }
      userSpeechActiveRef.current = true;
      userTranscriptRevisionRef.current += 1;
      appendTranscriptDelta("user", delta);
      latestUserTranscriptRef.current += delta;
      // A delegation can be created before one or more final transcript
      // fragments of the same utterance arrive. Keep appending to every
      // unsealed snapshot; speech_started seals snapshots from the previous
      // utterance before a new speech segment is accumulated.
      if (pendingDelegationIdsRef.current.size > 0) {
        for (const snapshot of delegationTranscriptSnapshotsRef.current.values()) {
          if (!snapshot.sealed) {
            snapshot.transcript += delta;
          }
        }
      }
      captureRef.current?.append({ role: "user", text: delta, ...transcriptTiming(event) });
      scheduleTranscriptIdleBoundary();
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
      clearTranscriptIdleTimer();
      userSpeechActiveRef.current = false;
      markUserTranscriptFinal("provider_event");
      beginUserTurnWatchdog("provider_event");
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
        const transcript = userSpeechActiveRef.current
          ? latestUserTranscriptRef.current
          : completedUserTranscriptRef.current || latestUserTranscriptRef.current;
        delegationTranscriptSnapshotsRef.current.set(delegationId, {
          transcript,
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
    clearTranscriptIdleTimer,
    cleanup,
    handleDelegatedFunctionCall,
    markFirstAudio,
    markUserTranscriptFinal,
    markResponseStarted,
    remoteAudioRef,
    scheduleTranscriptIdleBoundary,
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
    latestUserTranscriptRef.current = "";
    completedUserTranscriptRef.current = "";
    userTranscriptRevisionRef.current = 0;
    handledSpeechEndRevisionRef.current = -1;
    finalizedTranscriptRevisionRef.current = -1;
    userSpeechActiveRef.current = false;
    clearTranscriptIdleTimer();
    responseObservedRevisionRef.current = -1;
    turnSequenceRef.current = 0;
    activeTurnRef.current = null;
    handledToolCallIdsRef.current.clear();
    delegationToolCallSeenRef.current.clear();
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
  }, [cleanup, clearTranscriptIdleTimer, enabled, handleEvent, markFirstAudio, recordId, remoteAudioRef, t]);

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

type GPTLiveStateUpdatePayload = Parameters<typeof submitGPTLiveStateUpdate>[1];

async function submitGPTLiveStateUpdateWithRetry(
  recordId: string,
  payload: GPTLiveStateUpdatePayload,
  transcript: string,
  signal?: AbortSignal,
) {
  let lastError: unknown;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      return await submitGPTLiveStateUpdate(recordId, payload, transcript, signal);
    } catch (error) {
      lastError = error;
      if (attempt === 1 || !isRetryableLiveStateUpdateError(error)) {
        throw error;
      }
      console.warn("gpt_live_state_update_retry", {
        turn_id: payload.turn_id,
        attempt: attempt + 1,
      });
      await Promise.resolve();
    }
  }
  throw lastError;
}

function isRetryableLiveStateUpdateError(error: unknown): boolean {
  if (!error || typeof error !== "object") {
    return true;
  }
  const status = (error as { status?: unknown }).status;
  if (typeof status !== "number") {
    return true;
  }
  return status === 408 || status === 429 || status >= 500;
}
