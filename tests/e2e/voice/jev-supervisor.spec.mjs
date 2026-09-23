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
import {
  clearJevSupervisorCache,
  JevSupervisor,
} from "./supervisor/jevSupervisor.mjs";
import {
  createExplorationSummary,
  recordExplorationSession,
  recordExplorationTurn,
  writeExplorationSummary,
} from "./supervisor/explorationSummary.mjs";
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

function emptyJev(callCount, provider = null) {
  return {
    called: false,
    sessionAction: null,
    nextUserBehavior: null,
    confidence: null,
    cacheHit: false,
    callCount,
    provider,
    model: null,
    usage: null,
  };
}

function fieldAnswerIsPersisted(field) {
  return Boolean(
    field?.candidateAnswer
    || field?.recordAnswer
    || field?.answer
    || ["CONFIRMED", "ANSWERED"].includes(field?.answerState),
  );
}

function sessionTurnRecord({
  session,
  turn,
  behavior,
  transcript,
  payload,
  answer,
  before,
  after,
  expectedAssistantQuestion,
  assistantStartedEvent,
  simulatedTurnLatencyMs,
  hardGate,
  jev,
  turnTimeline,
  supervisorInput = null,
}) {
  return {
    sessionId: session.sessionId,
    sessionIndex: session.index,
    turn,
    audio: { startMs: 0, endMs: 1000, durationMs: 1000 },
    transcription: {
      partialCount: transcript ? 1 : 0,
      finalText: transcript,
      source: "fake_live_transport",
    },
    behavior,
    expectations: hardGate.expectations,
    observed: hardGate.observed,
    expectedConditions: hardGate.expectedConditions,
    expectationResult: hardGate.expectationResult,
    expectationViolations: hardGate.expectationViolations,
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
    supervisorInput,
    timeline: turnTimeline,
    anomalies: hardGate.anomalies ?? hardGate.codes,
  };
}

