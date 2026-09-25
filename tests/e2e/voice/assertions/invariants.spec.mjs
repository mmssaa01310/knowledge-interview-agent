import { test, expect } from "@playwright/test";
import { ANOMALIES, detectTurnAnomalies } from "./invariants.mjs";

test("deterministic voice anomalies are classified without an LLM", () => {
  const anomalies = detectTurnAnomalies({
    finalText: "回答は取得できた",
    state: { currentQuestionId: "q-1", fieldStates: { field: { answerState: "UNANSWERED" } } },
    fieldId: "field",
    expectedQuestionId: "q-1",
    nextQuestionId: "q-2",
    previousQuestionId: "q-1",
    responseIds: ["response-1", "response-1"],
    eventTypes: ["transport.closed"],
    responseStarted: false,
    committedBeforeFinal: true,
    apiStatuses: [502],
  });

  expect(anomalies).toEqual(expect.arrayContaining([
    ANOMALIES.ANSWER_NOT_PERSISTED,
    ANOMALIES.QUESTION_NOT_ADVANCED,
    ANOMALIES.REPEATED_QUESTION,
    ANOMALIES.DUPLICATE_RESPONSE,
    ANOMALIES.TURN_COMMITTED_TOO_EARLY,
    ANOMALIES.RESPONSE_TIMEOUT,
    ANOMALIES.WEBSOCKET_ERROR,
    ANOMALIES.API_5XX,
  ]));
});

test("missing final transcript is detected separately from persistence", () => {
  const anomalies = detectTurnAnomalies({
    finalText: "",
    state: { fieldStates: { field: { answerState: "UNANSWERED" } } },
    fieldId: "field",
    expectedQuestionId: "q-1",
  });

  expect(anomalies).toEqual([ANOMALIES.TRANSCRIPT_FINAL_MISSING]);
});

test("an open DataChannel is not a DataChannel error", () => {
  const anomalies = detectTurnAnomalies({
    finalText: "回答です",
    state: { fieldStates: { field: { answerState: "CONFIRMED", confirmed: "回答です" } } },
    fieldId: "field",
    expectedQuestionId: "q-1",
    eventTypes: ["data_channel.open"],
    responseStarted: true,
  });

  expect(anomalies).not.toContain(ANOMALIES.DATACHANNEL_ERROR);
});
