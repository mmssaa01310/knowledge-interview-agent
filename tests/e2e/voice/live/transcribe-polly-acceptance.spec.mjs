import fs from "node:fs";
import path from "node:path";

import { chromium, expect, test } from "@playwright/test";

import { readVoiceTestConfig, VoiceCostGuard } from "../config.mjs";
import { validateRealVoiceFixtureMetadata } from "../fixtures/realVoiceFixtureMetadata.mjs";
import {
  fetchInterviewContext,
  fetchInterviewState,
  resetVoiceDemo,
} from "../drivers/apiDriver.mjs";
import {
  buildFixtureCorrelationPreflight,
  runRealVoiceRobustnessSession,
} from "./realVoiceRobustnessRunner.mjs";

const config = readVoiceTestConfig();
const ACCEPTANCE_ENABLED = ["1", "true", "yes", "on"].includes(
  String(process.env.VOICE_TEST_TRANSCRIBE_POLLY_ACCEPTANCE ?? "").toLowerCase(),
);

const acceptancePlans = [
  {
    name: "partial-answer-completion-recovery",
    turns: [
      { behavior: "NORMAL", variant: 0 },
      { behavior: "PARTIAL_ANSWER", variant: 0 },
      { behavior: "PARTIAL_COMPLETION", variant: 0 },
      { behavior: "RECOVERY_NORMAL", variant: 0 },
    ],
  },
  {
    name: "clarification-then-completion",
    turns: [
      { behavior: "NORMAL", variant: 1 },
      { behavior: "ASK_CLARIFICATION", variant: 0 },
      { behavior: "PARTIAL_ANSWER", variant: 1 },
      { behavior: "PARTIAL_COMPLETION", variant: 1 },
    ],
  },
  {
    name: "barge-in-and-recovery",
    turns: [
      { behavior: "NORMAL", variant: 2 },
      { behavior: "PARTIAL_COMPLETION", variant: 2 },
      { behavior: "RECOVERY_NORMAL", variant: 2, interruptAssistant: true },
    ],
  },
];

function fixtureKey(turn) {
  return `${String(turn.behavior).toUpperCase()}:${Number(turn.variant) || 0}`;
}

