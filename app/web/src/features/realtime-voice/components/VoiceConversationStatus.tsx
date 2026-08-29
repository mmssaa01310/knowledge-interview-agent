import type { VoiceConversationStatus } from "../types";
import { useI18n, type Translate } from "../../../i18n";

type VoiceConversationStatusProps = {
  status: VoiceConversationStatus;
  message?: string;
  partialTranscript?: string;
};

export function VoiceConversationStatus(props: VoiceConversationStatusProps) {
  const { t } = useI18n();
  if (props.status === "idle" && !props.message && !props.partialTranscript) {
    return null;
  }

  return (
    <div className={`voice-conversation-status ${props.status}`} role={props.status === "error" ? "alert" : "status"}>
      <span className="voice-status-icon">{statusIcon(props.status)}</span>
      <span>{props.message || statusText(props.status, t)}</span>
      {props.partialTranscript ? (
        <span className="voice-partial-transcript">{props.partialTranscript}</span>
      ) : null}
    </div>
  );
}

function statusText(status: VoiceConversationStatus, t: Translate): string {
  switch (status) {
    case "checking":
      return t("interview.voice.checking");
    case "requesting_microphone":
      return t("interview.voice.requestingMicrophone");
    case "connecting":
      return t("interview.voice.connecting");
    case "preparing_initial_reply":
      return t("interview.voice.initialQuestion");
    case "listening":
      return t("interview.voice.listening");
    case "finalizing_transcript":
      return t("interview.voice.finalizingTranscript");
    case "processing_interview":
      return t("interview.voice.processingAnswer");
    case "preparing_audio":
      return t("interview.voice.preparingAudio");
    case "processing":
      return t("interview.voice.processingAnswer");
    case "speaking":
      return t("interview.voice.speaking");
    case "stopping":
      return t("interview.voice.stopping");
    case "completed":
      return t("interview.voice.completed");
    case "error":
      return t("interview.voice.error");
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
