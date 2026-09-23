import fs from "node:fs";
import path from "node:path";

import { readWavDurationSeconds } from "../fixtures/wavFixture.mjs";
import { extractFieldAnswer } from "../assertions/invariants.mjs";
import { evaluateHardGate } from "../supervisor/hardGate.mjs";
import { fetchInterviewContext, fetchInterviewState, resetVoiceDemo } from "../drivers/apiDriver.mjs";
import { observePageNetwork } from "../drivers/appRoutes.mjs";
import {
  extractRealTurnTrace,
  installRealLiveTrace,
  readGPTLiveTranscriptLines,
  readRealLiveTrace,
  realTraceTimeline,
} from "./realLiveTrace.mjs";
import { VoiceTelemetry, writeEvidencePackage } from "../telemetry/telemetry.mjs";

export const GPT_LIVE_RECORD_URL = "/knowledge-dbs/dev-voice-demo-db/knowledges/"
  + "dev-voice-demo-knowledge/records/dev-voice-demo-record";

const DEFAULT_TURN_TIMEOUT_MS = 90_000;

function hasText(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function integer(value, fallback) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function fieldIdForQuestion(context, questionId) {
  const fields = context?.fields ?? [];
  const matched = fields.find((field) => (
    `live-field-${field.id}` === questionId || field.id === questionId
  ));
  return matched?.id ?? (String(questionId ?? "").replace(/^live-field-/, "") || null);
}

function stateVersion(state) {
  return Number(state?.interviewState?.stateVersion ?? 0);
}

function questionId(state) {
  return state?.interviewState?.currentQuestionId ?? null;
}

function stateSnapshot(state) {
  const interview = state?.interviewState ?? {};
  return {
    stateVersion: interview.stateVersion ?? null,
    currentQuestionId: interview.currentQuestionId ?? null,
    status: interview.status ?? null,
  };
}

function traceHasFinal(entries) {
  return entries.some((entry) => {
    const name = entry?.args?.[0];
    const type = name === "gpt_live_event" ? entry?.args?.[1]?.type : null;
    return typeof type === "string" && (
      type.endsWith("input_audio_transcription.completed")
      || type.endsWith("input_transcript.completed")
      || type.endsWith("input_transcript.done")
    );
  });
}

function traceHasAssistantStartAfterUser(entries) {
  let userSpeechEnd = null;
  for (const entry of entries) {
    if (entry?.args?.[0] === "gpt_live_user_speech_end") {
      userSpeechEnd = entry.timestampMs;
      continue;
    }
    const type = entry?.args?.[0] === "gpt_live_event" ? entry?.args?.[1]?.type : null;
    if (userSpeechEnd !== null && (
      type === "assistant_speech_started"
      || entry?.args?.[0] === "gpt_live_response_started"
    ) && entry.timestampMs >= userSpeechEnd) {
      return true;
    }
  }
  return false;
}

async function waitForInterviewState(request, before, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let latest = null;
  while (Date.now() < deadline) {
    latest = await fetchInterviewState(request).catch(() => null);
    if (latest && stateVersion(latest) > stateVersion(before)) return latest;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return latest ?? await fetchInterviewState(request).catch(() => null);
}

function expectationValue(candidate, key, fallback) {
  const value = candidate?.level2?.expectations?.[key];
  return value ?? fallback;
}

export function createLevel3Config(config, outputDir) {
  return Object.freeze({
    ...config,
    outputDir: path.resolve(outputDir),
    maxSessions: config.maxLevel3Sessions,
    maxTurnsPerSession: config.maxLevel3TurnsPerSession,
    maxRetries: config.maxLevel3Retries,
  });
}

export async function runRealGPTLiveCandidate({
  page,
  request,
  testInfo,
  config,
  guard,
  candidate,
  sessionIndex,
  audioFixturePath,
}) {
  const sessionId = `level3-${candidate.seed}-session-${sessionIndex}-${candidate.behavior.toLowerCase()}`;
  const telemetry = new VoiceTelemetry({
    outputDir: testInfo.outputPath("level3", `session-${sessionIndex}`, "telemetry"),
    sessionId,
    scenario: candidate.behavior,
    seed: candidate.seed,
    costGuard: guard,
    audioInputMode: "fake_microphone_wav",
    transcriptSource: "gpt_live",
    liveApiUsed: true,
    level: 3,
    audioFixturePath,
    promotionCandidate: candidate,
  });
  const networkErrors = observePageNetwork(page, telemetry);
  const turnTimeoutMs = integer(process.env.VOICE_TEST_REAL_TURN_TIMEOUT_MS, DEFAULT_TURN_TIMEOUT_MS);
  let before = null;
  let after = null;
  let context = null;
  let realTiming = null;
  let transcript = "";
  let assistantResponse = "";
  let hardGate = null;
  let turnRecord = null;
  let traceEntries = [];

  await installRealLiveTrace(page);
  try {
    // A fresh navigation unmounts the previous Live hook before the next
    // candidate. The backend reset is intentionally performed before reading
    // the baseline state so the comparison has an unambiguous origin.
    await page.goto("about:blank");
    await resetVoiceDemo(request);
    context = await fetchInterviewContext(request);
    before = await fetchInterviewState(request);
    telemetry.event("level3_session_started", {
      sessionId,
      sessionIndex,
      seed: candidate.seed,
      candidateBehavior: candidate.behavior,
      level2Turn: candidate.turn,
      baseline: stateSnapshot(before),
    });
    await page.addInitScript(() => {
      window.localStorage.setItem("ai-interviewer-dev-token", "dev-manager");
    });
    await page.goto(GPT_LIVE_RECORD_URL);
    await page.selectOption("select", "gpt_live");
    await page.locator(".voice-conversation-button").click();
    await page.waitForFunction(
      () => document.querySelector(".voice-conversation-button")?.classList.contains("active"),
      null,
      { timeout: 30_000 },
    );
    telemetry.event("gpt_live_session_ready");

    const audioDurationSeconds = readWavDurationSeconds(audioFixturePath);
    guard.startTurn(audioDurationSeconds);
    telemetry.event("audio_fixture_started", {
      path: audioFixturePath,
      durationSeconds: audioDurationSeconds,
    });

    await page.waitForFunction(
      () => (window.__kikioriVoiceLiveTrace?.entries ?? []).some((entry) => {
        const type = entry?.args?.[0] === "gpt_live_event" ? entry?.args?.[1]?.type : null;
        return typeof type === "string" && (
          type.endsWith("input_audio_transcription.completed")
          || type.endsWith("input_transcript.completed")
          || type.endsWith("input_transcript.done")
        );
      }),
      null,
      { timeout: turnTimeoutMs },
    ).catch((error) => telemetry.error(error));
    await page.waitForFunction(
      () => {
        const entries = window.__kikioriVoiceLiveTrace?.entries ?? [];
        let speechEnd = null;
        for (const entry of entries) {
          if (entry?.args?.[0] === "gpt_live_user_speech_end") speechEnd = entry.timestampMs;
          const type = entry?.args?.[0] === "gpt_live_event" ? entry?.args?.[1]?.type : null;
          if (speechEnd !== null && type === "assistant_speech_started"
            && entry.timestampMs >= speechEnd) return true;
        }
        return false;
      },
      null,
      { timeout: turnTimeoutMs },
    ).catch((error) => telemetry.error(error));

    after = await waitForInterviewState(request, before, Math.min(turnTimeoutMs, 30_000));
    traceEntries = await readRealLiveTrace(page);
    telemetry.setLiveTrace(traceEntries);
    realTiming = extractRealTurnTrace(traceEntries);
    const transcriptLines = await readGPTLiveTranscriptLines(page).catch(() => []);
    const userLines = transcriptLines.filter((line) => line.role === "user" && hasText(line.text));
    const assistantLines = transcriptLines.filter((line) => line.role === "assistant" && hasText(line.text));
    transcript = userLines.at(-1)?.text ?? "";
    assistantResponse = assistantLines.at(-1)?.text ?? "";
    telemetry.eventAt("speech_started", realTiming.speechStart, { turn: candidate.turn })
      ;
    telemetry.eventAt("speech_ended", realTiming.speechEnd, { turn: candidate.turn })
      ;
    telemetry.eventAt("transcript_final", realTiming.transcriptFinal, {
      turn: candidate.turn,
      finalText: transcript,
      source: "gpt_live",
    });
    telemetry.eventAt("turn_committed", realTiming.turnCommit, {
      turn: candidate.turn,
      stateVersion: stateVersion(after),
    });
    telemetry.eventAt("assistant_speech_started", realTiming.assistantSpeechStarted, {
      turn: candidate.turn,
    });
    telemetry.eventAt("assistant_speech_ended", realTiming.assistantSpeechEnded, {
      turn: candidate.turn,
    });
    telemetry.transcriptFinal({
      turn: candidate.turn,
      source: "gpt_live",
      finalText: transcript,
      sourceTimestampMs: realTiming.transcriptFinal,
    });

    const beforeInterview = before?.interviewState ?? {};
    const afterInterview = after?.interviewState ?? {};
    const currentQuestionId = questionId(before);
    const fieldId = fieldIdForQuestion(context, currentQuestionId);
    const answer = extractFieldAnswer(afterInterview, fieldId);
    const questionAdvanced = Boolean(
      currentQuestionId && afterInterview.currentQuestionId
        && currentQuestionId !== afterInterview.currentQuestionId,
    );
    const answerPersistedBefore = Boolean(
      extractFieldAnswer(beforeInterview, fieldId).candidate
      || extractFieldAnswer(beforeInterview, fieldId).confirmed
      || ["CONFIRMED", "ANSWERED"].includes(extractFieldAnswer(beforeInterview, fieldId).answerState),
    );
    const answerPersistedAfter = Boolean(
      answer.candidate || answer.confirmed || ["CONFIRMED", "ANSWERED"].includes(answer.answerState),
    );
    const eventTypes = traceEntries
      .map((entry) => liveEventTypeFromEntry(entry))
      .filter(Boolean);
    const responseIds = traceEntries
      .map((entry) => entry?.args?.[1]?.response_id)
      .filter((value) => typeof value === "string");
    const expectedQuestionAdvance = expectationValue(candidate, "questionAdvance", "optional") === "required";
    const expectAnswerPersistence = !["not_required", "must_not"].includes(
      expectationValue(candidate, "answerPersistence", "optional"),
    );
    hardGate = evaluateHardGate({
      turn: 1,
      maxTurns: config.maxLevel3TurnsPerSession,
      jevCalls: 0,
      maxJevCalls: undefined,
      state: afterInterview,
      fieldId,
      expectedQuestionId: currentQuestionId,
      finalText: transcript,
      responseIds,
      eventTypes,
      responseStarted: realTiming.assistantSpeechStarted !== null,
      committedBeforeFinal: realTiming.turnCommit !== null
        && realTiming.transcriptFinal !== null
        && realTiming.turnCommit < realTiming.transcriptFinal,
      apiStatuses: telemetry.networkLog
        .filter((item) => item.direction === "response")
        .map((item) => item.status),
      dataChannelErrors: telemetry.dataChannelErrors,
      questionShouldAdvance: expectedQuestionAdvance,
      questionAdvanced,
      expectAnswerPersistence,
      expectations: candidate.level2?.expectations ?? null,
      answerPersistedBefore,
      answerPersistedThisTurn: answerPersistedAfter && !answerPersistedBefore,
    });
    telemetry.event("level3_hard_gate", {
      status: hardGate.status,
      codes: hardGate.codes,
      expectationResult: hardGate.expectationResult,
      expectationViolations: hardGate.expectationViolations,
    });
    turnRecord = {
      sessionId,
      sessionIndex,
      turn: candidate.turn ?? 1,
      scenario: candidate.behavior,
      audio: {
        startMs: realTiming.speechStart,
        endMs: realTiming.speechEnd,
        durationMs: realTiming.speechStart !== null && realTiming.speechEnd !== null
          ? Math.max(0, realTiming.speechEnd - realTiming.speechStart)
          : null,
      },
      transcription: {
        partialCount: traceEntries.filter((entry) => (
          liveEventTypeFromEntry(entry)?.endsWith("input_audio_transcription.delta")
        )).length,
        finalText: transcript,
        source: "gpt_live",
      },
      behavior: candidate.behavior,
      expectations: hardGate.expectations,
      observed: hardGate.observed,
      expectedConditions: hardGate.expectedConditions,
      expectationResult: hardGate.expectationResult,
      expectationViolations: hardGate.expectationViolations,
      interview: {
        questionId: currentQuestionId,
        candidate: answer.candidate,
        confirmed: answer.confirmed,
        answerState: answer.answerState,
        stateVersionBefore: stateVersion(before),
        stateVersionAfter: stateVersion(after),
        currentQuestionBefore: beforeInterview.currentQuestionId ?? null,
        currentQuestionAfter: afterInterview.currentQuestionId ?? null,
        nextQuestionId: afterInterview.currentQuestionId ?? null,
        nextQuestionText: null,
      },
      response: {
        assistantSpeechStarted: realTiming.assistantSpeechStarted !== null,
        assistantSpeechEnded: realTiming.assistantSpeechEnded !== null,
        assistantTranscript: assistantResponse,
        firstAudioLatencyMs: realTiming.speechEndToAssistantStartMs,
        speechEndToTranscriptFinalMs: realTiming.speechEndToTranscriptFinalMs,
        transcriptFinalToAssistantStartMs: realTiming.transcriptFinalToAssistantStartMs,
        speechEndToAssistantStartMs: realTiming.speechEndToAssistantStartMs,
      },
      realTiming,
      hardGate,
      jev: { called: false, sessionAction: null, nextUserBehavior: null },
      timeline: realTraceTimeline(traceEntries),
      errors: telemetry.errors,
      anomalies: hardGate.anomalies ?? hardGate.codes,
      transcript,
    };
    telemetry.recordTurn(turnRecord);
    return {
      sessionId,
      candidate,
      state: after,
      before,
      turn: turnRecord,
      hardGate,
      transcript,
      assistantResponse,
      realTiming,
      networkErrors,
      telemetry,
    };
  } catch (error) {
    telemetry.error(error);
    traceEntries = await readRealLiveTrace(page).catch(() => []);
    telemetry.setLiveTrace(traceEntries);
    const evidenceDir = testInfo.outputPath("level3", `session-${sessionIndex}`, "evidence");
    await writeEvidencePackage({
      testInfo,
      page,
      telemetry,
      state: after ?? before,
      networkErrors,
      candidate,
      audioFixturePath,
      assistantResponse,
      evidenceDir,
      replayCommand: level3ReplayCommand({ config, candidate }),
    });
    throw error;
  } finally {
    telemetry.setLiveTrace(traceEntries);
  }
}

export function liveEventTypeFromEntry(entry) {
  return entry?.args?.[0] === "gpt_live_event" && typeof entry?.args?.[1]?.type === "string"
    ? entry.args[1].type
    : null;
}

export function level3ReplayCommand({ config, candidate }) {
  const summary = config.level2SummaryPath ? ` VOICE_TEST_LEVEL2_SUMMARY=${config.level2SummaryPath}` : "";
  return "VOICE_TEST_REAL_API_ENABLED=true"
    + " VOICE_TEST_REAL_PROVIDER=gpt_live"
    + summary
    + " VOICE_TEST_SEED=" + candidate.seed
    + " pnpm exec playwright test tests/e2e/voice/live/level3-promotion.spec.mjs"
    + " --project=chromium --workers=1 --retries=0";
}

export function assertLevel3Fixture(audioFixturePath) {
  if (!audioFixturePath || !fs.existsSync(audioFixturePath)) {
    throw new Error(
      "Level 3 requires an existing prerecorded WAV via VOICE_TEST_AUDIO_FIXTURE; "
      + "the generated tone fixture is not a speech test input.",
    );
  }
}
