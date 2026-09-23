import { test, expect } from "@playwright/test";
import {
  buildLevel3Candidates,
  compareLevel2Level3,
} from "./level3Promotion.mjs";

function turn(overrides = {}) {
  return {
    turn: 1,
    behavior: "NORMAL",
    sessionAction: "CONTINUE",
    hardGateStatus: "PASS",
    hardGateCodes: [],
    expectationResult: "PASS",
    expectationViolations: [],
    observed: { questionAdvanced: true },
    interview: {
      currentQuestionAfter: "live-field-next",
      nextQuestionId: "live-field-next",
      answerState: "CONFIRMED",
      confirmed: "answer",
    },
    response: { assistantSpeechStarted: true },
    ...overrides,
  };
}

function summary(sessions) {
  return { seed: 38291, sessions };
}

test("clean NORMAL-only passes are not promoted", () => {
  const candidates = buildLevel3Candidates(summary([{
    sessionId: "s1",
    index: 1,
    turns: [turn()],
  }]));
  expect(candidates).toEqual([]);
});

test("stress behavior and RETRY_CURRENT are promoted and deduplicated", () => {
  const candidates = buildLevel3Candidates(summary([
    {
      sessionId: "s1",
      index: 1,
      turns: [turn({
        behavior: "SELF_CORRECT",
        sessionAction: "RETRY_CURRENT",
        interview: { answerState: "CONFIRMED", confirmed: "answer" },
      })],
    },
    {
      sessionId: "s2",
      index: 2,
      turns: [turn({ behavior: "SELF_CORRECT", turn: 4 })],
    },
  ]), { maxCandidates: 5 });
  expect(candidates).toHaveLength(1);
  expect(candidates[0].behavior).toBe("SELF_CORRECT");
  expect(candidates[0].reasonCode).toBe("RETRY_CURRENT");
  expect(candidates[0].session.id).toBe("s1");
  expect(candidates[0].reasons.map((reason) => reason.code)).toEqual(
    expect.arrayContaining(["RETRY_CURRENT", "STRESS_BEHAVIOR"]),
  );
});

test("expectation failures take precedence over behavior-only candidates", () => {
  const candidates = buildLevel3Candidates(summary([{
    sessionId: "s1",
    index: 1,
    turns: [turn({
      behavior: "AMBIGUOUS",
      expectationResult: "FAIL",
      expectationViolations: ["ANSWER_NOT_PERSISTED"],
      hardGateStatus: "FAIL",
      hardGateCodes: ["ANSWER_NOT_PERSISTED"],
    })],
  }]));
  expect(candidates[0].reasonCode).toBe("EXPECTATION_FAIL");
  expect(candidates[0].priority).toBe(100);
});

test("promotion queue is capped at five representative behaviors", () => {
  const behaviors = ["AMBIGUOUS", "SELF_CORRECT", "MULTI_ANSWER", "ASK_CLARIFICATION", "HESITATE", "UNKNOWN"];
  const candidates = buildLevel3Candidates(summary([{
    sessionId: "s1",
    index: 1,
    turns: behaviors.map((behavior, index) => turn({ behavior, turn: index + 1 })),
  }]), { maxCandidates: 5 });
  expect(candidates).toHaveLength(5);
  expect(candidates.map((candidate) => candidate.behavior)).not.toContain("NORMAL");
});

test("Level 2 and Level 3 outcomes classify PASS to FAIL and FAIL to FAIL", () => {
  const candidate = {
    level2: {
      transcript: "通常回答",
      expectationResult: "PASS",
      interview: {
        answerState: "CONFIRMED",
        currentQuestionAfter: "live-field-next",
        nextQuestionId: "live-field-next",
      },
    },
  };
  const level3Fail = compareLevel2Level3(candidate, {
    transcript: "通常回答",
    expectationResult: "FAIL",
    hardGateStatus: "FAIL",
    interview: {
      answerState: "CONFIRMED",
      currentQuestionAfter: "live-field-next",
      nextQuestionId: "live-field-next",
    },
  });
  expect(level3Fail.classification).toBe("LEVEL2_PASS_LEVEL3_FAIL");

  const failedCandidate = {
    ...candidate,
    level2: { ...candidate.level2, expectationResult: "FAIL" },
  };
  const repeatedFailure = compareLevel2Level3(failedCandidate, {
    ...level3Fail,
    expectationResult: "FAIL",
    hardGateStatus: "FAIL",
  });
  expect(repeatedFailure.classification).toBe("LEVEL2_FAIL_LEVEL3_FAIL");
});

