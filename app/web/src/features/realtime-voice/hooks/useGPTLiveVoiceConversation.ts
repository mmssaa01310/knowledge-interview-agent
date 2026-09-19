import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { useI18n } from "../../../i18n";
import { submitGPTLiveCapture } from "../api/realtimeVoiceClient";
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
  const startupStartedAtRef = useRef<number | null>(null);
  const transcriptLineSequenceRef = useRef(0);
  const captureRef = useRef<ReturnType<typeof createLiveTranscriptCapture> | null>(null);
  const currentRecordRef = useRef(recordId);
  currentRecordRef.current = recordId;
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

  }, []);

  const cleanup = useCallback(() => {
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

    if (isInputTranscriptDelta(eventType)) {
      const delta = typeof event.delta === "string" ? event.delta : "";
      console.info("gpt_live_input_transcript_delta", {
        session_id: eventSessionId,
        delta_length: delta.length,
      });
      appendTranscriptDelta("user", delta);
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

    if (eventType === "session.delegation.created") {
      const delegationId = readDelegationId(event);
      console.info("gpt_live_delegation_created", {
        session_id: eventSessionId,
        delegation_id: readDelegationId(event),
      });
      void captureRef.current?.flush();
      if (delegationId && captureRef.current) {
        peerRef.current?.sendEvent({
          type: "session.thinking.append",
          delegation_id: delegationId,
          content: "Transcript capture runs independently in the background. Saving is not yet confirmed. Continue the ongoing conversation without waiting; checklist updates will arrive separately.",
        });
      }
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
  }, [appendTranscriptDelta, cleanup, remoteAudioRef, t]);

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
    setConnectionState("connecting");
    setStatus("connecting");
    const startAbortController = new AbortController();
    const connectionGeneration = connectionGenerationRef.current + 1;
    connectionGenerationRef.current = connectionGeneration;
    startAbortRef.current = startAbortController;
    if (recordId) {
      const captureId = crypto.randomUUID();
      const sentChecklist = new Map<string, string>();
      captureRef.current = createLiveTranscriptCapture({
        submit: (revision, fragments) => submitGPTLiveCapture(recordId, captureId, revision, fragments),
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
          if (connectionGenerationRef.current !== connectionGeneration) return;
          for (const field of result.checklist) {
            const signature = JSON.stringify(field);
            if (sentChecklist.get(field.id) === signature) continue;
            const sent = peerRef.current?.sendEvent({
              type: "session.thinking.append", delegation_id: null,
              content: JSON.stringify({
                note: "Saved checklist observation. May lag speech. Continue naturally; do not repeat answered questions. Follow up missing details when appropriate.",
                field: field.label.slice(0, 100), state: field.answer_state,
                missing: field.missing_required_items.join("、").slice(0, 220),
              }),
            });
            if (sent) sentChecklist.set(field.id, signature);
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

function transcriptTiming(event: GPTLiveEvent): { start_ms?: number; end_ms?: number } {
  return {
    ...(typeof event.start_ms === "number" ? { start_ms: event.start_ms } : {}),
    ...(typeof event.end_ms === "number" ? { end_ms: event.end_ms } : {}),
  };
}
