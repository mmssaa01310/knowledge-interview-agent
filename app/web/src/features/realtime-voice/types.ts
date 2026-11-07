/* Shared types for the GPT Live voice interface. */

export type VoiceConversationStatus =
  | "idle"
  | "connecting"
  | "listening"
  | "processing"
  | "stopping"
  | "disconnected"
  | "completed"
  | "error";

export type VoiceConnectionStats = {
  microphoneTrackLive: boolean;
  remoteAudioTrackReceived: boolean;
};
