export type Route =
  | { name: "login" }
  | { name: "help" }
  | { name: "knowledge-dbs" }
  | { name: "knowledge-db"; knowledgeDbId: string }
  | { name: "knowledge-new"; knowledgeDbId: string }
  | { name: "knowledge-interview"; knowledgeDbId: string; knowledgeId: string }
  | { name: "knowledge-records"; knowledgeDbId: string; knowledgeId: string }
  | { name: "knowledge-record-detail"; knowledgeDbId: string; knowledgeId: string; recordId: string }
  | { name: "knowledge-settings"; knowledgeDbId: string; knowledgeId: string }
  | { name: "knowledge-documents"; knowledgeDbId: string; knowledgeId: string }
  | { name: "dashboard" }
  | { name: "settings" };
