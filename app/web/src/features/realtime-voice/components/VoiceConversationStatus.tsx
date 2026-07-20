import type { VoiceConversationStatus } from "../types";

type VoiceConversationStatusProps = {
  status: VoiceConversationStatus;
  message?: string;
  partialTranscript?: string;
};

export function VoiceConversationStatus(props: VoiceConversationStatusProps) {
  if (props.status === "idle" && !props.message && !props.partialTranscript) {
    return null;
  }

  return (
    <div className={`voice-conversation-status ${props.status}`} role={props.status === "error" ? "alert" : "status"}>
      <span className="voice-status-icon">{statusIcon(props.status)}</span>
      <span>{props.message || statusText(props.status)}</span>
      {props.partialTranscript ? (
        <span className="voice-partial-transcript">{props.partialTranscript}</span>
      ) : null}
    </div>
  );
}

function statusText(status: VoiceConversationStatus): string {
  switch (status) {
    case "checking":
      return "準備を確認しています…";
    case "requesting_microphone":
      return "マイクを準備しています…";
    case "connecting":
      return "接続しています…";
    case "preparing_initial_reply":
      return "初回質問を準備しています…";
    case "listening":
      return "聞いています";
    case "finalizing_transcript":
      return "発話を確認しています…";
    case "processing_interview":
      return "回答を考えています…";
    case "preparing_audio":
      return "音声を準備しています…";
    case "processing":
      return "回答を考えています…";
    case "speaking":
      return "インタビュアーが話しています";
    case "stopping":
      return "終了しています…";
    case "completed":
      return "インタビューが完了しました";
    case "error":
      return "音声インタビューでエラーが発生しました。";
    case "idle":
    default:
      return "";
  }
}

function statusIcon(status: VoiceConversationStatus): string {
  switch (status) {
    case "listening":
      return "●";
    case "processing":
    case "finalizing_transcript":
    case "processing_interview":
    case "preparing_initial_reply":
    case "preparing_audio":
      return "◌";
    case "speaking":
      return "≋";
    case "completed":
      return "✓";
    case "error":
      return "!";
    default:
      return "●";
  }
}
