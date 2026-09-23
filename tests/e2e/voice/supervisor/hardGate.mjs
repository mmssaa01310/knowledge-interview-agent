import {
  ANOMALIES,
  detectTurnAnomalies,
} from "../assertions/invariants.mjs";

export const HARD_GATE_STOP_CODES = Object.freeze({
  MAX_TURNS_EXCEEDED: "MAX_TURNS_EXCEEDED",
  MAX_JEV_CALLS_EXCEEDED: "MAX_JEV_CALLS_EXCEEDED",
});

const HARD_GATE_FAILURES = new Set([
  ANOMALIES.DUPLICATE_RESPONSE,
  ANOMALIES.API_5XX,
  ANOMALIES.WEBSOCKET_ERROR,
  ANOMALIES.DATACHANNEL_ERROR,
  ANOMALIES.TURN_COMMITTED_TOO_EARLY,
  ANOMALIES.UNEXPECTED_ANSWER_PERSISTED,
  ANOMALIES.UNEXPECTED_QUESTION_ADVANCE,
]);

export const EXPECTATION_LEVELS = Object.freeze({
  REQUIRED: "required",
  OPTIONAL: "optional",
  NOT_REQUIRED: "not_required",
  MUST_NOT: "must_not",
  OBSERVE: "observe",
});

