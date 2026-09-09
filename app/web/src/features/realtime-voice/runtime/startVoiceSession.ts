import type { Dispatch, MutableRefObject, RefObject, SetStateAction } from "react";
import { ApiError } from "../../../lib/api";
import type { Translate } from "../../../i18n";
import type { ChatMessage } from "../../../types/app";
import {
  createVoiceSession,
  deleteVoicePeerConnection,
  getVoiceIceConfig,
  sendOpenAIRealtimeOffer,
  sendVoiceOffer,
} from "../api/realtimeVoiceClient";
import type {
  OpenAIRealtimeEvent,
  VoiceConnectionStats,
  VoiceConversationStatus,
  VoiceDataChannelEvent,
  VoiceProvider,
  VoiceSessionResponse,
} from "../types";
import { toStartErrorMessage } from "../utils/voiceErrors";
import {
  createVoiceStartupTrace,
  logVoiceStartupEvent,
  VOICE_SIGNALING_TIMEOUT_MS,
} from "../utils/voiceTelemetry";
import {
  createOpenAIRealtimePeerConnection,
  type OpenAIRealtimePeerConnectionHandle,
} from "../webrtc/openaiRealtimePeerConnection";
import { createVoicePeerConnection, type VoicePeerConnectionHandle } from "../webrtc/voicePeerConnection";

type VoicePeerHandle = VoicePeerConnectionHandle | OpenAIRealtimePeerConnectionHandle;

export type StartVoiceSessionOptions = {
  recordId: string;
  provider: VoiceProvider;
  hasQuestions: boolean;
  remoteAudioRef: RefObject<HTMLAudioElement>;
  voiceSessionRef: MutableRefObject<VoiceSessionResponse | null>;
  peerRef: MutableRefObject<VoicePeerHandle | null>;
  microphoneStreamRef: MutableRefObject<MediaStream | null>;
  startingRef: MutableRefObject<boolean>;
  resetConversationTracking: () => void;
  setStatus: Dispatch<SetStateAction<VoiceConversationStatus>>;
  setMessage: Dispatch<SetStateAction<string>>;
  setInitialReplyActive: Dispatch<SetStateAction<boolean>>;
  setRequiresManualPlayback: Dispatch<SetStateAction<boolean>>;
  setConnectionState: Dispatch<SetStateAction<string>>;
  setStats: Dispatch<SetStateAction<VoiceConnectionStats>>;
  onMessageRef: MutableRefObject<(message: ChatMessage) => void>;
  handleEvent: (event: VoiceDataChannelEvent) => void;
  handleOpenAIEvent: (event: OpenAIRealtimeEvent) => void;
  hasPendingInitialReply: (session: VoiceSessionResponse | null) => boolean;
  t: Translate;
};

