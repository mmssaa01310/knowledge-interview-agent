import { useCallback, useRef, useState } from "react";
import type {
  VoiceConnectionStats,
  VoiceConversationStatus,
  VoiceSessionResponse,
} from "../types";
import type { VoiceFrontendTrace } from "../utils/voiceTelemetry";
import type { VoicePeerConnectionHandle } from "../webrtc/voicePeerConnection";
import type { OpenAIRealtimePeerConnectionHandle } from "../webrtc/openaiRealtimePeerConnection";

export type VoicePeerHandle = VoicePeerConnectionHandle | OpenAIRealtimePeerConnectionHandle;

export function useVoiceConversationState() {
  const [status, setStatus] = useState<VoiceConversationStatus>("idle");
  const [message, setMessage] = useState("");
  const [partialTranscript, setPartialTranscript] = useState("");
  const [connectionState, setConnectionState] = useState("");
  const [requiresManualPlayback, setRequiresManualPlayback] = useState(false);
  const [initialReplyActive, setInitialReplyActive] = useState(false);
  const [stats, setStats] = useState<VoiceConnectionStats>({
    microphoneTrackLive: false,
    remoteAudioTrackReceived: false,
  });

  const voiceSessionRef = useRef<VoiceSessionResponse | null>(null);
  const peerRef = useRef<VoicePeerHandle | null>(null);
  const microphoneStreamRef = useRef<MediaStream | null>(null);
  const startingRef = useRef(false);
  const stoppingRef = useRef(false);
  const finalizedMessageKeysRef = useRef(new Set<string>());
  const openAIUserTranscriptRef = useRef(new Map<string, string>());
  const openAIAssistantTranscriptRef = useRef(new Map<string, string>());
  const openAIResponseMetadataRef = useRef(new Map<string, Record<string, string>>());
  const openAITranscriptFinalCountRef = useRef(0);
  const openAITranscriptItemIdsRef = useRef(new Set<string>());
  const openAIUserMessageInsertCountRef = useRef(0);
  const openAIAssistantResponseIdsRef = useRef(new Set<string>());
  const openAIAssistantFirstAudioResponseIdsRef = useRef(new Set<string>());
  const openAIActiveResponseRef = useRef<string | null>(null);
  const frontendTraceRef = useRef<VoiceFrontendTrace>({});

  const resetConversationTracking = useCallback(() => {
    finalizedMessageKeysRef.current.clear();
    openAIUserTranscriptRef.current.clear();
    openAIAssistantTranscriptRef.current.clear();
    openAIResponseMetadataRef.current.clear();
    openAITranscriptFinalCountRef.current = 0;
    openAITranscriptItemIdsRef.current.clear();
    openAIUserMessageInsertCountRef.current = 0;
    openAIAssistantResponseIdsRef.current.clear();
    openAIAssistantFirstAudioResponseIdsRef.current.clear();
    openAIActiveResponseRef.current = null;
  }, []);

  return {
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
  };
}
