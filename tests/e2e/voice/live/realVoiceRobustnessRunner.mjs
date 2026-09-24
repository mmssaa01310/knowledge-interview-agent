import fs from "node:fs";
import path from "node:path";

import {
  extractFieldAnswer,
} from "../assertions/invariants.mjs";
import { readWavDurationSeconds } from "../fixtures/wavFixture.mjs";
import { observePageNetwork } from "../drivers/appRoutes.mjs";
import {
  fetchInterviewContext,
  fetchInterviewState,
  resetVoiceDemo,
  liveQuestionId,
  questionText,
} from "../drivers/apiDriver.mjs";
import { evaluateHardGate } from "../supervisor/hardGate.mjs";
import {
  realVoiceFixtureText,
  REAL_VOICE_BEHAVIORS,
  REAL_VOICE_BEHAVIORS_BY_ENGINE,
  REAL_VOICE_FIXTURE_TEXTS,
} from "../scenarios/scenarios.mjs";
import {
  dataChannelEventType,
  installRealLiveTrace,
  installVoiceDataChannelTrace,
  readRealLiveTrace,
  readVoiceDataChannelTrace,
  realRuntimeTimeline,
} from "./realLiveTrace.mjs";
import {
  VoiceTelemetry,
  writeEvidencePackage,
} from "../telemetry/telemetry.mjs";

export const ROBUSTNESS_ENGINE = Object.freeze({
  gpt_live: "gpt_live",
  transcribe_polly: "transcribe_polly",
});

const RECORD_URL = "/knowledge-dbs/dev-voice-demo-db/knowledges/"
  + "dev-voice-demo-knowledge/records/dev-voice-demo-record";
const DEFAULT_TURN_TIMEOUT_MS = 35_000;
const BEHAVIOR_FIXTURE_RE = /^(.*?)(?:[-_](\d+))?\.wav$/i;

const ANSWER_BEHAVIORS = new Set([
  "NORMAL",
  "HESITATION",
  "SELF_CORRECTION",
  "MULTI_ANSWER",
  "LONG_ANSWER",
  "ANSWER_FUTURE_QUESTION",
  "USER_INTERRUPTION",
  "CHANGE_PREVIOUS_ANSWER",
  "CONTRADICTION",
]);
const NO_ADVANCE_BEHAVIORS = new Set([
  "SILENCE",
  "ASK_CLARIFICATION",
  "AMBIGUOUS",
  "OFF_TOPIC",
  "DONT_KNOW",
  "PARTIAL_ANSWER",
]);
const TRANSCRIBE_SEGMENT_BEHAVIORS = new Set([
  "SPLIT_UTTERANCE",
  "SPLIT_UTTERANCE_LONG_PAUSE",
  "MULTIPLE_TRANSCRIBE_FINALS",
  "USER_RESUMES_BEFORE_COMMIT",
]);