export async function startVoiceSession(options: StartVoiceSessionOptions): Promise<void> {
  const {
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
  } = options;

  if (startingRef.current || peerRef.current) {
    return;
  }
  if (!hasQuestions) {
    setStatus("error");
    setMessage(t("errors.voiceMissingQuestions"));
    return;
  }

  startingRef.current = true;
  resetConversationTracking();
  setInitialReplyActive(false);
  setRequiresManualPlayback(false);
  setMessage("");
  setStatus("checking");
  let failedStage = "voice_session";
  const startStartedAt = performance.now();
  const markStartup = (event: string) => logVoiceStartupEvent({
    event,
    provider,
    voiceSessionId: voiceSessionRef.current?.id,
    startStartedAt,
  });
  markStartup("start_clicked");
  const trace = createVoiceStartupTrace();
  let microphonePromise: Promise<MediaStream> | null = null;
  let microphoneStreamForStart: MediaStream | null = null;
  let stopMicrophoneWhenReady = false;
  try {
    // The API may need to generate the first structured-interview question.
    // Start the browser microphone handshake at the same time so this latency
    // is not added to the server-side question-generation latency.
    const microphoneStartedAt = performance.now();
    markStartup("get_user_media_started");
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
      markStartup("get_user_media_ready");
      trace.microphone_ms = Math.round(performance.now() - microphoneStartedAt);
      if (stopMicrophoneWhenReady) {
        stream.getTracks().forEach((track) => track.stop());
      }
      return stream;
    });

    let stageStartedAt = performance.now();
    // Attach a rejection handler immediately while the API is pending.
    void microphonePromise.catch(() => undefined);
    markStartup("voice_session_request_started");
    const voiceSession = await withTimeout(
      (signal) => createVoiceSession(recordId, provider, signal),
      VOICE_SIGNALING_TIMEOUT_MS,
    );
    trace.voice_session_ms = Math.round(performance.now() - stageStartedAt);
    voiceSessionRef.current = voiceSession;
    markStartup("voice_session_ready");
    console.info("openai_realtime_latency", {
      event: "voice_session_created",
      voice_session_id: voiceSession.id,
      timestamp_ms: Math.round(performance.now()),
    });

    if (voiceSession.provider === "openai_realtime" && voiceSession.initialReplyText?.trim()) {
      const initialResponseId = `initial-response-${voiceSession.id}`;
      const initialTurnId = `initial-${voiceSession.id}`;
      onMessageRef.current({
        id: initialResponseId,
        role: "assistant",
        text: voiceSession.initialReplyText,
        questionId: voiceSession.initialQuestionId || voiceSession.currentQuestionId || undefined,
        voiceSessionId: voiceSession.id,
        voiceTurnId: initialTurnId,
        voiceResponseId: initialResponseId,
      });
      setInitialReplyActive(true);
      markStartup("frontend_initial_question_visible");
      console.info("openai_realtime_latency", {
        event: "initial_question_ready",
        voice_session_id: voiceSession.id,
        timestamp_ms: Math.round(performance.now()),
      });
      console.info("openai_realtime_latency", {
        event: "frontend_initial_question_visible",
        voice_session_id: voiceSession.id,
        timestamp_ms: Math.round(performance.now()),
      });
    }

    if (voiceSession.provider === "openai_realtime") {
      failedStage = "microphone";
      setStatus(hasPendingInitialReply(voiceSession) ? "preparing_initial_reply" : "connecting");
      const microphoneStream = await microphonePromise;
      microphoneStreamForStart = microphoneStream;
      microphoneStreamRef.current = microphoneStream;
      setStats((current) => ({
        ...current,
        microphoneTrackLive: microphoneStream.getAudioTracks().some((track) => track.readyState === "live"),
      }));

      failedStage = "peer_connection";
      stageStartedAt = performance.now();
      const peerHandle = await createOpenAIRealtimePeerConnection({
        voiceSessionId: voiceSession.id,
        microphoneStream,
        remoteAudioElement: remoteAudioRef.current,
        onEvent: handleOpenAIEvent,
        onConnectionStateChange: (state) => {
          setConnectionState(state);
          if (state === "connected" || state === "completed") {
            markStartup("provider_connected");
            if (state === "connected") {
              console.info("openai_realtime_latency", {
                event: "webrtc_connected",
                voice_session_id: voiceSession.id,
                timestamp_ms: Math.round(performance.now()),
              });
            }
            setStatus((current) => current === "connecting" ? "listening" : current);
          }
          if (state === "failed" || state === "closed") {
            setMessage(state === "failed" ? t("errors.webrtcFailed") : t("errors.connectionFailed"));
            setStatus((current) => current === "completed" ? current : state === "closed" ? "disconnected" : "error");
          }
        },
        onStatsChange: setStats,
      });
      trace.peer_connection_ms = Math.round(performance.now() - stageStartedAt);
      peerRef.current = peerHandle;

      failedStage = "offer";
      markStartup("provider_connect_started");
      stageStartedAt = performance.now();
      const answer = await withTimeout(
        (signal) => sendOpenAIRealtimeOffer(voiceSession.id, peerHandle.offer, signal),
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
      console.info("openai_realtime_connection_latency", trace);
      return;
    }

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
          markStartup("provider_connected");
          setStatus((current) => current === "connecting" ? "listening" : current);
        }
        if (state === "failed" || state === "closed") {
          setMessage(state === "failed" ? t("errors.webrtcFailed") : t("errors.connectionFailed"));
          setStatus((current) => current === "completed" ? current : state === "closed" ? "disconnected" : "error");
        }
      },
      onStatsChange: setStats,
    });
    trace.peer_connection_ms = Math.round(performance.now() - stageStartedAt);
    peerRef.current = peerHandle;
    failedStage = "offer";
    markStartup("provider_connect_started");
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
}

export async function withTimeout<T>(
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
