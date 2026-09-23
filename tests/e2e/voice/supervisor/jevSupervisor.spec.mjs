import { test, expect } from "@playwright/test";
import { VoiceCostGuard } from "../config.mjs";
import {
  JevSupervisor,
  clearJevSupervisorCache,
} from "./jevSupervisor.mjs";

function config(overrides = {}) {
  return {
    agentMode: "jev",
    seed: 38291,
    maxJevCalls: 5,
    maxTurnsPerSession: 5,
    ...overrides,
  };
}

function input() {
  return {
    task: "kikiori_voice_test_supervisor",
    session_id: "unit-jev-session",
    seed: 38291,
    turn: 1,
    currentQuestionId: "q-001",
    currentQuestion: "現在の役割を教えてください。",
    lastTranscript: "プロジェクトリーダーをしています。",
    answerState: "CONFIRMED",
    candidateAnswerExists: false,
    recordAnswerExists: true,
    stateVersionAdvanced: true,
    questionAdvanced: true,
    previousUserBehavior: "NORMAL",
    testedBehaviors: ["NORMAL"],
    remainingTurns: 4,
    remainingJevCalls: 5,
    errors: [],
    interviewState: { stateVersion: 2, currentQuestionId: "q-001" },
    telemetry: { simulatedTurnLatencyMs: 42 },
  };
}

test("Hard Gate failure skips Jev without consuming a call", async () => {
  clearJevSupervisorCache();
  const guard = new VoiceCostGuard(config());
  guard.startSession();
  let calls = 0;
  const supervisor = new JevSupervisor({
    config: config(),
    guard,
    sessionId: "unit-jev-session",
    apiUrl: "http://jev.invalid",
    fetchImpl: async () => {
      calls += 1;
      throw new Error("must not be called");
    },
  });

  const result = await supervisor.decideAfterTurn({
    hardGate: {
      status: "FAIL",
      shouldCallJev: false,
      codes: ["ANSWER_NOT_PERSISTED"],
    },
    input: input(),
  });

  expect(result.called).toBe(false);
  expect(result.skippedReason).toBe("hard_gate_fail");
  expect(calls).toBe(0);
  expect(guard.snapshot().jevCalls).toBe(0);
});

test("Jev returns a structured decision and caches the same state", async () => {
  clearJevSupervisorCache();
  const testConfig = config();
  const guard = new VoiceCostGuard(testConfig);
  guard.startSession();
  const requests = [];
  const supervisor = new JevSupervisor({
    config: testConfig,
    guard,
    sessionId: "unit-jev-session",
    apiUrl: "http://jev.test/supervise",
    apiKey: "test-key",
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      return {
        ok: true,
        status: 200,
        async json() {
          return {
            session_action: "STRESS_NEXT",
            next_user_behavior: "SELF_CORRECT",
            confidence: 0.87,
          };
        },
      };
    },
  });
  const hardGate = { status: "PASS", shouldCallJev: true, codes: [] };
  const rawInput = input();
  const requestInput = supervisor.buildInput(rawInput);

  const first = await supervisor.decideAfterTurn({ hardGate, input: requestInput });
  const second = await supervisor.decideAfterTurn({ hardGate, input: requestInput });
  const body = JSON.parse(requests[0].options.body);

  expect(first).toMatchObject({
    called: true,
    sessionAction: "STRESS_NEXT",
    nextUserBehavior: "SELF_CORRECT",
    confidence: 0.87,
    cacheHit: false,
    callCount: 1,
  });
  expect(second).toMatchObject({
    called: true,
    sessionAction: "STRESS_NEXT",
    nextUserBehavior: "SELF_CORRECT",
    cacheHit: true,
    callCount: 1,
  });
  expect(requests).toHaveLength(1);
  expect(requests[0].options.headers.authorization).toBe("Bearer test-key");
  expect(body.criteria.length).toBeGreaterThan(0);
  expect(body.lastTranscript).toBe(rawInput.lastTranscript);
  expect(guard.snapshot().jevCalls).toBe(1);
});
