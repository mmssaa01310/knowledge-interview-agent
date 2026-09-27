import fs from "node:fs";
import path from "node:path";

import { chromium, expect, test } from "@playwright/test";

import { readVoiceTestConfig, VoiceCostGuard } from "../config.mjs";
import {
  validateRealVoiceFixtureMetadata,
} from "../fixtures/realVoiceFixtureMetadata.mjs";
import {
  fetchInterviewContext,
  fetchInterviewState,
  resetVoiceDemo,
} from "../drivers/apiDriver.mjs";
import {
  buildFixtureCorrelationPreflight,
  partialAcceptancePlan,
  runRealVoiceRobustnessSession,
} from "./realVoiceRobustnessRunner.mjs";

const config = readVoiceTestConfig();
const ACCEPTANCE_ENABLED = [
  "1",
  "true",
  "yes",
  "on",
].includes(String(process.env.VOICE_TEST_PARTIAL_ACCEPTANCE ?? "").toLowerCase());

function fixtureKey(turn) {
  return `${String(turn.behavior).toUpperCase()}:${Number(turn.variant) || 0}`;
}

function decoratePlan(plan, recordsByKey) {
  return {
    ...plan,
    turns: plan.turns.map((turn) => ({
      ...turn,
      fixtureRecord: recordsByKey.get(fixtureKey(turn)) ?? null,
    })),
  };
}

function writePreflight(outputDir, sessionIndex, preflight) {
  const directory = path.join(outputDir, "acceptance", `session-${sessionIndex}`);
  fs.mkdirSync(directory, { recursive: true });
  const outputPath = path.join(directory, "fixture-correlation-preflight.json");
  fs.writeFileSync(outputPath, `${JSON.stringify(preflight, null, 2)}\n`, "utf8");
  for (const row of preflight.rows ?? []) {
    console.info("fixture_correlation_preflight", {
      session: sessionIndex,
      currentQuestionId: row.currentQuestionId,
      currentQuestion: row.currentQuestion,
      missingAspects: row.missingAspects,
      fixtureText: row.fixtureText,
      fixtureTargetField: row.fixtureTargetField,
    });
  }
  return outputPath;
}

function countApi5xx(snapshot) {
  return (snapshot.networkLog ?? []).filter((entry) => (
    entry.direction === "response" && Number(entry.status) >= 500
  )).length;
}

function acceptanceFailures(result) {
  const snapshot = result.telemetry?.snapshot?.() ?? {};
  const turns = snapshot.turns ?? [];
  const failures = [];
  const partial = turns.find((turn) => turn.behavior === "PARTIAL_ANSWER");
  const completion = turns.find((turn) => turn.behavior === "PARTIAL_COMPLETION");
  const recovery = turns.find((turn) => turn.behavior === "RECOVERY_NORMAL");

  if (result.status !== "PASS") failures.push(result.failureType ?? result.status ?? "SESSION_FAILED");
  if (turns.length < 4) failures.push("CONVERSATION_RECOVERY_NOT_OBSERVED");
  if (!partial) failures.push("PARTIAL_TURN_MISSING");
  if (!completion) failures.push("PARTIAL_COMPLETION_TURN_MISSING");
  if (!recovery) failures.push("NORMAL_RECOVERY_TURN_MISSING");

  if (partial) {
    const delegation = partial.delegation ?? {};
    const updates = partial.liveUpdates ?? {};
    const answerStatus = String(partial.interview?.answerStatus ?? "").toUpperCase();
    if (answerStatus !== "PARTIAL") failures.push("PARTIAL_STATUS_NOT_OBSERVED");
    if (!delegation.status || ![
      "NOT_OBSERVED",
      "OBSERVED_WITH_STATE_TOOL",
      "OBSERVED_WITHOUT_STATE_TOOL",
    ].includes(delegation.status)) {
      failures.push("DELEGATION_OBSERVATION_INVALID");
    }
    if (delegation.status === "OBSERVED_WITH_STATE_TOOL" && !partial.observed?.stateMutationOccurred) {
      failures.push("PARTIAL_STATE_MUTATION_MISSING");
    }
    if ((partial.response?.assistantResponseCount ?? 0) !== 1) {
      failures.push("PARTIAL_ASSISTANT_RESPONSE_COUNT_INVALID");
    }
    if (!partial.response?.liveOutputStarted || !partial.response?.assistantAudioStarted) {
      failures.push("PARTIAL_LIVE_RESPONSE_OR_AUDIO_MISSING");
    }
    if ((updates.interruptionBoundaryCount ?? 0) > 1) failures.push("DUPLICATE_INTERRUPTION_BOUNDARY");
    if ((updates.thinkingAppendCount ?? 0) > 1) failures.push("DUPLICATE_THINKING_APPEND");
    if ((updates.commentaryAppendCount ?? 0) > 1) failures.push("DUPLICATE_COMMENTARY_APPEND");
    if ((partial.response?.assistantResponseCount ?? 0) > 1) failures.push("DUPLICATE_ASSISTANT_RESPONSE");
    if (!(partial.fixtureCorrelation?.missingAspects ?? []).length) {
      failures.push("PARTIAL_MISSING_ASPECT_NOT_RETAINED");
    }
  }

  if (completion) {
    const answerStatus = String(completion.interview?.answerStatus ?? "").toUpperCase();
    if (![
      "ANSWERED",
      "CONFIRMED",
    ].includes(answerStatus)
      && !["ANSWERED", "CONFIRMED"].includes(String(completion.interview?.answerState ?? "").toUpperCase())) {
      failures.push("PARTIAL_COMPLETION_NOT_ACCEPTED");
    }
    if (!(Number(completion.interview?.stateVersionAfter) > Number(completion.interview?.stateVersionBefore))) {
      failures.push("PARTIAL_COMPLETION_STATE_VERSION_NOT_ADVANCED");
    }
  }

  if (recovery && (recovery.response?.assistantResponseCount ?? 0) !== 1) {
    failures.push("CONVERSATION_RECOVERY_RESPONSE_MISSING");
  }
  if (countApi5xx(snapshot) > 0) failures.push("API_5XX");
  if ((snapshot.dataChannelErrors ?? []).length > 0) failures.push("DATACHANNEL_ERROR");
  if ((snapshot.errors ?? []).some((error) => /websocket/i.test(error.message ?? ""))) {
    failures.push("WEBSOCKET_ERROR");
  }
  return [...new Set(failures)];
}

