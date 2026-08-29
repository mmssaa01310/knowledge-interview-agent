export function shouldUseVoiceAnswerSummary(fieldState: {
  answerState?: "UNANSWERED" | "CANDIDATE_PENDING" | "AWAITING_CONFIRMATION" | "CONFIRMED";
  status?: "pending" | "asking" | "completed";
  recordAnswer?: string | null;
} | null | undefined): boolean;

export function isRawVoiceUserMessageHiddenFromAnswer(message: {
  voiceSessionId?: string | null;
} | null | undefined): boolean;

export function getInterviewAnswerValue(fieldState: {
  answerState?: "UNANSWERED" | "CANDIDATE_PENDING" | "AWAITING_CONFIRMATION" | "CONFIRMED";
  answerSummary?: string | null;
  recordAnswer?: string | null;
} | null | undefined): string;

export function getInterviewDisplayAnswer(
  fieldState: {
    answerState?: "UNANSWERED" | "CANDIDATE_PENDING" | "AWAITING_CONFIRMATION" | "CONFIRMED";
    answerSummary?: string | null;
    recordAnswer?: string | null;
  } | null | undefined,
  editedValue?: string,
): string;

export function getInterviewAnswerStatusLabel(fieldState: {
  answerState?: "UNANSWERED" | "CANDIDATE_PENDING" | "AWAITING_CONFIRMATION" | "CONFIRMED";
} | null | undefined, translate?: (key: string) => string): string;
