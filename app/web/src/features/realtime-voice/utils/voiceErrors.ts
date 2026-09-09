import { ApiError } from "../../../lib/api";
import type { Translate } from "../../../i18n";

export function toStartErrorMessage(error: unknown, stage: string, t: Translate): string {
  if (error instanceof DOMException && error.name === "NotAllowedError") {
    return t("errors.microphoneDenied");
  }
  if (error instanceof DOMException && error.name === "NotFoundError") {
    return t("errors.microphoneNotFound");
  }
  if (error instanceof ApiError && error.status === 401) {
    return t("errors.voiceUnauthorized");
  }
  if (error instanceof ApiError && error.status === 403) {
    return t("errors.voiceForbidden");
  }
  if (error instanceof ApiError && error.status === 409 && error.detail === "voice_session_missing_questions") {
    return t("errors.voiceMissingQuestions");
  }
  if (
    error instanceof ApiError
    && error.status === 409
    && (error.detail === "voice_session_missing_current_question" || error.detail === "voice_session_completed")
  ) {
    return t("errors.voiceNoQuestion");
  }
  if (error instanceof ApiError && error.status === 409 && error.detail === "voice_session_already_connected") {
    return t("errors.voiceAlreadyConnected");
  }
  if (stage === "voice_session") {
    return t("errors.voiceSessionFailed");
  }
  if (stage === "microphone" || stage === "microphone_or_ice_config") {
    return t("errors.microphonePrepareFailed");
  }
  if (stage === "ice_config") {
    return t("errors.iceConfigFailed");
  }
  if (stage === "offer" || stage === "answer" || stage === "peer_connection") {
    return t("errors.webrtcFailed");
  }
  return t("errors.voiceConnectFailed");
}

export function toUserFacingError(
  message: string | undefined,
  t: Translate,
  code?: "PROCESS_TIMEOUT" | "API_ERROR" | "NETWORK_ERROR",
): string {
  if (code === "PROCESS_TIMEOUT") {
    return t("interview.voice.processingDelayed");
  }
  if (code === "NETWORK_ERROR") {
    return t("interview.voice.networkError");
  }
  if (code === "API_ERROR") {
    return t("interview.voice.apiError");
  }
  if (message === "audio_playback_failed") {
    return t("errors.audioPlaybackFailed");
  }
  if (message === "transcribe_stream_failed") {
    return t("errors.transcribeFailed");
  }
  if (message === "polly_synthesis_failed") {
    return t("errors.pollyFailed");
  }
  if (message) {
    return t("errors.voiceErrorWithMessage", { message });
  }
  return t("errors.voiceError");
}
