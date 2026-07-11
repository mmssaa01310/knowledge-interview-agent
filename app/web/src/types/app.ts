export type AppSection = "knowledge" | "chatbots" | "settings";

export type Chatbot = {
  id: string;
  name: string;
  referenceKnowledgeDbIds: string[];
  referenceKnowledgeIds: string[];
  referenceDocumentIds: string[];
  excludedDocumentIds: string[];
  modelId: string;
  searchLimit: number;
  confidenceThreshold: number;
};

export type ChatMessageEvidence = {
  type: "knowledge" | "document";
  title: string;
  detail: string;
  status?: string;
};

export type DocumentReadState = {
  readStatus: "unread" | "opened" | "reading" | "read" | "acknowledged";
  readProgress: number;
  acknowledged: boolean;
  lastOpenedAt?: string;
  readAt?: string;
  acknowledgedAt?: string;
};

export type ChatMessage = {
  role: "user" | "ai";
  text: string;
  evidences?: ChatMessageEvidence[];
};

export type InterviewAnswerTarget = {
  scope: "configured" | "extra";
  answerKey: string;
};

export type InterviewStreamMetadata = {
  answer_status?: "answered" | "not_answered";
  reask_question?: string | null;
  next_questions: string[];
  draft_updates: Record<string, unknown>;
  used_tools: string[];
};
