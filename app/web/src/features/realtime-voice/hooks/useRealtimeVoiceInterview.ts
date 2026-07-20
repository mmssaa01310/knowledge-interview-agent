import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { ApiError } from "../../../lib/api";
import type { ChatMessage } from "../../../types/app";
import {
  createVoiceSession,
  deleteVoicePeerConnection,
  getVoiceIceConfig,
  sendVoiceOffer,
} from "../api/realtimeVoiceClient";
import type {
  VoiceConnectionStats,
  VoiceConversationStatus,
  VoiceDataChannelEvent,
  VoiceSessionResponse,
} from "../types";
import { createVoicePeerConnection, type VoicePeerConnectionHandle } from "../webrtc/voicePeerConnection";

type UseRealtimeVoiceInterviewArgs = {
  recordId?: string;
  hasQuestions: boolean;
  remoteAudioRef: RefObject<HTMLAudioElement>;
  onMessage: (message: ChatMessage) => void;
  onInterviewStateChanged: () => void;
  onCompleted: () => void;
};

export function useRealtimeVoiceInterview(args: UseRealtimeVoiceInterviewArgs) {
  const {
    recordId,
    hasQuestions,
    remoteAudioRef,
    onMessage,
    onInterviewStateChanged,
    onCompleted,
  } = args;
  const [status, setStatus] = useState<VoiceConversationStatus>("idle");
  const [message, setMessage] = useState("");
  const [partialTranscript, setPartialTranscript] = useState("");
  const [connectionState, setConnectionState] = useState("");
  const [requiresManualPlayback, setRequiresManualPlayback] = useState(false);
  const [stats, setStats] = useState<VoiceConnectionStats>({
    microphoneTrackLive: false,
    remoteAudioTrackReceived: false,
  });
  const voiceSessionRef = useRef<VoiceSessionResponse | null>(null);
  const peerRef = useRef<VoicePeerConnectionHandle | null>(null);
  const microphoneStreamRef = useRef<MediaStream | null>(null);
  const onMessageRef = useRef(onMessage);
  const onInterviewStateChangedRef = useRef(onInterviewStateChanged);
  const onCompletedRef = useRef(onCompleted);
  const startingRef = useRef(false);
  const stoppingRef = useRef(false);
  const finalizedMessageKeysRef = useRef(new Set<string>());
  const frontendTraceRef = useRef<{
    userTranscriptFinalAt?: number;
    processingStartedAt?: number;
    assistantSpeechStartedAt?: number;
    remoteAudioTrackReceivedAt?: number;
    audioPlayEventAt?: number;
  }>({});

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
    const voiceSessionId = voiceSessionRef.current?.id;
    try {
      microphoneStreamRef.current?.getTracks().forEach((track) => track.stop());
      microphoneStreamRef.current = null;
      if (voiceSessionId) {
        await deleteVoicePeerConnection(voiceSessionId, reason).catch(() => undefined);
      }
      peerRef.current?.stop();
      peerRef.current = null;
      if (remoteAudioRef.current) {
        remoteAudioRef.current.srcObject = null;
      }
      setStats({ microphoneTrackLive: false, remoteAudioTrackReceived: false });
      setPartialTranscript("");
      setConnectionState("closed");
      setStatus((current) => current === "completed" ? "completed" : "idle");
      onInterviewStateChangedRef.current();
    } finally {
      stoppingRef.current = false;
      startingRef.current = false;
    }
  }, [remoteAudioRef]);

  const handleEvent = useCallback((event: VoiceDataChannelEvent) => {
    switch (event.type) {
      case "connection_state":
        setConnectionState(event.state);
        if (event.state === "connected") {
          setStatus((current) => current === "connecting" ? "listening" : current);
        }
        if (event.state === "failed" || event.state === "closed") {
          setMessage(event.state === "failed" ? "webrtc_connection_failed" : "webrtc_connection_closed");
          setStatus((current) => current === "completed" ? current : "error");
        }
        return;
      case "runtime_ready":
        setStatus(hasPendingInitialReply(voiceSessionRef.current) ? "preparing_initial_reply" : "listening");
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
          default:
            break;
        }
        return;
      case "user_speech_started":
        setPartialTranscript("");
        setStatus("listening");
        return;
      case "user_speech_ended":
        setStatus("finalizing_transcript");
        return;
      case "user_transcript_partial":
        setPartialTranscript(event.text);
        return;
      case "user_transcript_final": {
        setPartialTranscript("");
        const key = event.turnId ?? `voice-user-${event.questionId ?? "unknown"}-${event.stateVersion ?? "unknown"}-${event.text}`;
        if (!finalizedMessageKeysRef.current.has(key)) {
          finalizedMessageKeysRef.current.add(key);
          onMessageRef.current({
            id: key,
            role: "user",
            text: event.text,
            answerToQuestionId: event.questionId ?? undefined,
            voiceSessionId: event.voiceSessionId,
            voiceTurnId: event.turnId ?? undefined,
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
        setStatus("processing");
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
        if (event.message === "audio_playback_failed") {
          setRequiresManualPlayback(true);
          setMessage(toUserFacingError(event.message));
          return;
        }
        setStatus("error");
        setMessage(toUserFacingError(event.message));
        return;
      default:
        return;
    }
  }, []);

  const start = useCallback(async () => {
    if (startingRef.current || peerRef.current) {
      return;
    }
    if (!recordId) {
      setStatus("error");
      setMessage("記録が選択されていません。");
      return;
    }
    if (!hasQuestions) {
      setStatus("error");
      setMessage("音声インタビューを開始するには、先に質問項目を作成してください。");
      return;
    }

    startingRef.current = true;
    setRequiresManualPlayback(false);
    setMessage("");
    setStatus("checking");
    let failedStage = "voice_session";
    const startStartedAt = performance.now();
    const trace = {
      voice_session_ms: 0,
      microphone_ms: 0,
      ice_config_ms: 0,
      peer_connection_ms: 0,
      offer_ms: 0,
      answer_ms: 0,
      total_ms: 0,
    };
    try {
      let stageStartedAt = performance.now();
      const voiceSession = await createVoiceSession(recordId);
      trace.voice_session_ms = Math.round(performance.now() - stageStartedAt);
      voiceSessionRef.current = voiceSession;

      failedStage = "microphone";
      setStatus("requesting_microphone");
      stageStartedAt = performance.now();
      const microphoneStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      });
      trace.microphone_ms = Math.round(performance.now() - stageStartedAt);
      microphoneStreamRef.current = microphoneStream;
      setStats((current) => ({
        ...current,
        microphoneTrackLive: microphoneStream.getAudioTracks().some((track) => track.readyState === "live"),
      }));

      failedStage = "ice_config";
      setStatus("connecting");
      stageStartedAt = performance.now();
      const iceConfig = await getVoiceIceConfig(voiceSession.id);
      trace.ice_config_ms = Math.round(performance.now() - stageStartedAt);
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
            setStatus((current) => current === "connecting" ? "listening" : current);
          }
          if (state === "failed" || state === "closed") {
            setMessage(state === "failed" ? "webrtc_connection_failed" : "webrtc_connection_closed");
            setStatus((current) => current === "completed" ? current : "error");
          }
        },
        onStatsChange: setStats,
      });
      trace.peer_connection_ms = Math.round(performance.now() - stageStartedAt);
      peerRef.current = peerHandle;
      failedStage = "offer";
      stageStartedAt = performance.now();
      const answer = await sendVoiceOffer(voiceSession.id, peerHandle.offer);
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
        await deleteVoicePeerConnection(voiceSessionId, `start_failed_${failedStage}`).catch(() => undefined);
      }
      setStatus("error");
      setMessage(toStartErrorMessage(error, failedStage));
    } finally {
      startingRef.current = false;
    }
  }, [recordId, hasQuestions, handleEvent, remoteAudioRef]);

  useEffect(() => {
    const onBeforeUnload = () => {
      microphoneStreamRef.current?.getTracks().forEach((track) => track.stop());
      peerRef.current?.stop();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      void stop("component_unmounted");
    };
  }, [stop]);

  const isActive = !["idle", "completed", "error"].includes(status);

  return {
    status,
    message,
    partialTranscript,
    connectionState,
    stats,
    requiresManualPlayback,
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

function toStartErrorMessage(error: unknown, stage: string): string {
  if (error instanceof DOMException && error.name === "NotAllowedError") {
    return "マイクの使用が許可されていません。ブラウザの設定からマイクを許可してください。";
  }
  if (error instanceof DOMException && error.name === "NotFoundError") {
    return "利用できるマイクが見つかりません。マイクの接続とブラウザ設定を確認してください。";
  }
  if (error instanceof ApiError && error.status === 401) {
    return "音声インタビューの認証に失敗しました。";
  }
  if (error instanceof ApiError && error.status === 403) {
    return "この音声インタビューへ接続する権限がありません。";
  }
  if (error instanceof ApiError && error.status === 409 && error.detail === "voice_session_missing_questions") {
    return "音声インタビューを開始するには、先に質問項目を作成してください。";
  }
  if (
    error instanceof ApiError
    && error.status === 409
    && (error.detail === "voice_session_missing_current_question" || error.detail === "voice_session_completed")
  ) {
    return "音声インタビューを開始できる質問がありません。質問項目を確認するか、新しい記録で開始してください。";
  }
  if (error instanceof ApiError && error.status === 409 && error.detail === "voice_session_already_connected") {
    return "既存の音声接続が残っています。少し待ってから再度お試しください。";
  }
  if (stage === "voice_session") {
    return "Voice Sessionを作成できませんでした。Recordの権限とapp/apiの起動状態を確認してください。";
  }
  if (stage === "microphone") {
    return "マイクを準備できませんでした。ブラウザのマイク設定を確認してください。";
  }
  if (stage === "ice_config") {
    return "音声サーバーのICE設定を取得できませんでした。app/voiceの起動状態とWeb dev serverのproxy設定を確認してください。";
  }
  if (stage === "offer" || stage === "answer" || stage === "peer_connection") {
    return "WebRTC接続を開始できませんでした。app/voiceのログとブラウザの接続状態を確認してください。";
  }
  return "音声インタビューへ接続できませんでした。時間を置いて、もう一度お試しください。";
}

function toUserFacingError(message?: string): string {
  if (message === "audio_playback_failed") {
    return "音声を自動再生できませんでした。ブラウザの再生ボタンを押してください。";
  }
  if (message) {
    return `音声インタビューでエラーが発生しました: ${message}`;
  }
  return "音声インタビューでエラーが発生しました。";
}

function hasPendingInitialReply(session: VoiceSessionResponse | null) {
  if (!session?.initialReplyText?.trim()) {
    return false;
  }
  return session.initialReplyStatus === "pending" || session.initialReplyStatus === "sending";
}
