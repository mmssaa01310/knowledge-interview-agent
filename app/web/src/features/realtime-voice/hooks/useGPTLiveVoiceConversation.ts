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
// If Live already received the bounded wait notice, give a late Responses
// delegation one additional watchdog interval before creating a competing
// generic response. This is an event-based grace period, not a completeness
// or transcript timeout change.
const GPT_LIVE_DELEGATION_GRACE_AFTER_WAIT_NOTICE_ATTEMPTS = 1;
const GPT_LIVE_STATE_RESUME_RETRY_TIMEOUT_MS = 1000;
// Some GPT-Live sessions do not emit a provider-level response/audio done
// event for a response that was interrupted by the next user utterance. Keep
// the persisted-state handoff behind a short assistant-output quiet period so
// that a trailing acknowledgement cannot occupy the Live timeline when the
// state context is returned. This is an assistant-output transport fallback;
// it is not used to infer user utterance completeness.
const GPT_LIVE_OUTPUT_IDLE_TIMEOUT_MS = 1000;
const GPT_LIVE_STATE_RESUME_COMMENTARY =
  "保存されたStateと現在の会話を踏まえ、ユーザーに短く応答してください。取得済みの内容を再質問せず、重要な不足があれば一つだけ確認し、回答が成立していれば次の質問へ進んでください。相槌だけで終わらず、続けて一つの質問をしてください。";
