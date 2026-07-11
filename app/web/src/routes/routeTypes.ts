export type Route =
  | { name: "login" }
  | { name: "knowledge-dbs" }
  | { name: "knowledge-db"; knowledgeDbId: string }
  | { name: "knowledge-new"; knowledgeDbId: string }
  | { name: "knowledge"; knowledgeDbId: string; knowledgeId: string }
  | { name: "knowledge-settings"; knowledgeDbId: string; knowledgeId: string }
  | { name: "knowledge-documents"; knowledgeDbId: string; knowledgeId: string }
  | { name: "knowledge-records"; knowledgeDbId: string; knowledgeId: string }
  | { name: "record-detail"; knowledgeDbId: string; knowledgeId: string; recordId: string }
  | { name: "chatbots"; chatbotId?: string }
  | { name: "chatbot-overview"; chatbotId: string }
  | { name: "chatbot-chat"; chatbotId: string }
  | { name: "chatbot-references"; chatbotId: string }
  | { name: "settings" };