function readJsonl(filePath) {
  if (!fs.existsSync(filePath)) return [];
  return fs.readFileSync(filePath, "utf8")
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

function matchingValues(text, pattern) {
  return [...text.matchAll(pattern)].map((match) => match[1]);
}

function acceptanceFailures(result, plan) {
  const snapshot = result.telemetry?.snapshot?.() ?? {};
  const turns = snapshot.turns ?? [];
  const failures = [];

  if (result.status !== "PASS") {
    failures.push(result.failureType ?? result.status ?? "SESSION_FAILED");
  }
  if (turns.length !== plan.turns.length) failures.push("PLANNED_TURN_COUNT_MISMATCH");

  for (const [index, planTurn] of plan.turns.entries()) {
    const turn = turns[index];
    if (!turn) continue;
    const behavior = String(planTurn.behavior).toUpperCase();
    const lifecycle = turn.interview?.questionLifecycle ?? {};
    const before = lifecycle.before ?? {};
    const after = lifecycle.after ?? {};
    const currentFieldBeforeId = turn.interview?.currentFieldBeforeId
      ?? before.fieldId
      ?? null;
    const currentFieldAfterId = turn.interview?.currentFieldAfterId
      ?? after.fieldId
      ?? null;
    const expectedFieldAfterId = turn.interview?.expectedFieldAfterId ?? null;

    if (!turn.transcription?.finalText?.trim()) {
      failures.push(`TURN_${index + 1}_FINAL_TRANSCRIPT_MISSING`);
    }
    if (Number(turn.transcription?.finalCount) !== 1) {
      failures.push(`TURN_${index + 1}_FINAL_TRANSCRIPT_COUNT_NOT_ONE`);
    }
    if (!(Number(turn.transcription?.partialCount) > 0)) {
      failures.push(`TURN_${index + 1}_PARTIAL_TRANSCRIPT_MISSING`);
    }
    if (behavior === "ASK_CLARIFICATION") {
      if (turn.observed?.stateMutationOccurred) {
        failures.push(`TURN_${index + 1}_CLARIFICATION_CHANGED_ANSWER_STATE`);
      }
      if (currentFieldBeforeId !== currentFieldAfterId) {
        failures.push(`TURN_${index + 1}_CLARIFICATION_ADVANCED_QUESTION`);
      }
      if (!(Number(turn.observed?.assistantResponseCount) > 0)) {
        failures.push(`TURN_${index + 1}_CLARIFICATION_RESPONSE_MISSING`);
      }
      continue;
    }

    if (turn.interview?.currentFieldBeforeId == null) {
      failures.push(`TURN_${index + 1}_CURRENT_QUESTION_FIELD_MISSING`);
    }
    if (expectedFieldAfterId != null && currentFieldAfterId !== expectedFieldAfterId) {
      failures.push(`TURN_${index + 1}_EXPECTED_QUESTION_FIELD_NOT_SELECTED`);
    }
    if (behavior === "PARTIAL_ANSWER") {
      if (currentFieldBeforeId !== currentFieldAfterId) {
        failures.push(`TURN_${index + 1}_PARTIAL_ANSWER_ADVANCED_QUESTION`);
      }
      if (!turn.interview?.candidate?.trim() || turn.observed?.answerState === "CONFIRMED") {
        failures.push(`TURN_${index + 1}_PARTIAL_CANDIDATE_NOT_PERSISTED`);
      }
    }
    if (behavior === "PARTIAL_COMPLETION") {
      const answerState = String(turn.interview?.answerState ?? "").toUpperCase();
      const awaitingExplicitConfirmation = answerState === "AWAITING_CONFIRMATION"
        && currentFieldBeforeId === currentFieldAfterId
        && Boolean(turn.interview?.candidate?.trim());
      if (![
        "CONFIRMED",
        "ANSWERED",
      ].includes(answerState) && !awaitingExplicitConfirmation) {
        failures.push(`TURN_${index + 1}_PARTIAL_COMPLETION_NOT_CONFIRMED`);
      }
    }
    if (!(Number(turn.observed?.assistantResponseCount) > 0)) {
      failures.push(`TURN_${index + 1}_ASSISTANT_RESPONSE_MISSING`);
    }
  }

  if (snapshot.errors?.length) failures.push("RUNTIME_ERRORS_OBSERVED");
  if (snapshot.dataChannelErrors?.length) failures.push("DATACHANNEL_ERRORS_OBSERVED");
  if ((result.networkErrors ?? []).length) failures.push("NETWORK_ERRORS_OBSERVED");
  if (plan.turns.some((turn) => turn.interruptAssistant)
    && !snapshot.timeline?.some((item) => (
      item.event === "real_barge_in_observed"
      && item.observed === true
      && item.inputResumed === true
    ))) {
    failures.push("BARGE_IN_OR_INPUT_RESUME_NOT_OBSERVED");
  }
  return [...new Set(failures)];
}

function writeReport(reportPath, report) {
  fs.mkdirSync(path.dirname(reportPath), { recursive: true });
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
}

test.describe("Transcribe + Polly real AWS acceptance", () => {
  test.describe.configure({ retries: 0 });
  test.skip(
    !config.realApiEnabled
      || config.realProvider !== "transcribe_polly"
      || !ACCEPTANCE_ENABLED,
    "Run through pnpm voice:acceptance:transcribe-polly to enable the complete real AWS acceptance.",
  );

  test("accepts real Transcribe, API state updates, Polly playback and recovery", async ({ request }, testInfo) => {
    const fixtureBehaviors = [
      "NORMAL",
      "PARTIAL_ANSWER",
      "PARTIAL_COMPLETION",
      "RECOVERY_NORMAL",
      "ASK_CLARIFICATION",
    ];
    const records = validateRealVoiceFixtureMetadata({
      directory: config.audioFixtureDirectory,
      behaviors: fixtureBehaviors,
    }).validated;
    const recordsByKey = new Map(records.map((record) => [
      `${record.behavior}:${record.variant}`,
      record,
    ]));
    const plans = acceptancePlans.map((plan) => ({
      ...plan,
      turns: plan.turns.map((turn) => ({
        ...turn,
        fixtureRecord: recordsByKey.get(fixtureKey(turn)) ?? null,
      })),
    }));
    const expectedTurnCount = plans.reduce((sum, plan) => sum + plan.turns.length, 0);
    const acceptanceConfig = Object.freeze({
      ...config,
      maxSessions: plans.length,
      maxTurnsPerSession: Math.max(...plans.map((plan) => plan.turns.length)),
      maxRetries: 0,
      runMode: "acceptance",
    });
    const guard = new VoiceCostGuard(acceptanceConfig);
    const reportPath = path.join(config.outputDir, "acceptance", "transcribe-polly-report.json");
    const sessions = [];

    await resetVoiceDemo(request);
    const context = await fetchInterviewContext(request);
    const state = await fetchInterviewState(request);
    const preflights = plans.map((plan, index) => {
      // ASK_CLARIFICATION intentionally has no answer-target fixture metadata.
      // Omitting this no-state turn preserves the following answer target in
      // the question/fixture correlation check.
      const answerTurns = plan.turns.filter((turn) => turn.fixtureRecord?.targetField);
      const preflight = buildFixtureCorrelationPreflight({
        context,
        state,
        plannedTurns: answerTurns,
        fixtureRecords: answerTurns.map((turn) => turn.fixtureRecord),
      });
      const outputPath = path.join(
        config.outputDir,
        "acceptance",
        `session-${index + 1}`,
        "fixture-correlation-preflight.json",
      );
      writeReport(outputPath, preflight);
      return { session: index + 1, plan: plan.name, outputPath, ...preflight };
    });
    const preflightFailures = preflights.flatMap((preflight) => preflight.violations ?? []);
    if (preflightFailures.length) {
      const report = {
        acceptance: "Transcribe + Polly real AWS",
        status: "FAIL",
        failureType: "TEST_INFRA_FAILURE",
        preflightFailures,
        preflights,
      };
      writeReport(reportPath, report);
      throw new Error(`Fixture correlation preflight failed: ${preflightFailures.join("; ")}`);
    }

    guard.requireRealApiOptIn();
    test.setTimeout(900_000);
    for (const [index, plan] of plans.entries()) {
      guard.startSession();
      const result = await runRealVoiceRobustnessSession({
        browserType: chromium,
        request,
        testInfo,
        config: acceptanceConfig,
        guard,
        engine: "transcribe_polly",
        behavior: plan.turns[0].behavior,
        variant: plan.turns[0].variant,
        sessionIndex: index + 1,
        conversationTurns: plan.turns,
        strictTranscribePlayback: true,
      });
      result.acceptanceFailures = acceptanceFailures(result, plan);
      if (result.acceptanceFailures.length) {
        result.status = "FAIL";
        result.failureType = result.failureType ?? "PRODUCT_FAILURE";
      }
      sessions.push({
        plan: plan.name,
        status: result.status,
        failureType: result.failureType ?? null,
        acceptanceFailures: result.acceptanceFailures,
        telemetry: result.telemetry?.snapshot?.() ?? null,
        evidenceWritten: Boolean(result.evidenceWritten),
      });
    }

    const apiLogPath = path.join(config.outputDir, "api-service.log");
    const voiceLogPath = path.join(config.outputDir, "voice-service.log");
    const apiLog = fs.existsSync(apiLogPath) ? fs.readFileSync(apiLogPath, "utf8") : "";
    const voiceLog = fs.existsSync(voiceLogPath) ? fs.readFileSync(voiceLogPath, "utf8") : "";
    const createdTurnEvents = [...apiLog.matchAll(
      /voice_turn_created[^\r\n]*\bvoice_session_id=([^\s]+)[^\r\n]*\bturn_id=([^\s]+)[^\r\n]*\bclient_turn_id=([^\s]+)[^\r\n]*\bsequence=(\d+)/g,
    )].map((match) => ({
      voiceSessionId: match[1],
      turnId: match[2],
      clientTurnId: match[3],
      sequence: Number(match[4]),
    }));
    const createdTurnIds = createdTurnEvents.map((turn) => turn.clientTurnId);
    const committedTurnIds = matchingValues(
      voiceLog,
      /transcribe_turn_telemetry event=user_turn_committed[^\r\n]*\bclient_turn_id=([^\s]+)/g,
    );
    const claimedTurnIds = matchingValues(
      apiLog,
      /voice_turn_processing_claimed[^\r\n]*\bturn_id=([^\s]+)/g,
    );
    const reusedTurnIds = matchingValues(
      apiLog,
      /voice_turn_reused(?:_after_unique_conflict)?[^\r\n]*\bclient_turn_id=([^\s]+)/g,
    );
    const answerStateUpdateTurnIds = matchingValues(
      apiLog,
      /interview_answer_state_committed[^\r\n]*\bsource_turn_id=([^\s]+)/g,
    );
    const apiSessionIdsInPlanOrder = [...new Set(
      createdTurnEvents
        .filter((turn) => turn.sequence === 1)
        .map((turn) => turn.voiceSessionId),
    )];
    const sessionIndexByVoiceSessionId = new Map(
      apiSessionIdsInPlanOrder.map((voiceSessionId, index) => [voiceSessionId, index]),
    );
    const answerStateUpdateCountsByTurnId = new Map();
    for (const turnId of answerStateUpdateTurnIds) {
      answerStateUpdateCountsByTurnId.set(
        turnId,
        (answerStateUpdateCountsByTurnId.get(turnId) ?? 0) + 1,
      );
    }
    const answerStateUpdatesByTurn = createdTurnEvents.map((turn) => {
      const sessionIndex = sessionIndexByVoiceSessionId.get(turn.voiceSessionId) ?? -1;
      const behavior = plans[sessionIndex]?.turns[turn.sequence - 1]?.behavior ?? null;
      return {
        voiceSessionId: turn.voiceSessionId,
        turnId: turn.turnId,
        clientTurnId: turn.clientTurnId,
        sequence: turn.sequence,
        behavior,
        expectedCount: behavior === null ? null : behavior === "ASK_CLARIFICATION" ? 0 : 1,
        observedCount: answerStateUpdateCountsByTurnId.get(turn.turnId) ?? 0,
      };
    });
    const reconnectCount = (voiceLog.match(/transcribe_reconnecting\b/g) ?? []).length;
    const forcedReconnectCount = (voiceLog.match(/transcribe_test_reconnect_forced\b/g) ?? []).length;
    const pollyTextChunks = readJsonl(path.join(config.outputDir, "polly-text-chunks.jsonl"));
    const pollyAwsRequests = readJsonl(path.join(config.outputDir, "polly-aws-requests.jsonl"));
    const expectedCommittedIds = new Set(committedTurnIds);
    const uniqueCreatedIds = new Set(createdTurnIds);
    const stateAndPersistenceFailures = [];
    const expectedAnswerStateUpdateCount = plans.reduce(
      (sum, plan) => sum + plan.turns.filter((turn) => turn.behavior !== "ASK_CLARIFICATION").length,
      0,
    );

    if (sessions.some((session) => session.status !== "PASS")) {
      stateAndPersistenceFailures.push("SESSION_ACCEPTANCE_FAILED");
    }
    if (createdTurnIds.length !== expectedTurnCount) {
      stateAndPersistenceFailures.push("VOICE_TURN_CREATE_COUNT_MISMATCH");
    }
    if (uniqueCreatedIds.size !== createdTurnIds.length) {
      stateAndPersistenceFailures.push("DUPLICATE_VOICE_TURN_CREATION");
    }
    if (committedTurnIds.length !== expectedTurnCount) {
      stateAndPersistenceFailures.push("TRANSCRIBE_COMMIT_COUNT_MISMATCH");
    }
    if (expectedCommittedIds.size !== committedTurnIds.length) {
      stateAndPersistenceFailures.push("DUPLICATE_TRANSCRIBE_COMMIT");
    }
    if (createdTurnIds.some((turnId) => !expectedCommittedIds.has(turnId))) {
      stateAndPersistenceFailures.push("COMMITTED_AND_PERSISTED_TURN_IDS_DIFFER");
    }
    if (claimedTurnIds.length !== expectedTurnCount) {
      stateAndPersistenceFailures.push("VOICE_TURN_PROCESSING_CLAIM_COUNT_MISMATCH");
    }
    if (new Set(claimedTurnIds).size !== claimedTurnIds.length) {
      stateAndPersistenceFailures.push("DUPLICATE_VOICE_TURN_PROCESSING_CLAIM");
    }
    if (answerStateUpdateTurnIds.length !== expectedAnswerStateUpdateCount) {
      stateAndPersistenceFailures.push("ANSWER_STATE_UPDATE_COUNT_MISMATCH");
    }
    if (new Set(answerStateUpdateTurnIds).size !== answerStateUpdateTurnIds.length) {
      stateAndPersistenceFailures.push("DUPLICATE_ANSWER_STATE_UPDATE");
    }
    for (const answerStateUpdate of answerStateUpdatesByTurn) {
      if (answerStateUpdate.expectedCount === null) continue;
      if (answerStateUpdate.observedCount > answerStateUpdate.expectedCount) {
        stateAndPersistenceFailures.push(
          answerStateUpdate.behavior === "ASK_CLARIFICATION"
            ? "CLARIFICATION_MUTATED_ANSWER_STATE"
            : "DUPLICATE_ANSWER_STATE_UPDATE",
        );
      } else if (answerStateUpdate.observedCount < answerStateUpdate.expectedCount) {
        stateAndPersistenceFailures.push(
          answerStateUpdate.behavior === "ASK_CLARIFICATION"
            ? "CLARIFICATION_MUTATED_ANSWER_STATE"
            : "ANSWER_STATE_UPDATE_MISSING",
        );
      }
    }
    if (reusedTurnIds.length > 0) {
      stateAndPersistenceFailures.push("DUPLICATE_VOICE_TURN_REUSE_OBSERVED");
    }
    if (reconnectCount < plans.length || forcedReconnectCount < plans.length) {
      stateAndPersistenceFailures.push("TRANSCRIBE_RECONNECT_NOT_OBSERVED_IN_EACH_SESSION");
    }
    if (!pollyTextChunks.length || !pollyAwsRequests.length) {
      stateAndPersistenceFailures.push("POLLY_TEXT_CHUNK_OR_AWS_REQUEST_CAPTURE_MISSING");
    }

    const report = {
      acceptance: "Transcribe + Polly real AWS",
      status: stateAndPersistenceFailures.length ? "FAIL" : "PASS",
      startedAt: sessions[0]?.telemetry?.timeline?.[0]?.timestampMs ?? null,
      completedAt: new Date().toISOString(),
      provider: "transcribe_polly",
      plans: plans.map(({ name, turns }) => ({
        name,
        turns: turns.map(({ behavior, variant, interruptAssistant = false }) => ({
          behavior,
          variant,
          interruptAssistant,
        })),
      })),
      expectedTurnCount,
      sessions,
      preflights,
      turnLifecycle: {
        createdCount: createdTurnIds.length,
        uniqueCreatedCount: uniqueCreatedIds.size,
        committedCount: committedTurnIds.length,
        uniqueCommittedCount: expectedCommittedIds.size,
        processingClaimCount: claimedTurnIds.length,
        reusedCount: reusedTurnIds.length,
        createdTurnIds,
        committedTurnIds,
        reusedTurnIds,
        expectedAnswerStateUpdateCount,
        answerStateUpdateCount: answerStateUpdateTurnIds.length,
        uniqueAnswerStateUpdateCount: new Set(answerStateUpdateTurnIds).size,
        answerStateUpdatesByTurn,
        reconnectCount,
        forcedReconnectCount,
      },
      polly: {
        runtimeChunkCount: pollyTextChunks.length,
        awsRequestCount: pollyAwsRequests.length,
        runtimeChunks: pollyTextChunks,
        awsRequests: pollyAwsRequests,
        runtimeChunkLog: path.join(config.outputDir, "polly-text-chunks.jsonl"),
        awsRequestLog: path.join(config.outputDir, "polly-aws-requests.jsonl"),
      },
      stateAndPersistenceFailures,
      costGuard: guard.snapshot(),
    };
    writeReport(reportPath, report);

    expect(
      stateAndPersistenceFailures,
      `Transcribe + Polly Acceptance evidence: ${reportPath}`,
    ).toEqual([]);
  });
});
