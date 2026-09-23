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

test("TypeSafe Jev sends named choice questions and normalizes the response", async () => {
  clearJevSupervisorCache();
  const testConfig = config({ maxJevCalls: 5 });
  const guard = new VoiceCostGuard(testConfig);
  guard.startSession();
  const requests = [];
  const supervisor = new JevSupervisor({
    config: testConfig,
    guard,
    sessionId: "typesafe-jev-session",
    typeSafeApiKey: "typesafe-test-key",
    typeSafeBaseUrl: "https://typesafe.test/v1/systemone",
    typeSafeModel: "jev-test",
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      return {
        ok: true,
        status: 200,
        async json() {
          return {
            model: "jev-test",
            answers: {
              session_action: {
                type: "choice",
                choice: "STRESS_NEXT",
                confidence: 0.91,
                probabilities: { STRESS_NEXT: 0.91 },
              },
              next_user_behavior: {
                type: "choice",
                choice: "MULTI_ANSWER",
                confidence: 0.84,
                probabilities: { MULTI_ANSWER: 0.84 },
              },
            },
            usage: { input_tokens: 120, output_tokens: 12 },
          };
        },
      };
    },
  });
  const hardGate = { status: "PASS", shouldCallJev: true, codes: [] };

  const requestInput = supervisor.buildInput(input());
  const result = await supervisor.decideAfterTurn({
    hardGate,
    input: requestInput,
  });
  const body = JSON.parse(requests[0].options.body);

  expect(supervisor.provider).toBe("typesafe");
  expect(requests).toHaveLength(1);
  expect(requests[0].url).toBe("https://typesafe.test/v1/systemone");
  expect(requests[0].options.headers.authorization).toBe("Bearer typesafe-test-key");
  expect(body.model).toBe("jev-test");
  expect(body.state.lastTranscript).toBe(requestInput.lastTranscript);
  expect(body.questions.session_action.type).toBe("choice");
  expect(body.questions.session_action.criteria.STRESS_NEXT).toBeTruthy();
  expect(body.questions.next_user_behavior.criteria.MULTI_ANSWER).toBeTruthy();
  expect(result).toMatchObject({
    called: true,
    provider: "typesafe",
    model: "jev-test",
    sessionAction: "STRESS_NEXT",
    nextUserBehavior: "MULTI_ANSWER",
    confidence: 0.91,
    cacheHit: false,
  });
  expect(result.callCount).toBe(guard.snapshot().jevCalls);
  expect(result.usage).toEqual({ input_tokens: 120, output_tokens: 12 });
  expect(guard.snapshot().jevCalls).toBe(result.callCount);
});

test("TypeSafe Jev caches the same state and re-evaluates a changed state", async () => {
  clearJevSupervisorCache();
  const testConfig = config({ maxJevCalls: 5 });
  const guard = new VoiceCostGuard(testConfig);
  guard.startSession();
  const requests = [];
  const supervisor = new JevSupervisor({
    config: testConfig,
    guard,
    sessionId: "typesafe-cache-session",
    typeSafeApiKey: "typesafe-test-key",
    typeSafeBaseUrl: "https://typesafe.test",
    typeSafeModel: "jev-test",
    fetchImpl: async (url, options) => {
      const body = JSON.parse(options.body);
      requests.push({ url, options, body });
      const changedState = body.state.interviewState?.stateVersion === 3;
      return {
        ok: true,
        status: 200,
        async json() {
          return {
            model: "jev-test",
            answers: {
              session_action: {
                type: "choice",
                choice: changedState ? "STRESS_NEXT" : "CONTINUE",
                confidence: 0.8,
                probabilities: changedState
                  ? { STRESS_NEXT: 0.8 }
                  : { CONTINUE: 0.8 },
              },
              next_user_behavior: {
                type: "choice",
                choice: changedState ? "SELF_CORRECT" : "NORMAL",
                confidence: 0.8,
                probabilities: changedState
                  ? { SELF_CORRECT: 0.8 }
                  : { NORMAL: 0.8 },
              },
            },
            usage: { input_tokens: 10, output_tokens: 2 },
          };
        },
      };
    },
  });
  const hardGate = { status: "PASS", shouldCallJev: true, codes: [] };
  const baseInput = supervisor.buildInput(input());
  const changedInput = supervisor.buildInput({
    ...input(),
    turn: 2,
    lastTranscript: "別の状態を表す回答です。",
    interviewState: { stateVersion: 3, currentQuestionId: "q-002" },
  });

  const first = await supervisor.decideAfterTurn({ hardGate, input: baseInput });
  const sameState = await supervisor.decideAfterTurn({ hardGate, input: baseInput });
  const changedState = await supervisor.decideAfterTurn({ hardGate, input: changedInput });

  expect(first.cacheHit).toBe(false);
  expect(sameState.cacheHit).toBe(true);
  expect(changedState.cacheHit).toBe(false);
  expect(first.sessionAction).toBe("CONTINUE");
  expect(changedState.sessionAction).toBe("STRESS_NEXT");
  expect(changedState.nextUserBehavior).toBe("SELF_CORRECT");
  expect(requests).toHaveLength(2);
  expect(requests[0].body.state.interviewState.stateVersion).toBe(2);
  expect(requests[1].body.state.interviewState.stateVersion).toBe(3);
  expect(requests[1].body.state.lastTranscript).toBe("別の状態を表す回答です。");
});
