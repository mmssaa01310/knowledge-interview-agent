export type AppSection = "knowledge" | "settings";

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

export type InterviewQuestionType = "configured_field" | "follow_up" | "structured";

export type InterviewQuestion = {
  questionId: string;
  questionType: InterviewQuestionType;
  fieldId: string | null;
  text: string;
  retrievalPolicy?: "never" | "auto" | "required";
  targetType?: string | null;
  targetId?: string | null;
  candidateSource?: "user_statement" | "assistant_proposal" | null;
};

export type InterviewAnswerResolution = "AUTO_CONFIRM" | "TENTATIVE" | "RETRY" | "CONFIRM_REQUIRED";

export type InterviewFieldState = {
  fieldId: string;
  status: "pending" | "asking" | "completed";
  answerSummary: string | null;
  missingInformation: string[];
  answerState?: "UNANSWERED" | "CANDIDATE_PENDING" | "AWAITING_CONFIRMATION" | "CONFIRMED";
  answerResolution?: InterviewAnswerResolution | null;
  candidateAnswer?: string | null;
  candidateSource?: "user_statement" | "assistant_proposal" | null;
  candidateProposalMessageId?: string | null;
  confirmedSource?: "user_statement" | "assistant_proposal" | "management_edit" | null;
  confirmedProposalMessageId?: string | null;
  confirmationEvidenceTranscriptIds?: string[];
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
  interviewProfile?: "fixed_form" | "business_process" | "system_requirement";
  nextQuestionTarget?: {
    targetType: string;
    targetId: string;
    label: string;
    priority: number;
    candidateSource?: "user_statement" | "assistant_proposal" | null;
  } | null;
  deferredProposalTarget?: string | null;
  tentativeBridgeFieldId?: string | null;
  tentativeBridgeShown?: boolean;
  lastTentativeTarget?: { targetType: string; targetId: string } | null;
  requirementStates?: Record<string, {
    requirementId: string;
    label: string;
    kind: string;
    status: "UNANSWERED" | "CANDIDATE_PENDING" | "AWAITING_CONFIRMATION" | "CONFIRMED";
    answerResolution?: InterviewAnswerResolution | null;
    candidateValue?: string | null;
    candidateSource?: "user_statement" | "assistant_proposal" | null;
    candidateProposalMessageId?: string | null;
    confirmedSource?: "user_statement" | "assistant_proposal" | "management_edit" | null;
    confirmedProposalMessageId?: string | null;
    confirmationEvidenceTranscriptIds?: string[];
    value?: string | null;
    evidenceTranscriptIds?: string[];
  }>;
  processState?: {
    version?: number;
    sourceMessageIds?: string[];
    participants?: Array<Record<string, unknown>>;
    nodes?: Array<Record<string, unknown>>;
    edges?: Array<Record<string, unknown>>;
    interactions?: Array<Record<string, unknown>>;
  };
  applicabilityState?: Record<string, {
    topic: string;
    status: "unknown" | "present" | "not_applicable";
    evidenceTranscriptIds?: string[];
    reason?: string | null;
  }>;
  contradictions?: Array<Record<string, unknown>>;
  openIssues?: Array<Record<string, unknown>>;
  processVersion?: number;
  stateVersion?: number;
};

export type ProcessModelState = NonNullable<InterviewState["processState"]>;

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
  targetType?: string | null;
  targetId?: string | null;
  candidateSource?: "user_statement" | "assistant_proposal" | null;
};

export type InterviewAnswerTarget = {
  questionId: string;
  questionType: InterviewQuestionType;
  fieldId: string | null;
  targetType?: string | null;
  targetId?: string | null;
};

export type InterviewStreamMetadata = {
  status: "in_progress" | "completed";
  action: "ask_configured_field" | "ask_follow_up" | "ask_structured" | "finish";
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
  nextQuestionTarget?: InterviewState["nextQuestionTarget"];
  completionStatus?: "in_progress" | "completed";
  missingRequiredTargets?: Array<Record<string, unknown>>;
  error?: string;
};