const GPT_LIVE_CONVERSATION_RESUME_COMMENTARY =
  "最新のユーザー発話を受け止め、現在のインタビューを自然に続けてください。取得済みの内容を再質問せず、重要な不足があれば一つだけ確認し、回答が成立していれば次の質問へ進んでください。相槌だけで終わらず、続けて一つの質問をしてください。";
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
  const liveOutputIdleTimeoutIdRef = useRef<number | undefined>();
  const responseObservedRevisionRef = useRef(-1);
  const liveOutputActiveRevisionRef = useRef<number | null>(null);
  const liveOutputInterruptRevisionRef = useRef<number | null>(null);
  const liveOutputInterruptEventIdRef = useRef<string | null>(null);
  const liveOutputInterruptSourceRef = useRef<string | null>(null);
  const liveOutputInterruptAcknowledgedEventIdRef = useRef<string | null>(null);
  // An input-side interruption belongs to the next user turn even when the
  // provider emits several transcript deltas before speech-end. Keep that
  // boundary separately from the transcript revision so the following
  // Responses handoff cannot send a second interruption for the same turn.
  const pendingInputInterruptEventIdRef = useRef<string | null>(null);
  const activeTurnRef = useRef<{
    turnId: string;
    transcriptRevision: number;
    speechEndedAt: number;
    responseStarted: boolean;
    liveResponseActive: boolean;
    responseInterruptRequested: boolean;
    responseInterruptAcknowledged: boolean;
    responseInterruptSource?: "input" | "state_resume";
    responseInterruptEventId?: string;
    postDelegationResponseStarted: boolean;
    responseObserved: boolean;
    userFacingOutputObserved: boolean;
    firstAudio: boolean;
    waitNoticeSent: boolean;
    continuationSent: boolean;
    delegationResumeSent: boolean;
    resumedDelegationIds: Set<string>;
    delegationHandoffCompleted: boolean;
    resumeCommentaryAcknowledged: boolean;
    resumeCommentaryEventId?: string;
    resumeCommentaryRetryEventId?: string;
    resumeCommentaryDelegationId?: string;
    delegationObserved: boolean;
    delegationWaitAttempts: number;
    responseStartTimeoutId?: number;
    waitNoticeTimeoutId?: number;
    resumeInterruptAckTimeoutId?: number;
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
  const delegationTranscriptWaitersRef = useRef(new Map<string, Set<() => void>>());
  const pendingStateResumeRef = useRef<{
    delegationId: string;
    stateOutput: unknown;
    callId?: string;
  } | null>(null);
  const pendingLiveContinuationRef = useRef<{
    reason: string;
    relatedDelegationId?: string;
  } | null>(null);
  const flushPendingLiveContinuationRef = useRef<(() => void) | null>(null);
  const pendingLiveResumeAfterInterruptRef = useRef<{
    eventId: string;
    flush: () => void;
  } | null>(null);
  const flushPendingStateResumeRef = useRef<(() => void) | null>(null);
  const sendStateResumeCommentaryRef = useRef<((
    delegationId: string,
    stateOutput: unknown,
    callId?: string,
  ) => boolean) | null>(null);
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
    if (turn.resumeInterruptAckTimeoutId !== undefined) {
      window.clearTimeout(turn.resumeInterruptAckTimeoutId);
    }
    if (turn.resumeCommentaryRetryTimeoutId !== undefined) {
      window.clearTimeout(turn.resumeCommentaryRetryTimeoutId);
    }
    turn.responseStartTimeoutId = undefined;
    turn.waitNoticeTimeoutId = undefined;
    turn.resumeInterruptAckTimeoutId = undefined;
    turn.resumeCommentaryRetryTimeoutId = undefined;
  }, []);

  const clearTranscriptIdleTimer = useCallback(() => {
    if (transcriptIdleTimeoutIdRef.current !== undefined) {
      window.clearTimeout(transcriptIdleTimeoutIdRef.current);
      transcriptIdleTimeoutIdRef.current = undefined;
    }
  }, []);

  const clearLiveOutputIdleTimer = useCallback(() => {
    if (liveOutputIdleTimeoutIdRef.current !== undefined) {
      window.clearTimeout(liveOutputIdleTimeoutIdRef.current);
      liveOutputIdleTimeoutIdRef.current = undefined;
    }
  }, []);

  const scheduleLiveOutputIdleBoundary = useCallback(() => {
    clearLiveOutputIdleTimer();
    const transcriptRevision = userTranscriptRevisionRef.current;
    const turnId = activeTurnRef.current?.turnId;
    liveOutputIdleTimeoutIdRef.current = window.setTimeout(() => {
      liveOutputIdleTimeoutIdRef.current = undefined;
      const activeTurn = activeTurnRef.current;
      if (activeTurn && activeTurn.transcriptRevision !== transcriptRevision) {
        return;
      }
      if (activeTurn?.transcriptRevision === transcriptRevision) {
        activeTurn.liveResponseActive = false;
      }
      if (liveOutputActiveRevisionRef.current === transcriptRevision) {
        liveOutputActiveRevisionRef.current = null;
      }
      console.info("gpt_live_live_output_idle", {
        session_id: sessionIdRef.current,
        turn_id: turnId,
        transcript_revision: transcriptRevision,
        idle_timeout_ms: GPT_LIVE_OUTPUT_IDLE_TIMEOUT_MS,
      });
      // A delegated State result may have arrived while the preceding Live
      // output was still draining. Retry the same handoff now that the
      // timeline is quiet; this does not create another interruption boundary.
      if (!userSpeechActiveRef.current) {
        flushPendingStateResumeRef.current?.();
      }
    }, GPT_LIVE_OUTPUT_IDLE_TIMEOUT_MS);
  }, [clearLiveOutputIdleTimer]);

  const scheduleLiveResumeAfterInterruptAck = useCallback((
    eventId: string,
    flush: () => void,
    reason: string,
  ) => {
    const turn = activeTurnRef.current;
    if (!turn) {
      return;
    }
    if (turn.resumeInterruptAckTimeoutId !== undefined) {
      window.clearTimeout(turn.resumeInterruptAckTimeoutId);
    }
    const turnId = turn.turnId;
    const transcriptRevision = turn.transcriptRevision;
    turn.resumeInterruptAckTimeoutId = window.setTimeout(() => {
      const currentTurn = activeTurnRef.current;
      const pending = pendingLiveResumeAfterInterruptRef.current;
      if (!currentTurn || currentTurn.turnId !== turnId
        || currentTurn.transcriptRevision !== transcriptRevision
        || !pending || pending.eventId !== eventId) {
        return;
      }
      currentTurn.resumeInterruptAckTimeoutId = undefined;
      pendingLiveResumeAfterInterruptRef.current = null;
      if (userSpeechActiveRef.current) {
        console.info("gpt_live_live_update_interrupt_ack_timeout_skipped", {
          session_id: sessionIdRef.current,
          turn_id: turnId,
          event_id: eventId,
          reason: "new_user_speech_started",
        });
        return;
      }
      console.warn("gpt_live_live_update_interrupt_ack_timeout", {
        session_id: sessionIdRef.current,
        turn_id: turnId,
        transcript_revision: transcriptRevision,
        event_id: eventId,
        reason,
        action: "flush_live_resume_without_ack",
      });
      // The append ACK is a transport acknowledgement, not proof that Live
      // can or will speak. A missing ACK must not make the persisted turn a
      // silent dead-end. Flush the already prepared context/commentary once;
      // the caller still owns the single interruption boundary.
      flush();
    }, GPT_LIVE_STATE_RESUME_RETRY_TIMEOUT_MS);
  }, []);

  const resolveDelegationTranscriptWaiters = useCallback((delegationId: string) => {
    const waiters = delegationTranscriptWaitersRef.current.get(delegationId);
    if (!waiters) {
      return;
    }
    delegationTranscriptWaitersRef.current.delete(delegationId);
    for (const resolve of waiters) {
      resolve();
    }
  }, []);

  const waitForDelegationTranscriptBoundary = useCallback((delegationId: string) => {
    const snapshot = delegationTranscriptSnapshotsRef.current.get(delegationId);
    if (!snapshot || snapshot.sealed || !userSpeechActiveRef.current) {
      return Promise.resolve();
    }
    return new Promise<void>((resolve) => {
      const waiters = delegationTranscriptWaitersRef.current.get(delegationId) ?? new Set();
      waiters.add(resolve);
      delegationTranscriptWaitersRef.current.set(delegationId, waiters);
      // A provider speech boundary may arrive between the initial check and
      // waiter registration. Resolve immediately in that race.
      const current = delegationTranscriptSnapshotsRef.current.get(delegationId);
      if (!current || current.sealed || !userSpeechActiveRef.current) {
        resolveDelegationTranscriptWaiters(delegationId);
      }
    });
  }, [resolveDelegationTranscriptWaiters]);

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
    clearLiveOutputIdleTimer();
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
    liveOutputInterruptSourceRef.current = null;
    liveOutputInterruptAcknowledgedEventIdRef.current = null;
    pendingInputInterruptEventIdRef.current = null;
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
    for (const delegationId of delegationTranscriptWaitersRef.current.keys()) {
      resolveDelegationTranscriptWaiters(delegationId);
    }
    delegationTranscriptWaitersRef.current.clear();
    pendingStateResumeRef.current = null;
    pendingLiveContinuationRef.current = null;
    flushPendingLiveContinuationRef.current = null;
    pendingLiveResumeAfterInterruptRef.current = null;
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
  }, [
    clearActiveTurnTimers,
    clearLiveOutputIdleTimer,
    clearTranscriptIdleTimer,
    remoteAudioRef,
    resolveDelegationTranscriptWaiters,
  ]);

  const interruptActiveLiveResponse = useCallback((source: string, force = false) => {
    const turn = activeTurnRef.current;
    const peer = peerRef.current;
    const transcriptRevision = turn?.transcriptRevision ?? userTranscriptRevisionRef.current;
    const hasTurnOutput = Boolean(turn?.liveResponseActive);
    const hasUnassignedOutput = liveOutputActiveRevisionRef.current === transcriptRevision;
    if (!peer || (!force && !hasTurnOutput && !hasUnassignedOutput)) {
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
      content: "現在の応答を中断し、新しいユーザー発話を優先してください。発話内容の確定と必要な評価結果が戻るまで、新しい応答を開始しないでください。この指示自体には発話で応答しないでください。",
    });
    if (sent) {
      // The acknowledgement only confirms that the steering instruction was
      // accepted by the Live timeline. It is not a response-completion event,
      // so keep the response marked active until a real Live completion event
      // arrives. A delayed output delta can still arrive after this ACK.
      liveOutputInterruptRevisionRef.current = transcriptRevision;
      liveOutputInterruptEventIdRef.current = eventId;
      liveOutputInterruptSourceRef.current = source;
      liveOutputInterruptAcknowledgedEventIdRef.current = null;
      if (source === "input_transcript_delta" || source === "user_speech_started") {
        pendingInputInterruptEventIdRef.current = eventId;
      }
      if (turn) {
        // A delegation-created hold may be sent before Live has emitted its
        // first output delta. In that case the hold is a guard, not evidence
        // that a Live response is already active. A later output delta will
        // establish the active lifecycle in markResponseStarted.
        turn.liveResponseActive = force && !hasTurnOutput && !hasUnassignedOutput
          ? false
          : true;
        turn.responseInterruptRequested = true;
        turn.responseInterruptAcknowledged = false;
        turn.responseInterruptEventId = eventId;
      }
    }
    console.info("gpt_live_response_interrupt_requested", {
      session_id: sessionIdRef.current,
      turn_id: turn?.turnId,
      transcript_revision: transcriptRevision,
      event_id: eventId,
      sent: Boolean(sent),
      source,
    });
    return Boolean(sent);
  }, []);

  const markResponseStarted = useCallback((source: string) => {
    const userFacingOutputStarted = source !== "response.created";
    if (userFacingOutputStarted && userTranscriptRevisionRef.current > 0) {
      responseObservedRevisionRef.current = userTranscriptRevisionRef.current;
    }
    const turn = activeTurnRef.current;
    if (!turn) {
      if (userTranscriptRevisionRef.current > 0) {
        liveOutputActiveRevisionRef.current = userTranscriptRevisionRef.current;
      }
      const pendingDelegationForTranscript = [
        ...pendingDelegationIdsRef.current,
        ...delegatedResponsesInFlightRef.current,
      ].some((delegationId) => (
        delegationTranscriptRevisionsRef.current.get(delegationId)
          === userTranscriptRevisionRef.current
      ));
      if (pendingDelegationForTranscript) {
        interruptActiveLiveResponse("responses_delegation_pending_without_turn");
      } else {
        // The initial greeting can start before the first user turn exists.
        // Keep that unassigned output observable so the first transcript can
        // interrupt it instead of being spoken over by the greeting.
        liveOutputActiveRevisionRef.current = 0;
      }
      return;
    }
    if (!turn || (turn.responseStarted
      && !turn.delegationHandoffCompleted
      && turn.userFacingOutputObserved)) {
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
    if (userTranscriptRevisionRef.current > 0) {
      liveOutputActiveRevisionRef.current = userTranscriptRevisionRef.current;
    }
    const pendingDelegationForTurn = [
      ...pendingDelegationIdsRef.current,
      ...delegatedResponsesInFlightRef.current,
    ].some((delegationId) => (
      delegationTranscriptRevisionsRef.current.get(delegationId) === turn.transcriptRevision
    ));
    if (!turn.delegationHandoffCompleted && pendingDelegationForTurn) {
      // Live may emit a short acknowledgement while the semantic evaluator is
      // still running. Leave that response alone until the delegated result
      // is ready; the state-resume path installs exactly one boundary after
      // the Responses continuation so it cannot race two interruptions.
      turn.liveResponseActive = true;
      console.info("gpt_live_live_output_deferred", {
        session_id: sessionIdRef.current,
        turn_id: turn.turnId,
        source,
        reason: "responses_delegation_pending",
        interrupted: false,
      });
      return;
    }
    if (turn.delegationHandoffCompleted && !turn.resumeCommentaryAcknowledged) {
      // Output that arrives before the Live steering append is acknowledged
      // may still belong to the response that was already draining. Do not
      // count it as the post-delegation interview response or cancel the
      // bounded retry based on an ACK that only confirms context acceptance.
      turn.liveResponseActive = true;
      console.info("gpt_live_live_output_deferred", {
        session_id: sessionIdRef.current,
        turn_id: turn.turnId,
        source,
        reason: "await_state_resume_commentary_ack",
      });
      return;
    }
    if (userFacingOutputStarted) {
      turn.userFacingOutputObserved = true;
    }
    turn.responseStarted = true;
    turn.liveResponseActive = true;
    // The interruption marker belongs to the response that has just crossed
    // its boundary. Keep it while the pending delegation response is being
    // drained. The input-side marker is consumed explicitly when the next
    // user turn is created; it must not be inferred from transcript revision
    // arithmetic because one utterance can contain many deltas.
    if (liveOutputInterruptRevisionRef.current === turn.transcriptRevision) {
      liveOutputInterruptRevisionRef.current = null;
      liveOutputInterruptEventIdRef.current = null;
      liveOutputInterruptSourceRef.current = null;
      liveOutputInterruptAcknowledgedEventIdRef.current = null;
    }
    turn.responseInterruptRequested = false;
    turn.responseInterruptAcknowledged = false;
    turn.responseInterruptEventId = undefined;
    if (turn.delegationHandoffCompleted) {
      turn.postDelegationResponseStarted = true;
    }
    if (userFacingOutputStarted) {
      turn.responseObserved = true;
    }
    if (userFacingOutputStarted || turn.userFacingOutputObserved) {
      if (turn.responseStartTimeoutId !== undefined) {
        window.clearTimeout(turn.responseStartTimeoutId);
        turn.responseStartTimeoutId = undefined;
      }
      if (turn.waitNoticeTimeoutId !== undefined) {
        window.clearTimeout(turn.waitNoticeTimeoutId);
        turn.waitNoticeTimeoutId = undefined;
      }
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
    const pendingDelegationForTurn = [
      ...pendingDelegationIdsRef.current,
      ...delegatedResponsesInFlightRef.current,
    ].some((delegationId) => (
      delegationTranscriptRevisionsRef.current.get(delegationId) === turn.transcriptRevision
    ));
    if (!pendingDelegationForTurn || turn.delegationHandoffCompleted) {
      turn.userFacingOutputObserved = true;
    }
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
    for (const [delegationId, snapshot] of delegationTranscriptSnapshotsRef.current) {
      if (!snapshot.sealed) {
        snapshot.sealed = true;
        resolveDelegationTranscriptWaiters(delegationId);
      }
    }
    // GPT-Live supplies transcript deltas but does not guarantee a completed
    // turn event. This is the application's transport-level grouping marker,
    // not a punctuation or keyword based completeness guess.
    console.info("gpt_live_input_transcript_final", {
      session_id: sessionIdRef.current,
      source,
      transcript_revision: transcriptRevision,
      transcript_length: transcript.length,
    });
    flushPendingLiveContinuationRef.current?.();
  }, [resolveDelegationTranscriptWaiters]);

  const sendLiveConversationContinuation = useCallback((
    reason: string,
    relatedDelegationId?: string,
  ) => {
    const activeTurn = activeTurnRef.current;
    const peer = peerRef.current;
    if (!activeTurn || !peer || activeTurn.continuationSent
      || activeTurn.userFacingOutputObserved) {
      console.info("gpt_live_live_continuation_skipped", {
        session_id: sessionIdRef.current,
        turn_id: activeTurn?.turnId,
        related_delegation_id: relatedDelegationId,
        reason: activeTurn?.continuationSent
          ? "already_sent"
          : activeTurn?.userFacingOutputObserved
            ? "live_response_already_started"
            : !activeTurn
              ? "active_turn_missing"
              : "peer_missing",
      });
      return false;
    }
    if (userSpeechActiveRef.current) {
      pendingLiveContinuationRef.current = {
        reason,
        relatedDelegationId,
      };
      console.info("gpt_live_live_continuation_deferred", {
        session_id: sessionIdRef.current,
        turn_id: activeTurn.turnId,
        related_delegation_id: relatedDelegationId,
        reason: "await_user_transcript_final",
      });
      return false;
    }

    // This is a Live-only recovery prompt. It must not be sent through
    // response.create: that command starts/continues the Responses backend,
    // but does not itself request a spoken Live response. Commentary is used
    // here because this branch has no verified state result to inject; it is
    // only a user-facing continuation request. A null delegation ID is
    // intentional because there is no task result to associate with it.
    activeTurn.continuationSent = true;
    const eventId = `gpt_live_continuation_${activeTurn.turnId}`;
    const turnId = activeTurn.turnId;
    const transcriptRevision = activeTurn.transcriptRevision;
    const sendCommentary = () => {
      const currentTurn = activeTurnRef.current;
      if (!currentTurn || currentTurn.turnId !== turnId
        || currentTurn.transcriptRevision !== transcriptRevision
        || currentTurn.userFacingOutputObserved
        || userSpeechActiveRef.current) {
        console.info("gpt_live_live_continuation_skipped_stale_turn", {
          session_id: sessionIdRef.current,
          turn_id: turnId,
          related_delegation_id: relatedDelegationId,
          reason: !currentTurn
            ? "active_turn_missing"
            : currentTurn.turnId !== turnId
              ? "turn_id_changed"
              : currentTurn.userFacingOutputObserved
                ? "live_response_already_started"
                : "await_user_transcript_final",
        });
        return false;
      }
      console.info("gpt_live_live_continuation_prepared", {
        session_id: sessionIdRef.current,
        turn_id: currentTurn.turnId,
        related_delegation_id: relatedDelegationId,
        delegation_id: null,
        type: "session.commentary.append",
        event_id: eventId,
        content_length: GPT_LIVE_CONVERSATION_RESUME_COMMENTARY.length,
        reason,
      });
      const sent = peer.sendEvent({
        type: "session.commentary.append",
        event_id: eventId,
        delegation_id: null,
        content: GPT_LIVE_CONVERSATION_RESUME_COMMENTARY,
      });
      console.info("gpt_live_live_continuation_sent", {
        session_id: sessionIdRef.current,
        turn_id: currentTurn.turnId,
        related_delegation_id: relatedDelegationId,
        delegation_id: null,
        type: "session.commentary.append",
        event_id: eventId,
        sent: Boolean(sent),
        content_length: GPT_LIVE_CONVERSATION_RESUME_COMMENTARY.length,
        reason,
        speech_end_to_continuation_ms: Math.round(performance.now() - currentTurn.speechEndedAt),
      });
      return Boolean(sent);
    };

    const existingInterruptEventId = activeTurn.responseInterruptRequested
      ? activeTurn.responseInterruptEventId
      : undefined;
    if (existingInterruptEventId && !activeTurn.responseInterruptAcknowledged
      && liveOutputInterruptAcknowledgedEventIdRef.current !== existingInterruptEventId) {
      pendingLiveResumeAfterInterruptRef.current = {
        eventId: existingInterruptEventId,
        flush: sendCommentary,
      };
      scheduleLiveResumeAfterInterruptAck(
        existingInterruptEventId,
        sendCommentary,
        reason,
      );
      console.info("gpt_live_live_continuation_waiting_for_interrupt_ack", {
        session_id: sessionIdRef.current,
        turn_id: turnId,
        related_delegation_id: relatedDelegationId,
        event_id: existingInterruptEventId,
        reason,
      });
      return true;
    }
    if (existingInterruptEventId) {
      pendingLiveResumeAfterInterruptRef.current = null;
      return sendCommentary();
    }

    const liveResponseStillActive = Boolean(
      activeTurn.liveResponseActive
        || liveOutputActiveRevisionRef.current === transcriptRevision,
    );
    if (!liveResponseStillActive) {
      return sendCommentary();
    }

    // A prior Live response can still be draining when a delegation finishes
    // or when the watchdog notices that no delegation was created. Serialize
    // the one Live-only continuation behind one interruption boundary. The
    // boundary is a steering instruction; the actual user-facing request is
    // sent only after its ACK, and neither event is a Responses continuation.
    const interruptEventId = `${eventId}_interrupt`;
    pendingLiveResumeAfterInterruptRef.current = {
      eventId: interruptEventId,
      flush: sendCommentary,
    };
    const interruptSent = peer.sendEvent({
      type: "session.instructions.append",
      event_id: interruptEventId,
      delegation_id: null,
      content: "現在の保留中の応答を終了し、直前のユーザー発話を受け止めてインタビューを続けてください。この指示自体には発話で応答しないでください。",
    });
    if (interruptSent) {
      activeTurn.responseInterruptRequested = true;
      activeTurn.responseInterruptAcknowledged = false;
      activeTurn.responseInterruptEventId = interruptEventId;
      liveOutputInterruptRevisionRef.current = transcriptRevision;
      liveOutputInterruptEventIdRef.current = interruptEventId;
      liveOutputInterruptSourceRef.current = "live_continuation";
      liveOutputInterruptAcknowledgedEventIdRef.current = null;
      scheduleLiveResumeAfterInterruptAck(
        interruptEventId,
        sendCommentary,
        reason,
      );
    } else {
      pendingLiveResumeAfterInterruptRef.current = null;
      activeTurn.continuationSent = false;
      return sendCommentary();
    }
    console.info("gpt_live_live_continuation_boundary_sent", {
      session_id: sessionIdRef.current,
      turn_id: turnId,
      related_delegation_id: relatedDelegationId,
      type: "session.instructions.append",
      event_id: interruptEventId,
      sent: Boolean(interruptSent),
      reason,
    });
    return true;
  }, [scheduleLiveResumeAfterInterruptAck]);

  const flushPendingLiveContinuation = useCallback(() => {
    if (userSpeechActiveRef.current) {
      return;
    }
    const pending = pendingLiveContinuationRef.current;
    if (!pending) {
      return;
    }
    pendingLiveContinuationRef.current = null;
    sendLiveConversationContinuation(
      pending.reason,
      pending.relatedDelegationId,
    );
  }, [sendLiveConversationContinuation]);

  flushPendingLiveContinuationRef.current = flushPendingLiveContinuation;

  const scheduleResponseStartWatchdog = useCallback((turn: NonNullable<typeof activeTurnRef.current>) => {
    turn.responseStartTimeoutId = window.setTimeout(() => {
      const active = activeTurnRef.current;
      if (!active || active.turnId !== turn.turnId || active.userFacingOutputObserved
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
      const maxDelegationWaitAttempts = active.waitNoticeSent
        ? GPT_LIVE_DELEGATION_WAIT_ATTEMPTS + GPT_LIVE_DELEGATION_GRACE_AFTER_WAIT_NOTICE_ATTEMPTS
        : GPT_LIVE_DELEGATION_WAIT_ATTEMPTS;
      if (!active.delegationObserved
        && active.delegationWaitAttempts < maxDelegationWaitAttempts) {
        active.delegationWaitAttempts += 1;
        console.info("gpt_live_continuation_deferred", {
          session_id: sessionIdRef.current,
          turn_id: active.turnId,
          reason: active.waitNoticeSent
            ? "await_delegation_after_wait_notice"
            : "await_delegation_created",
          wait_attempt: active.delegationWaitAttempts,
          max_wait_attempts: maxDelegationWaitAttempts,
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
      sendLiveConversationContinuation("delegation_not_observed");
    }, GPT_LIVE_RESPONSE_START_TIMEOUT_MS);
  }, [sendLiveConversationContinuation]);

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
    // A user can start speaking while the previous Live output is still
    // draining. The provider/browser interruption is already the supported
    // boundary for that new utterance. Carry its ACK into the turn created at
    // speech-end/delegation so the Responses handoff does not send a second
    // instruction boundary for the same transition. Restrict this to an
    // input-side boundary immediately preceding the current transcript
    // revision; a state-resume instruction from an older turn must not be
    // mistaken for the new user's interruption.
    const inheritedInterruptEventId = pendingInputInterruptEventIdRef.current ?? undefined;
    pendingInputInterruptEventIdRef.current = null;
    handledSpeechEndRevisionRef.current = transcriptRevision;
    clearActiveTurnTimers();
    turnSequenceRef.current += 1;
    const turn: NonNullable<typeof activeTurnRef.current> = {
      turnId: `gpt-live-turn-${connectionGenerationRef.current}-${turnSequenceRef.current}`,
      transcriptRevision,
      speechEndedAt: performance.now(),
      responseStarted: false,
      liveResponseActive: liveOutputActiveRevisionRef.current === transcriptRevision,
      responseInterruptRequested: Boolean(inheritedInterruptEventId),
      responseInterruptAcknowledged: Boolean(
        inheritedInterruptEventId
          && liveOutputInterruptAcknowledgedEventIdRef.current === inheritedInterruptEventId,
      ),
      responseInterruptSource: inheritedInterruptEventId ? "input" : undefined,
      responseInterruptEventId: inheritedInterruptEventId,
      postDelegationResponseStarted: false,
      responseObserved: responseObservedRevisionRef.current === transcriptRevision,
      userFacingOutputObserved: false,
      firstAudio: false,
      waitNoticeSent: false,
      continuationSent: false,
      delegationResumeSent: false,
      resumedDelegationIds: new Set(),
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
      if (!active || active.turnId !== turn.turnId || active.userFacingOutputObserved
        || active.responseObserved || active.waitNoticeSent) {
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
      scheduleLiveOutputIdleBoundary();
      flushPendingStateResumeRef.current?.();
      console.info("gpt_live_transcript_idle_boundary", {
        session_id: sessionIdRef.current,
        transcript_revision: transcriptRevision,
        idle_timeout_ms: GPT_LIVE_TRANSCRIPT_IDLE_TIMEOUT_MS,
      });
      beginUserTurnWatchdog("transcript_idle");
    }, GPT_LIVE_TRANSCRIPT_IDLE_TIMEOUT_MS);
  }, [
    beginUserTurnWatchdog,
    clearTranscriptIdleTimer,
    markUserTranscriptFinal,
    scheduleLiveOutputIdleBoundary,
  ]);

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
    if (transcriptSnapshot && !transcriptSnapshot.sealed && userSpeechActiveRef.current) {
      console.info("gpt_live_state_update_waiting_for_transcript_final", {
        session_id: sessionIdRef.current,
        delegation_id: delegationId,
        call_id: callId,
        transcript_revision: userTranscriptRevisionRef.current,
      });
      await waitForDelegationTranscriptBoundary(delegationId);
      if (!isCurrentLiveCall()) {
        return;
      }
      console.info("gpt_live_state_update_resumed_after_transcript_final", {
        session_id: sessionIdRef.current,
        delegation_id: delegationId,
        call_id: callId,
        transcript_revision: userTranscriptRevisionRef.current,
      });
    }
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
  }, [waitForDelegationTranscriptBoundary]);

  const sendResumeCommentary = useCallback((
    delegationId: string,
    commentaryEventId: string,
    turnId: string,
    transcriptRevision: number,
    stateContextEventId?: string,
    stateContextContent?: string,
  ) => {
    const currentTurn = activeTurnRef.current;
    const peer = peerRef.current;
    const skipReason = !currentTurn
      ? "active_turn_missing"
      : !peer
        ? "peer_missing"
        : currentTurn.turnId !== turnId
          ? "turn_id_mismatch"
          : currentTurn.transcriptRevision !== transcriptRevision
            ? "transcript_revision_mismatch"
            : currentTurn.postDelegationResponseStarted
              && currentTurn.resumeCommentaryEventId !== commentaryEventId
              ? "post_delegation_output_already_started"
              : currentTurn.resumeCommentaryAcknowledged
                ? "commentary_already_acknowledged"
                : undefined;
    if (skipReason) {
      console.info("gpt_live_live_update_skipped_stale_turn", {
        session_id: sessionIdRef.current,
        related_delegation_id: delegationId,
        requested_turn_id: turnId,
        requested_transcript_revision: transcriptRevision,
        active_turn_id: currentTurn?.turnId,
        active_transcript_revision: currentTurn?.transcriptRevision,
        post_delegation_response_started: currentTurn?.postDelegationResponseStarted,
        resume_commentary_event_id: currentTurn?.resumeCommentaryEventId,
        requested_commentary_event_id: commentaryEventId,
        resume_commentary_acknowledged: currentTurn?.resumeCommentaryAcknowledged,
        skip_reason: skipReason,
      });
      return false;
    }
    // Keep the values narrowed after the diagnostic guard. TypeScript cannot
    // infer that the derived `skipReason` also proves these references are
    // present.
    if (!currentTurn || !peer) {
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
        if (stateContextEventId && stateContextContent) {
          const retryStateContextEventId = `${stateContextEventId}_retry`;
          console.info("gpt_live_live_update_retry_prepared", {
            session_id: sessionIdRef.current,
            related_delegation_id: delegationId,
            delegation_id: null,
            type: "session.thinking.append",
            event_id: retryStateContextEventId,
            original_event_id: stateContextEventId,
            content_length: stateContextContent.length,
          });
          const retryStateContextSent = peer.sendEvent({
            type: "session.thinking.append",
            event_id: retryStateContextEventId,
            delegation_id: null,
            content: stateContextContent,
          });
          console.info("gpt_live_live_update_retry_sent", {
            session_id: sessionIdRef.current,
            related_delegation_id: delegationId,
            delegation_id: null,
            type: "session.thinking.append",
            event_id: retryStateContextEventId,
            original_event_id: stateContextEventId,
            sent: Boolean(retryStateContextSent),
            content_length: stateContextContent.length,
          });
        }
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
    callIdOverride?: string,
  ) => {
    const activeTurn = activeTurnRef.current;
    const peer = peerRef.current;
    if (!activeTurn || !peer || activeTurn.postDelegationResponseStarted) {
      return false;
    }
    // A single user turn can legitimately produce more than one Responses
    // delegation while the Live transcript is still settling: for example,
    // the first semantic snapshot may be PARTIAL and a later snapshot may
    // contain the remaining answer. De-duplicate by delegation ID, not by
    // the whole turn. The previous turn-wide guard silently dropped the
    // second handoff and left Live without a user-facing response.
    if (activeTurn.resumedDelegationIds.has(delegationId)) {
      console.info("gpt_live_live_update_skipped_duplicate_delegation", {
        session_id: sessionIdRef.current,
        related_delegation_id: delegationId,
        turn_id: activeTurn.turnId,
        transcript_revision: activeTurn.transcriptRevision,
      });
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
      pendingStateResumeRef.current = {
        delegationId,
        stateOutput,
        callId: callIdOverride,
      };
      console.info("gpt_live_live_update_deferred", {
        session_id: sessionIdRef.current,
        related_delegation_id: delegationId,
        reason: "await_user_transcript_final",
      });
      return true;
    }
    const liveResponseStillActive = Boolean(
      activeTurn.liveResponseActive
        || liveOutputActiveRevisionRef.current === activeTurn.transcriptRevision,
    );
    if (liveResponseStillActive) {
      pendingStateResumeRef.current = {
        delegationId,
        stateOutput,
        callId: callIdOverride,
      };
      console.info("gpt_live_live_update_deferred", {
        session_id: sessionIdRef.current,
        related_delegation_id: delegationId,
        reason: "await_live_output_idle",
      });
      return true;
    }
    const callId = callIdOverride
      ?? delegationCallIdsRef.current.get(delegationId)
      ?? delegationId;
    const commentaryEventId = `gpt_live_state_resume_${callId}`;
    const turnId = activeTurn.turnId;
    const transcriptRevision = activeTurn.transcriptRevision;
    activeTurn.delegationResumeSent = true;
    activeTurn.resumedDelegationIds.add(delegationId);
    if (activeTurn.resumeCommentaryRetryTimeoutId !== undefined) {
      window.clearTimeout(activeTurn.resumeCommentaryRetryTimeoutId);
      activeTurn.resumeCommentaryRetryTimeoutId = undefined;
    }
    activeTurn.resumeCommentaryRetryEventId = undefined;
    activeTurn.resumeCommentaryAcknowledged = false;
    activeTurn.continuationSent = true;
    activeTurn.resumeCommentaryEventId = commentaryEventId;
    activeTurn.resumeCommentaryDelegationId = delegationId;

    const stateContextEventId = `${commentaryEventId}_state`;
    const stateContext = compactLiveStateUpdateOutput(stateOutput);
    // Keep this private context below the Live append limit. The complete
    // checklist was useful for debugging, but sending it here can exceed the
    // 500-token append limit and leave the following commentary append
    // unacknowledged. The Live prompt already contains the checklist; this
    // update only needs to identify the persisted revision and the delta for
    // the current question.
    const currentState = stateContext.current && typeof stateContext.current === "object"
      ? stateContext.current as Record<string, unknown>
      : {};
    const coveredAspectIds = Array.isArray(stateContext.coveredAspects)
      ? stateContext.coveredAspects.map((aspect) => (
        typeof aspect === "string"
          ? aspect
          : aspect && typeof aspect === "object" && typeof (aspect as Record<string, unknown>).aspect_id === "string"
            ? (aspect as Record<string, unknown>).aspect_id
            : null
      )).filter((aspectId): aspectId is string => Boolean(aspectId))
      : [];
    const missingAspectIds = Array.isArray(stateContext.missingAspects)
      ? stateContext.missingAspects.filter((aspectId): aspectId is string => typeof aspectId === "string")
      : [];
    const stateContextContent = [
      "Private application state update; do not read these labels aloud.",
      `status=${String(stateContext.status ?? "unknown")}`,
      `state_version=${String(stateContext.stateVersion ?? "unknown")}`,
      `current_question_id=${String(stateContext.currentQuestionId ?? "")}`,
      `covered_aspects=${JSON.stringify(coveredAspectIds)}`,
      `missing_aspects=${JSON.stringify(missingAspectIds)}`,
      `current=${JSON.stringify({
        field_id: typeof currentState.field_id === "string" ? currentState.field_id : null,
        question_id: typeof currentState.question_id === "string" ? currentState.question_id : null,
        answer_evaluation: typeof currentState.answer_evaluation === "string"
          ? currentState.answer_evaluation
          : null,
        clarification_kind: typeof currentState.clarification_kind === "string"
          ? currentState.clarification_kind
          : null,
        clarification_target: typeof currentState.clarification_target === "string"
          ? currentState.clarification_target.slice(0, 160)
          : null,
      })}`,
    ].join(" ");

    // The Responses continuation is not itself spoken by GPT-Live. Feed the
    // persisted semantic state back as private context. The normal path then
    // uses commentary to request the user-facing continuation.
    const sendStateContextAndCommentary = () => {
      const currentTurn = activeTurnRef.current;
      if (!currentTurn || currentTurn.turnId !== turnId
        || currentTurn.transcriptRevision !== transcriptRevision
        || (currentTurn.postDelegationResponseStarted
          && currentTurn.resumeCommentaryEventId !== commentaryEventId)
        || userSpeechActiveRef.current) {
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
      sendResumeCommentary(
        delegationId,
        commentaryEventId,
        turnId,
        transcriptRevision,
        stateContextEventId,
        stateContextContent,
      );
    };
    window.setTimeout(() => {
      const currentTurn = activeTurnRef.current;
      if (!currentTurn || currentTurn.turnId !== turnId
        || currentTurn.transcriptRevision !== transcriptRevision
        || currentTurn.postDelegationResponseStarted) {
        console.info("gpt_live_live_update_skipped_stale_turn", {
          session_id: sessionIdRef.current,
          related_delegation_id: delegationId,
          reason: "state_resume_context_after_turn_change",
        });
        return;
      }
      const liveResponseStillActive = Boolean(
        currentTurn.liveResponseActive
          || liveOutputActiveRevisionRef.current === transcriptRevision,
      );
      console.info("gpt_live_live_update_queueing_after_live_output", {
        session_id: sessionIdRef.current,
        related_delegation_id: delegationId,
        live_response_still_active: liveResponseStillActive,
      });
      const existingInterruptEventId = currentTurn.responseInterruptRequested
        ? currentTurn.responseInterruptEventId
        : undefined;
      const inputInterruptPending = currentTurn.responseInterruptSource === "input";
      if (existingInterruptEventId) {
        if (inputInterruptPending) {
          if (currentTurn.responseInterruptAcknowledged
            || liveOutputInterruptAcknowledgedEventIdRef.current === existingInterruptEventId) {
            pendingLiveResumeAfterInterruptRef.current = null;
            sendStateContextAndCommentary();
            return;
          }
          pendingLiveResumeAfterInterruptRef.current = {
            eventId: existingInterruptEventId,
            flush: sendStateContextAndCommentary,
          };
          console.info("gpt_live_state_resume_waiting_for_input_interrupt_ack", {
            session_id: sessionIdRef.current,
            related_delegation_id: delegationId,
            event_id: existingInterruptEventId,
          });
          scheduleLiveResumeAfterInterruptAck(
            existingInterruptEventId,
            sendStateContextAndCommentary,
            "state_resume_waiting_for_input_interrupt_ack",
          );
          return;
        }
        if (currentTurn.responseInterruptAcknowledged) {
          pendingLiveResumeAfterInterruptRef.current = null;
          console.info("gpt_live_live_update_interrupt_already_acknowledged", {
            session_id: sessionIdRef.current,
            related_delegation_id: delegationId,
            event_id: existingInterruptEventId,
          });
          sendStateContextAndCommentary();
          return;
        }
        pendingLiveResumeAfterInterruptRef.current = {
          eventId: existingInterruptEventId,
          flush: sendStateContextAndCommentary,
        };
        scheduleLiveResumeAfterInterruptAck(
          existingInterruptEventId,
          sendStateContextAndCommentary,
          "delegation_pending_interrupt_already_sent",
        );
        console.info("gpt_live_live_update_waiting_for_interrupt_ack", {
          session_id: sessionIdRef.current,
          related_delegation_id: delegationId,
          event_id: existingInterruptEventId,
          reason: "delegation_pending_interrupt_already_sent",
        });
        return;
      }
      // The Responses continuation has already returned its function result
      // to GPT-Live. Do not manufacture a second interruption boundary based
      // on a possibly incomplete Live output lifecycle; the provider may not
      // emit a speech-end event for that output. A single general context and
      // commentary append is the supported way to resume the conversation.
      console.info("gpt_live_state_resume_direct", {
        session_id: sessionIdRef.current,
        turn_id: turnId,
        related_delegation_id: delegationId,
        live_response_still_active: liveResponseStillActive,
        reason: "responses_continuation_already_returned",
      });
      sendStateContextAndCommentary();
    }, 0);
    return true;
  }, [scheduleLiveResumeAfterInterruptAck, sendResumeCommentary]);

  sendStateResumeCommentaryRef.current = sendStateResumeCommentary;

  const flushPendingStateResume = useCallback(() => {
    if (userSpeechActiveRef.current) {
      return;
    }
    const pending = pendingStateResumeRef.current;
    if (!pending) {
      return;
    }
    pendingStateResumeRef.current = null;
    sendStateResumeCommentary(pending.delegationId, pending.stateOutput, pending.callId);
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
        clearLiveOutputIdleTimer();
      }
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
      if (eventType !== "response.event") {
        flushPendingStateResumeRef.current?.();
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
          // The nested Responses continuation is only the semantic/state
          // half of this user turn. Reuse the existing state-resume path so
          // Live receives both the persisted state (`thinking.append`) and
          // the user-facing continuation (`commentary.append`). The direct
          // commentary-only path loses the state delta and can leave Live
          // with no actionable context after a PARTIAL result.
          sendStateResumeCommentary(
            delegationId,
            delegationStateOutputsRef.current.get(delegationId),
            delegationCallIdsRef.current.get(delegationId),
          );
        }
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
          const liveContinuationSent = sendLiveConversationContinuation(
            "delegation_completed_without_state_tool",
            delegationId,
          );
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
            live_continuation_requested: liveContinuationSent,
          });
        }
      } else if (delegationId && !delegationToolCallSeenRef.current.has(delegationId)) {
        const liveContinuationSent = sendLiveConversationContinuation(
          "delegation_completed_without_state_tool",
          delegationId,
        );
        pendingDelegationIdsRef.current.delete(delegationId);
        delegationTranscriptSnapshotsRef.current.delete(delegationId);
        console.warn("gpt_live_delegation_finished_without_state_tool", {
          session_id: eventSessionId,
          delegation_id: delegationId,
          response_event: nestedType,
          live_continuation_requested: liveContinuationSent,
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
      const pendingLiveResume = pendingLiveResumeAfterInterruptRef.current;
      if (activeTurn && activeTurn.responseInterruptEventId === acknowledgedEventId) {
        activeTurn.responseInterruptAcknowledged = true;
        if (activeTurn.resumeInterruptAckTimeoutId !== undefined) {
          window.clearTimeout(activeTurn.resumeInterruptAckTimeoutId);
          activeTurn.resumeInterruptAckTimeoutId = undefined;
        }
        console.info("gpt_live_response_interrupt_acknowledged", {
          session_id: eventSessionId,
          turn_id: activeTurn.turnId,
          type: eventType,
          client_event_id: acknowledgedEventId,
        });
      }
      if (liveOutputInterruptEventIdRef.current === acknowledgedEventId) {
        liveOutputInterruptAcknowledgedEventIdRef.current = acknowledgedEventId;
      }
      if (pendingLiveResume && pendingLiveResume.eventId === acknowledgedEventId) {
        pendingLiveResumeAfterInterruptRef.current = null;
        console.info("gpt_live_live_update_interrupt_acknowledged", {
          session_id: eventSessionId,
          event_id: acknowledgedEventId,
          action: "flush_state_resume",
        });
        pendingLiveResume.flush();
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
      const hasExistingInterviewTurn = Boolean(activeTurnRef.current)
        || (liveOutputActiveRevisionRef.current !== null
          && liveOutputActiveRevisionRef.current !== 0);
      if (hasExistingInterviewTurn) {
        interruptActiveLiveResponse("user_speech_started");
      }
      for (const [delegationId, snapshot] of delegationTranscriptSnapshotsRef.current) {
        snapshot.sealed = true;
        resolveDelegationTranscriptWaiters(delegationId);
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
      // The browser/WebRTC input stream is the Live barge-in boundary. A
      // transcript delta is normally only an observation of that boundary.
      // Some sessions omit input_audio_buffer.speech_started/stopped from the
      // DataChannel, however. If an existing interview response is still
      // active, use this first transcript delta as the one input-side
      // interruption boundary. The state-resume path carries that same event
      // into the persisted handoff; it must not send a second boundary.
      const wasSpeechActive = userSpeechActiveRef.current;
      console.info("gpt_live_input_transcript_delta", {
        session_id: eventSessionId,
        delta_length: delta.length,
      });
      if (!wasSpeechActive) {
        const hasExistingInterviewTurn = Boolean(activeTurnRef.current)
          || (liveOutputActiveRevisionRef.current !== null
            && liveOutputActiveRevisionRef.current !== 0);
        if (hasExistingInterviewTurn) {
          interruptActiveLiveResponse("input_transcript_delta");
        }
        clearTranscriptIdleTimer();
        for (const [delegationId, snapshot] of delegationTranscriptSnapshotsRef.current) {
          snapshot.sealed = true;
          resolveDelegationTranscriptWaiters(delegationId);
        }
        latestUserTranscriptRef.current = "";
        // A transcript delta after an idle boundary belongs to a new user
        // utterance. The previous turn may already have started its Live
        // response, so checking only responseStarted would keep that old turn
        // attached to the new Delegation. Detach it here; any still-active
        // response was already offered the interruption above, and output
        // that starts after this boundary must be correlated with the new
        // transcript revision.
        if (activeTurnRef.current) {
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
      scheduleLiveOutputIdleBoundary();
      return;
    }

    if (isUserSpeechEndEvent(eventType)) {
      clearTranscriptIdleTimer();
      userSpeechActiveRef.current = false;
      markUserTranscriptFinal("provider_event");
      scheduleLiveOutputIdleBoundary();
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
          // Responses delegation can be created just before Live starts a
          // short acknowledgement. Keep that output correlated to this turn;
          // do not interrupt it because an instructions append is not a
          // response-completion signal and can leave Live unable to resume
          // after the Responses function result returns.
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
    clearLiveOutputIdleTimer,
    clearTranscriptIdleTimer,
    cleanup,
    handleDelegatedFunctionCall,
    markFirstAudio,
    markUserTranscriptFinal,
    markResponseStarted,
    interruptActiveLiveResponse,
    remoteAudioRef,
    scheduleTranscriptIdleBoundary,
    scheduleLiveOutputIdleBoundary,
    sendLiveConversationContinuation,
    sendResumeCommentary,
    sendStateResumeCommentary,
    resolveDelegationTranscriptWaiters,
    waitForDelegationTranscriptBoundary,
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
    clearLiveOutputIdleTimer();
    responseObservedRevisionRef.current = -1;
    liveOutputActiveRevisionRef.current = null;
    liveOutputInterruptRevisionRef.current = null;
    liveOutputInterruptEventIdRef.current = null;
    liveOutputInterruptSourceRef.current = null;
    liveOutputInterruptAcknowledgedEventIdRef.current = null;
    pendingInputInterruptEventIdRef.current = null;
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
  }, [cleanup, clearLiveOutputIdleTimer, clearTranscriptIdleTimer, enabled, handleEvent, markFirstAudio, recordId, remoteAudioRef, t]);

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
