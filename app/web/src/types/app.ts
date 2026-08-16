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

export type InterviewQuestionType = "configured_field" | "follow_up";

export type InterviewQuestion = {
  questionId: string;
  questionType: InterviewQuestionType;
  fieldId: string | null;
  text: string;
  retrievalPolicy?: "never" | "auto" | "required";
};

export type InterviewFieldState = {
  fieldId: string;
  status: "pending" | "asking" | "completed";
  answerSummary: string | null;
  missingInformation: string[];
  answerState?: "UNANSWERED" | "CANDIDATE_PENDING" | "AWAITING_CONFIRMATION" | "CONFIRMED";
  candidateAnswer?: string | null;
  rawAnswer?: string | null;
  rawAnswerHistory?: string[];
  recordAnswer?: string | null;
  capturedItems?: Array<{ itemId: string; value: string; evidenceTranscriptIds?: string[] }>;
  candidateEvidenceTranscriptIds?: string[];
  needsClarification?: boolean;
  clarificationQuestion?: string | null;
};

export type InterviewState = {
  status: "in_progress" | "completed";
  currentFieldId: string | null;
  currentQuestionId: string | null;
  completedFieldIds: string[];
  pendingFieldIds: string[];
  askedQuestions: InterviewQuestion[];
  followUpCounts: Record<string, number>;
  fieldStates: Record<string, InterviewFieldState>;
  lastProcessedUserMessageId: string | null;
};

export type ChatMessage = {
  id?: string;
  recordId?: string;
  role: "user" | "assistant" | "ai";
  text: string;
  evidences?: ChatMessageEvidence[];
  questionId?: string;
  questionType?: InterviewQuestionType;
  fieldId?: string | null;
  answerToQuestionId?: string;
  answerToFieldId?: string | null;
  turnType?: "ANSWER" | "CONTROL";
  voiceSessionId?: string | null;
  voiceTurnId?: string | null;
  voiceResponseId?: string | null;
  isActualUtterance?: boolean;
  isLegacy?: boolean;
};

export type InterviewAnswerTarget = {
  questionId: string;
  questionType: InterviewQuestionType;
  fieldId: string | null;
};

export type InterviewStreamMetadata = {
  status: "in_progress" | "completed";
  action: "ask_configured_field" | "ask_follow_up" | "finish";
  reply: string;
  question: InterviewQuestion | null;
  completedFieldId: string | null;
  currentFieldId: string | null;
  answerSummary: string | null;
  recordAnswer?: string | null;
  missingInformation: string[];
  used_tools: string[];
  assistantMessage?: ChatMessage | null;
  interviewState?: InterviewState | null;
  structuredDraft?: Record<string, string>;
};
