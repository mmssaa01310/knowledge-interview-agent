import { test, expect } from "@playwright/test";
import { readVoiceTestConfig, VoiceCostGuard } from "./config.mjs";
import {
  deterministicSeed,
  resolveScenario,
} from "./scenarios/scenarios.mjs";
import {
  buildLiveAssessment,
  fetchInterviewContext,
  fetchInterviewState,
  liveQuestionId,
  questionText,
  resetVoiceDemo,
} from "./drivers/apiDriver.mjs";
import {
  installFakeLiveTransport,
  readFakeLiveDiagnostics,
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

test.describe("voice E2E level 2: NORMAL scenario", () => {
  test.skip(
    config.agentMode === "jev",
    "Jev mode uses the Jev Supervisor scenario",
  );
  test.skip(
    !config.runBrowserAudio,
    "Set VOICE_TEST_RUN_BROWSER_AUDIO=true to run browser audio tests",
  );

  test("streams one WAV-backed microphone session and verifies the NORMAL turn lifecycle", async ({
    page,
    request,
  }, testInfo) => {
    const guard = new VoiceCostGuard(config);
    for (let retry = 0; retry < testInfo.retry; retry += 1) guard.recordRetry();
    guard.startSession();

    const seed = deterministicSeed(config.seed);
    const scenario = resolveScenario("NORMAL");
    const telemetry = new VoiceTelemetry({
      outputDir: testInfo.outputPath("telemetry"),
      sessionId: "browser-normal-" + seed,
      scenario: scenario.name,
      seed,
      costGuard: guard,
      audioInputMode: "fake_microphone_wav",
      transcriptSource: "fake_live_transport",
      liveApiUsed: false,
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
      expect(fields.length).toBeGreaterThanOrEqual(scenario.turns.length);

      let previousQuestionId = null;
      for (let index = 0; index < scenario.turns.length; index += 1) {
        const turn = index + 1;
        const field = fields[index];
        const nextField = fields[index + 1] ?? null;
        const nextQuestion = nextField
          ? {
              id: liveQuestionId(nextField),
              text: questionText(nextField),
            }
          : null;
        const transcript = scenario.turns[index];

        guard.startTurn(1);
        state = await fetchInterviewState(request);
        const before = state.interviewState;
        const payload = buildLiveAssessment({
          field,
          transcript,
          stateVersion: before.stateVersion,
          turn,
        });
        const turnTimeline = [];
        const recordTimeline = (name, detail = {}) => {
          const item = telemetry.event(name, { turn, ...detail });
          turnTimeline.push(item);
          return item;
        };

        const updateResponse = page.waitForResponse(
          (response) => response.url().includes("/api/live/state-updates")
            && response.request().method() === "POST",
        );
        telemetry.event("delegation_created", {
          turn,
          delegationId: payload.delegation_id,
          source: "fake_live_transport",
        });
        telemetry.event("function_tool_call", {
          turn,
          callId: payload.call_id,
          source: "fake_live_transport",
        });

        await page.evaluate(({ text, assessment, followingQuestion }) => {
          window.__kikioriVoiceTest.emitTurn({
            transcript: text,
            assessment,
            nextQuestion: followingQuestion,
          });
        }, { text: transcript, assessment: payload, followingQuestion: nextQuestion });

        await page.waitForFunction(
          ({ turnId }) => window.__kikioriVoiceTest?.serverEvents?.some(
            (item) => item.event?.type === "conversation.item.input_audio_transcription.completed"
              && item.event?.turn_id === turnId,
          ),
          { turnId: payload.turn_id },
        );
        const afterFinal = await readFakeLiveDiagnostics(page);
        const finalEvent = afterFinal.events.find(
          (item) => item.event?.type === "conversation.item.input_audio_transcription.completed"
            && item.event?.turn_id === payload.turn_id,
        );
        expect(finalEvent).toBeTruthy();
        telemetry.transcriptFinal({
          turn,
          finalText: transcript,
          source: "fake_live_transport",
          sourceTimestampMs: finalEvent.timestampMs,
        });
        recordTimeline("user_transcript_final", {
          transcript,
          source: "fake_live_transport",
          sourceTimestampMs: finalEvent.timestampMs,
        });
        recordTimeline("user_turn_completed", {
          source: "fake_live_transport",
          sourceTimestampMs: finalEvent.timestampMs,
        });

        const persistedResponse = await updateResponse;
        expect(persistedResponse.status()).toBe(200);
        await page.waitForFunction(
          ({ turnId }) => window.__kikioriVoiceTest?.serverEvents?.some(
            (item) => item.event?.type === "assistant_speech_started"
              && item.event?.turn_id === turnId,
          ),
          { turnId: payload.turn_id },
        );
        await page.waitForTimeout(25);

        state = await fetchInterviewState(request);
        const after = state.interviewState;
        const answer = after.fieldStates?.[field.id] ?? {};
        const diagnostics = await readFakeLiveDiagnostics(page);
        const turnEvents = diagnostics.events.filter(
          (item) => item.event?.turn_id === payload.turn_id,
        );
        const assistantStartedEvent = turnEvents.find(
          (item) => item.event?.type === "assistant_speech_started",
        );
        const responseIds = turnEvents
          .filter((item) => item.event?.type === "response.created")
          .map((item) => item.event?.response?.id ?? item.event?.response_id)
          .filter(Boolean);
        const simulatedTurnLatencyMs = assistantStartedEvent
          ? Math.max(0, assistantStartedEvent.timestampMs - finalEvent.timestampMs)
          : null;

        recordTimeline("candidate_answer_saved", {
          value: answer.candidateAnswer ?? null,
          present: Boolean(answer.candidateAnswer),
          resolution: answer.candidateAnswer ? "CANDIDATE" : "AUTO_CONFIRM",
        });
        expect(answer.recordAnswer).toBeTruthy();
        recordTimeline("record_answer_saved", {
          value: answer.recordAnswer,
        });
        expect(answer.answerState).toBe("CONFIRMED");
        recordTimeline("answer_state_updated", {
          answerState: answer.answerState,
        });
        expect(Number(after.stateVersion)).toBeGreaterThan(Number(before.stateVersion));
        recordTimeline("state_version_updated", {
          before: before.stateVersion,
          after: after.stateVersion,
        });
        expect(after.currentQuestionId).toBe(payload.question_id);
        recordTimeline("current_question_updated", {
          before: before.currentQuestionId,
          after: after.currentQuestionId,
        });
        expect(assistantStartedEvent).toBeTruthy();
        if (nextQuestion) {
          expect(assistantStartedEvent.event.question_id).toBe(nextQuestion.id);
          if (nextQuestion.text) {
            expect(assistantStartedEvent.event.question_text).toBe(nextQuestion.text);
          }
        }
        recordTimeline("assistant_speech_started", {
          source: "fake_live_transport",
          sourceTimestampMs: assistantStartedEvent.timestampMs,
          nextQuestionId: nextQuestion?.id ?? null,
          nextQuestionText: nextQuestion?.text ?? null,
          simulatedTurnLatencyMs,
        });

        const anomalies = detectTurnAnomalies({
          finalText: transcript,
          state: after,
          fieldId: field.id,
          expectedQuestionId: payload.question_id,
          previousQuestionId,
          responseIds,
          eventTypes: turnEvents.map((item) => item.event?.type).filter(Boolean),
          responseStarted: Boolean(assistantStartedEvent),
          apiStatuses: telemetry.networkLog
            .filter((item) => item.direction === "response")
            .map((item) => item.status),
        });
        assertNoAnomalies(anomalies);
        expect(diagnostics.dataChannelErrors).toEqual([]);
        previousQuestionId = payload.question_id;

        telemetry.recordTurn({
          turn,
          audio: { startMs: 0, endMs: 1000, durationMs: 1000 },
          transcription: {
            partialCount: 1,
            finalText: transcript,
            source: "fake_live_transport",
          },
          interview: {
            questionId: payload.question_id,
            candidate: answer.candidateAnswer,
            confirmed: answer.recordAnswer,
            answerState: answer.answerState,
            stateVersionBefore: before.stateVersion,
            stateVersionAfter: after.stateVersion,
            currentQuestionBefore: before.currentQuestionId,
            currentQuestionAfter: after.currentQuestionId,
            nextQuestionId: nextQuestion?.id ?? null,
            nextQuestionText: nextQuestion?.text ?? null,
          },
          response: {
            assistantSpeechStarted: Boolean(assistantStartedEvent),
            simulatedTurnLatencyMs,
            firstAudioLatencyMs: simulatedTurnLatencyMs,
          },
          timeline: turnTimeline,
          anomalies,
        });
      }

      expect(networkErrors).toEqual([]);
      expect(telemetry.consoleErrors).toEqual([]);
      expect(telemetry.dataChannelErrors).toEqual([]);
      expect(telemetry.networkLog.some((item) => Number(item.status) >= 500)).toBe(false);
      expect(apiEvents.some((event) => event.type === "live_session_created")).toBe(true);
      telemetry.event("test_completed", {
        audioInputMode: "fake_microphone_wav",
        transcriptSource: "fake_live_transport",
        liveApiUsed: false,
      });
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
