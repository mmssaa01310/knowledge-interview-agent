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
  realBehaviorPlan,
  runRealVoiceRobustnessSession,
  robustnessReplayCommand,
} from "./realVoiceRobustnessRunner.mjs";

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
    const plan = realBehaviorPlan({
      engine: config.realProvider,
      behaviors: behaviorList,
      sessions: sessionLimit,
    });
    if (!plan.length) {
      test.skip("No real voice behavior fixture is selected.");
    }
    if (!config.audioFixtureDirectory && !config.audioFixturePath) {
      test.skip(
        "Real robustness requires VOICE_TEST_AUDIO_FIXTURE_DIR or VOICE_TEST_AUDIO_FIXTURE.",
      );
    }

    test.setTimeout(Math.max(180_000, plan.length * integerEnv("VOICE_TEST_REAL_SESSION_TIMEOUT_MS", 90_000)));
    const guardConfig = Object.freeze({
      ...config,
      maxSessions: sessionLimit,
      maxRetries: 0,
    });
    const guard = new VoiceCostGuard(guardConfig);
    guard.requireRealApiOptIn();
    const startedAt = new Date().toISOString();
    const results = [];

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
      });
      results.push(result);
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
        maxTurnsPerSession: config.maxTurnsPerSession,
        maxAudioSecondsPerSession: config.maxAudioSecondsPerSession,
      },
      selectedBehaviors: behaviorList,
      sessions,
      totals: {
        sessions: sessions.length,
        turns: turns.length,
        failures: failures.length,
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
