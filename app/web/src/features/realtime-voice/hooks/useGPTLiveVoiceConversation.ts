import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { useI18n } from "../../../i18n";
import { submitGPTLiveDelegation } from "../api/realtimeVoiceClient";
import type { VoiceConnectionStats, VoiceConversationStatus } from "../types";
import {
  createGPTLivePeerConnection,
  type GPTLiveEvent,
  type GPTLivePeerConnectionHandle,
} from "../webrtc/gptLivePeerConnection";
import { toStartErrorMessage } from "../utils/voiceErrors";

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

const EMPTY_STATS: VoiceConnectionStats = {
  microphoneTrackLive: false,
  remoteAudioTrackReceived: false,
};
const GPT_LIVE_START_TIMEOUT_MS = 30000;

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
  const transcriptLineSequenceRef = useRef(0);
  const pendingUserTranscriptRef = useRef("");
  const processedDelegationIdsRef = useRef(new Set<string>());
  const delegationQueueRef = useRef(Promise.resolve());
  const onInterviewStateChangedRef = useRef(onInterviewStateChanged);

  useEffect(() => {
    onInterviewStateChangedRef.current = onInterviewStateChanged;
  }, [onInterviewStateChanged]);

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
    if (role === "user") {
      pendingUserTranscriptRef.current += delta;
    }
  }, []);

  const processDelegation = useCallback(async (
    delegationId: string,
    transcript: string,
    generation: number,
  ) => {
    if (generation !== connectionGenerationRef.current) return;
    if (!recordId) {
      console.warn("gpt_live_delegation_without_record", { delegation_id: delegationId });
      return;
    }
    const startedAt = performance.now();
    try {
      const result = await submitGPTLiveDelegation(recordId, delegationId, transcript);
      if (generation !== connectionGenerationRef.current) return;
      const sent = peerRef.current?.sendEvent({
        type: "session.thinking.append",
        event_id: `gpt_live_state_${delegationId}`,
        delegation_id: delegationId,
        content: result.status === "completed"
          ? "Background validation confirmed the interview is complete. Close naturally when the user has finished speaking."
          : "Background saving and validation succeeded for the delegated answer. Continue the ongoing conversation; this update does not request a new spoken response or repetition of a question.",
      });
      if (!sent) {
        console.warn("gpt_live_delegation_result_not_sent", { delegation_id: delegationId });
      }
      // Refreshing the sidebar must neither delay Live context nor fail saving.
      void Promise.resolve().then(() => {
        if (generation === connectionGenerationRef.current) {
          return onInterviewStateChangedRef.current?.();
        }
      }).catch(() => console.warn("gpt_live_state_refresh_failed"));
      console.info("gpt_live_delegation_applied", {
        delegation_id: delegationId,
        status: result.status,
        state_version: result.stateVersion,
        elapsed_ms: Math.round(performance.now() - startedAt),
      });
    } catch (error) {
      if (generation !== connectionGenerationRef.current) return;
      processedDelegationIdsRef.current.delete(delegationId);
      pendingUserTranscriptRef.current = transcript + "\n" + pendingUserTranscriptRef.current;
      peerRef.current?.sendEvent({
        type: "session.thinking.append",
        event_id: `gpt_live_state_failed_${delegationId}`,
        delegation_id: delegationId,
        content: "Background saving failed. Do not claim this answer is saved. Continue listening naturally.",
      });
      console.warn("gpt_live_delegation_failed", {
        delegation_id: delegationId,
        error_name: error instanceof Error ? error.name : "unknown",
      });
    }
  }, [recordId]);

  const enqueueDelegation = useCallback((event: GPTLiveEvent) => {
    const delegationId = readDelegationId(event);
    const transcript = pendingUserTranscriptRef.current.trim();
    if (!delegationId || !transcript || processedDelegationIdsRef.current.has(delegationId)) {
      return;
    }
    processedDelegationIdsRef.current.add(delegationId);
    // Reserve this fragment now so a later delegation cannot save it twice.
    pendingUserTranscriptRef.current = "";
    const generation = connectionGenerationRef.current;
    const work = delegationQueueRef.current
      .catch(() => undefined)
      .then(() => processDelegation(delegationId, transcript, generation));
    delegationQueueRef.current = work.then(() => undefined, () => undefined);
  }, [processDelegation]);

  const cleanup = useCallback(() => {
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
  }, [remoteAudioRef]);

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
      cleanup();
      setConnectionState("closed");
      setStatus((current) => current === "stopping" ? current : "disconnected");
      return;
    }

    if (eventType === "session.instructions.appended") {
      const acknowledgedEventId = readClientEventId(event);
      const initialGreetingInstructionId = initialGreetingInstructionIdRef.current;
      if (initialGreetingInstructionId && acknowledgedEventId === initialGreetingInstructionId) {
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

    if (isInputTranscriptDelta(eventType)) {
      const delta = typeof event.delta === "string" ? event.delta : "";
      console.info("gpt_live_input_transcript_delta", {
        session_id: eventSessionId,
        delta_length: delta.length,
      });
      appendTranscriptDelta("user", delta);
      return;
    }

    if (isOutputTranscriptDelta(eventType)) {
      const delta = typeof event.delta === "string" ? event.delta : "";
      console.info("gpt_live_output_transcript_delta", {
        session_id: eventSessionId,
        delta_length: delta.length,
      });
      appendTranscriptDelta("assistant", delta);
      return;
    }

    if (eventType === "session.delegation.created") {
      console.info("gpt_live_delegation_created", {
        session_id: eventSessionId,
        delegation_id: readDelegationId(event),
      });
      enqueueDelegation(event);
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
    // Transcript deltas are observational/UI updates. Only the model's
    // delegation event can request canonical application-state processing.
  }, [appendTranscriptDelta, cleanup, enqueueDelegation, remoteAudioRef, t]);

  const start = useCallback(async () => {
    if (!enabled || startingRef.current || peerRef.current) {
      return;
    }
    startingRef.current = true;
    setMessage("");
    setRequiresManualPlayback(false);
    setStats(EMPTY_STATS);
    setTranscriptLines([]);
    pendingUserTranscriptRef.current = "";
    processedDelegationIdsRef.current.clear();
    delegationQueueRef.current = Promise.resolve();
    setConnectionState("connecting");
    setStatus("connecting");
    const startAbortController = new AbortController();
    const connectionGeneration = connectionGenerationRef.current + 1;
    connectionGenerationRef.current = connectionGeneration;
    startAbortRef.current = startAbortController;
    let startTimedOut = false;
    const startTimeoutId = window.setTimeout(() => {
      startTimedOut = true;
      startAbortController.abort();
    }, GPT_LIVE_START_TIMEOUT_MS);
    try {
      const peer = await createGPTLivePeerConnection({
        remoteAudioElement: remoteAudioRef.current,
        recordId,
        onEvent: (event) => {
          if (connectionGenerationRef.current === connectionGeneration) handleEvent(event);
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
      const initialGreetingSent = peer.sendEvent({
        type: "session.instructions.append",
        event_id: initialGreetingEventId,
        delegation_id: null,
        content: "会話を今すぐ開始してください。日本語で短く自然に挨拶し、これからインタビューを始めることを伝えてください。アプリケーションのチェックリストで最初に不足している項目について、一度に一つだけ具体的な質問をしてください。ユーザーが先に話し始めるのを待たず、その後は回答を遮らずに聞いてください。",
      });
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
  }, [cleanup, enabled, handleEvent, recordId, remoteAudioRef, t]);

  const stop = useCallback(() => {
    if (stoppingRef.current) {
      return;
    }
    stoppingRef.current = true;
    setStatus("stopping");
    cleanup();
    setConnectionState("closed");
    setStatus("idle");
    stoppingRef.current = false;
  }, [cleanup]);

  useEffect(() => {
    if (enabled || (!startingRef.current && !peerRef.current)) {
      return;
    }
    stop();
  }, [enabled, stop]);

  useEffect(() => () => stop(), [stop]);

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
  return type === "session.input_transcript.delta" || type === "input_transcript.delta";
}

function isOutputTranscriptDelta(type: string): boolean {
  return type === "session.output_transcript.delta" || type === "output_transcript.delta";
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