async function runSession({
  page,
  request,
  fields,
  scenario,
  config,
  guard,
  telemetry,
  supervisor,
  summary,
  session,
}) {
  let state = null;
  let fieldIndex = 0;
  let pendingAction = "CONTINUE";
  let pendingBehavior = "NORMAL";
  let retryCount = 0;
  let previousBehavior = null;

  await resetVoiceDemo(request);
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

  for (let turn = 1; turn <= config.maxTurnsPerSession; turn += 1) {
    const field = fields[fieldIndex];
    if (!field) {
      session.status = "NO_REMAINING_QUESTIONS";
      telemetry.event("session_completed", {
        turn,
        sessionId: session.sessionId,
        sessionIndex: session.index,
        reason: "no_remaining_questions",
      });
      break;
    }
    const isRetry = pendingAction === "RETRY_CURRENT";
    const fixture = fixtureForBehavior({
      behavior: pendingBehavior,
      scenario,
      turnIndex: fieldIndex,
      retryCount,
    });
    const turnExpectations = {
      ...(fixture.expectations ?? {}),
      ...(isRetry && fixture.expectations?.questionAdvance === "required"
        ? { questionAdvance: "must_not" }
        : {}),
    };
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
    session.state = state;
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
      const item = telemetry.event(name, {
        turn,
        sessionId: session.sessionId,
        sessionIndex: session.index,
        ...detail,
      });
      turnTimeline.push(item);
      return item;
    };
    const updateResponse = page.waitForResponse(
      (response) => response.url().includes("/api/live/state-updates")
        && response.request().method() === "POST",
    );

    telemetry.event("delegation_created", {
      turn,
      sessionId: session.sessionId,
      sessionIndex: session.index,
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
    const responseWaitOptions = turnExpectations.assistantRecovery === "observe"
      ? { timeout: 1_500 }
      : { timeout: 5_000 };
    await page.waitForFunction(
      ({ turnId }) => window.__kikioriVoiceTest?.serverEvents?.some(
        (item) => item.event?.type === "assistant_speech_started"
          && item.event?.turn_id === turnId,
      ),
      { turnId: payload.turn_id },
      responseWaitOptions,
    ).catch((error) => {
      if (turnExpectations.assistantRecovery === "observe") return null;
      throw error;
    });
    state = await fetchInterviewState(request);
    session.state = state;
    const after = state.interviewState;
    const beforeField = before.fieldStates?.[field.id] ?? {};
    const answer = after.fieldStates?.[field.id] ?? {};
    const answerPersistedBefore = fieldAnswerIsPersisted(beforeField);
    const answerPersistedAfter = fieldAnswerIsPersisted(answer);
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
    const assistantQuestionId = assistantStartedEvent?.event?.question_id ?? null;
    const questionAdvanced = Boolean(
      (assistantQuestionId && assistantQuestionId !== payload.question_id)
      || (
        after.currentQuestionId
        && after.currentQuestionId !== payload.question_id
        && after.currentQuestionId !== before.currentQuestionId
      ),
    );
    const assistantQuestionMatchesExpectation = assistantStartedEvent?.event?.question_id
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
      assistantQuestionMatchesExpectation,
      simulatedTurnLatencyMs,
    });

    const hardGate = evaluateHardGate({
      turn,
      maxTurns: config.maxTurnsPerSession,
      jevCalls: guard.snapshot().jevCalls,
      maxJevCalls: config.maxJevCallsTotal ?? config.maxJevCalls,
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
      expectations: turnExpectations,
      answerPersistedBefore,
      answerPersistedThisTurn: answerPersistedAfter && !answerPersistedBefore,
    });
    telemetry.event("hard_gate", {
      turn,
      sessionId: session.sessionId,
      sessionIndex: session.index,
      status: hardGate.status,
      codes: hardGate.codes,
      expectationResult: hardGate.expectationResult,
      expectationViolations: hardGate.expectationViolations,
      observed: hardGate.observed,
    });

    let jev = emptyJev(guard.snapshot().jevCalls, supervisor.provider);
    if (hardGate.status === "FAIL") {
      const turnRecord = sessionTurnRecord({
        session,
        turn,
        behavior: fixture.behavior,
        transcript,
        payload,
        answer,
        before,
        after,
        expectedAssistantQuestion,
        assistantStartedEvent,
        simulatedTurnLatencyMs,
        hardGate,
        jev,
        turnTimeline,
      });
      telemetry.recordTurn(turnRecord);
      recordExplorationTurn(summary, {
        session,
        behavior: fixture.behavior,
        jev,
        hardGate,
        turnRecord,
      });
      session.path.push({
        turn,
        behavior: fixture.behavior,
        sessionAction: null,
        nextUserBehavior: null,
      });
      session.testedBehaviors.push(fixture.behavior);
      throw turnError("Hard Gate failed; Jev was not called", hardGate);
    }
    if (hardGate.status === "STOP") {
      const turnRecord = sessionTurnRecord({
        session,
        turn,
        behavior: fixture.behavior,
        transcript,
        payload,
        answer,
        before,
        after,
        expectedAssistantQuestion,
        assistantStartedEvent,
        simulatedTurnLatencyMs,
        hardGate,
        jev,
        turnTimeline,
      });
      telemetry.recordTurn(turnRecord);
      recordExplorationTurn(summary, {
        session,
        behavior: fixture.behavior,
        jev,
        hardGate,
        turnRecord,
      });
      session.path.push({
        turn,
        behavior: fixture.behavior,
        sessionAction: null,
        nextUserBehavior: null,
      });
      session.testedBehaviors.push(fixture.behavior);
      session.status = "STOP";
      telemetry.event("session_stopped", {
        turn,
        sessionId: session.sessionId,
        sessionIndex: session.index,
        reason: hardGate.reason,
      });
      break;
    }

    const currentBehaviors = [...new Set([
      ...session.testedBehaviors,
      fixture.behavior,
    ])];
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
      previousUserBehavior: previousBehavior,
      testedBehaviors: currentBehaviors,
      remainingTurns: config.maxTurnsPerSession - turn,
      remainingJevCalls: (
        config.maxJevCallsTotal ?? config.maxJevCalls
      ) - guard.snapshot().jevCalls,
      errors: hardGate.codes,
      interviewState: after,
      telemetry: {
        timeline: turnTimeline,
        simulatedTurnLatencyMs,
        anomalies: hardGate.anomalies,
      },
      expectations: hardGate.expectations,
      observed: hardGate.observed,
      expectedConditions: hardGate.expectedConditions,
      expectationResult: hardGate.expectationResult,
      expectationViolations: hardGate.expectationViolations,
    });
    jev = await supervisor.decideAfterTurn({ hardGate, input });
    telemetry.event("jev_decision", {
      turn,
      sessionId: session.sessionId,
      sessionIndex: session.index,
      ...jev,
    });
    const turnRecord = sessionTurnRecord({
      session,
      turn,
      behavior: fixture.behavior,
      transcript,
      payload,
      answer,
      before,
      after,
      expectedAssistantQuestion,
      assistantStartedEvent,
      simulatedTurnLatencyMs,
      hardGate,
      jev,
      turnTimeline,
      supervisorInput: input,
    });
    telemetry.recordTurn(turnRecord);
    recordExplorationTurn(summary, {
      session,
      behavior: fixture.behavior,
      jev,
      hardGate,
      turnRecord,
    });
    session.path.push({
      turn,
      behavior: fixture.behavior,
      sessionAction: jev.sessionAction,
      nextUserBehavior: jev.nextUserBehavior,
      stateVersion: after.stateVersion,
      currentQuestionId: after.currentQuestionId,
      questionAdvanced,
    });
    session.testedBehaviors.push(fixture.behavior);
    previousBehavior = fixture.behavior;

    if (jev.sessionAction === "END_SUCCESS") {
      session.status = "END_SUCCESS";
      telemetry.event("session_completed", {
        turn,
        sessionId: session.sessionId,
        sessionIndex: session.index,
        reason: "jev_end_success",
      });
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

  if (!session.status) session.status = "MAX_TURNS";
  return state;
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

  test("explores bounded sessions with Hard Gate and TypeSafe Jev", async ({
    page,
    request,
  }, testInfo) => {
    // The exploration is deliberately bounded by VoiceCostGuard, but the
    // default Playwright timeout is too short for the configured multi-session
    // Jev run. Derive a finite timeout from those same caps.
    test.setTimeout(Math.max(
      45_000,
      30_000 + config.maxSessions * config.maxTurnsPerSession * 30_000,
    ));
    const guard = new VoiceCostGuard(config);
    for (let retry = 0; retry < testInfo.retry; retry += 1) guard.recordRetry();
    clearJevSupervisorCache();

    const seed = deterministicSeed(config.seed);
    const scenario = resolveScenario("NORMAL");
    const runSessionId = "browser-jev-exploration-" + seed;
    const telemetry = new VoiceTelemetry({
      outputDir: testInfo.outputPath("telemetry"),
      sessionId: runSessionId,
      scenario: "JEV_EXPLORATION",
      seed,
      costGuard: guard,
      audioInputMode: "fake_microphone_wav",
      transcriptSource: "fake_live_transport",
      liveApiUsed: false,
    });
    const summary = createExplorationSummary({
      seed,
      limits: {
        maxSessions: config.maxSessions,
        maxTurnsPerSession: config.maxTurnsPerSession,
        maxJevCallsTotal: config.maxJevCallsTotal ?? config.maxJevCalls,
        maxRetries: config.maxRetries,
      },
    });
    const apiEvents = [];
    const networkErrors = observePageNetwork(page, telemetry);
    let state = null;
    let activeSession = null;
    let activeSessionRecorded = false;

    try {
      await installFakeLiveTransport(page);
      await installLocalApiRoutes(page, { apiEvents });
      await page.addInitScript(() => {
        window.localStorage.setItem("ai-interviewer-dev-token", "dev-manager");
      });

      // Read the fixed question master before opening the first session. This
      // avoids creating an uncounted browser voice session outside the guard.
      await resetVoiceDemo(request);
      const context = await fetchInterviewContext(request);
      const fields = [...context.fields].sort(
        (left, right) => left.displayOrder - right.displayOrder,
      );
      expect(fields.length).toBeGreaterThan(0);

      for (let sessionIndex = 1; sessionIndex <= config.maxSessions; sessionIndex += 1) {
        guard.startSession();
        const session = {
          sessionId: "browser-jev-" + seed + "-session-" + sessionIndex,
          index: sessionIndex,
          status: null,
          turns: [],
          path: [],
          testedBehaviors: [],
          state: null,
          error: null,
        };
        activeSession = session;
        activeSessionRecorded = false;
        const supervisor = new JevSupervisor({
          config,
          guard,
          sessionId: session.sessionId,
        });
        if (!summary.provider) summary.provider = supervisor.provider;
        if (!summary.model && supervisor.provider === "typesafe") {
          summary.model = supervisor.typeSafeModel;
        }
        telemetry.event("session_started", {
          sessionId: session.sessionId,
          sessionIndex,
          provider: supervisor.provider,
          model: supervisor.typeSafeModel ?? null,
        });
        state = await runSession({
          page,
          request,
          fields,
          scenario,
          config,
          guard,
          telemetry,
          supervisor,
          summary,
          session,
        });
        recordExplorationSession(summary, session);
        activeSessionRecorded = true;
        telemetry.event("session_summary", {
          sessionId: session.sessionId,
          sessionIndex,
          status: session.status,
          turns: session.turns.length,
          testedBehaviors: [...new Set(session.testedBehaviors)],
        });
      }

      expect(summary.totals.sessions).toBe(config.maxSessions);
      expect(guard.snapshot().sessions).toBe(config.maxSessions);
      expect(guard.snapshot().turns).toBeLessThanOrEqual(
        config.maxSessions * config.maxTurnsPerSession,
      );
      expect(guard.snapshot().jevCalls).toBeLessThanOrEqual(
        config.maxJevCallsTotal ?? config.maxJevCalls,
      );
      expect(networkErrors).toEqual([]);
      expect(telemetry.consoleErrors).toEqual([]);
      expect(telemetry.dataChannelErrors).toEqual([]);
      expect(telemetry.networkLog.some((item) => Number(item.status) >= 500)).toBe(false);
      expect(apiEvents.some((event) => event.type === "live_session_created")).toBe(true);
      summary.provider = summary.provider ?? "unknown";
      writeExplorationSummary({
        outputDir: telemetry.outputDir,
        summary,
        costGuard: guard,
      });
      telemetry.event("test_completed", {
        sessions: summary.totals.sessions,
        turns: summary.totals.turns,
        jevApiCalls: summary.totals.jevApiCalls,
        cacheHits: summary.totals.cacheHits,
        cacheMisses: summary.totals.cacheMisses,
      });
    } catch (error) {
      if (activeSession && !activeSessionRecorded) {
        activeSession.status = "FAIL";
        activeSession.error = error instanceof Error ? error.message : String(error);
        recordExplorationSession(summary, activeSession);
      }
      telemetry.error(error);
      writeExplorationSummary({
        outputDir: telemetry.outputDir,
        summary,
        costGuard: guard,
      });
      await writeEvidencePackage({
        testInfo,
        page,
        telemetry,
        state: activeSession?.state ?? state,
        networkErrors,
      });
      throw error;
    }
  });
});
