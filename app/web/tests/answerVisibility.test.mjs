import test from "node:test";
import assert from "node:assert/strict";

import {
  getInterviewAnswerStatusLabel,
  getInterviewDisplayAnswer,
  getInterviewAnswerValue,
  isRawVoiceUserMessageHiddenFromAnswer,
  shouldUseVoiceAnswerSummary,
} from "../src/features/interviews/answerVisibility.js";

test("raw voice transcript is hidden from answer area before confirmation", () => {
  assert.equal(
    isRawVoiceUserMessageHiddenFromAnswer({
      role: "user",
      text: "宮崎です",
      voiceSessionId: "vs-1",
      answerToQuestionId: "q-001",
    }),
    true,
  );
});

test("confirmed record answer is visible in answer area", () => {
  assert.equal(
    shouldUseVoiceAnswerSummary({
      fieldId: "field-1",
      status: "asking",
      answerSummary: "自己紹介として、宮崎健一という名前が回答されました。",
      recordAnswer: "宮崎です",
      missingInformation: [],
      answerState: "CONFIRMED",
    }),
    true,
  );
});

test("awaiting confirmation answer summary is not visible in answer area", () => {
  assert.equal(
    shouldUseVoiceAnswerSummary({
      fieldId: "field-1",
      status: "asking",
      answerSummary: null,
      missingInformation: [],
      answerState: "AWAITING_CONFIRMATION",
    }),
    false,
  );
});

test("only confirmed record answer is returned for answer area", () => {
  assert.equal(
    getInterviewAnswerValue({
      fieldId: "field-1",
      status: "asking",
      answerSummary: "自己紹介として、宮崎正之という名前が回答されました。",
      recordAnswer: "宮崎正之です",
      answerState: "CONFIRMED",
    }),
    "宮崎正之です",
  );
  assert.equal(
    getInterviewAnswerValue({
      fieldId: "field-1",
      status: "asking",
      answerSummary: "候補",
      recordAnswer: null,
      answerState: "AWAITING_CONFIRMATION",
    }),
    "",
  );
});

test("answer summary alone is never displayed as a formal answer", () => {
  assert.equal(
    getInterviewAnswerValue({
      answerState: "CONFIRMED",
      answerSummary: "自己紹介として、宮崎という名前が回答されました。",
      recordAnswer: null,
    }),
    "",
  );
  assert.equal(
    shouldUseVoiceAnswerSummary({
      answerState: "CONFIRMED",
      answerSummary: "自己紹介として、宮崎という名前が回答されました。",
      recordAnswer: null,
    }),
    false,
  );
});

test("draft fallback is hidden until the field is confirmed", () => {
  assert.equal(
    getInterviewDisplayAnswer(
      { answerState: "CANDIDATE_PENDING", answerSummary: null },
      "未確認のdraft",
    ),
    "",
  );
  assert.equal(
    getInterviewDisplayAnswer(
      { answerState: "AWAITING_CONFIRMATION", answerSummary: null },
      "未確認のdraft",
    ),
    "",
  );
  assert.equal(
    getInterviewDisplayAnswer(
      {
        answerState: "CONFIRMED",
        answerSummary: "メタ要約",
        recordAnswer: "ユーザー発話",
      },
      "編集中",
    ),
    "編集中",
  );
});

test("status labels follow answer state machine", () => {
  assert.equal(getInterviewAnswerStatusLabel({ answerState: "UNANSWERED" }), "未回答");
  assert.equal(getInterviewAnswerStatusLabel({ answerState: "CANDIDATE_PENDING" }), "追加確認中");
  assert.equal(getInterviewAnswerStatusLabel({ answerState: "AWAITING_CONFIRMATION" }), "確認中");
  assert.equal(getInterviewAnswerStatusLabel({ answerState: "CONFIRMED" }), "回答済み");
});
