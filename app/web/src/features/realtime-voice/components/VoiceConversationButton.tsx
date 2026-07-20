import type { VoiceConversationStatus } from "../types";

type VoiceConversationButtonProps = {
  status: VoiceConversationStatus;
  disabled?: boolean;
  onStart: () => void;
  onStop: () => void;
};

export function VoiceConversationButton(props: VoiceConversationButtonProps) {
  const isActive = !["idle", "completed", "error"].includes(props.status);
  const label = buttonLabel(props.status);
  const icon = buttonIcon(props.status);

  return (
    <button
      className={`voice-conversation-button ${isActive ? "active" : ""}`}
      type="button"
      disabled={props.disabled || props.status === "checking" || props.status === "requesting_microphone" || props.status === "connecting" || props.status === "stopping"}
      onClick={isActive ? props.onStop : props.onStart}
    >
      <span className={`voice-button-icon ${props.status === "connecting" || props.status === "checking" || props.status === "requesting_microphone" ? "spin" : ""}`}>
        {icon}
      </span>
      {label}
    </button>
  );
}

function buttonLabel(status: VoiceConversationStatus): string {
  switch (status) {
    case "checking":
      return "準備を確認しています…";
    case "requesting_microphone":
      return "マイクを準備しています…";
    case "connecting":
      return "接続しています…";
    case "listening":
    case "processing":
    case "finalizing_transcript":
    case "processing_interview":
    case "preparing_initial_reply":
    case "preparing_audio":
    case "speaking":
      return "会話を終了";
    case "stopping":
      return "終了しています…";
    case "completed":
      return "インタビューが完了しました";
    case "error":
      return "再接続する";
    case "idle":
    default:
      return "インタビュアーと会話する";
  }
}

function buttonIcon(status: VoiceConversationStatus): string {
  switch (status) {
    case "checking":
    case "requesting_microphone":
    case "connecting":
    case "preparing_initial_reply":
      return "◌";
    case "listening":
    case "processing":
    case "finalizing_transcript":
    case "processing_interview":
    case "preparing_audio":
    case "speaking":
    case "stopping":
      return "■";
    case "error":
      return "↻";
    case "completed":
      return "✓";
    case "idle":
    default:
      return "≋";
  }
}
