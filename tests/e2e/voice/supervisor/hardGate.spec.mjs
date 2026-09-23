import { test, expect } from "@playwright/test";
import { ANOMALIES } from "../assertions/invariants.mjs";
import {
  evaluateHardGate,
  HARD_GATE_STOP_CODES,
} from "./hardGate.mjs";

const base = {
  turn: 1,
  maxTurns: 5,
  jevCalls: 0,
  maxJevCalls: 5,
  state: {
    currentQuestionId: "q-001",
    fieldStates: { field: { answerState: "CONFIRMED", recordAnswer: "回答" } },
  },
  fieldId: "field",
  expectedQuestionId: "q-001",
  finalText: "回答",
  responseIds: ["response-1"],
  eventTypes: ["response.created"],
  responseStarted: true,
  questionShouldAdvance: true,
  questionAdvanced: true,
};

test("DataChannel error fails the gate before Jev", () => {
  const result = evaluateHardGate({
    ...base,
    dataChannelErrors: [{ message: "closed" }],
  });
  expect(result.status).toBe("FAIL");
  expect(result.shouldCallJev).toBe(false);
  expect(result.codes).toContain(ANOMALIES.DATACHANNEL_ERROR);
});

test("deterministic transport and lifecycle anomalies fail before Jev", () => {
  const cases = [
    [ANOMALIES.TRANSCRIPT_FINAL_MISSING, { finalText: "" }],
    [ANOMALIES.ANSWER_NOT_PERSISTED, {
      state: {
        currentQuestionId: "q-001",
        fieldStates: { field: { answerState: "UNANSWERED" } },
      },
    }],
    [ANOMALIES.QUESTION_NOT_ADVANCED, { questionAdvanced: false }],
    [ANOMALIES.DUPLICATE_RESPONSE, { responseIds: ["response-1", "response-1"] }],
    [ANOMALIES.API_5XX, { apiStatuses: [503] }],
    [ANOMALIES.WEBSOCKET_ERROR, { eventTypes: ["websocket.error"] }],
    [ANOMALIES.RESPONSE_TIMEOUT, { responseStarted: false }],
  ];

  for (const [code, overrides] of cases) {
    const result = evaluateHardGate({ ...base, ...overrides });
    expect(result.status, code).toBe("FAIL");
    expect(result.shouldCallJev, code).toBe(false);
    expect(result.codes, code).toContain(code);
  }
});

test("retrying the current question does not become a repeated-question failure", () => {
  const result = evaluateHardGate({
    ...base,
    previousQuestionId: "q-001",
    allowRepeatedQuestion: true,
    questionShouldAdvance: false,
  });
  expect(result.status).toBe("PASS");
  expect(result.shouldCallJev).toBe(true);
});

test("the turn limit stops before another Jev call", () => {
  const result = evaluateHardGate({
    ...base,
    turn: 5,
  });
  expect(result.status).toBe("STOP");
  expect(result.shouldCallJev).toBe(false);
  expect(result.codes).toEqual([HARD_GATE_STOP_CODES.MAX_TURNS_EXCEEDED]);
});

test("the Jev call limit stops before another Jev call", () => {
  const result = evaluateHardGate({
    ...base,
    jevCalls: 5,
  });
  expect(result.status).toBe("STOP");
  expect(result.shouldCallJev).toBe(false);
  expect(result.codes).toEqual([HARD_GATE_STOP_CODES.MAX_JEV_CALLS_EXCEEDED]);
});

test("intentional SILENCE accepts a missing final and keeps the current question", () => {
  const result = evaluateHardGate({
    ...base,
    finalText: "",
    state: {
      currentQuestionId: "q-001",
      fieldStates: { field: { answerState: "UNANSWERED" } },
    },
    questionShouldAdvance: false,
    questionAdvanced: false,
    expectAnswerPersistence: false,
    expectations: {
      transcriptFinal: "not_required",
      answerPersistence: "must_not",
      questionAdvance: "must_not",
      assistantRecovery: "observe",
    },
    answerPersistedBefore: false,
    answerPersistedThisTurn: false,
  });

  expect(result.status).toBe("PASS");
  expect(result.shouldCallJev).toBe(true);
  expect(result.expectationResult).toBe("PASS");
  expect(result.observed.transcriptFinal).toBe(false);
  expect(result.expectedConditions).toEqual(expect.arrayContaining([
    expect.objectContaining({
      name: "transcript_final",
      expectedCondition: true,
      satisfied: true,
    }),
  ]));
  expect(result.codes).not.toContain(ANOMALIES.TRANSCRIPT_FINAL_MISSING);
});

test("SILENCE fails when it unexpectedly persists an answer or advances", () => {
  const result = evaluateHardGate({
    ...base,
    finalText: "",
    state: {
      currentQuestionId: "q-002",
      fieldStates: { field: { answerState: "CONFIRMED", recordAnswer: "回答" } },
    },
    questionShouldAdvance: false,
    questionAdvanced: true,
    expectAnswerPersistence: false,
    expectations: {
      transcriptFinal: "not_required",
      answerPersistence: "must_not",
      questionAdvance: "must_not",
      assistantRecovery: "observe",
    },
    answerPersistedBefore: false,
    answerPersistedThisTurn: true,
  });

  expect(result.status).toBe("FAIL");
  expect(result.shouldCallJev).toBe(false);
  expect(result.codes).toEqual(expect.arrayContaining([
    ANOMALIES.UNEXPECTED_ANSWER_PERSISTED,
    ANOMALIES.UNEXPECTED_QUESTION_ADVANCE,
  ]));
  expect(result.expectationResult).toBe("FAIL");
});

test("optional ambiguity does not fail for a valid but unpersisted answer", () => {
  const result = evaluateHardGate({
    ...base,
    finalText: "そのあたりを色々やっています。",
    state: {
      currentQuestionId: "q-001",
      fieldStates: { field: { answerState: "UNANSWERED" } },
    },
    questionShouldAdvance: false,
    questionAdvanced: false,
    expectAnswerPersistence: false,
    expectations: {
      transcriptFinal: "required",
      answerPersistence: "optional",
      questionAdvance: "optional",
      assistantRecovery: "required",
    },
    answerPersistedBefore: false,
    answerPersistedThisTurn: false,
  });

  expect(result.status).toBe("PASS");
  expect(result.expectationResult).toBe("PASS");
  expect(result.expectationViolations).toEqual([]);
});