test.describe("GPT-Live PARTIAL real API acceptance", () => {
  test.describe.configure({ retries: 0 });

  test.skip(
    !config.realApiEnabled || !ACCEPTANCE_ENABLED,
    "Set VOICE_TEST_REAL_API_ENABLED=true and VOICE_TEST_PARTIAL_ACCEPTANCE=true explicitly.",
  );

  test("runs two bounded PARTIAL recovery sessions", async ({ request }, testInfo) => {
    const sessionCount = 2;
    const maxTurns = 5;
    const plans = partialAcceptancePlan({ sessions: sessionCount, maxTurns });
    const behaviorNames = [
      "NORMAL",
      "PARTIAL_ANSWER",
      "PARTIAL_COMPLETION",
      "RECOVERY_NORMAL",
    ];
    const records = validateRealVoiceFixtureMetadata({
      directory: config.audioFixtureDirectory,
      behaviors: behaviorNames,
    }).validated;
    const recordsByKey = new Map(records.map((record) => [
      `${record.behavior}:${record.variant}`,
      record,
    ]));
    const acceptanceConfig = Object.freeze({
      ...config,
      maxSessions: sessionCount,
      maxTurnsPerSession: maxTurns,
      maxRetries: 0,
      runMode: "acceptance",
    });
    const guard = new VoiceCostGuard(acceptanceConfig);

    // Reset/context/state are local application calls only. No Live session is
    // opened until every session's fixture correlation has passed this gate.
    await resetVoiceDemo(request);
    const context = await fetchInterviewContext(request);
    const state = await fetchInterviewState(request);
    const decoratedPlans = plans.map((plan) => decoratePlan(plan, recordsByKey));
    const preflights = decoratedPlans.map((plan, index) => {
      const preflight = buildFixtureCorrelationPreflight({
        context,
        state,
        plannedTurns: plan.turns,
        fixtureRecords: plan.turns.map((turn) => turn.fixtureRecord).filter(Boolean),
      });
      const outputPath = writePreflight(config.outputDir, index + 1, preflight);
      return { session: index + 1, outputPath, ...preflight };
    });
    fs.mkdirSync(config.outputDir, { recursive: true });
    fs.writeFileSync(
      path.join(config.outputDir, "acceptance", "fixture-correlation-preflight.json"),
      `${JSON.stringify(preflights, null, 2)}\n`,
      "utf8",
    );
    const preflightFailures = preflights.flatMap((preflight) => preflight.violations ?? []);
    if (preflightFailures.length) {
      throw new Error(`TEST_INFRA_FAILURE: fixture correlation preflight failed: ${preflightFailures.join("; ")}`);
    }

    guard.requireRealApiOptIn();
    test.setTimeout(900_000);
    const results = [];
    for (const [index, plan] of decoratedPlans.entries()) {
      guard.startSession();
      const result = await runRealVoiceRobustnessSession({
        browserType: chromium,
        request,
        testInfo,
        config: acceptanceConfig,
        guard,
        engine: "gpt_live",
        behavior: plan.behavior,
        variant: plan.variant,
        sessionIndex: index + 1,
        conversationTurns: plan.turns,
      });
      result.acceptanceFailures = acceptanceFailures(result);
      if (result.acceptanceFailures.length) {
        result.status = "FAIL";
        result.failureType = result.acceptanceFailures.some((failure) => failure === "TEST_INFRA_FAILURE")
          ? "TEST_INFRA_FAILURE"
          : result.failureType ?? "PRODUCT_FAILURE";
      }
      results.push(result);
      if (result.status !== "PASS") break;
    }

    const report = {
      acceptance: "GPT-Live PARTIAL recovery",
      sessions: results.map((result) => ({
        sessionId: result.sessionId,
        status: result.status,
        failureType: result.failureType ?? null,
        acceptanceFailures: result.acceptanceFailures ?? [],
        telemetry: result.telemetry?.snapshot?.() ?? null,
        evidenceWritten: Boolean(result.evidenceWritten),
      })),
      preflights,
      costGuard: guard.snapshot(),
    };
    const reportPath = path.join(config.outputDir, "acceptance", "report.json");
    fs.mkdirSync(path.dirname(reportPath), { recursive: true });
    fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");

    expect(
      results.filter((result) => result.status !== "PASS"),
      `GPT-Live PARTIAL acceptance failed. Evidence/report: ${reportPath}`,
    ).toEqual([]);
  });
});
