import { test, expect } from "@playwright/test";
import { readVoiceTestConfig, VoiceCostGuard } from "./config.mjs";
import {
  chooseNextAction,
  deterministicSeed,
  resolveScenario,
  transcriptForAction,
} from "./scenarios/scenarios.mjs";
import {
  applyLiveAssessment,
  buildLiveAssessment,
  fetchInterviewContext,
  fetchInterviewState,
  resetVoiceDemo,
} from "./drivers/apiDriver.mjs";
import {
  ANOMALIES,
  detectTurnAnomalies,
  assertNoAnomalies,
} from "./assertions/invariants.mjs";
import {
  VoiceTelemetry,
  writeEvidencePackage,
} from "./telemetry/telemetry.mjs";

const config = readVoiceTestConfig();

test.describe("voice E2E level 1: local state contract", () => {
  test("selected scenario persists final answers and keeps the canonical state durable", async ({
    request,
  }, testInfo) => {
    const guard = new VoiceCostGuard(config);
    for (let retry = 0; retry < testInfo.retry; retry += 1) guard.recordRetry();
    guard.startSession();
    const scenario = resolveScenario(config.scenario);
    const seed = deterministicSeed(config.seed);
    const telemetry = new VoiceTelemetry({
      outputDir: testInfo.outputPath("telemetry"),
      sessionId: "local-" + seed,
      scenario: scenario.name,
      seed,
      costGuard: guard,
    });
    let state = null;
    try {
      await resetVoiceDemo(request);
      const context = await fetchInterviewContext(request);
      const fields = [...context.fields].sort(
        (left, right) => left.displayOrder - right.displayOrder,
      );
      expect(fields.length).toBeGreaterThan(0);
      let previousQuestionId = null;

      for (let index = 0; index < fields.length; index += 1) {
        const field = fields[index];
        guard.startTurn(1);
        state = await fetchInterviewState(request);
        const before = state.interviewState;
        const action = await chooseNextAction({
          scenario,
          turn: index,
          state: before,
          config,
          guard,
        });
        const transcript = transcriptForAction(action, scenario, index)
          || "テスト回答 " + (index + 1);
        const payload = buildLiveAssessment({
          field,
          transcript,
          stateVersion: before.stateVersion,
          turn: index + 1,
        });
        telemetry.event("user_speech_end", { turn: index + 1 });
        telemetry.event("transcript_final", {
          turn: index + 1,
          textLength: transcript.length,
        });
        telemetry.event("delegation_created", {
          turn: index + 1,
          delegationId: payload.delegation_id,
        });
        telemetry.event("function_tool_call", {
          turn: index + 1,
          callId: payload.call_id,
        });
        const result = await applyLiveAssessment(
          request,
          "dev-voice-demo-record",
          payload,
        );
        state = await fetchInterviewState(request);
        const after = state.interviewState;
        telemetry.event("backend_state_persisted", {
          turn: index + 1,
          stateVersion: after.stateVersion,
        });
        telemetry.event("delegation_completed", { turn: index + 1 });
        telemetry.event("response_start", { turn: index + 1 });
        telemetry.event("first_audio", { turn: index + 1, latencyMs: 0 });
        telemetry.event("response_done", { turn: index + 1 });
        const answer = after.fieldStates?.[field.id] ?? {};
        const anomalies = detectTurnAnomalies({
          finalText: transcript,
          state: after,
          fieldId: field.id,
          expectedQuestionId: payload.question_id,
          previousQuestionId,
          responseIds: ["response-" + (index + 1)],
          responseStarted: true,
        });
        assertNoAnomalies(anomalies);
        expect(result.status).toBe("applied");
        expect(Number(after.stateVersion)).toBeGreaterThan(Number(before.stateVersion));
        expect(after.currentQuestionId).toBe(payload.question_id);
        expect(answer.answerState).toBe("CONFIRMED");
        expect(answer.recordAnswer || answer.candidateAnswer).toBeTruthy();
        telemetry.recordTurn({
          turn: index + 1,
          audio: { startMs: 0, endMs: 1000, durationMs: 1000 },
          transcription: { partialCount: 1, finalText: transcript },
          interview: {
            questionId: payload.question_id,
            candidate: answer.candidateAnswer,
            confirmed: answer.recordAnswer,
            stateVersionBefore: before.stateVersion,
            stateVersionAfter: after.stateVersion,
          },
          response: { firstAudioLatencyMs: 0 },
          anomalies,
        });
        previousQuestionId = payload.question_id;
      }
      expect(state.interviewState.fieldStates).toBeTruthy();
    } catch (error) {
      telemetry.error(error);
      await writeEvidencePackage({ testInfo, telemetry, state });
      throw error;
    }
  });

  test("deterministically reports ANSWER_NOT_PERSISTED", async () => {
    const anomalies = detectTurnAnomalies({
      finalText: "文字起こしは取得できた回答",
      state: { fieldStates: { field: { answerState: "UNANSWERED" } } },
      fieldId: "field",
      expectedQuestionId: "question",
      responseStarted: true,
    });
    expect(anomalies).toContain(ANOMALIES.ANSWER_NOT_PERSISTED);
  });
});
