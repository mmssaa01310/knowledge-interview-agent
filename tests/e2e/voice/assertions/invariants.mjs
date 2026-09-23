export const ANOMALIES = Object.freeze({
  ANSWER_NOT_PERSISTED: "ANSWER_NOT_PERSISTED",
  QUESTION_NOT_ADVANCED: "QUESTION_NOT_ADVANCED",
  REPEATED_QUESTION: "REPEATED_QUESTION",
  DUPLICATE_RESPONSE: "DUPLICATE_RESPONSE",
  TRANSCRIPT_FINAL_MISSING: "TRANSCRIPT_FINAL_MISSING",
  TURN_COMMITTED_TOO_EARLY: "TURN_COMMITTED_TOO_EARLY",
  RESPONSE_TIMEOUT: "RESPONSE_TIMEOUT",
  WEBSOCKET_ERROR: "WEBSOCKET_ERROR",
  API_5XX: "API_5XX",
});

function hasText(value) {
  return typeof value === "string" && value.trim().length > 0;
}

export function extractFieldAnswer(state, fieldId) {
  const field = state?.fieldStates?.[fieldId] ?? {};
  const questionAnswer = Object.values(state?.questionAnswers ?? {}).find(
    (answer) => answer?.fieldId === fieldId,
  ) ?? {};
  return {
    candidate: field.candidateAnswer ?? questionAnswer.candidateAnswer ?? null,
    confirmed: field.recordAnswer ?? field.answer ?? questionAnswer.answer ?? null,
    answerState: field.answerState ?? questionAnswer.answerStatus ?? null,
  };
}

export function detectTurnAnomalies({
  finalText,
  state,
  fieldId,
  expectedQuestionId,
  nextQuestionId,
  previousQuestionId,
  responseIds = [],
  eventTypes = [],
  responseStarted = true,
  committedBeforeFinal = false,
  apiStatuses = [],
}) {
  const anomalies = [];
  const answer = extractFieldAnswer(state, fieldId);
  if (!hasText(finalText)) anomalies.push(ANOMALIES.TRANSCRIPT_FINAL_MISSING);
  if (hasText(finalText) && !hasText(answer.candidate) && !hasText(answer.confirmed)
    && !["CONFIRMED", "ANSWERED"].includes(answer.answerState)) {
    anomalies.push(ANOMALIES.ANSWER_NOT_PERSISTED);
  }
  if (nextQuestionId && state?.currentQuestionId === expectedQuestionId) {
    anomalies.push(ANOMALIES.QUESTION_NOT_ADVANCED);
  }
  if (previousQuestionId && expectedQuestionId === previousQuestionId) {
    anomalies.push(ANOMALIES.REPEATED_QUESTION);
  }
  const ids = responseIds.filter(hasText);
  if (new Set(ids).size !== ids.length) anomalies.push(ANOMALIES.DUPLICATE_RESPONSE);
  if (committedBeforeFinal) anomalies.push(ANOMALIES.TURN_COMMITTED_TOO_EARLY);
  if (!responseStarted) anomalies.push(ANOMALIES.RESPONSE_TIMEOUT);
  if (eventTypes.some((type) => /websocket|data_channel_error|transport\.closed/i.test(type))) {
    anomalies.push(ANOMALIES.WEBSOCKET_ERROR);
  }
  if (apiStatuses.some((status) => Number(status) >= 500)) anomalies.push(ANOMALIES.API_5XX);
  return [...new Set(anomalies)];
}

export function assertNoAnomalies(anomalies, message = "Voice E2E anomalies detected") {
  if (anomalies.length) throw new Error(message + ": " + anomalies.join(", "));
}
