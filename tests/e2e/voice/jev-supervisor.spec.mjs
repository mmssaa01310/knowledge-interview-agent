import { test, expect } from "@playwright/test";
import { readVoiceTestConfig, VoiceCostGuard } from "./config.mjs";
import {
  deterministicSeed,
  fixtureForBehavior,
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
import { evaluateHardGate } from "./supervisor/hardGate.mjs";
import { JevSupervisor } from "./supervisor/jevSupervisor.mjs";
import {
  VoiceTelemetry,
  writeEvidencePackage,
} from "./telemetry/telemetry.mjs";

const config = readVoiceTestConfig();
const recordUrl = "/knowledge-dbs/dev-voice-demo-db/knowledges/"
  + "dev-voice-demo-knowledge/records/dev-voice-demo-record";

function turnError(message, hardGate) {
  const error = new Error(message + ": " + hardGate.codes.join(", "));
  error.hardGate = hardGate;
  return error;
}

test.describe("voice E2E Jev Supervisor", () => {
  test.skip(
    config.agentMode !== "jev",
    "Set TEST_AGENT_MODE=jev to enable the Jev Supervisor",
  );
  test.skip(
    !config.runBrowserAudio,
    "Set VOICE_TEST_RUN_BROWSER_AUDIO=true to run browser audio tests",
  );

  test("uses Hard Gate before Jev and advances with bounded fixed fixtures", async ({
    page,
    request,
  }, testInfo) => {
    const guard = new VoiceCostGuard(config);
    for (let retry = 0; retry < testInfo.retry; retry += 1) guard.recordRetry();
    guard.startSession();

    const seed = deterministicSeed(config.seed);
    const scenario = resolveScenario("NORMAL");
    const sessionId = "browser-jev-" + seed;
    const telemetry = new VoiceTelemetry({
      outputDir: testInfo.outputPath("telemetry"),
      sessionId,
      scenario: "JEV_SUPERVISOR",
      seed,
      costGuard: guard,
      audioInputMode: "fake_microphone_wav",
      transcriptSource: "fake_live_transport",
      liveApiUsed: false,
    });
    const supervisor = new JevSupervisor({ config, guard, sessionId });
    const apiEvents = [];
    const networkErrors = observePageNetwork(page, telemetry);
    let state = null;
    let fieldIndex = 0;
    let pendingAction = "CONTINUE";
    let pendingBehavior = "NORMAL";
    let retryCount = 0;
    let previousBehavior = null;
    const testedBehaviors = [];

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

      for (let turn = 1; turn <= config.maxTurnsPerSession; turn += 1) {
        const field = fields[fieldIndex];
        if (!field) {
          telemetry.event("session_completed", { turn, reason: "no_remaining_questions" });
          break;
        }
        const isRetry = pendingAction === "RETRY_CURRENT";
        const fixture = fixtureForBehavior({
          behavior: pendingBehavior,
          scenario,
          turnIndex: fieldIndex,
          retryCount,
        });
        const nextField = !isRetry && fixture.expectedQuestionTransition
          ? fields[fieldIndex + 1] ?? null
          : null;
        const expectedAssistantQuestion = nextField
          ? { id: liveQuestionId(nextField), text: questionText(nextField) }
          : {
              id: liveQuestionId(field),
              text: questionText(field),
            };
        const transcript = fixture.transcript;

        guard.startTurn(1);
        state = await fetchInterviewState(request);
        const before = state.interviewState;
        const payload = buildLiveAssessment({
          field,
          transcript,
          answerStatus: fixture.answerStatus,
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
        await page.evaluate(({ text, assessment, followingQuestion }) => {
          window.__kikioriVoiceTest.emitTurn({
            transcript: text,
            assessment,
            nextQuestion: followingQuestion,
          });
        }, {
          text: transcript,
          assessment: payload,
          followingQuestion: expectedAssistantQuestion,
        });

        const afterFinal = await page.waitForFunction(
          ({ turnId }) => window.__kikioriVoiceTest?.serverEvents?.find(
            (item) => item.event?.type === "conversation.item.input_audio_transcription.completed"
              && item.event?.turn_id === turnId,
          ),
          { turnId: payload.turn_id },
        );
        const finalEvent = await afterFinal.jsonValue();
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
        const questionAdvanced = assistantStartedEvent?.event?.question_id
          === expectedAssistantQuestion.id;
        const simulatedTurnLatencyMs = assistantStartedEvent
          ? Math.max(0, assistantStartedEvent.timestampMs - finalEvent.timestampMs)
          : null;
        recordTimeline("candidate_answer_saved", {
          value: answer.candidateAnswer ?? null,
          present: Boolean(answer.candidateAnswer),
          resolution: answer.candidateAnswer ? "CANDIDATE" : "AUTO_CONFIRM",
        });
        recordTimeline("record_answer_saved", {
          value: answer.recordAnswer ?? null,
          present: Boolean(answer.recordAnswer),
        });
        recordTimeline("answer_state_updated", { answerState: answer.answerState ?? null });
        recordTimeline("state_version_updated", {
          before: before.stateVersion,
          after: after.stateVersion,
        });
        recordTimeline("current_question_updated", {
          before: before.currentQuestionId,
          after: after.currentQuestionId,
        });
        recordTimeline("assistant_speech_started", {
          source: "fake_live_transport",
          sourceTimestampMs: assistantStartedEvent?.timestampMs ?? null,
          nextQuestionId: expectedAssistantQuestion.id,
          nextQuestionText: expectedAssistantQuestion.text,
          simulatedTurnLatencyMs,
        });

        const hardGate = evaluateHardGate({
          turn,
          maxTurns: config.maxTurnsPerSession,
          jevCalls: guard.snapshot().jevCalls,
          maxJevCalls: config.maxJevCalls,
          state: after,
          fieldId: field.id,
          expectedQuestionId: payload.question_id,
          finalText: transcript,
          previousQuestionId: isRetry ? payload.question_id : null,
          responseIds,
          eventTypes: turnEvents.map((item) => item.event?.type).filter(Boolean),
          responseStarted: Boolean(assistantStartedEvent),
          apiStatuses: telemetry.networkLog
            .filter((item) => item.direction === "response")
            .map((item) => item.status),
          dataChannelErrors: diagnostics.dataChannelErrors,
          questionShouldAdvance: fixture.expectedQuestionTransition && !isRetry,
          questionAdvanced,
          expectAnswerPersistence: fixture.expectAnswerPersistence !== false,
          allowRepeatedQuestion: isRetry,
        });
        telemetry.event("hard_gate", {
          turn,
          status: hardGate.status,
          codes: hardGate.codes,
        });

        let jev = {
          called: false,
          sessionAction: null,
          nextUserBehavior: null,
          confidence: null,
          cacheHit: false,
          callCount: guard.snapshot().jevCalls,
        };
        if (hardGate.status === "FAIL") {
          telemetry.recordTurn({
            turn,
            audio: { startMs: 0, endMs: 1000, durationMs: 1000 },
            transcription: { partialCount: 1, finalText: transcript, source: "fake_live_transport" },
            interview: {
              questionId: payload.question_id,
              candidate: answer.candidateAnswer,
              confirmed: answer.recordAnswer,
              answerState: answer.answerState,
              stateVersionBefore: before.stateVersion,
              stateVersionAfter: after.stateVersion,
              currentQuestionBefore: before.currentQuestionId,
              currentQuestionAfter: after.currentQuestionId,
              nextQuestionId: expectedAssistantQuestion.id,
              nextQuestionText: expectedAssistantQuestion.text,
            },
            response: {
              assistantSpeechStarted: Boolean(assistantStartedEvent),
              simulatedTurnLatencyMs,
              firstAudioLatencyMs: simulatedTurnLatencyMs,
            },
            hardGate,
            jev,
            timeline: turnTimeline,
            anomalies: hardGate.codes,
          });
          throw turnError("Hard Gate failed; Jev was not called", hardGate);
        }
        if (hardGate.status === "STOP") {
          telemetry.recordTurn({
            turn,
            audio: { startMs: 0, endMs: 1000, durationMs: 1000 },
            transcription: { partialCount: 1, finalText: transcript, source: "fake_live_transport" },
            interview: {
              questionId: payload.question_id,
              candidate: answer.candidateAnswer,
              confirmed: answer.recordAnswer,
              answerState: answer.answerState,
              stateVersionBefore: before.stateVersion,
              stateVersionAfter: after.stateVersion,
              currentQuestionBefore: before.currentQuestionId,
              currentQuestionAfter: after.currentQuestionId,
              nextQuestionId: expectedAssistantQuestion.id,
              nextQuestionText: expectedAssistantQuestion.text,
            },
            response: {
              assistantSpeechStarted: Boolean(assistantStartedEvent),
              simulatedTurnLatencyMs,
              firstAudioLatencyMs: simulatedTurnLatencyMs,
            },
            hardGate,
            jev,
            timeline: turnTimeline,
            anomalies: [],
          });
          telemetry.event("session_stopped", { turn, reason: hardGate.reason });
          break;
        }

        const currentBehaviors = [...new Set([...testedBehaviors, fixture.behavior])];
        const input = supervisor.buildInput({
          turn,
          currentQuestionId: payload.question_id,
          currentQuestion: questionText(field),
          lastTranscript: transcript,
          answerState: answer.answerState,
          candidateAnswerExists: Boolean(answer.candidateAnswer),
          recordAnswerExists: Boolean(answer.recordAnswer),
          stateVersionAdvanced: Number(after.stateVersion) > Number(before.stateVersion),
          questionAdvanced,
          previousUserBehavior: fixture.behavior,
          testedBehaviors: currentBehaviors,
          remainingTurns: config.maxTurnsPerSession - turn,
          remainingJevCalls: config.maxJevCalls - guard.snapshot().jevCalls,
          errors: hardGate.codes,
          interviewState: after,
          telemetry: {
            timeline: turnTimeline,
            simulatedTurnLatencyMs,
            anomalies: hardGate.anomalies,
          },
        });
        jev = await supervisor.decideAfterTurn({ hardGate, input });
        telemetry.event("jev_decision", { turn, ...jev });
        telemetry.recordTurn({
          turn,
          audio: { startMs: 0, endMs: 1000, durationMs: 1000 },
          transcription: { partialCount: 1, finalText: transcript, source: "fake_live_transport" },
          interview: {
            questionId: payload.question_id,
            candidate: answer.candidateAnswer,
            confirmed: answer.recordAnswer,
            answerState: answer.answerState,
            stateVersionBefore: before.stateVersion,
            stateVersionAfter: after.stateVersion,
            currentQuestionBefore: before.currentQuestionId,
            currentQuestionAfter: after.currentQuestionId,
            nextQuestionId: expectedAssistantQuestion.id,
            nextQuestionText: expectedAssistantQuestion.text,
          },
          response: {
            assistantSpeechStarted: Boolean(assistantStartedEvent),
            simulatedTurnLatencyMs,
            firstAudioLatencyMs: simulatedTurnLatencyMs,
          },
          hardGate,
          jev,
          timeline: turnTimeline,
          anomalies: hardGate.anomalies,
        });

        testedBehaviors.push(fixture.behavior);
        previousBehavior = fixture.behavior;
        if (jev.sessionAction === "END_SUCCESS") {
          telemetry.event("session_completed", { turn, reason: "jev_end_success" });
          break;
        }
        if (jev.sessionAction === "RETRY_CURRENT") {
          pendingAction = "RETRY_CURRENT";
          pendingBehavior = jev.nextUserBehavior;
          retryCount += 1;
        } else {
          fieldIndex += 1;
          pendingAction = jev.sessionAction;
          pendingBehavior = jev.sessionAction === "STRESS_NEXT"
            ? jev.nextUserBehavior
            : "NORMAL";
          retryCount = 0;
        }
      }

      expect(guard.snapshot().sessions).toBe(1);
      expect(guard.snapshot().turns).toBeLessThanOrEqual(config.maxTurnsPerSession);
      expect(guard.snapshot().jevCalls).toBeLessThanOrEqual(config.maxJevCalls);
      expect(networkErrors).toEqual([]);
      expect(telemetry.consoleErrors).toEqual([]);
      expect(telemetry.dataChannelErrors).toEqual([]);
      expect(telemetry.networkLog.some((item) => Number(item.status) >= 500)).toBe(false);
      expect(apiEvents.some((event) => event.type === "live_session_created")).toBe(true);
      telemetry.event("test_completed", {
        testedBehaviors: [...new Set(testedBehaviors)],
        previousUserBehavior: previousBehavior,
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
