/*
Role:
  realtime voice frontendで共有する型定義。

Summary:
  接続状態、data channelイベント、voice session応答などの契約をまとめ、
  hook・WebRTC実装・画面の間で同じイベント境界を使う。

Relations:
  Used by realtime voice hooks, WebRTC helpers, and UI components.
*/

export type VoiceConversationStatus =
  | "idle"
  | "checking"
  | "requesting_microphone"
  | "connecting"
  | "preparing_initial_reply"
  | "listening"
  | "finalizing_transcript"
  | "processing_interview"
  | "preparing_audio"
  | "processing"
  | "speaking"
  | "stopping"
  | "completed"
  | "error";

export type VoiceSessionResponse = {
  id: string;
  recordId: string;
  provider: string;
  status: string;
  currentQuestionId?: string | null;
  stateVersion?: number;
  initialReplyText?: string | null;
  initialQuestionId?: string | null;
  initialReplyStatus?: "pending" | "sending" | "sent" | "failed_retryable" | "failed_terminal" | null;
};

export type IceServerConfig = {
  urls: string[];
  username?: string | null;
  credential?: string | null;
};

export type VoiceIceConfigResponse = {
  iceServers: IceServerConfig[];
  expiresAt: string;
};

export type VoiceDataChannelEvent =
  | {
      type: "connection_state";
      voiceSessionId: string;
      state: string;
    }
  | {
      type: "runtime_ready" | "user_speech_started" | "user_speech_ended";
      voiceSessionId: string;
    }
  | {
      type: "user_transcript_partial" | "user_transcript_final";
      voiceSessionId: string;
      text: string;
      turnId?: string;
      questionId?: string | null;
      stateVersion?: number | null;
    }
  | {
      type: "input_state_changed";
      voiceSessionId: string;
      inputState:
        | "ASSISTANT_SPEAKING"
        | "ANSWER_LISTENING"
        | "ANSWER_PROCESSING"
        | "CONFIRMATION_LISTENING";
      generation?: number | null;
    }
  | {
      type: "assistant_speech_started" | "assistant_interrupted";
      voiceSessionId: string;
      responseId?: string | null;
      generation?: number | null;
    }
  | {
      type: "assistant_speech_ended";
      voiceSessionId: string;
      responseId?: string | null;
      generation?: number | null;
      audioDurationMs: number;
    }
  | {
      type: "assistant_response_preparing";
      voiceSessionId: string;
      responseId?: string | null;
      generation?: number | null;
    }
  | {
      type: "assistant_transcript_final";
      voiceSessionId: string;
      responseId?: string | null;
      generation?: number | null;
      text: string;
      questionId?: string | null;
      stateVersion?: number | null;
    }
  | {
      type: "interview_state" | "interview_completed";
      voiceSessionId: string;
      status?: string | null;
      questionId?: string | null;
      stateVersion?: number | null;
    }
  | {
      type: "initial_reply_sent";
      voiceSessionId: string;
      responseId?: string | null;
      questionId?: string | null;
      stateVersion?: number | null;
    }
  | {
      type: "error";
      voiceSessionId?: string;
      message?: string;
    };

export type VoiceConnectionStats = {
  microphoneTrackLive: boolean;
  remoteAudioTrackReceived: boolean;
};
