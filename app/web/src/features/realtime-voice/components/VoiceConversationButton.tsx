import type { VoiceConversationStatus } from "../types";
import { useI18n, type Translate } from "../../../i18n";

type VoiceConversationButtonProps = {
  status: VoiceConversationStatus;
  disabled?: boolean;
  onStart: () => void;
  onStop: () => void;
};

export function VoiceConversationButton(props: VoiceConversationButtonProps) {
  const { t } = useI18n();
  const isActive = !["idle", "completed", "error"].includes(props.status);
  const label = buttonLabel(props.status, t);
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

function buttonLabel(status: VoiceConversationStatus, t: Translate): string {
  switch (status) {
    case "checking":
      return t("interview.voice.checking");
    case "requesting_microphone":
      return t("interview.voice.requestingMicrophone");
    case "connecting":
      return t("interview.voice.connecting");
    case "listening":
    case "processing":
    case "finalizing_transcript":
    case "processing_interview":
    case "preparing_initial_reply":
    case "preparing_audio":
    case "speaking":
      return t("interview.voice.endConversation");
    case "stopping":
      return t("interview.voice.stopping");
    case "completed":
      return t("interview.voice.completed");
    case "error":
      return t("interview.voice.reconnect");
    case "idle":
    default:
      return t("interview.voice.startConversation");
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