function hasText(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function answerPersistedFromState(state, fieldId) {
  const field = state?.fieldStates?.[fieldId] ?? {};
  const answer = Object.values(state?.questionAnswers ?? {}).find(
    (candidate) => candidate?.fieldId === fieldId,
  ) ?? {};
  return Boolean(
    hasText(field.candidateAnswer)
    || hasText(field.recordAnswer ?? field.answer)
    || hasText(answer.candidateAnswer)
    || hasText(answer.answer)
    || ["CONFIRMED", "ANSWERED"].includes(field.answerState ?? answer.answerStatus),
  );
}

function defaultExpectations({
  questionShouldAdvance,
  expectAnswerPersistence,
}) {
  return {
    transcriptFinal: EXPECTATION_LEVELS.REQUIRED,
    answerPersistence: expectAnswerPersistence
      ? EXPECTATION_LEVELS.REQUIRED
      : EXPECTATION_LEVELS.NOT_REQUIRED,
    questionAdvance: questionShouldAdvance
      ? EXPECTATION_LEVELS.REQUIRED
      : EXPECTATION_LEVELS.OPTIONAL,
    assistantRecovery: EXPECTATION_LEVELS.REQUIRED,
  };
}

export function evaluateBehaviorExpectations({
  expectations: suppliedExpectations,
  finalText,
  answerPersisted,
  answerPersistedBefore,
  answerPersistedThisTurn,
  questionAdvanced,
  responseStarted,
  committedBeforeFinal = false,
  questionShouldAdvance = false,
  expectAnswerPersistence = true,
}) {
  const expectations = {
    ...defaultExpectations({ questionShouldAdvance, expectAnswerPersistence }),
    ...(suppliedExpectations ?? {}),
  };
  const inferredAnswerPersistedThisTurn = answerPersistedThisTurn !== null
    && answerPersistedThisTurn !== undefined
    ? Boolean(answerPersistedThisTurn)
    : answerPersistedBefore === null || answerPersistedBefore === undefined
      ? Boolean(answerPersisted)
      : Boolean(answerPersisted && !answerPersistedBefore);
  const observed = {
    transcriptFinal: hasText(finalText),
    answerPersisted: Boolean(answerPersisted),
    answerPersistedThisTurn: inferredAnswerPersistedThisTurn,
    questionAdvanced: Boolean(questionAdvanced),
    assistantResponseStarted: Boolean(responseStarted),
    committedBeforeFinal: Boolean(committedBeforeFinal),
  };
  const violations = [];
  const expectedConditions = [];

  if (expectations.transcriptFinal === EXPECTATION_LEVELS.REQUIRED) {
    const satisfied = observed.transcriptFinal;
    expectedConditions.push({
      name: "transcript_final",
      expected: "required",
      satisfied,
    });
    if (!satisfied) violations.push(ANOMALIES.TRANSCRIPT_FINAL_MISSING);
  } else {
    expectedConditions.push({
      name: "transcript_final",
      expected: expectations.transcriptFinal,
      satisfied: true,
      expectedCondition: !observed.transcriptFinal,
    });
  }

  if (expectations.answerPersistence === EXPECTATION_LEVELS.REQUIRED) {
    const satisfied = observed.answerPersisted;
    expectedConditions.push({
      name: "answer_persistence",
      expected: "required",
      satisfied,
    });
    if (!satisfied) violations.push(ANOMALIES.ANSWER_NOT_PERSISTED);
  } else if (expectations.answerPersistence === EXPECTATION_LEVELS.MUST_NOT) {
    const satisfied = !observed.answerPersistedThisTurn;
    expectedConditions.push({
      name: "answer_persistence",
      expected: "must_not",
      satisfied,
    });
    if (!satisfied) violations.push(ANOMALIES.UNEXPECTED_ANSWER_PERSISTED);
  } else {
    expectedConditions.push({
      name: "answer_persistence",
      expected: expectations.answerPersistence,
      satisfied: true,
    });
  }

  if (expectations.questionAdvance === EXPECTATION_LEVELS.REQUIRED) {
    const satisfied = observed.questionAdvanced;
    expectedConditions.push({
      name: "question_advance",
      expected: "required",
      satisfied,
    });
    if (!satisfied) violations.push(ANOMALIES.QUESTION_NOT_ADVANCED);
  } else if (expectations.questionAdvance === EXPECTATION_LEVELS.MUST_NOT) {
    const satisfied = !observed.questionAdvanced;
    expectedConditions.push({
      name: "question_advance",
      expected: "must_not",
      satisfied,
    });
    if (!satisfied) violations.push(ANOMALIES.UNEXPECTED_QUESTION_ADVANCE);
  } else {
    expectedConditions.push({
      name: "question_advance",
      expected: expectations.questionAdvance,
      satisfied: true,
    });
  }

  if (expectations.assistantRecovery === EXPECTATION_LEVELS.REQUIRED) {
    const satisfied = observed.assistantResponseStarted;
    expectedConditions.push({
      name: "assistant_recovery",
      expected: "required",
      satisfied,
    });
    if (!satisfied) violations.push(ANOMALIES.RESPONSE_TIMEOUT);
  } else {
    expectedConditions.push({
      name: "assistant_recovery",
      expected: expectations.assistantRecovery,
      satisfied: true,
    });
  }

  return {
    status: violations.length > 0 ? "FAIL" : "PASS",
    passed: violations.length === 0,
    expectations,
    observed,
    expectedConditions,
    violations: [...new Set(violations)],
  };
}

/**
 * Execute deterministic checks before any Jev request is made.
 *
 * A valid non-answer fixture (for example SILENCE or a clarification request)
 * supplies behavior-specific expectations. Transport and duplicate-response
 * invariants remain hard failures; transcript, persistence, advance, and
 * response timing are checked against those expectations.
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
  expectations = null,
  answerPersistedBefore = null,
  answerPersistedThisTurn = null,
}) {
  const answerPersisted = answerPersistedFromState(
    state ?? answer?.state ?? answer,
    fieldId,
  );
  const expectation = evaluateBehaviorExpectations({
    expectations,
    finalText,
    answerPersisted,
    answerPersistedThisTurn: answerPersistedThisTurn ?? (
      answerPersisted && answerPersistedBefore === false
        ? true
        : answerPersistedThisTurn
    ),
    questionAdvanced,
    responseStarted,
    committedBeforeFinal,
    questionShouldAdvance,
    expectAnswerPersistence,
  });
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
    responseTimeoutRequired: expectation.expectations.assistantRecovery
      === EXPECTATION_LEVELS.REQUIRED,
    committedBeforeFinal,
    apiStatuses,
    dataChannelErrors,
    questionShouldAdvance,
    questionAdvanced,
    expectAnswerPersistence: expectation.expectations.answerPersistence
      === EXPECTATION_LEVELS.REQUIRED,
    transcriptFinalRequired: expectation.expectations.transcriptFinal
      === EXPECTATION_LEVELS.REQUIRED,
    allowRepeatedQuestion,
  });

  const failureCodes = [
    ...anomalies.filter((code) => HARD_GATE_FAILURES.has(code)),
    ...expectation.violations,
  ].filter((code, index, all) => all.indexOf(code) === index);
  if (failureCodes.length > 0) {
    return {
      status: "FAIL",
      passed: false,
      shouldCallJev: false,
      codes: failureCodes,
      anomalies,
      expectations: expectation.expectations,
      observed: expectation.observed,
      expectedConditions: expectation.expectedConditions,
      expectationResult: expectation.status,
      expectationViolations: expectation.violations,
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
      expectations: expectation.expectations,
      observed: expectation.observed,
      expectedConditions: expectation.expectedConditions,
      expectationResult: expectation.status,
      expectationViolations: expectation.violations,
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
      expectations: expectation.expectations,
      observed: expectation.observed,
      expectedConditions: expectation.expectedConditions,
      expectationResult: expectation.status,
      expectationViolations: expectation.violations,
      reason: "max_jev_calls_reached",
    };
  }

  return {
    status: "PASS",
    passed: true,
    shouldCallJev: true,
    codes: [],
    anomalies,
    expectations: expectation.expectations,
    observed: expectation.observed,
    expectedConditions: expectation.expectedConditions,
    expectationResult: expectation.status,
    expectationViolations: expectation.violations,
    reason: "deterministic_hard_gate_passed",
  };
}
