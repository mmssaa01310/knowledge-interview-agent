import type { Chatbot } from "../../../types/app";

const DEFAULT_CHAT_MODEL_ID = "global.amazon.nova-2-lite-v1:0";

export function createLocalChatbot(name: string): Chatbot {
  return {
    id: `chatbot-${Date.now()}`,
    name,
    referenceKnowledgeDbIds: [],
    referenceKnowledgeIds: [],
    referenceDocumentIds: [],
    excludedDocumentIds: [],
    modelId: DEFAULT_CHAT_MODEL_ID,
    searchLimit: 5,
    confidenceThreshold: 0.7
  };
}
