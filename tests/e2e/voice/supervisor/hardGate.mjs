import {
  ANOMALIES,
  detectTurnAnomalies,
} from "../assertions/invariants.mjs";

export const HARD_GATE_STOP_CODES = Object.freeze({
  MAX_TURNS_EXCEEDED: "MAX_TURNS_EXCEEDED",
  MAX_JEV_CALLS_EXCEEDED: "MAX_JEV_CALLS_EXCEEDED",
});

const HARD_GATE_FAILURES = new Set([
  ANOMALIES.TRANSCRIPT_FINAL_MISSING,
  ANOMALIES.ANSWER_NOT_PERSISTED,
  ANOMALIES.QUESTION_NOT_ADVANCED,
  ANOMALIES.DUPLICATE_RESPONSE,
  ANOMALIES.API_5XX,
  ANOMALIES.WEBSOCKET_ERROR,
  ANOMALIES.DATACHANNEL_ERROR,
  ANOMALIES.RESPONSE_TIMEOUT,
]);

/**
 * Execute deterministic checks before any Jev request is made.
 *
 * A valid non-answer fixture (for example a user clarification request) can
 * opt out of the answer-persistence and question-advance assertions while
 * still being checked for transcript, transport, duplicate-response, and
 * timeout failures.
 */
export function evaluateHardGate({
  turn,
  maxTurns,
  jevCalls,
  maxJevCalls,
  answer,
  state,
  fieldId,
  expectedQuestionId,
  finalText,
  nextQuestionId,
  previousQuestionId,
  responseIds = [],
  eventTypes = [],
  responseStarted = true,
  committedBeforeFinal = false,
  apiStatuses = [],
  dataChannelErrors = [],
  questionShouldAdvance = false,
  questionAdvanced = null,
  expectAnswerPersistence = true,
  allowRepeatedQuestion = false,
}) {
  const anomalies = detectTurnAnomalies({
    finalText,
    state: state ?? answer?.state ?? answer,
    fieldId,
    expectedQuestionId,
    nextQuestionId,
    previousQuestionId,
    responseIds,
    eventTypes,
    responseStarted,
    committedBeforeFinal,
    apiStatuses,
    dataChannelErrors,
    questionShouldAdvance,
    questionAdvanced,
    expectAnswerPersistence,
    allowRepeatedQuestion,
  });

  const failureCodes = anomalies.filter((code) => HARD_GATE_FAILURES.has(code));
  if (failureCodes.length > 0) {
    return {
      status: "FAIL",
      passed: false,
      shouldCallJev: false,
      codes: failureCodes,
      anomalies,
      reason: "deterministic_hard_gate_failure",
    };
  }

  if (Number.isFinite(Number(maxTurns)) && Number(turn) >= Number(maxTurns)) {
    return {
      status: "STOP",
      passed: false,
      shouldCallJev: false,
      codes: [HARD_GATE_STOP_CODES.MAX_TURNS_EXCEEDED],
      anomalies,
      reason: "max_turns_reached",
    };
  }

  if (Number.isFinite(Number(maxJevCalls)) && Number(jevCalls) >= Number(maxJevCalls)) {
    return {
      status: "STOP",
      passed: false,
      shouldCallJev: false,
      codes: [HARD_GATE_STOP_CODES.MAX_JEV_CALLS_EXCEEDED],
      anomalies,
      reason: "max_jev_calls_reached",
    };
  }

  return {
    status: "PASS",
    passed: true,
    shouldCallJev: true,
    codes: [],
    anomalies,
    reason: "deterministic_hard_gate_passed",
  };
}