function hasText(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function number(value, fallback = null) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function stateVersion(state) {
  return number(state?.interviewState?.stateVersion, 0);
}

function interviewState(state) {
  return state?.interviewState ?? state ?? {};
}

function sortedFields(context) {
  return [...(context?.fields ?? [])].sort(
    (left, right) => number(left.displayOrder, 0) - number(right.displayOrder, 0),
  );
}

function fieldForQuestion(context, questionId) {
  const fields = sortedFields(context);
  const normalized = String(questionId ?? "").replace(/^live-field-/, "");
  return fields.find((field) => field.id === normalized || `live-field-${field.id}` === questionId)
    ?? fields[0]
    ?? null;
}

function stateSnapshot(state) {
  const interview = interviewState(state);
  return {
    stateVersion: interview.stateVersion ?? null,
    currentQuestionId: interview.currentQuestionId ?? null,
    status: interview.status ?? null,
  };
}

function fieldAnswerPersisted(state, fieldId) {
  if (!fieldId) return false;
  const answer = extractFieldAnswer(interviewState(state), fieldId);
  return Boolean(
    hasText(answer.candidate)
    || hasText(answer.confirmed)
    || ["CONFIRMED", "ANSWERED"].includes(answer.answerState),
  );
}

function engineLabel(engine) {
  return engine === ROBUSTNESS_ENGINE.gpt_live ? "GPT-Live" : "Transcribe + Polly";
}

function normalizeBehavior(value) {
  const normalized = String(value ?? "").trim().toUpperCase();
  if (normalized === "HESITATE") return "HESITATION";
  if (normalized === "SELF_CORRECT") return "SELF_CORRECTION";
  return normalized;
}

function fixtureCandidates(directory, behavior, variant) {
  const numberSuffix = String(variant + 1);
  return [
    path.join(directory, `${behavior}-${numberSuffix}.wav`),
    path.join(directory, `${behavior}_${numberSuffix}.wav`),
    path.join(directory, `${behavior.toLowerCase()}-${numberSuffix}.wav`),
    path.join(directory, `${behavior.toLowerCase()}_${numberSuffix}.wav`),
  ];
}

export function resolveRealAudioFixture({
  config,
  behavior,
  variant = 0,
}) {
  const normalized = normalizeBehavior(behavior);
  if (config.audioFixtureDirectory) {
    const match = fixtureCandidates(config.audioFixtureDirectory, normalized, variant)
      .find((candidate) => fs.existsSync(candidate));
    if (match) return path.resolve(match);
  }
  if (config.audioFixturePath && fs.existsSync(config.audioFixturePath)) {
    return path.resolve(config.audioFixturePath);
  }
  if (normalized === "SILENCE" && config.audioFixtureDirectory) {
    const silence = fs.readdirSync(config.audioFixtureDirectory)
      .filter((name) => /^silence(?:[-_]\d+)?\.wav$/i.test(name))
      .sort()[variant];
    if (silence) return path.resolve(config.audioFixtureDirectory, silence);
  }
  throw new Error(
    `Missing real speech fixture for ${normalized} variant ${variant + 1}. `
      + "Set VOICE_TEST_AUDIO_FIXTURE_DIR or VOICE_TEST_AUDIO_FIXTURE.",
  );
}

export function realBehaviorPlan({
  engine = null,
  behaviors = engine ? REAL_VOICE_BEHAVIORS_BY_ENGINE[engine] ?? REAL_VOICE_BEHAVIORS : REAL_VOICE_BEHAVIORS,
  sessions = 0,
} = {}) {
  const selected = [...new Set(behaviors.map(normalizeBehavior))]
    .filter((behavior) => REAL_VOICE_FIXTURE_TEXTS[behavior]);
  if (!selected.length) return [];
  const requested = Number(sessions);
  const count = Number.isFinite(requested) && requested > 0 ? Math.floor(requested) : selected.length;
  return Array.from({ length: count }, (_, index) => ({
    behavior: selected[index % selected.length],
    variant: Math.floor(index / selected.length) % 3,
    ordinal: index + 1,
    fixtureText: realVoiceFixtureText(selected[index % selected.length], Math.floor(index / selected.length) % 3),
  }));
}

function behaviorExpectations(behavior, engine) {
  const normalized = normalizeBehavior(behavior);
  const isSegmentCase = engine === ROBUSTNESS_ENGINE.transcribe_polly
    && TRANSCRIBE_SEGMENT_BEHAVIORS.has(normalized);
  if (normalized === "SILENCE") {
    return {
      transcriptFinal: "not_required",
      answerPersistence: "must_not",
      questionAdvance: "must_not",
      assistantRecovery: "observe",
    };
  }
  if (NO_ADVANCE_BEHAVIORS.has(normalized)) {
    return {
      transcriptFinal: "required",
      answerPersistence: "not_required",
      questionAdvance: "must_not",
      assistantRecovery: "required",
    };
  }
  return {
    transcriptFinal: "required",
    answerPersistence: "required",
    questionAdvance: isSegmentCase ? "required" : "required",
    assistantRecovery: "required",
  };
}

function eventEntries(dataTrace, provider) {
  return dataTrace.filter((entry) => entry.direction === "server").map((entry) => ({
    timestampMs: entry.timestampMs,
    type: dataChannelEventType(entry),
    event: entry.event ?? {},
    provider,
  }));
}

function consoleEventEntries(consoleTrace) {
  return consoleTrace.map((entry) => ({
    timestampMs: entry.timestampMs,
    type: entry.args?.[0] === "gpt_live_event" ? entry.args?.[1]?.type : null,
    logger: typeof entry.args?.[0] === "string" ? entry.args[0] : null,
    details: entry.args?.[1] ?? {},
    provider: "gpt_live",
  }));
}

function findFirst(entries, predicate) {
  return entries.find(predicate) ?? null;
}

function collectTranscript(dataEntries, consoleEntries, provider) {
  const runtime = eventEntries(dataEntries, provider);
  const finals = runtime.filter((entry) => entry.type === "user_transcript_final");
  const gptFinals = consoleEntries.filter((entry) => (
    entry.args?.[0] === "gpt_live_event"
      && typeof entry.args?.[1]?.type === "string"
      && /input_(?:audio_)?transcript\.(?:completed|done)$/.test(entry.args[1].type)
  ));
  const allFinals = provider === ROBUSTNESS_ENGINE.gpt_live ? gptFinals : finals;
  const texts = allFinals.map((entry) => (
    entry.event?.transcript ?? entry.args?.[1]?.transcript ?? entry.event?.text ?? ""
  )).filter(hasText);
  const partials = runtime.filter((entry) => entry.type === "user_transcript_partial");
  const gptDeltas = consoleEntries.filter((entry) => (
    entry.args?.[0] === "gpt_live_event"
      && entry.args?.[1]?.type === "session.input_transcript.delta"
  ));
  return {
    finalText: texts.join(" ").trim(),
    finalCount: allFinals.length,
    partialCount: partials.length + gptDeltas.length,
    finalEntries: allFinals,
    partialEntries: partials,
  };
}

function collectAssistant(dataEntries, consoleEntries, provider) {
  const runtime = eventEntries(dataEntries, provider);
  const backchannels = runtime.filter((entry) => entry.type === "assistant_backchannel");
  const runtimeMain = runtime.filter((entry) => entry.type === "assistant_speech_started");
  const gptMain = consoleEntries.filter((entry) => (
    entry.args?.[0] === "gpt_live_response_started"
      || (entry.args?.[0] === "gpt_live_event" && entry.args?.[1]?.type === "response.created")
  ));
  const allStarts = provider === ROBUSTNESS_ENGINE.gpt_live ? gptMain : runtimeMain;
  const speechEnd = [...runtime, ...consoleEntries.map((entry) => ({
    timestampMs: entry.timestampMs,
    type: entry.args?.[0] === "gpt_live_user_speech_end" ? "gpt_live_user_speech_end" : null,
  }))]
    .filter((entry) => entry.type === "user_speech_ended" || entry.type === "gpt_live_user_speech_end")
    .sort((left, right) => number(left.timestampMs, 0) - number(right.timestampMs, 0))[0]?.timestampMs ?? null;
  const starts = speechEnd === null
    ? allStarts
    : allStarts.filter((entry) => number(entry.timestampMs, 0) >= speechEnd);
  const backchannelsAfterSpeech = speechEnd === null
    ? backchannels
    : backchannels.filter((entry) => number(entry.timestampMs, 0) >= speechEnd);
  const transcriptLines = runtime
    .filter((entry) => entry.type === "assistant_transcript_final")
    .filter((entry) => speechEnd === null || number(entry.timestampMs, 0) >= speechEnd)
    .map((entry) => entry.event?.text)
    .filter(hasText);
  return {
    starts,
    backchannels: backchannelsAfterSpeech,
    mainResponseCount: starts.length,
    ackCount: backchannels.length,
    text: transcriptLines.join(" ").trim(),
  };
}

function extractTiming({
  provider,
  dataTrace,
  consoleTrace,
  transcript,
  assistant,
  stateObservedAt,
}) {
  const runtime = eventEntries(dataTrace, provider);
  const consoleEvents = consoleEventEntries(consoleTrace);
  const combined = [...runtime, ...consoleEvents].sort((left, right) => (
    number(left.timestampMs, 0) - number(right.timestampMs, 0)
  ));
  const speechStart = findFirst(combined, (entry) => (
    entry.type === "user_speech_started"
      || entry.type === "session.input_audio_buffer.speech_started"
      || entry.type === "input_audio_buffer.speech_started"
  ));
  const speechEnd = findFirst(combined, (entry) => (
    entry.type === "user_speech_ended"
      || entry.type === "gpt_live_user_speech_end"
      || entry.type === "session.input_audio_buffer.speech_stopped"
      || entry.type === "input_audio_buffer.speech_stopped"
  ));
  const final = transcript.finalEntries[0] ?? findFirst(combined, (entry) => (
    entry.type === "user_transcript_final"
      || /input_(?:audio_)?transcript\.(?:completed|done)$/.test(entry.type ?? "")
  ));
  const finalAt = final?.timestampMs ?? null;
  const speechEndAt = speechEnd?.timestampMs ?? null;
  const assistantStart = assistant.starts[0] ?? null;
  const assistantStartAt = assistantStart?.timestampMs ?? null;
  const commitAt = consoleEvents.find((entry) => entry.logger === "gpt_live_state_persisted")?.timestampMs
    ?? stateObservedAt
    ?? null;
  const ackAt = assistant.backchannels[0]?.timestampMs ?? null;
  const mainAfterAck = assistant.starts.find((entry) => (
    ackAt === null || entry.timestampMs > ackAt
  ));
  return {
    speechStart: speechStart?.timestampMs ?? null,
    speechEnd: speechEndAt,
    transcriptFinal: finalAt,
    turnCommit: commitAt,
    assistantSpeechStarted: assistantStartAt,
    assistantSpeechEnded: findFirst(combined, (entry) => (
      entry.type === "assistant_speech_ended" || entry.logger === "gpt_live_response_done"
    ))?.timestampMs ?? null,
    speechEndToTranscriptFinalMs: speechEndAt !== null && finalAt !== null
      ? Math.max(0, finalAt - speechEndAt) : null,
    transcriptFinalToAssistantStartMs: finalAt !== null && assistantStartAt !== null
      ? Math.max(0, assistantStartAt - finalAt) : null,
    speechEndToAssistantStartMs: speechEndAt !== null && assistantStartAt !== null
      ? Math.max(0, assistantStartAt - speechEndAt) : null,
    segmentFinalToTurnCommitMs: finalAt !== null && commitAt !== null
      ? Math.max(0, commitAt - finalAt) : null,
    turnCommitToAckMs: commitAt !== null && ackAt !== null
      ? Math.max(0, ackAt - commitAt) : null,
    ackToMainResponseMs: ackAt !== null && mainAfterAck?.timestampMs !== undefined
      ? Math.max(0, mainAfterAck.timestampMs - ackAt) : null,
    fullTurnLatencyMs: speechEndAt !== null && assistantStartAt !== null
      ? Math.max(0, assistantStartAt - speechEndAt) : null,
  };
}

async function waitForTrace(page, predicate, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await page.evaluate(() => ({
      live: window.__kikioriVoiceLiveTrace?.entries ?? [],
      channel: window.__kikioriVoiceDataChannelTrace?.entries ?? [],
    })).catch(() => ({ live: [], channel: [] }));
    if (predicate(value)) return value;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return page.evaluate(() => ({
    live: window.__kikioriVoiceLiveTrace?.entries ?? [],
    channel: window.__kikioriVoiceDataChannelTrace?.entries ?? [],
  })).catch(() => ({ live: [], channel: [] }));
}

function eventTypes(dataTrace, consoleTrace) {
  return [
    ...dataTrace.map((entry) => dataChannelEventType(entry)).filter(Boolean),
    ...consoleTrace
      .filter((entry) => entry.args?.[0] === "gpt_live_event")
      .map((entry) => entry.args?.[1]?.type)
      .filter(Boolean),
  ];
}

function responseIds(dataTrace, consoleTrace) {
  const ids = [];
  for (const entry of dataTrace) {
    const type = dataChannelEventType(entry);
    if (type !== "assistant_speech_started" && type !== "response.created") continue;
    const id = entry.event?.responseId ?? entry.event?.response_id ?? entry.event?.response?.id;
    if (typeof id === "string") ids.push(id);
  }
  for (const entry of consoleTrace) {
    const type = entry.args?.[0] === "gpt_live_event" ? entry.args?.[1]?.type : null;
    if (entry.args?.[0] !== "gpt_live_response_started" && type !== "response.created") continue;
    const id = entry.args?.[1]?.response_id ?? entry.args?.[1]?.responseId;
    if (typeof id === "string") ids.push(id);
  }
  return ids;
}

function failureCodes({ behavior, engine, hardGate, transcript, assistant, dataTrace, consoleTrace }) {
  const codes = [...(hardGate?.codes ?? [])];
  const types = eventTypes(dataTrace, consoleTrace);
  const expected = behaviorExpectations(behavior, engine);
  if (assistant.mainResponseCount > 1) codes.push("DUPLICATE_RESPONSE");
  if (engine === ROBUSTNESS_ENGINE.transcribe_polly && assistant.ackCount > 1) {
    codes.push("ACK_DUPLICATE");
  }
  if (types.some((type) => /data[_-]?channel.*error|websocket.*error|transport\.closed/i.test(type))) {
    codes.push("WebSocket/DataChannel error");
  }
  if (expected.transcriptFinal === "required" && !hasText(transcript.finalText)) {
    codes.push("TRANSCRIPT_FINAL_MISSING");
  }
  return [...new Set(codes)];
}

function questionAdvanced({ before, after, currentField, assistant, dataTrace }) {
  const beforeInterview = interviewState(before);
  const afterInterview = interviewState(after);
  // The application uses q-### in runtime events and live-field-* in the
  // context payload. Compare the persisted State IDs only; a transcript
  // event's questionId is the question being answered, not evidence that the
  // checklist advanced.
  if (beforeInterview.currentQuestionId !== afterInterview.currentQuestionId) {
    return beforeInterview.currentQuestionId !== null && afterInterview.currentQuestionId !== null;
  }
  return false;
}

export function robustnessSessionId({ engine, seed, index, behavior, variant }) {
  return `robust-${engine}-${seed}-session-${index}-${behavior.toLowerCase()}-${variant + 1}`;
}

export async function runRealVoiceRobustnessSession({
  browserType,
  request,
  testInfo,
  config,
  guard,
  engine,
  behavior,
  variant,
  sessionIndex,
}) {
  const normalizedBehavior = normalizeBehavior(behavior);
  const fixturePath = resolveRealAudioFixture({ config, behavior: normalizedBehavior, variant });
  const sessionId = robustnessSessionId({
    engine,
    seed: config.seed,
    index: sessionIndex,
    behavior: normalizedBehavior,
    variant,
  });
  const sessionDir = path.join(config.outputDir, "robustness", engine, `session-${sessionIndex}-${normalizedBehavior.toLowerCase()}`);
  const evidenceDir = path.join(sessionDir, "evidence");
  const telemetry = new VoiceTelemetry({
    outputDir: sessionDir,
    sessionId,
    scenario: normalizedBehavior,
    seed: config.seed,
    costGuard: guard,
    audioInputMode: "fake_microphone_wav",
    transcriptSource: engine,
    engine,
    provider: engine,
    liveApiUsed: true,
    level: 3,
    audioFixturePath: fixturePath,
  });
  const browser = await browserType.launch({
    headless: true,
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      "--use-file-for-fake-audio-capture=" + fixturePath,
      "--autoplay-policy=no-user-gesture-required",
    ],
  });
  const context = await browser.newContext({
    baseURL: config.baseURL,
    permissions: ["microphone"],
  });
  const page = await context.newPage();
  const networkErrors = observePageNetwork(page, telemetry);
  const expectations = behaviorExpectations(normalizedBehavior, engine);
  const startedAt = Date.now();
  let before = null;
  let after = null;
  let contextData = null;
  let hardGate = null;
  let finalTrace = { live: [], channel: [] };
  let evidenceWritten = false;
  let tracingStarted = false;
  const turn = 1;

  try {
    try {
      await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
      tracingStarted = true;
    } catch (error) {
      if (/already (?:been )?started|been already started/i.test(error instanceof Error ? error.message : String(error))) {
        // Playwright may already own tracing for the test context. Reuse it
        // so the evidence package still receives a trace on failure.
        tracingStarted = true;
      } else {
        throw error;
      }
    }
    await installRealLiveTrace(page);
    await installVoiceDataChannelTrace(page);
    await resetVoiceDemo(request);
    contextData = await fetchInterviewContext(request);
    before = await fetchInterviewState(request);
    const fields = sortedFields(contextData);
    let currentField = fieldForQuestion(contextData, interviewState(before).currentQuestionId);
    let currentQuestionId = currentField ? liveQuestionId(currentField) : null;
    let currentQuestion = currentField ? questionText(currentField) : null;
    telemetry.event("robustness_session_started", {
      engine,
      provider: engine,
      behavior: normalizedBehavior,
      variant: variant + 1,
      fixturePath,
      fixtureText: realVoiceFixtureText(normalizedBehavior, variant),
      currentQuestionId,
      currentQuestion,
      baseline: stateSnapshot(before),
    });
    await page.addInitScript(() => {
      window.localStorage.setItem("ai-interviewer-dev-token", "dev-manager");
    });
    await page.goto(RECORD_URL);
    await page.selectOption("select", engine);
    await page.locator(".voice-conversation-button").click();
    await page.waitForFunction(
      () => document.querySelector(".voice-conversation-button")?.classList.contains("active"),
      null,
      { timeout: 30_000 },
    );
    // The reset response has no active question yet. Establish the turn
    // baseline after the provider has sent its initial question, otherwise
    // q-001 would be mistaken for an answer-driven question transition.
    const readyDeadline = Date.now() + 30_000;
    while (Date.now() < readyDeadline) {
      const readyState = await fetchInterviewState(request).catch(() => null);
      if (readyState && interviewState(readyState).currentQuestionId) {
        before = readyState;
        currentField = fieldForQuestion(contextData, interviewState(before).currentQuestionId);
        currentQuestionId = currentField ? liveQuestionId(currentField) : null;
        currentQuestion = currentField ? questionText(currentField) : null;
        telemetry.event("turn_baseline_ready", {
          baseline: stateSnapshot(before),
          currentQuestionId,
        });
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    telemetry.event("voice_session_ready", { engine });

    const audioSeconds = readWavDurationSeconds(fixturePath);
    guard.startTurn(audioSeconds);
    telemetry.event("audio_fixture_started", {
      fixturePath,
      durationSeconds: audioSeconds,
      engine,
    });

    const timeoutMs = Number.parseInt(
      process.env.VOICE_TEST_REAL_TURN_TIMEOUT_MS || String(DEFAULT_TURN_TIMEOUT_MS),
      10,
    );
    const trace = await waitForTrace(page, (value) => {
      const runtime = eventEntries(value.channel, engine);
      const hasFinal = engine === ROBUSTNESS_ENGINE.transcribe_polly
        ? runtime.some((entry) => entry.type === "user_transcript_final")
        : value.live.some((entry) => (
          entry.args?.[0] === "gpt_live_event"
            && /input_(?:audio_)?transcript\.(?:completed|done)$/.test(entry.args?.[1]?.type ?? "")
        ));
      return normalizedBehavior === "SILENCE" ? Date.now() - startedAt > 8_000 : hasFinal;
    }, timeoutMs);
    finalTrace = trace;
    const preliminaryTranscript = collectTranscript(trace.channel, trace.live, engine);
    if (preliminaryTranscript.finalCount > 0 || normalizedBehavior === "SILENCE") {
      telemetry.event("transcript_final", {
        finalText: preliminaryTranscript.finalText,
        finalCount: preliminaryTranscript.finalCount,
        partialCount: preliminaryTranscript.partialCount,
        engine,
      });
    }

    // If the provider never emits a final transcript, there is no useful
    // reason to keep polling the State Store for the full turn timeout. Keep
    // the failure evidence bounded and let the hard gate classify it.
    const stateWaitMs = normalizedBehavior === "SILENCE"
      ? 8_000
      : preliminaryTranscript.finalCount > 0
        ? timeoutMs
        : Math.min(timeoutMs, 3_000);
    const stateDeadline = Date.now() + stateWaitMs;
    let stateObservedAt = null;
    while (Date.now() < stateDeadline) {
      const candidateState = await fetchInterviewState(request).catch(() => null);
      const stateChanged = candidateState && stateVersion(candidateState) > stateVersion(before);
      const questionChanged = candidateState
        && interviewState(candidateState).currentQuestionId !== interviewState(before).currentQuestionId;
      const answerReady = candidateState && fieldAnswerPersisted(candidateState, currentField?.id);
      if (candidateState && (
        normalizedBehavior === "SILENCE"
        || (stateChanged && (
          answerReady
          || questionChanged
          || NO_ADVANCE_BEHAVIORS.has(normalizedBehavior)
        ))
      )) {
        after = candidateState;
        stateObservedAt = Date.now();
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    after ??= await fetchInterviewState(request).catch(() => before);
    stateObservedAt ??= Date.now();
    telemetry.event("interview_state_observed", {
      before: stateSnapshot(before),
      after: stateSnapshot(after),
      stateVersionAdvanced: stateVersion(after) > stateVersion(before),
    });

    const afterTrace = await page.evaluate(() => ({
      live: window.__kikioriVoiceLiveTrace?.entries ?? [],
      channel: window.__kikioriVoiceDataChannelTrace?.entries ?? [],
    })).catch(() => finalTrace);
    finalTrace = afterTrace;
    telemetry.setLiveTrace(afterTrace.live);
    const transcript = collectTranscript(afterTrace.channel, afterTrace.live, engine);
    const assistant = collectAssistant(afterTrace.channel, afterTrace.live, engine);
    const timing = extractTiming({
      provider: engine,
      dataTrace: afterTrace.channel,
      consoleTrace: afterTrace.live,
      transcript,
      assistant,
      stateObservedAt,
    });
    const answer = extractFieldAnswer(
      interviewState(after),
      currentField?.id,
    );
    const advanced = questionAdvanced({
      before,
      after,
      currentField,
      assistant,
      dataTrace: afterTrace.channel,
    });
    const responseIdsValue = responseIds(afterTrace.channel, afterTrace.live);
    const runtimeEvents = eventTypes(afterTrace.channel, afterTrace.live);
    const persisted = Boolean(
      hasText(answer.candidate)
      || hasText(answer.confirmed)
      || ["CONFIRMED", "ANSWERED"].includes(answer.answerState),
    );
    const hardGateBase = evaluateHardGate({
      turn,
      maxTurns: config.maxTurnsPerSession,
      jevCalls: 0,
      maxJevCalls: undefined,
      state: interviewState(after),
      fieldId: currentField?.id,
      expectedQuestionId: currentQuestionId,
      finalText: transcript.finalText,
      responseIds: responseIdsValue,
      eventTypes: runtimeEvents,
      responseStarted: assistant.mainResponseCount > 0,
      committedBeforeFinal: timing.turnCommit !== null
        && timing.transcriptFinal !== null
        && timing.turnCommit < timing.transcriptFinal,
      apiStatuses: telemetry.networkLog
        .filter((item) => item.direction === "response")
        .map((item) => item.status),
      dataChannelErrors: telemetry.dataChannelErrors,
      questionShouldAdvance: expectations.questionAdvance === "required",
      questionAdvanced: advanced,
      expectAnswerPersistence: expectations.answerPersistence === "required",
      expectations,
      answerPersistedBefore: false,
      answerPersistedThisTurn: persisted,
    });
    const extraFailures = failureCodes({
      behavior: normalizedBehavior,
      engine,
      hardGate: hardGateBase,
      transcript,
      assistant,
      dataTrace: afterTrace.channel,
      consoleTrace: afterTrace.live,
    });
    hardGate = {
      ...hardGateBase,
      status: extraFailures.length ? "FAIL" : hardGateBase.status,
      passed: !extraFailures.length && hardGateBase.passed,
      shouldCallJev: false,
      codes: [...new Set([...hardGateBase.codes, ...extraFailures])],
      anomalies: [...new Set([...(hardGateBase.anomalies ?? []), ...extraFailures])],
      reason: extraFailures.length ? "real_voice_robustness_failure" : hardGateBase.reason,
    };
    telemetry.event("hard_gate", {
      status: hardGate.status,
      codes: hardGate.codes,
      expectations,
      observed: hardGate.observed,
    });
    const timeline = realRuntimeTimeline({
      consoleEntries: afterTrace.live,
      dataChannelEntries: afterTrace.channel,
    });
    const turnRecord = {
      sessionId,
      sessionIndex,
      turn,
      engine,
      provider: engine,
      behavior: normalizedBehavior,
      audio: {
        startMs: timing.speechStart,
        endMs: timing.speechEnd,
        durationMs: timing.speechStart !== null && timing.speechEnd !== null
          ? Math.max(0, timing.speechEnd - timing.speechStart)
          : null,
      },
      transcription: {
        partialCount: transcript.partialCount,
        finalText: transcript.finalText,
        finalCount: transcript.finalCount,
        source: engine,
      },
      expectations,
      observed: {
        transcriptFinal: hasText(transcript.finalText),
        transcriptFinalCount: transcript.finalCount,
        candidateAnswerExists: hasText(answer.candidate),
        confirmedAnswerExists: hasText(answer.confirmed),
        answerState: answer.answerState,
        stateVersionAdvanced: stateVersion(after) > stateVersion(before),
        questionAdvanced: advanced,
        assistantResponseCount: assistant.mainResponseCount,
        ackCount: assistant.ackCount,
      },
      expectedConditions: [],
      expectationResult: hardGate.expectationResult,
      expectationViolations: hardGate.expectationViolations,
      interview: {
        questionId: currentQuestionId,
        currentQuestionText: currentQuestion,
        candidate: answer.candidate,
        confirmed: answer.confirmed,
        answerState: answer.answerState,
        stateVersionBefore: stateVersion(before),
        stateVersionAfter: stateVersion(after),
        currentQuestionBefore: interviewState(before).currentQuestionId ?? null,
        currentQuestionAfter: interviewState(after).currentQuestionId ?? null,
        nextQuestionId: interviewState(after).currentQuestionId ?? null,
        nextQuestionText: assistant.text || null,
      },
      response: {
        assistantSpeechStarted: assistant.mainResponseCount > 0,
        assistantSpeechEnded: timing.assistantSpeechEnded !== null,
        assistantTranscript: assistant.text || null,
        firstAudioLatencyMs: timing.speechEndToAssistantStartMs,
        speechEndToTranscriptFinalMs: timing.speechEndToTranscriptFinalMs,
        transcriptFinalToAssistantStartMs: timing.transcriptFinalToAssistantStartMs,
        speechEndToAssistantStartMs: timing.speechEndToAssistantStartMs,
        fullTurnLatencyMs: timing.fullTurnLatencyMs,
        segmentFinalToTurnCommitMs: timing.segmentFinalToTurnCommitMs,
        turnCommitToAckMs: timing.turnCommitToAckMs,
        ackToMainResponseMs: timing.ackToMainResponseMs,
        assistantResponseCount: assistant.mainResponseCount,
        ackCount: assistant.ackCount,
        segmentFinalCount: transcript.finalCount,
      },
      timing,
      hardGate,
      timeline,
      conversation: {
        engine: engineLabel(engine),
        question: currentQuestion,
        user: transcript.finalText,
        assistant: assistant.text,
      },
      transcript: transcript.finalText,
      anomalies: hardGate.anomalies ?? hardGate.codes,
      errors: telemetry.errors,
    };
    telemetry.transcriptFinal({
      turn,
      source: engine,
      finalText: transcript.finalText,
      sourceTimestampMs: timing.transcriptFinal,
    });
    telemetry.recordTurn(turnRecord);
    fs.writeFileSync(path.join(sessionDir, "telemetry.json"), JSON.stringify(telemetry.snapshot(), null, 2));
    fs.writeFileSync(path.join(sessionDir, "interview-state.json"), JSON.stringify(after, null, 2));
    if (hardGate.status === "FAIL") {
      await writeEvidencePackage({
        testInfo,
        page,
        telemetry,
        state: after,
        networkErrors,
        evidenceDir,
        audioFixturePath: fixturePath,
        assistantResponse: assistant.text,
        replayCommand: robustnessReplayCommand({ config, engine, behavior: normalizedBehavior }),
      });
      evidenceWritten = true;
      if (tracingStarted) {
        await context.tracing.stop({ path: path.join(evidenceDir, "trace.zip") });
        tracingStarted = false;
      }
      fs.writeFileSync(path.join(evidenceDir, "playwright-trace-path.txt"), path.join(evidenceDir, "trace.zip") + "\n");
    } else {
      if (tracingStarted) {
        await context.tracing.stop();
        tracingStarted = false;
      }
    }
    return {
      sessionId,
      index: sessionIndex,
      engine,
      provider: engine,
      behavior: normalizedBehavior,
      variant: variant + 1,
      fixturePath,
      status: hardGate.status === "FAIL" ? "FAIL" : "PASS",
      turn: turnRecord,
      hardGate,
      before,
      state: after,
      networkErrors,
      telemetry,
      evidenceWritten,
    };
  } catch (error) {
    telemetry.error(error);
    const trace = await page.evaluate(() => ({
      live: window.__kikioriVoiceLiveTrace?.entries ?? [],
      channel: window.__kikioriVoiceDataChannelTrace?.entries ?? [],
    })).catch(() => finalTrace);
    finalTrace = trace;
    telemetry.setLiveTrace(trace.live);
    if (!evidenceWritten) {
      await writeEvidencePackage({
        testInfo,
        page,
        telemetry,
        state: after ?? before,
        networkErrors,
        evidenceDir,
        audioFixturePath: fixturePath,
        replayCommand: robustnessReplayCommand({ config, engine, behavior: normalizedBehavior }),
      }).catch(() => undefined);
      if (tracingStarted) {
        await context.tracing.stop({ path: path.join(evidenceDir, "trace.zip") }).catch(() => undefined);
        tracingStarted = false;
      }
      fs.writeFileSync(path.join(evidenceDir, "playwright-trace-path.txt"), path.join(evidenceDir, "trace.zip") + "\n");
    }
    return {
      sessionId,
      index: sessionIndex,
      engine,
      provider: engine,
      behavior: normalizedBehavior,
      variant: variant + 1,
      fixturePath,
      status: "ERROR",
      error: error instanceof Error ? error.message : String(error),
      before,
      state: after ?? before,
      telemetry,
      networkErrors,
      evidenceWritten: true,
    };
  } finally {
    await context.close().catch(() => undefined);
    await browser.close().catch(() => undefined);
  }
}

export function robustnessReplayCommand({ config, engine, behavior }) {
  return "set -a; . ./.env; set +a; "
    + "VOICE_TEST_REAL_API_ENABLED=true "
    + "VOICE_TEST_REAL_PROVIDER=" + engine + " "
    + "VOICE_TEST_ROBUSTNESS=true "
    + "VOICE_TEST_ROBUSTNESS_BEHAVIORS=" + behavior + " "
    + "VOICE_TEST_SEED=" + config.seed + " "
    + "pnpm exec playwright test tests/e2e/voice/live/conversation-robustness.spec.mjs "
    + "--project=chromium --workers=1 --retries=0";
}
