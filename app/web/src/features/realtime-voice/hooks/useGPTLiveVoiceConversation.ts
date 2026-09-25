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
// The delegation-created event can arrive after the first response watchdog
// interval. Keep the fallback response from racing a late Responses handoff
// for two intervals; once the delegation exists, its terminal event releases
// the pending guard instead of creating a competing response.
const GPT_LIVE_DELEGATION_WAIT_ATTEMPTS = 2;
const GPT_LIVE_STATE_RESUME_RETRY_TIMEOUT_MS = 1000;
const GPT_LIVE_STATE_RESUME_COMMENTARY =
  "保存された最新のチェックリストを確認し、取得済みの内容は繰り返さず、必要な確認は一つだけ行うか、現在の質問が完了していれば次の質問へ進んでください。相槌だけで終わらず、続けて一つの質問をしてください。";
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
  const liveOutputActiveRevisionRef = useRef<number | null>(null);
  const liveOutputInterruptRevisionRef = useRef<number | null>(null);
  const liveOutputInterruptEventIdRef = useRef<string | null>(null);
  const activeTurnRef = useRef<{
    turnId: string;
    transcriptRevision: number;
    speechEndedAt: number;
    responseStarted: boolean;
    liveResponseActive: boolean;
    responseInterruptRequested: boolean;
    responseInterruptEventId?: string;
    postDelegationResponseStarted: boolean;
    responseObserved: boolean;
    firstAudio: boolean;
    waitNoticeSent: boolean;
    continuationSent: boolean;
    delegationResumeSent: boolean;
    delegationHandoffCompleted: boolean;
    resumeCommentaryAcknowledged: boolean;
    resumeCommentaryEventId?: string;
    resumeCommentaryRetryEventId?: string;
    resumeCommentaryDelegationId?: string;
    delegationObserved: boolean;
    delegationWaitAttempts: number;
    responseStartTimeoutId?: number;
    waitNoticeTimeoutId?: number;
    resumeCommentaryRetryTimeoutId?: number;
  } | null>(null);
  const turnSequenceRef = useRef(0);
  const handledToolCallIdsRef = useRef(new Set<string>());
  const delegationToolCallSeenRef = useRef(new Set<string>());
  const delegatedResponseIdsRef = useRef(new Map<string, string>());
  const delegationInitialResponseIdsRef = useRef(new Map<string, string>());
  const delegationContinuationResponseIdsRef = useRef(new Map<string, string>());
  const delegationCallIdsRef = useRef(new Map<string, string>());
  const delegationStateOutputsRef = useRef(new Map<string, unknown>());
  const pendingDelegationIdsRef = useRef(new Set<string>());
  const delegatedResponsesInFlightRef = useRef(new Set<string>());
  const delegationTranscriptRevisionsRef = useRef(new Map<string, number>());
  const delegationTranscriptSnapshotsRef = useRef(new Map<string, DelegationTranscriptSnapshot>());
  const pendingStateResumeRef = useRef<{
    delegationId: string;
    stateOutput: unknown;
  } | null>(null);
  const flushPendingStateResumeRef = useRef<(() => void) | null>(null);
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
    if (turn.resumeCommentaryRetryTimeoutId !== undefined) {
      window.clearTimeout(turn.resumeCommentaryRetryTimeoutId);
    }
    turn.responseStartTimeoutId = undefined;
    turn.waitNoticeTimeoutId = undefined;
    turn.resumeCommentaryRetryTimeoutId = undefined;
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
    liveOutputActiveRevisionRef.current = null;
    liveOutputInterruptRevisionRef.current = null;
    liveOutputInterruptEventIdRef.current = null;
    handledToolCallIdsRef.current.clear();
    delegationToolCallSeenRef.current.clear();
    delegatedResponseIdsRef.current.clear();
    delegationInitialResponseIdsRef.current.clear();
    delegationContinuationResponseIdsRef.current.clear();
    delegationCallIdsRef.current.clear();
    delegationStateOutputsRef.current.clear();
    pendingDelegationIdsRef.current.clear();
    delegatedResponsesInFlightRef.current.clear();
    delegationTranscriptRevisionsRef.current.clear();
    delegationTranscriptSnapshotsRef.current.clear();
    pendingStateResumeRef.current = null;
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

  const interruptActiveLiveResponse = useCallback((source: string) => {
    const turn = activeTurnRef.current;
    const peer = peerRef.current;
    const transcriptRevision = turn?.transcriptRevision ?? userTranscriptRevisionRef.current;
    const hasTurnOutput = Boolean(turn?.liveResponseActive);
    const hasUnassignedOutput = liveOutputActiveRevisionRef.current === transcriptRevision;
    if (!peer || transcriptRevision <= 0 || (!hasTurnOutput && !hasUnassignedOutput)) {
      return false;
    }
    if (turn?.responseInterruptRequested
      || liveOutputInterruptRevisionRef.current === transcriptRevision) {
      return false;
    }
    const eventId = `gpt_live_response_interrupt_${turn?.turnId ?? `revision-${transcriptRevision}`}`;
    const sent = peer.sendEvent({
      type: "session.instructions.append",
      event_id: eventId,
      delegation_id: null,
      content: "現在の応答を中断し、新しいユーザー発話を優先してください。この指示自体には発話で応答しないでください。",
    });
    if (sent) {
      // Do not wait for the append acknowledgement before accepting the new
      // transcript. The instruction is the supported GPT-Live interruption
      // path, and the old output may continue to drain while it is applied.
      liveOutputActiveRevisionRef.current = null;
      liveOutputInterruptRevisionRef.current = transcriptRevision;
      liveOutputInterruptEventIdRef.current = eventId;
      if (turn) {
        turn.liveResponseActive = false;
        turn.responseInterruptRequested = true;
        turn.responseInterruptEventId = eventId;
      }
    }
    console.info("gpt_live_response_interrupt_requested", {
      session_id: sessionIdRef.current,
      turn_id: turn?.turnId,
      transcript_revision: transcriptRevision,
      type: "session.instructions.append",
      event_id: eventId,
      delegation_id: null,
      sent: Boolean(sent),
      source,
    });
    return Boolean(sent);
  }, []);

  const markResponseStarted = useCallback((source: string) => {
    if (userTranscriptRevisionRef.current > 0) {
      responseObservedRevisionRef.current = userTranscriptRevisionRef.current;
      liveOutputActiveRevisionRef.current = userTranscriptRevisionRef.current;
    }
    const turn = activeTurnRef.current;
    if (!turn) {
      const pendingDelegationForTranscript = [
        ...pendingDelegationIdsRef.current,
        ...delegatedResponsesInFlightRef.current,
      ].some((delegationId) => (
        delegationTranscriptRevisionsRef.current.get(delegationId)
          === userTranscriptRevisionRef.current
      ));
      if (pendingDelegationForTranscript) {
        interruptActiveLiveResponse("responses_delegation_pending_without_turn");
      }
      return;
    }
    if (!turn || (turn.responseStarted && !turn.delegationHandoffCompleted)) {
      return;
    }
    if (turn.delegationHandoffCompleted && turn.postDelegationResponseStarted) {
      return;
    }
    // The wait notice is a transport-level UX message sent when Live has not
    // started its substantive response yet. It can itself produce an output
    // transcript, but that output must not close the watchdog or suppress a
    // late Responses delegation. Once a continuation or the Responses handoff
    // is active, subsequent output is the actual interview response again.
    if (turn.waitNoticeSent && !turn.continuationSent && !turn.delegationHandoffCompleted) {
      console.info("gpt_live_wait_notice_output_started", {
        session_id: sessionIdRef.current,
        turn_id: turn.turnId,
        source,
      });
      return;
    }
    const pendingDelegationForTurn = [
      ...pendingDelegationIdsRef.current,
      ...delegatedResponsesInFlightRef.current,
    ].some((delegationId) => (
      delegationTranscriptRevisionsRef.current.get(delegationId) === turn.transcriptRevision
    ));
    if (!turn.delegationHandoffCompleted && pendingDelegationForTurn) {
      // Live may start a short acknowledgement while the Responses evaluator
      // is still processing. GPT-Live and delegated work are allowed to run
      // concurrently; do not interrupt this response from the application.
      // An instruction append does not cancel or complete the Live response,
      // and leaving that response in an interrupted state prevents the later
      // post-persistence commentary from reaching the model.
      turn.liveResponseActive = true;
      console.info("gpt_live_live_output_deferred", {
        session_id: sessionIdRef.current,
        turn_id: turn.turnId,
        source,
        reason: "responses_delegation_pending",
      });
      return;
    }
    turn.responseStarted = true;
    turn.liveResponseActive = true;
    turn.responseInterruptRequested = false;
    turn.responseInterruptEventId = undefined;
    if (turn.delegationHandoffCompleted) {
      turn.postDelegationResponseStarted = true;
    }
    turn.responseObserved = true;
    if (turn.responseStartTimeoutId !== undefined) {
      window.clearTimeout(turn.responseStartTimeoutId);
      turn.responseStartTimeoutId = undefined;
    }
    if (turn.waitNoticeTimeoutId !== undefined) {
      window.clearTimeout(turn.waitNoticeTimeoutId);
      turn.waitNoticeTimeoutId = undefined;
    }
    if (turn.resumeCommentaryRetryTimeoutId !== undefined) {
      window.clearTimeout(turn.resumeCommentaryRetryTimeoutId);
      turn.resumeCommentaryRetryTimeoutId = undefined;
    }
    console.info("gpt_live_response_started", {
      session_id: sessionIdRef.current,
      turn_id: turn.turnId,
      source,
      phase: turn.delegationHandoffCompleted ? "post_delegation" : "pre_delegation",
      speech_end_to_response_start_ms: Math.round(performance.now() - turn.speechEndedAt),
    });
    console.info("gpt_live_live_output_started", {
      session_id: sessionIdRef.current,
      turn_id: turn.turnId,
      source,
      phase: turn.delegationHandoffCompleted ? "post_delegation" : "pre_delegation",
    });
  }, [interruptActiveLiveResponse]);

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
    console.info("gpt_live_live_audio_started", {
      session_id: sessionIdRef.current,
      turn_id: turn.turnId,
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
      const pendingForTurn = [...pendingDelegationIdsRef.current]
        .filter((delegationId) => (
          delegationTranscriptRevisionsRef.current.get(delegationId) === active.transcriptRevision
        ));
      if (pendingForTurn.length > 0) {
        console.info("gpt_live_continuation_deferred", {
          session_id: sessionIdRef.current,
          turn_id: active.turnId,
          pending_delegations: pendingForTurn.length,
        });
        scheduleResponseStartWatchdog(active);
        return;
      }
      const inFlightForTurn = [...delegatedResponsesInFlightRef.current]
        .filter((delegationId) => (
          delegationTranscriptRevisionsRef.current.get(delegationId) === active.transcriptRevision
        ));
      if (inFlightForTurn.length > 0) {
        console.info("gpt_live_continuation_deferred", {
          session_id: sessionIdRef.current,
          turn_id: active.turnId,
          in_flight_delegations: inFlightForTurn.length,
        });
        scheduleResponseStartWatchdog(active);
        return;
      }
      if (!active.delegationObserved
        && active.delegationWaitAttempts < GPT_LIVE_DELEGATION_WAIT_ATTEMPTS) {
        active.delegationWaitAttempts += 1;
        console.info("gpt_live_continuation_deferred", {
          session_id: sessionIdRef.current,
          turn_id: active.turnId,
          reason: "await_delegation_created",
          wait_attempt: active.delegationWaitAttempts,
        });
        scheduleResponseStartWatchdog(active);
        return;
      }
      if (active.delegationResumeSent) {
        if (userSpeechActiveRef.current) {
          console.info("gpt_live_continuation_deferred", {
            session_id: sessionIdRef.current,
            turn_id: active.turnId,
            reason: "await_user_transcript_final",
          });
          scheduleResponseStartWatchdog(active);
          return;
        }
        active.continuationSent = true;
        const eventId = `gpt_live_continuation_${active.turnId}`;
        const sent = peerRef.current?.sendEvent({
          type: "session.commentary.append",
          event_id: eventId,
          delegation_id: null,
          content: GPT_LIVE_STATE_RESUME_COMMENTARY,
        });
        console.info("gpt_live_continuation_trigger", {
          session_id: sessionIdRef.current,
          turn_id: active.turnId,
          sent: Boolean(sent),
          type: "session.commentary.append",
          event_id: eventId,
          delegation_id: null,
          content_length: GPT_LIVE_STATE_RESUME_COMMENTARY.length,
          speech_end_to_continuation_ms: Math.round(performance.now() - active.speechEndedAt),
        });
        return;
      }
      active.continuationSent = true;
      const eventId = `gpt_live_continuation_${active.turnId}`;
      const sent = peerRef.current?.sendEvent({
        type: "response.create",
        event_id: eventId,
      });
      console.info("gpt_live_continuation_trigger", {
        session_id: sessionIdRef.current,
        turn_id: active.turnId,
        sent: Boolean(sent),
        type: "response.create",
        event_id: eventId,
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
      transcriptRevision,
      speechEndedAt: performance.now(),
      responseStarted: false,
      liveResponseActive: liveOutputActiveRevisionRef.current === transcriptRevision,
      responseInterruptRequested: liveOutputInterruptRevisionRef.current === transcriptRevision,
      responseInterruptEventId: liveOutputInterruptRevisionRef.current === transcriptRevision
        ? liveOutputInterruptEventIdRef.current ?? undefined
        : undefined,
      postDelegationResponseStarted: false,
      responseObserved: responseObservedRevisionRef.current === transcriptRevision,
      firstAudio: false,
      waitNoticeSent: false,
      continuationSent: false,
      delegationResumeSent: false,
      delegationHandoffCompleted: false,
      resumeCommentaryAcknowledged: false,
      delegationObserved: false,
      delegationWaitAttempts: 0,
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
      const delegationPending = [
        ...pendingDelegationIdsRef.current,
        ...delegatedResponsesInFlightRef.current,
      ].some((delegationId) => (
        delegationTranscriptRevisionsRef.current.get(delegationId) === active.transcriptRevision
      ));
      if (active.delegationObserved || delegationPending) {
        // A Delegation is an in-flight semantic evaluation, not a slow Live
        // response. Sending a wait notice here creates another Live output
        // which can occupy the session when the persisted result is returned.
        console.info("gpt_live_wait_notice_deferred", {
          session_id: sessionIdRef.current,
          turn_id: active.turnId,
          reason: "responses_delegation_pending",
        });
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
      flushPendingStateResumeRef.current?.();
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
    if (!eventDelegationId) {
      console.warn("gpt_live_delegation_id_missing", {
        session_id: sessionIdRef.current,
        call_id: callId,
        response_id: readResponseId(event),
      });
      return;
    }
    const delegationId = eventDelegationId;
    delegationCallIdsRef.current.set(delegationId, callId);
    const transcriptSnapshot = delegationTranscriptSnapshotsRef.current.get(delegationId);
    if (activeTurnRef.current && !transcriptSnapshot?.sealed) {
      delegationTranscriptRevisionsRef.current.set(
        delegationId,
        activeTurnRef.current.transcriptRevision,
      );
    }
    delegationToolCallSeenRef.current.add(delegationId);
    const responseId = readResponseId(event)
      ?? delegatedResponseIdsRef.current.get(delegationId);
    if (responseId && !delegationInitialResponseIdsRef.current.has(delegationId)) {
      delegationInitialResponseIdsRef.current.set(delegationId, responseId);
    }
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
      console.info("gpt_live_state_update_prepared", {
        session_id: sessionIdRef.current,
        delegation_id: delegationId,
        call_id: callId,
        turn_id: stateUpdatePayload.turn_id,
        question_id: stateUpdatePayload.question_id,
        base_state_version: stateUpdatePayload.base_state_version,
      });
      stateUpdateAbortController = new AbortController();
      stateUpdateAbortControllersRef.current.add(stateUpdateAbortController);
      const persist = async () => {
        if (!isCurrentLiveCall()) {
          return;
        }
        console.info("gpt_live_state_persistence_request", {
          session_id: sessionIdRef.current,
          delegation_id: delegationId,
          call_id: callId,
          turn_id: stateUpdatePayload.turn_id,
        });
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
      console.info("gpt_live_state_persistence_succeeded", {
        session_id: sessionIdRef.current,
        delegation_id: delegationId,
        call_id: callId,
        turn_id: stateUpdatePayload.turn_id,
        state_version: acknowledgedStateVersion,
        current_question_id: (output as { currentQuestionId?: unknown })?.currentQuestionId,
        answer_status: stateUpdatePayload.answer_status,
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
    const peer = peerRef.current;
    if (!peer) {
      return;
    }
    // The State Store response also contains the complete persisted checklist
    // for the UI refresh. That record is not the delegation result the Live
    // model needs. Returning it verbatim makes the follow-up Responses turn
    // re-reason over the whole interview state and can delay the Live reply
    // long enough for the response watchdog to fire. Keep the tool result at
    // the semantic state boundary: status, revision, current question, and
    // the aspect delta used by the Live interviewer.
    const functionOutput = JSON.stringify(compactLiveStateUpdateOutput(output));
    delegationStateOutputsRef.current.set(delegationId, output);
    const toolResultEventId = `gpt_live_state_tool_result_${callId}`;
    console.info("gpt_live_delegation_result_prepared", {
      session_id: sessionIdRef.current,
      delegation_id: delegationId,
      call_id: callId,
      type: "response.item.create",
      event_id: toolResultEventId,
      content_length: functionOutput.length,
    });
    const outputSent = peer.sendEvent({
      type: "response.item.create",
      event_id: toolResultEventId,
      item: {
        type: "function_call_output",
        call_id: callId,
        output: functionOutput,
      },
    });
    console.info("gpt_live_delegation_result_sent", {
      session_id: sessionIdRef.current,
      delegation_id: delegationId,
      call_id: callId,
      type: "response.item.create",
      event_id: toolResultEventId,
      sent: Boolean(outputSent),
      content_length: functionOutput.length,
    });
    const continueEventId = `gpt_live_state_tool_continue_${callId}`;
    const responseRequested = outputSent && peer.sendEvent({
      type: "response.create",
      event_id: continueEventId,
    });
    if (responseRequested) {
      delegatedResponsesInFlightRef.current.add(delegationId);
      delegationContinuationResponseIdsRef.current.delete(delegationId);
      if (!delegationTranscriptRevisionsRef.current.has(delegationId)
        && activeTurnRef.current) {
        delegationTranscriptRevisionsRef.current.set(
          delegationId,
          activeTurnRef.current.transcriptRevision,
        );
      }
    }
    console.info("gpt_live_delegation_result_sent", {
      session_id: sessionIdRef.current,
      delegation_id: delegationId,
      call_id: callId,
      type: "response.create",
      event_id: continueEventId,
      sent: Boolean(responseRequested),
      content_length: 0,
    });
    if (!responseRequested) {
      pendingDelegationIdsRef.current.delete(delegationId);
      delegationTranscriptRevisionsRef.current.delete(delegationId);
      delegationTranscriptSnapshotsRef.current.delete(delegationId);
      delegationToolCallSeenRef.current.delete(delegationId);
      delegationInitialResponseIdsRef.current.delete(delegationId);
      delegationContinuationResponseIdsRef.current.delete(delegationId);
      delegationCallIdsRef.current.delete(delegationId);
      delegationStateOutputsRef.current.delete(delegationId);
      delegatedResponseIdsRef.current.delete(delegationId);
    } else {
      console.info("gpt_live_live_update_deferred", {
        session_id: sessionIdRef.current,
        related_delegation_id: delegationId,
        reason: "await_responses_handoff_completion",
      });
    }
    console.info("gpt_live_delegation_completed", {
      session_id: sessionIdRef.current,
      delegation_id: delegationId,
      call_id: callId,
      response_id: responseId,
      function_output_sent: outputSent,
      response_requested: Boolean(responseRequested),
    });
  }, []);

  const sendResumeCommentary = useCallback((
    delegationId: string,
    commentaryEventId: string,
    turnId: string,
    transcriptRevision: number,
  ) => {
    const currentTurn = activeTurnRef.current;
    const peer = peerRef.current;
    if (!currentTurn || !peer || currentTurn.turnId !== turnId
      || currentTurn.transcriptRevision !== transcriptRevision
      || currentTurn.postDelegationResponseStarted
      || currentTurn.resumeCommentaryAcknowledged) {
      return false;
    }
    if (currentTurn.resumeCommentaryRetryEventId) {
      return false;
    }
    console.info("gpt_live_live_update_prepared", {
      session_id: sessionIdRef.current,
      related_delegation_id: delegationId,
      delegation_id: null,
      type: "session.commentary.append",
      event_id: commentaryEventId,
      content_length: GPT_LIVE_STATE_RESUME_COMMENTARY.length,
      after_responses_handoff: true,
    });
    const commentarySent = peer.sendEvent({
      type: "session.commentary.append",
      event_id: commentaryEventId,
      delegation_id: null,
      content: GPT_LIVE_STATE_RESUME_COMMENTARY,
    });
    console.info("gpt_live_live_update_sent", {
      session_id: sessionIdRef.current,
      related_delegation_id: delegationId,
      delegation_id: null,
      type: "session.commentary.append",
      event_id: commentaryEventId,
      sent: Boolean(commentarySent),
      content_length: GPT_LIVE_STATE_RESUME_COMMENTARY.length,
      after_responses_handoff: true,
    });
    if (commentarySent) {
      currentTurn.resumeCommentaryRetryTimeoutId = window.setTimeout(() => {
        const retryTurn = activeTurnRef.current;
        if (!retryTurn || retryTurn.turnId !== turnId
          || retryTurn.transcriptRevision !== transcriptRevision
          || retryTurn.postDelegationResponseStarted
          || retryTurn.resumeCommentaryRetryEventId) {
          return;
        }
        const retryEventId = `${commentaryEventId}_retry`;
        retryTurn.resumeCommentaryRetryEventId = retryEventId;
        console.info("gpt_live_live_update_retry_prepared", {
          session_id: sessionIdRef.current,
          related_delegation_id: delegationId,
          delegation_id: null,
          type: "session.commentary.append",
          event_id: retryEventId,
          original_event_id: commentaryEventId,
          content_length: GPT_LIVE_STATE_RESUME_COMMENTARY.length,
        });
        const retrySent = peer.sendEvent({
          type: "session.commentary.append",
          event_id: retryEventId,
          delegation_id: null,
          content: GPT_LIVE_STATE_RESUME_COMMENTARY,
        });
        console.info("gpt_live_live_update_retry_sent", {
          session_id: sessionIdRef.current,
          related_delegation_id: delegationId,
          delegation_id: null,
          type: "session.commentary.append",
          event_id: retryEventId,
          original_event_id: commentaryEventId,
          sent: Boolean(retrySent),
          content_length: GPT_LIVE_STATE_RESUME_COMMENTARY.length,
        });
      }, GPT_LIVE_STATE_RESUME_RETRY_TIMEOUT_MS);
    }
    return Boolean(commentarySent);
  }, []);

  const sendStateResumeCommentary = useCallback((
    delegationId: string,
    stateOutput: unknown,
  ) => {
    const activeTurn = activeTurnRef.current;
    const peer = peerRef.current;
    if (!activeTurn || !peer || activeTurn.postDelegationResponseStarted || activeTurn.continuationSent) {
      return false;
    }
    const delegationRevision = delegationTranscriptRevisionsRef.current.get(delegationId);
    if (delegationRevision !== undefined && delegationRevision !== activeTurn.transcriptRevision) {
      console.info("gpt_live_live_update_skipped_stale_turn", {
        session_id: sessionIdRef.current,
        related_delegation_id: delegationId,
        active_transcript_revision: activeTurn.transcriptRevision,
        delegation_transcript_revision: delegationRevision,
      });
      return false;
    }
    if (userSpeechActiveRef.current) {
      activeTurn.delegationResumeSent = true;
      pendingStateResumeRef.current = { delegationId, stateOutput };
      console.info("gpt_live_live_update_deferred", {
        session_id: sessionIdRef.current,
        related_delegation_id: delegationId,
        reason: "await_user_transcript_final",
      });
      return true;
    }
    const callId = delegationCallIdsRef.current.get(delegationId) ?? delegationId;
    const commentaryEventId = `gpt_live_state_resume_${callId}`;
    const turnId = activeTurn.turnId;
    const transcriptRevision = activeTurn.transcriptRevision;
    activeTurn.delegationResumeSent = true;
    activeTurn.continuationSent = true;
    activeTurn.resumeCommentaryEventId = commentaryEventId;
    activeTurn.resumeCommentaryDelegationId = delegationId;

    const stateContextEventId = `${commentaryEventId}_state`;
    const stateContext = compactLiveStateUpdateOutput(stateOutput);
    const stateContextContent = [
      "Private application state update; do not read these labels aloud.",
      `status=${String(stateContext.status ?? "unknown")}`,
      `state_version=${String(stateContext.stateVersion ?? "unknown")}`,
      `current_question_id=${String(stateContext.currentQuestionId ?? "")}`,
      `covered_aspects=${JSON.stringify(stateContext.coveredAspects ?? [])}`,
      `missing_aspects=${JSON.stringify(stateContext.missingAspects ?? [])}`,
      `checklist=${JSON.stringify(stateContext.checklist ?? [])}`,
      `current=${JSON.stringify(stateContext.current ?? null)}`,
    ].join(" ");

    // The Responses continuation is not itself spoken by GPT-Live. Feed the
    // persisted semantic state back as private context, then use commentary
    // to request the user-facing continuation. The two channels have
    // different responsibilities: state is not spoken, while commentary is
    // the only text the Live model should turn into speech here.
    window.setTimeout(() => {
      const currentTurn = activeTurnRef.current;
      if (!currentTurn || currentTurn.turnId !== turnId
        || currentTurn.transcriptRevision !== transcriptRevision
        || currentTurn.postDelegationResponseStarted) {
        console.info("gpt_live_live_update_skipped_stale_turn", {
          session_id: sessionIdRef.current,
          related_delegation_id: delegationId,
        type: "session.thinking.append",
        event_id: stateContextEventId,
        reason: "state_resume_context_after_turn_change",
        });
        return;
      }
      console.info("gpt_live_live_update_prepared", {
        session_id: sessionIdRef.current,
        related_delegation_id: delegationId,
        delegation_id: null,
        type: "session.thinking.append",
        event_id: stateContextEventId,
        content_length: stateContextContent.length,
        after_responses_handoff: true,
      });
      const stateContextSent = peer.sendEvent({
        type: "session.thinking.append",
        event_id: stateContextEventId,
        delegation_id: null,
        content: stateContextContent,
      });
      console.info("gpt_live_live_update_sent", {
        session_id: sessionIdRef.current,
        related_delegation_id: delegationId,
        delegation_id: null,
        type: "session.thinking.append",
        event_id: stateContextEventId,
        sent: Boolean(stateContextSent),
        content_length: stateContextContent.length,
        after_responses_handoff: true,
      });
      // An append acknowledgement only confirms that the context reached the
      // Live timeline; it does not mean that Live will start speaking. Send
      // the speakable continuation in the same turn instead of waiting for an
      // acknowledgement that may remain pending while the timeline is idle.
      console.info("gpt_live_state_context_ready_for_commentary", {
        session_id: sessionIdRef.current,
        related_delegation_id: delegationId,
        state_context_event_id: stateContextEventId,
        reason: stateContextSent ? "state_context_sent" : "state_context_send_failed",
      });
      sendResumeCommentary(delegationId, commentaryEventId, turnId, transcriptRevision);
    }, 0);
    return true;
  }, [sendResumeCommentary]);

  const flushPendingStateResume = useCallback(() => {
    if (userSpeechActiveRef.current) {
      return;
    }
    const pending = pendingStateResumeRef.current;
    if (!pending) {
      return;
    }
    pendingStateResumeRef.current = null;
    sendStateResumeCommentary(pending.delegationId, pending.stateOutput);
  }, [sendStateResumeCommentary]);
  flushPendingStateResumeRef.current = flushPendingStateResume;

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
        const initialResponseId = delegationInitialResponseIdsRef.current.get(delegationId);
        if (delegatedResponsesInFlightRef.current.has(delegationId)
          && responseId !== initialResponseId) {
          delegationContinuationResponseIdsRef.current.set(delegationId, responseId);
        }
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
    const responseDoneType = isResponseDoneEvent(eventType)
      ? eventType
      : isResponseDoneEvent(nestedType)
        ? nestedType
        : undefined;
    if (responseDoneType) {
      clearActiveTurnTimers();
      if (eventType !== "response.event") {
        const activeTurn = activeTurnRef.current;
        if (activeTurn
          && [
            "response.done",
            "response.output_audio.done",
            "session.output_audio.done",
            "response.audio.done",
          ].includes(responseDoneType)) {
          activeTurn.liveResponseActive = false;
        }
        if (liveOutputActiveRevisionRef.current === activeTurn?.transcriptRevision
          || !activeTurn) {
          liveOutputActiveRevisionRef.current = null;
        }
      }
      console.info("gpt_live_response_done", {
        session_id: eventSessionId,
        turn_id: activeTurnRef.current?.turnId,
        type: responseDoneType,
      });
      if (["response.output_audio.done", "session.output_audio.done", "response.audio.done"].includes(responseDoneType)) {
        console.info("gpt_live_live_audio_completed", {
          session_id: eventSessionId,
          turn_id: activeTurnRef.current?.turnId,
          type: responseDoneType,
        });
      }
      // A nested Responses response.done is backend work, not the Live
      // assistant's output completion. Only the top-level Live event closes
      // the user-facing output lifecycle.
      if (eventType === "response.done") {
        console.info("gpt_live_live_output_completed", {
          session_id: eventSessionId,
          turn_id: activeTurnRef.current?.turnId,
          type: responseDoneType,
        });
      }
    }
    if (eventType === "response.cancelled" || eventType === "response.canceled") {
      const activeTurn = activeTurnRef.current;
      if (activeTurn) {
        activeTurn.liveResponseActive = false;
      }
      console.info("gpt_live_live_output_cancelled", {
        session_id: eventSessionId,
        turn_id: activeTurn?.turnId,
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
      const responseId = readResponseId(event);
      const initialResponseId = delegationId
        ? delegationInitialResponseIdsRef.current.get(delegationId)
        : undefined;
      const continuationResponseId = delegationId
        ? delegationContinuationResponseIdsRef.current.get(delegationId)
        : undefined;
      const isContinuationTerminal = Boolean(
        delegationId
        && delegatedResponsesInFlightRef.current.has(delegationId)
        && ((responseId && responseId === continuationResponseId)
          || (!responseId && Boolean(continuationResponseId))),
      );
      if (isContinuationTerminal && delegationId) {
        delegatedResponsesInFlightRef.current.delete(delegationId);
        pendingDelegationIdsRef.current.delete(delegationId);
        const delegationRevision = delegationTranscriptRevisionsRef.current.get(delegationId);
        const activeTurn = activeTurnRef.current;
        if (activeTurn && delegationRevision === activeTurn.transcriptRevision) {
          activeTurn.delegationHandoffCompleted = true;
        }
        sendStateResumeCommentary(
          delegationId,
          delegationStateOutputsRef.current.get(delegationId),
        );
        delegationTranscriptSnapshotsRef.current.delete(delegationId);
        delegationToolCallSeenRef.current.delete(delegationId);
        delegationTranscriptRevisionsRef.current.delete(delegationId);
        delegationInitialResponseIdsRef.current.delete(delegationId);
        delegationContinuationResponseIdsRef.current.delete(delegationId);
        delegationCallIdsRef.current.delete(delegationId);
        delegationStateOutputsRef.current.delete(delegationId);
        delegatedResponseIdsRef.current.delete(delegationId);
        console.info("gpt_live_delegation_result_acknowledged", {
          session_id: eventSessionId,
          delegation_id: delegationId,
          response_id: responseId ?? continuationResponseId,
          response_event: nestedType,
          transcript_revision: delegationRevision,
        });
        console.info("gpt_live_responses_backend_completed", {
          session_id: eventSessionId,
          delegation_id: delegationId,
          response_event: nestedType,
        });
      } else if (delegationId && responseId && responseId === initialResponseId) {
        console.info("gpt_live_responses_initial_backend_completed", {
          session_id: eventSessionId,
          delegation_id: delegationId,
          response_id: responseId,
          response_event: nestedType,
        });
        // A Responses delegation may complete without requesting the state
        // tool. It is still a terminal delegation result; leaving it in the
        // pending set makes the response watchdog defer forever and prevents
        // Live from continuing the conversation.
        if (!delegationToolCallSeenRef.current.has(delegationId)) {
          pendingDelegationIdsRef.current.delete(delegationId);
          delegationTranscriptSnapshotsRef.current.delete(delegationId);
          delegationTranscriptRevisionsRef.current.delete(delegationId);
          delegationInitialResponseIdsRef.current.delete(delegationId);
          delegationContinuationResponseIdsRef.current.delete(delegationId);
          delegationCallIdsRef.current.delete(delegationId);
          delegationStateOutputsRef.current.delete(delegationId);
          delegatedResponseIdsRef.current.delete(delegationId);
          delegationToolCallSeenRef.current.delete(delegationId);
          console.warn("gpt_live_delegation_finished_without_state_tool", {
            session_id: eventSessionId,
            delegation_id: delegationId,
            response_event: nestedType,
          });
        }
      } else if (delegationId && !delegationToolCallSeenRef.current.has(delegationId)) {
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
      const activeTurn = activeTurnRef.current;
      if (activeTurn && activeTurn.responseInterruptEventId === acknowledgedEventId) {
        console.info("gpt_live_response_interrupt_acknowledged", {
          session_id: eventSessionId,
          turn_id: activeTurn.turnId,
          type: eventType,
          client_event_id: acknowledgedEventId,
        });
      }
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

    if (eventType === "session.thinking.appended") {
      const acknowledgedEventId = readClientEventId(event);
      const activeTurn = activeTurnRef.current;
      // The ACK is telemetry only. The commentary is sent immediately after
      // the thinking append so an idle Live timeline cannot delay the spoken
      // continuation.
      return;
    }

    if (eventType === "session.commentary.appended") {
      const acknowledgedEventId = readClientEventId(event);
      const activeTurn = activeTurnRef.current;
      if (activeTurn && acknowledgedEventId
        && [activeTurn.resumeCommentaryEventId, activeTurn.resumeCommentaryRetryEventId]
          .includes(acknowledgedEventId)) {
        activeTurn.resumeCommentaryAcknowledged = true;
      }
      console.info("gpt_live_live_update_acknowledged", {
        session_id: eventSessionId,
        type: eventType,
        client_event_id: acknowledgedEventId,
      });
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
      interruptActiveLiveResponse("user_speech_started");
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
      // Some GPT-Live sessions do not emit input_audio_buffer.speech_started
      // over the browser DataChannel. The first input transcript is still a
      // reliable indication that the user has started a new utterance, so use
      // it to stop a previous Live output before the next delegation resumes.
      interruptActiveLiveResponse("input_transcript_delta");
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
      flushPendingStateResumeRef.current?.();
      beginUserTurnWatchdog("provider_event");
      return;
    }

    if (eventType === "session.delegation.created") {
      const delegationId = readDelegationId(event);
      const responseId = readResponseId(event);
      if (delegationId && responseId) {
        delegationInitialResponseIdsRef.current.set(delegationId, responseId);
        delegatedResponseIdsRef.current.set(delegationId, responseId);
      }
      if (delegationId) {
        pendingDelegationIdsRef.current.add(delegationId);
        delegationTranscriptRevisionsRef.current.set(
          delegationId,
          userTranscriptRevisionRef.current,
        );
        // Responses delegation can be created before GPT-Live emits a
        // provider speech-end event (the browser may only receive transcript
        // deltas). Establish the application turn at this boundary so the
        // persisted result and the following Live response remain attached to
        // the same user utterance. This is not an utterance-completeness
        // decision; the delegated evaluator is still the source of semantic
        // state, while the delegation event is only the transport correlation
        // boundary.
        if (!activeTurnRef.current) {
          beginUserTurnWatchdog("delegation_created");
        }
        const activeTurn = activeTurnRef.current;
        if (activeTurn
          && activeTurn.transcriptRevision === userTranscriptRevisionRef.current) {
          activeTurn.delegationObserved = true;
        }
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
        target: event.delegation && typeof event.delegation === "object"
          ? (event.delegation as Record<string, unknown>).target
          : undefined,
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
    interruptActiveLiveResponse,
    remoteAudioRef,
    scheduleTranscriptIdleBoundary,
    sendResumeCommentary,
    sendStateResumeCommentary,
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
    liveOutputActiveRevisionRef.current = null;
    liveOutputInterruptRevisionRef.current = null;
    liveOutputInterruptEventIdRef.current = null;
    turnSequenceRef.current = 0;
    activeTurnRef.current = null;
    handledToolCallIdsRef.current.clear();
    delegationToolCallSeenRef.current.clear();
    delegatedResponseIdsRef.current.clear();
    delegationInitialResponseIdsRef.current.clear();
    delegationContinuationResponseIdsRef.current.clear();
    delegationCallIdsRef.current.clear();
    delegationStateOutputsRef.current.clear();
    pendingDelegationIdsRef.current.clear();
    delegatedResponsesInFlightRef.current.clear();
    delegationTranscriptRevisionsRef.current.clear();
    delegationTranscriptSnapshotsRef.current.clear();
    pendingStateResumeRef.current = null;
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

function compactLiveStateUpdateOutput(output: unknown): Record<string, unknown> {
  if (!output || typeof output !== "object") {
    return { status: "error", code: "invalid_state_update_result" };
  }
  const value = output as Record<string, unknown>;
  const checklist = Array.isArray(value.checklist)
    ? value.checklist
      .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"))
      .map((item) => ({
        ...(typeof item.id === "string" ? { id: item.id } : {}),
        ...(typeof item.question_id === "string" ? { question_id: item.question_id } : {}),
        ...(typeof item.answer_state === "string" ? { answer_state: item.answer_state } : {}),
        ...(Array.isArray(item.missing_required_items)
          ? { missing_required_items: item.missing_required_items }
          : {}),
      }))
    : [];
  const current = value.current && typeof value.current === "object"
    ? value.current as Record<string, unknown>
    : null;
  return {
    ...(typeof value.status === "string" ? { status: value.status } : {}),
    ...(typeof value.stateVersion === "number" ? { stateVersion: value.stateVersion } : {}),
    ...(typeof value.currentQuestionId === "string" || value.currentQuestionId === null
      ? { currentQuestionId: value.currentQuestionId }
      : {}),
    ...(Array.isArray(value.coveredAspects) ? { coveredAspects: value.coveredAspects } : {}),
    ...(Array.isArray(value.missingAspects) ? { missingAspects: value.missingAspects } : {}),
    ...(checklist.length > 0 ? { checklist } : {}),
    ...(current
      ? {
        current: {
          ...(typeof current.field_id === "string" || current.field_id === null
            ? { field_id: current.field_id }
            : {}),
          ...(typeof current.question_id === "string" || current.question_id === null
            ? { question_id: current.question_id }
            : {}),
          ...(typeof current.state_version === "number"
            ? { state_version: current.state_version }
            : {}),
          ...(typeof current.answer_evaluation === "string" || current.answer_evaluation === null
            ? { answer_evaluation: current.answer_evaluation }
            : {}),
          ...(typeof current.clarification_kind === "string"
            ? { clarification_kind: current.clarification_kind }
            : {}),
          ...(typeof current.clarification_target === "string" || current.clarification_target === null
            ? { clarification_target: current.clarification_target }
            : {}),
        },
      }
      : {}),
  };
}

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
