import fs from "node:fs";
import path from "node:path";

import { chromium, test, expect } from "@playwright/test";

import {
  readVoiceTestConfig,
  VoiceCostGuard,
} from "../config.mjs";
import {
  REAL_VOICE_BEHAVIORS_BY_ENGINE,
} from "../scenarios/scenarios.mjs";
import {
  validateRealVoiceFixtureMetadata,
} from "../fixtures/realVoiceFixtureMetadata.mjs";
import {
  realConversationPlan,
  runRealVoiceRobustnessSession,
  robustnessReplayCommand,
} from "./realVoiceRobustnessRunner.mjs";
import {
  createResponsesJudgeEvaluator,
  runSemanticJudge,
} from "../telemetry/semanticJudge.mjs";

const config = readVoiceTestConfig();

function integerEnv(name, fallback) {
  const value = Number.parseInt(String(process.env[name] ?? ""), 10);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function selectedBehaviors(engine) {
  const configured = config.robustnessBehaviors;
  if (configured.length) return configured;
  return REAL_VOICE_BEHAVIORS_BY_ENGINE[engine] ?? [];
}

function sessionRecord(result) {
  const snapshot = result.telemetry?.snapshot?.() ?? result.telemetry ?? {};
  return {
    sessionId: result.sessionId,
    index: result.index,
    level: 3,
    engine: result.engine,
    provider: result.provider,
    liveApiUsed: true,
    liveModel: snapshot.liveModel ?? null,
    responsesModels: snapshot.responsesModels ?? [],
    semanticJudge: snapshot.semanticJudge ?? result.semanticJudge ?? null,
    failureType: result.failureType ?? null,
    seed: config.seed,
    scenario: result.behavior,
    behavior: result.behavior,
    variant: result.variant,
    harnessGate: result.turn?.harnessGate ?? null,
    failureAnalysis: result.turn?.failureAnalysis ?? null,
    status: result.status,
    error: result.error ?? null,
    fixturePath: result.fixturePath,
    evidenceWritten: Boolean(result.evidenceWritten),
    turns: snapshot.turns ?? (result.turn ? [result.turn] : []),
    errors: snapshot.errors ?? [],
    networkErrors: result.networkErrors ?? [],
  };
}

async function evaluateSessionQuality(result, { guard, config }) {
  if (!config.llmJudgeEnabled) {
    return { status: "skipped", reason: "llm_judge_disabled" };
  }
  const snapshot = result.telemetry?.snapshot?.() ?? {};
  const deterministicFailure = (snapshot.turns ?? []).some((turn) => (
    [
      "TEST_INFRA_FAILURE",
      "API_5XX",
      "WEBSOCKET_ERROR",
      "DATACHANNEL_ERROR",
      "AUDIO_NOT_SENT",
      "TRANSCRIPT_FINAL_MISSING",
    ].some((code) => (
      turn.hardGate?.codes?.includes(code)
      || turn.failureAnalysis?.rootFailure === code
    ))
  ));
  if (deterministicFailure) {
    return { status: "skipped", reason: "deterministic_failure_already_classified" };
  }
  const evaluator = createResponsesJudgeEvaluator({ model: config.llmJudgeModel });
  const input = {
    sessionId: result.sessionId,
    engine: result.engine,
    behavior: result.behavior,
    seed: config.seed,
    turns: (snapshot.turns ?? []).map((turn) => ({
      turn: turn.turn,
      behavior: turn.behavior,
      question: turn.interview?.currentQuestionText ?? null,
      userTranscript: turn.transcription?.finalText ?? "",
      assistantResponse: turn.response?.assistantTranscript ?? null,
      answerState: turn.interview?.answerState ?? null,
      candidate: turn.interview?.candidate ?? null,
      confirmed: turn.interview?.confirmed ?? null,
      stateVersionAdvanced: turn.observed?.stateVersionAdvanced ?? null,
      questionAdvanced: turn.observed?.questionAdvanced ?? null,
    })),
  };
  try {
    const judged = await runSemanticJudge({
      enabled: true,
      anomalyDetected: false,
      guard,
      evaluator,
      input,
    });
    return {
      ...judged,
      called: judged.status === "evaluated",
      model: config.llmJudgeModel,
    };
  } catch (error) {
    return {
      status: "error",
      called: true,
      model: config.llmJudgeModel,
      reason: error instanceof Error ? error.message : String(error),
    };
  }
}

test.describe("voice robustness: real GPT-Live / Transcribe + Polly", () => {
  test.describe.configure({ retries: 0 });

  test.skip(
    !config.realApiEnabled || !config.robustnessEnabled,
    "Set VOICE_TEST_REAL_API_ENABLED=true and VOICE_TEST_ROBUSTNESS=true explicitly.",
  );

  test("runs the bounded real-provider behavior matrix", async ({ request }, testInfo) => {
    const requestedSessions = integerEnv(
      "VOICE_TEST_ROBUSTNESS_SESSIONS",
      config.maxSessions,
    );
    const sessionLimit = Math.min(requestedSessions, config.maxSessions);
    const behaviorList = selectedBehaviors(config.realProvider);
    const maxTurnsPerSession = integerEnv(
      "VOICE_TEST_MAX_TURNS_PER_SESSION",
      Math.max(7, config.maxTurnsPerSession),
    );
    const plan = realConversationPlan({
      engine: config.realProvider,
      behaviors: behaviorList,
      sessions: sessionLimit,
      maxTurns: maxTurnsPerSession,
    });
    if (!plan.length) {
      test.skip("No real voice behavior fixture is selected.");
    }
    if (!config.audioFixtureDirectory && !config.audioFixturePath) {
      test.skip(
        "Real robustness requires VOICE_TEST_AUDIO_FIXTURE_DIR or VOICE_TEST_AUDIO_FIXTURE.",
      );
    }
    const metadataBehaviors = behaviorList.filter((behavior) => (
      ["NORMAL", "PARTIAL_ANSWER"].includes(behavior)
    ));
    if (metadataBehaviors.length) {
      validateRealVoiceFixtureMetadata({
        directory: config.audioFixtureDirectory,
        behaviors: metadataBehaviors,
      });
    }

    test.setTimeout(Math.max(180_000, plan.length * integerEnv("VOICE_TEST_REAL_SESSION_TIMEOUT_MS", 90_000)));
    const guardConfig = Object.freeze({
      ...config,
      maxSessions: sessionLimit,
      maxTurnsPerSession,
      maxRetries: 0,
    });
    const guard = new VoiceCostGuard(guardConfig);
    guard.requireRealApiOptIn();
    const startedAt = new Date().toISOString();
    const results = [];

    let stoppedEarly = false;
    for (const item of plan) {
      guard.startSession();
      const result = await runRealVoiceRobustnessSession({
        browserType: chromium,
        request,
        testInfo,
        config: guardConfig,
        guard,
        engine: config.realProvider,
        behavior: item.behavior,
        variant: item.variant,
        sessionIndex: item.ordinal,
        conversationTurns: item.turns,
      });
      const semanticJudge = await evaluateSessionQuality(result, { guard, config });
      result.semanticJudge = semanticJudge;
      result.telemetry?.setSemanticJudge?.(semanticJudge);
      if (semanticJudge.status === "evaluated"
        && String(semanticJudge.result?.overall ?? "").toUpperCase() === "FAIL"
        && result.status === "PASS") {
        result.status = "FAIL";
        result.failureType = "CONVERSATION_QUALITY_FAILURE";
      } else if (semanticJudge.status === "error" && result.status === "PASS") {
        result.status = "FAIL";
        result.failureType = "TEST_INFRA_FAILURE";
      }
      if (result.telemetry?.outputDir) {
        fs.writeFileSync(
          path.join(result.telemetry.outputDir, "telemetry.json"),
          JSON.stringify(result.telemetry.snapshot(), null, 2),
          "utf8",
        );
      }
      results.push(result);
      if (result.status !== "PASS"
        && String(process.env.VOICE_TEST_STOP_ON_MAJOR_FAILURE ?? "true").toLowerCase() !== "false") {
        stoppedEarly = true;
        break;
      }
    }

    const sessions = results.map(sessionRecord);
    const turns = sessions.flatMap((session) => session.turns ?? []);
    const failures = sessions.filter((session) => session.status !== "PASS");
    const summary = {
      runId: config.runId,
      startedAt,
      completedAt: new Date().toISOString(),
      level: 3,
      runMode: config.runMode,
      engine: config.realProvider,
      provider: config.realProvider,
      liveApiUsed: true,
      seed: config.seed,
      limits: {
        maxSessions: sessionLimit,
        maxTurnsPerSession,
        maxAudioSecondsPerSession: config.maxAudioSecondsPerSession,
      },
      selectedBehaviors: behaviorList,
      plannedSessions: plan,
      stoppedEarly,
      sessions,
      totals: {
        sessions: sessions.length,
        turns: turns.length,
        failures: failures.length,
        llmJudgeCalls: guard.snapshot().llmJudgeCalls,
        responsesModels: [...new Set(sessions.flatMap((session) => session.responsesModels ?? []))],
        liveModels: [...new Set(sessions.map((session) => session.liveModel).filter(Boolean))],
        api5xx: turns.filter((turn) => (turn.hardGate?.codes ?? []).includes("API_5XX")).length,
        websocketErrors: turns.filter((turn) => (turn.hardGate?.codes ?? []).includes("WEBSOCKET_ERROR")).length,
        dataChannelErrors: turns.filter((turn) => (turn.hardGate?.codes ?? []).includes("DATACHANNEL_ERROR")).length,
      },
      runValidity: config.runMode === "robustness"
        && config.maxTurnsPerSession > 1
        && sessions.length > 0
        && sessions.length === turns.length
        ? {
            status: "ROBUSTNESS_RUN_INVALID",
            reason: "複数ターン設定なのに、1セッションあたり1ターンしか実行されていません。",
          }
        : { status: "VALID" },
      costGuard: guard.snapshot(),
      replay: robustnessReplayCommand({
        config: guardConfig,
        engine: config.realProvider,
        behavior: behaviorList[0] ?? "NORMAL",
      }),
    };
    fs.mkdirSync(config.outputDir, { recursive: true });
    fs.writeFileSync(
      path.join(config.outputDir, "summary.json"),
      JSON.stringify(summary, null, 2),
      "utf8",
    );

    expect(
      failures,
      `実音声ロバストネスの失敗は ${config.outputDir} に証拠を保存しました。`,
    ).toEqual([]);
  });
});
