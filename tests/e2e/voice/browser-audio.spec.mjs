import { test, expect } from "@playwright/test";
import { readVoiceTestConfig, VoiceCostGuard } from "./config.mjs";
import {
  chooseNextAction,
  deterministicSeed,
  resolveScenario,
  transcriptForAction,
} from "./scenarios/scenarios.mjs";
import {
  buildLiveAssessment,
  fetchInterviewContext,
  fetchInterviewState,
  resetVoiceDemo,
} from "./drivers/apiDriver.mjs";
import {
  installFakeLiveTransport,
  readFakeLiveEvents,
} from "./drivers/fakeLiveTransport.mjs";
import {
  installLocalApiRoutes,
  observePageNetwork,
} from "./drivers/appRoutes.mjs";
import {
  detectTurnAnomalies,
  assertNoAnomalies,
} from "./assertions/invariants.mjs";
import {
  VoiceTelemetry,
  writeEvidencePackage,
} from "./telemetry/telemetry.mjs";

const config = readVoiceTestConfig();
const recordUrl = "/knowledge-dbs/dev-voice-demo-db/knowledges/"
  + "dev-voice-demo-knowledge/records/dev-voice-demo-record";

test.describe("voice E2E level 2: browser + audio fixture", () => {
  test.skip(
    !config.runBrowserAudio,
    "Set VOICE_TEST_RUN_BROWSER_AUDIO=true to run browser audio tests",
  );

  test("selected scenario streams a fixture mic, receives finals, and persists state", async ({
    page,
    request,
  }, testInfo) => {
    const guard = new VoiceCostGuard(config);
    for (let retry = 0; retry < testInfo.retry; retry += 1) guard.recordRetry();
    guard.startSession();
    const seed = deterministicSeed(config.seed);
    const scenario = resolveScenario(config.scenario);
    const telemetry = new VoiceTelemetry({
      outputDir: testInfo.outputPath("telemetry"),
      sessionId: "browser-" + seed,
      scenario: scenario.name,
      seed,
      costGuard: guard,
    });
    const apiEvents = [];
    const networkErrors = observePageNetwork(page, telemetry);
    let state = null;
    try {
      await resetVoiceDemo(request);
      await installFakeLiveTransport(page);
      await installLocalApiRoutes(page, { apiEvents });
      await page.addInitScript(() => {
        window.localStorage.setItem("ai-interviewer-dev-token", "dev-manager");
      });
      await page.goto(recordUrl);
      await page.selectOption("select", "gpt_live");
      await page.locator(".voice-conversation-button").click();
      await page.waitForFunction(
        () => window.__kikioriVoiceTest?.audioTrackLive === true,
      );
      await page.waitForFunction(
        () => window.__kikioriVoiceTest?.serverEvents?.some(
          (item) => item.event?.type === "session.started",
        ),
      );

      const context = await fetchInterviewContext(request);
      const fields = [...context.fields].sort(
        (left, right) => left.displayOrder - right.displayOrder,
      );
      expect(fields.length).toBeGreaterThan(0);
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
          || "ブラウザ音声テスト回答 " + (index + 1);
        const payload = buildLiveAssessment({
          field,
          transcript,
          stateVersion: before.stateVersion,
          turn: index + 1,
        });
        const updateResponse = page.waitForResponse(
          (response) => response.url().includes("/api/live/state-updates")
            && response.request().method() === "POST",
        );
        telemetry.event("delegation_created", {
          turn: index + 1,
          delegationId: payload.delegation_id,
        });
        telemetry.event("function_tool_call", {
          turn: index + 1,
          callId: payload.call_id,
        });
        await page.evaluate(({ text, assessment }) => {
          window.__kikioriVoiceTest.emitTurn({
            transcript: text,
            assessment,
          });
        }, { text: transcript, assessment: payload });
        await updateResponse;
        await page.waitForTimeout(50);
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
        const events = await readFakeLiveEvents(page);
        const responseIds = events
          .filter((item) => item.event?.type === "response.created")
          .map((item) => item.event?.response?.id ?? item.event?.response_id)
          .filter(Boolean);
        const anomalies = detectTurnAnomalies({
          finalText: transcript,
          state: after,
          fieldId: field.id,
          expectedQuestionId: payload.question_id,
          responseIds,
          eventTypes: events.map((item) => item.event?.type).filter(Boolean),
          responseStarted: events.some(
            (item) => item.event?.type === "response.created",
          ),
        });
        assertNoAnomalies(anomalies);
        expect(answer.answerState).toBe("CONFIRMED");
        expect(after.currentQuestionId).toBe(payload.question_id);
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
      }
      expect(networkErrors).toEqual([]);
      expect(apiEvents.some((event) => event.type === "live_session_created")).toBe(true);
    } catch (error) {
      telemetry.error(error);
      await writeEvidencePackage({
        testInfo,
        page,
        telemetry,
        state,
        networkErrors,
      });
      throw error;
    }
  });
});
