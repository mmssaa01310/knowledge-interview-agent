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
  const isActive = !["idle", "completed", "error", "disconnected"].includes(props.status);
  const label = buttonLabel(props.status, t);

  return (
    <button
      className={`voice-conversation-button ${isActive ? "active" : ""}`}
      type="button"
      disabled={props.disabled || props.status === "checking" || props.status === "requesting_microphone" || props.status === "connecting" || props.status === "stopping"}
      onClick={isActive ? props.onStop : props.onStart}
    >
      <span className="voice-button-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <rect x="9" y="3" width="6" height="12" rx="3" />
          <path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6" />
        </svg>
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
    case "interrupted":
      return t("interview.voice.endConversation");
    case "disconnected":
      return t("interview.voice.reconnect");
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
