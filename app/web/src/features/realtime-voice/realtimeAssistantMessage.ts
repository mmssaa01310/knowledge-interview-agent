import type { ChatMessage } from "../../types/app";
import type { VoiceSessionResponse } from "./types";

// Response metadata, not arrival order or text equality, owns message identity.
// If response.created was missed, retain deltas until response.done supplies it.
export function realtimeAssistantMessage(
  session: VoiceSessionResponse | null,
  metadata: Record<string, string> | undefined,
  text: string,
): ChatMessage | undefined {
  if (!session || !metadata?.kikiori_response_id) return undefined;
  const initial = metadata.kikiori_kind === "initial";
  const responseId = initial ? `initial-response-${session.id}` : metadata.kikiori_response_id;
  return {
    id: responseId,
    role: "assistant",
    // The initial canonical output is already visible. Audio deltas must not
    // replace it with a fragment, or with a model's paraphrase.
    text: initial ? session.initialReplyText || text : text,
    questionId: metadata.kikiori_question_id || undefined,
    voiceSessionId: session.id,
    voiceTurnId: initial ? `initial-${session.id}` : metadata.kikiori_turn_id || undefined,
    voiceResponseId: responseId,
  };
}
