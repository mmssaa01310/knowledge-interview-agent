export type AppSection = "knowledge" | "settings";

export type ChatMessageEvidence = {
  type: "knowledge" | "document";
  title: string;
  detail: string;
  status?: string;
};

export type InterviewQuestionType = "configured_field" | "follow_up" | "structured";
export type InterviewCandidateSource = "user_statement" | "assistant_proposal" | "document_reference";

export type RetrievedSourceReference = {
  sourceType: "document" | "document_chunk";
  sourceId: string;
  title: string;
  score: number;
};

export type InterviewQuestion = {
  questionId: string;
  questionType: InterviewQuestionType;
  fieldId: string | null;
  text: string;
  retrievalPolicy?: "never" | "auto" | "required";
  targetType?: string | null;
  targetId?: string | null;
  targetLabel?: string | null;
  candidateSource?: InterviewCandidateSource | null;
  candidateValue?: string | null;
  candidateSourceIds?: string[];
  retrievedSources?: RetrievedSourceReference[];
};

export type InterviewAnswerResolution = "AUTO_CONFIRM" | "TENTATIVE" | "RETRY" | "CONFIRM_REQUIRED";
export type UtteranceCompleteness = "COMPLETE" | "INCOMPLETE" | "UNCERTAIN";
export type TranscriptCorrectionStatus = "NONE" | "CORRECTED" | "UNCERTAIN";
export type AnswerSufficiency =
  | "SUFFICIENT"
  | "PARTIAL"
  | "AMBIGUOUS"
  | "EXAMPLE_MISSING"
  | "REASON_MISSING"
  | "CRITERIA_MISSING"
  | "UNANSWERABLE"
  | "REFUSAL"
  | "INCOMPLETE";
export type ProbeType = "NONE" | "REFRAME" | "EXAMPLE" | "REASON" | "CRITERIA" | "CLARIFY" | "RETRY";

export type TranscriptAssessment = {
  rawTranscript: string;
  normalizedTranscript: string;
  correctionStatus: TranscriptCorrectionStatus;
  correctionCandidates: string[];
  correctionReason?: string | null;
  confirmed?: boolean;
};

export type AnswerAssessment = {
  sufficiency: AnswerSufficiency;
  probeType: ProbeType;
};

export type InterviewFieldState = {
  fieldId: string;
  status: "pending" | "asking" | "completed";
  answerSummary: string | null;
  missingInformation: string[];
  answerState?: "UNANSWERED" | "CANDIDATE_PENDING" | "AWAITING_CONFIRMATION" | "CONFIRMED";
  answerResolution?: InterviewAnswerResolution | null;
  candidateAnswer?: string | null;
  candidateSource?: InterviewCandidateSource | null;
  candidateSourceIds?: string[];
  candidateProposalMessageId?: string | null;
  confirmedSource?: "user_statement" | "assistant_proposal" | "document_reference" | "management_edit" | null;
  confirmedSourceIds?: string[];
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
  lastUtteranceCompleteness?: UtteranceCompleteness | null;
  lastTranscriptAssessment?: TranscriptAssessment | null;
  lastAnswerAssessment?: AnswerAssessment | null;
  activeProbeTarget?: {
    targetType: string;
    targetId: string;
    label: string;
    probeType: Exclude<ProbeType, "NONE">;
    probeCount: number;
  } | null;
  pendingTranscriptConfirmation?: {
    messageId: string;
    rawTranscript: string;
    normalizedTranscript: string;
    correctionCandidates: string[];
    targetRefs: Array<{ targetType: string; targetId: string }>;
    sourceQuestion?: InterviewQuestion | null;
  } | null;
  interviewProfile?: "fixed_form" | "business_process" | "system_requirement";
  nextQuestionTarget?: {
    targetType: string;
    targetId: string;
    label: string;
    priority: number;
    candidateSource?: InterviewCandidateSource | null;
    candidateValue?: string | null;
    candidateSourceIds?: string[];
    probeType?: ProbeType;
    probeCount?: number;
  } | null;
  deferredProposalTarget?: string | null;
  lastTentativeTarget?: { targetType: string; targetId: string } | null;
  closingState?: "UNANSWERED" | "ASKING" | "CONFIRMED";
  closingAnswer?: {
    rawTranscript: string;
    normalizedTranscript: string;
    evidenceTranscriptIds: string[];
  } | null;
  requirementStates?: Record<string, {
    requirementId: string;
    label: string;
    kind: string;
    status: "UNANSWERED" | "CANDIDATE_PENDING" | "AWAITING_CONFIRMATION" | "CONFIRMED";
    answerResolution?: InterviewAnswerResolution | null;
    candidateValue?: string | null;
    candidateSource?: InterviewCandidateSource | null;
    candidateSourceIds?: string[];
    candidateProposalMessageId?: string | null;
    confirmedSource?: "user_statement" | "assistant_proposal" | "document_reference" | "management_edit" | null;
    confirmedSourceIds?: string[];
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
  rawTranscript?: string;
  normalizedTranscript?: string | null;
  correctionStatus?: TranscriptCorrectionStatus;
  correctionCandidates?: string[];
  correctionReason?: string | null;
  targetType?: string | null;
  targetId?: string | null;
  candidateSource?: InterviewCandidateSource | null;
  retrievedSources?: RetrievedSourceReference[];
  messageType?: "process_model_edit_command" | "process_model_edit_reply" | string;
  processCommandId?: string | null;
  instructionSummary?: string | null;
  updatedTargets?: Array<"requirements" | "flowchart" | "sequence" | string>;
  processChangeSummary?: string | null;
  processUpdatedPoints?: string[];
  processVersion?: number | null;
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
  assistantMessage?: ChatMessage | null;
  interviewState?: InterviewState | null;
  structuredDraft?: Record<string, string>;
  nextQuestionTarget?: InterviewState["nextQuestionTarget"];
  retrievalPolicy?: "never" | "auto" | "required";
  retrievalExecuted?: boolean;
  retrievedSources?: RetrievedSourceReference[];
  completionStatus?: "in_progress" | "completed";
  missingRequiredTargets?: Array<Record<string, unknown>>;
  error?: string;
};
