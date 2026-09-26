import fs from "node:fs";
import path from "node:path";

import {
  extractFieldAnswer,
} from "../assertions/invariants.mjs";
import { readWavMetadata } from "../fixtures/wavFixture.mjs";
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
  dataChannelEventPayload,
  installRealLiveTrace,
  installVoiceAudioTrace,
  installVoiceDataChannelTrace,
  installWavFixtureMicrophone,
  replayWavFixtureMicrophone,
  startWavFixtureMicrophone,
  readRealLiveTrace,
  readVoiceAudioTrace,
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
const DEFAULT_GPT_LIVE_INITIAL_RESPONSE_IDLE_MS = 2_500;
// GPT-Live may pause output transcript deltas for several seconds while the
// same spoken response is still in progress. This is the bounded drain
// window used by the test harness because the browser does not expose a
// reliable per-response audio-complete event; it is not the application
// response timeout and does not change runtime behavior.
const DEFAULT_GPT_LIVE_OUTPUT_IDLE_MS = 6_000;
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

function resolveRealAudioFixtureSequence({ config, behavior, variant = 0 }) {
  if (!config.audioFixtureDirectory || config.audioFixturePath) {
    return [resolveRealAudioFixture({ config, behavior, variant })];
  }
  const available = [];
  for (let candidateVariant = 0; candidateVariant < 10; candidateVariant += 1) {
    const match = fixtureCandidates(config.audioFixtureDirectory, behavior, candidateVariant)
      .find((candidate) => fs.existsSync(candidate));
    if (match) available.push(path.resolve(match));
  }
  if (!available.length) {
    return [resolveRealAudioFixture({ config, behavior, variant })];
  }
  return available.slice(variant).concat(available.slice(0, variant));
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
  return Array.from({ length: count }, (_, index) => {
    const behavior = selected[index % selected.length];
    // A multi-turn fixture directory is an ordered conversation, not a set
    // of interchangeable opening utterances. When a run repeats one
    // behavior (for example NORMAL x 3 sessions), every session must start
    // from the first fixture so its question lifecycle is comparable. Keep
    // rotation for a repeated behavior matrix with multiple behaviors, where
    // a later pass may intentionally use another deterministic variant.
    const variant = selected.length === 1
      ? 0
      : Math.floor(index / selected.length) % 3;
    return {
      behavior,
      variant,
      ordinal: index + 1,
      fixtureText: realVoiceFixtureText(behavior, variant),
    };
  });
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

function eventEntries(dataTrace, provider, { includeClient = false } = {}) {
  return dataTrace.filter((entry) => includeClient || entry.direction === "server").map((entry) => ({
    timestampMs: entry.timestampMs,
    type: dataChannelEventType(entry),
    event: dataChannelEventPayload(entry),
    envelope: entry.event ?? {},
    delegationId: entry.event?.delegation_id ?? null,
    provider,
  }));
}

function traceEventType(entry) {
  if (typeof entry?.type === "string") return entry.type;
  if (entry?.args?.[0] === "gpt_live_event" && typeof entry.args?.[1]?.type === "string") {
    return entry.args[1].type;
  }
  if (typeof entry?.event?.type === "string") return entry.event.type;
  return null;
}

/**
 * GPT-Live transcript events do not have a guaranteed response.done event.
 * The real runner therefore treats an observed output-transcript idle period
 * as the initial greeting boundary. This is deliberately protocol-based and
 * does not inspect the greeting text or a question-specific keyword.
 */
export function isGPTLiveInitialResponseReady(
  liveEntries,
  {
    nowMs = Date.now(),
    idleMs = DEFAULT_GPT_LIVE_INITIAL_RESPONSE_IDLE_MS,
  } = {},
) {
  const idle = Number(idleMs);
  const latestOutputTimestamp = (liveEntries ?? [])
    .filter((entry) => (
      entry?.direction === undefined || entry.direction === "server"
    ))
    .filter((entry) => traceEventType(entry) === "session.output_transcript.delta")
    .map((entry) => number(entry.timestampMs, null))
    .filter((timestamp) => timestamp !== null)
    .reduce((latest, timestamp) => Math.max(latest, timestamp), null);
  return latestOutputTimestamp !== null
    && Number.isFinite(Number(nowMs))
    && Number.isFinite(idle)
    && idle >= 0
    && Number(nowMs) - latestOutputTimestamp >= idle;
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

export function collectTranscript(
  dataEntries,
  consoleEntries,
  provider,
  { boundaryAt = null } = {},
) {
  const isAfterBoundary = (entry) => (
    boundaryAt === null || number(entry.timestampMs, 0) >= number(boundaryAt, 0)
  );
  const runtime = eventEntries(dataEntries, provider).filter(isAfterBoundary);
  const finals = runtime.filter((entry) => entry.type === "user_transcript_final");
  const gptFinals = runtime.filter((entry) => (
    /(?:^|\.)input_(?:audio_)?transcript\.(?:completed|done)$/.test(entry.type ?? "")
      || /(?:^|\.)input_audio_transcription\.(?:completed|done)$/.test(entry.type ?? "")
  ));
  const scopedConsoleEntries = consoleEntries.filter(isAfterBoundary);
  const consoleGptFinals = scopedConsoleEntries.filter((entry) => (
    entry.args?.[0] === "gpt_live_event"
      && typeof entry.args?.[1]?.type === "string"
      && /(?:input_(?:audio_)?transcript|input_audio_transcription)\.(?:completed|done)$/.test(entry.args[1].type)
  ));
  const applicationGptFinals = scopedConsoleEntries.filter((entry) => (
    entry.args?.[0] === "gpt_live_input_transcript_final"
  ));
  const allFinals = provider === ROBUSTNESS_ENGINE.gpt_live
    ? (gptFinals.length ? gptFinals : consoleGptFinals.length ? consoleGptFinals : applicationGptFinals)
    : finals;
  const partials = runtime.filter((entry) => entry.type === "user_transcript_partial");
  const gptDeltas = runtime.filter((entry) => (
    /(?:^|\.)input_(?:audio_)?transcript\.delta$/.test(entry.type ?? "")
      || /(?:^|\.)input_audio_transcription\.delta$/.test(entry.type ?? "")
  ));
  const consoleGptDeltas = scopedConsoleEntries.filter((entry) => (
    entry.args?.[0] === "gpt_live_event"
      && typeof entry.args?.[1]?.type === "string"
      && /(?:input_(?:audio_)?transcript|input_audio_transcription)\.delta$/.test(entry.args[1].type)
  ));
  const deltaEntries = gptDeltas.length ? gptDeltas : consoleGptDeltas;
  const deltaText = deltaEntries.map((entry) => (
    entry.event?.delta ?? entry.args?.[1]?.delta
  )).filter(hasText).join("").trim();
  const texts = allFinals.map((entry) => (
    entry.event?.transcript ?? entry.args?.[1]?.transcript ?? entry.event?.text ?? ""
  )).filter(hasText);
  return {
    // GPT-Live deliberately exposes transcript deltas without a completed
    // turn event. When the application emits its transport grouping marker,
    // the accumulated deltas become the application-level final transcript.
    finalText: texts.join(" ").trim() || (applicationGptFinals.length ? deltaText : ""),
    finalCount: allFinals.length,
    partialText: deltaText,
    partialCount: partials.length + deltaEntries.length,
    finalEntries: allFinals,
    partialEntries: [...partials, ...deltaEntries],
  };
}

export function collectAssistant(
  dataEntries,
  consoleEntries,
  provider,
  { boundaryAt = null, inputEndAt = null, endAt = null } = {},
) {
  const allRuntime = eventEntries(dataEntries, provider);
  const allRuntimeWithClientEvents = eventEntries(dataEntries, provider, { includeClient: true });
  const allConsoleEntries = consoleEntries;
  // A full-session trace contains later user turns by the time the current
  // turn is finalized. When the persistence boundary is known, use the
  // first subsequent input-transcript event as the end of this turn so a
  // later Live response cannot be attributed to the current one.
  const nextInputTranscriptAt = inputEndAt === null
    ? null
    : allRuntime
      .filter((entry) => (
        /(?:^|\.)input_(?:audio_)?transcript\.delta$/.test(entry.type ?? "")
          || /(?:^|\.)input_audio_transcription\.delta$/.test(entry.type ?? "")
      ))
      .map((entry) => number(entry.timestampMs, null))
      .filter((timestamp) => timestamp !== null && timestamp > number(inputEndAt, 0))
      .sort((left, right) => left - right)[0] ?? null;
  const effectiveEndAt = endAt ?? nextInputTranscriptAt;
  const isInWindow = (entry) => (
    (boundaryAt === null || number(entry.timestampMs, 0) >= number(boundaryAt, 0))
      && (effectiveEndAt === null || number(entry.timestampMs, 0) < effectiveEndAt)
  );
  const runtime = allRuntime.filter(isInWindow);
  const runtimeWithClientEvents = allRuntimeWithClientEvents.filter(isInWindow);
  const scopedConsoleEntries = allConsoleEntries.filter(isInWindow);
  const backchannels = runtime.filter((entry) => entry.type === "assistant_backchannel");
  const backchannelResponseIds = new Set(backchannels
    .map((entry) => entry.event?.responseId ?? entry.event?.response_id)
    .filter((value) => typeof value === "string" && value.length > 0));
  const runtimeMain = runtime.filter((entry) => (
    entry.type === "assistant_speech_started"
      && !backchannelResponseIds.has(entry.event?.responseId ?? entry.event?.response_id)
  ));
  // Responses Delegation events can arrive directly on the same DataChannel
  // as Live events. In that form their response.created payload is not
  // wrapped in response.event, so filtering only by the envelope type counts
  // the semantic evaluator as a second Live response. The application emits
  // gpt_live_response_started only for user-facing Live output; use that
  // lifecycle marker as the GPT-Live response boundary.
  const gptMain = scopedConsoleEntries.filter((entry) => (
    entry.args?.[0] === "gpt_live_response_started"
  ));
  const allStarts = provider === ROBUSTNESS_ENGINE.gpt_live ? gptMain : runtimeMain;
  const speechEnd = [...runtime, ...scopedConsoleEntries.map((entry) => ({
    timestampMs: entry.timestampMs,
    type: entry.args?.[0] === "gpt_live_user_speech_end" ? "gpt_live_user_speech_end" : null,
  }))]
    .filter((entry) => entry.type === "user_speech_ended" || entry.type === "gpt_live_user_speech_end")
    .sort((left, right) => number(left.timestampMs, 0) - number(right.timestampMs, 0))[0]?.timestampMs ?? null;
  const inputTranscriptDeltas = runtime.filter((entry) => (
    /(?:^|\.)input_(?:audio_)?transcript\.delta$/.test(entry.type ?? "")
      || /(?:^|\.)input_audio_transcription\.delta$/.test(entry.type ?? "")
  )).filter((entry) => (
    inputEndAt === null || number(entry.timestampMs, 0) <= number(inputEndAt, 0)
  ));
  const lastInputTranscriptAt = inputTranscriptDeltas.length
    ? Math.max(...inputTranscriptDeltas.map((entry) => number(entry.timestampMs, 0)))
    : null;
  const responseBoundary = provider === ROBUSTNESS_ENGINE.gpt_live
    ? (lastInputTranscriptAt ?? speechEnd)
    : speechEnd;
  const starts = responseBoundary === null
    ? allStarts
    : allStarts.filter((entry) => number(entry.timestampMs, 0) >= responseBoundary);
  const backchannelsAfterSpeech = speechEnd === null
    ? backchannels
    : backchannels.filter((entry) => number(entry.timestampMs, 0) >= speechEnd);
  const outputTranscriptEntries = runtime
    .filter((entry) => entry.type === "session.output_transcript.delta")
    .filter((entry) => responseBoundary === null || number(entry.timestampMs, 0) >= responseBoundary);
  const assistantTranscriptEntries = runtime
    .filter((entry) => entry.type === "assistant_transcript_final")
    .filter((entry) => responseBoundary === null || number(entry.timestampMs, 0) >= responseBoundary);
  const transcriptLines = runtime
    .filter((entry) => entry.type === "assistant_transcript_final" || entry.type === "session.output_transcript.delta")
    .filter((entry) => responseBoundary === null || number(entry.timestampMs, 0) >= responseBoundary)
    .map((entry) => ({
      timestampMs: number(entry.timestampMs, null),
      text: entry.event?.text ?? entry.event?.delta,
    }))
    .filter((entry) => hasText(entry.text));
  const playbackStarts = runtimeWithClientEvents.filter((entry) => (
    entry.type === "assistant_playback_started" || entry.type === "audio_playing_started"
  ));
  return {
    starts,
    backchannels: backchannelsAfterSpeech,
    mainResponseCount: starts.length,
    ackCount: backchannelsAfterSpeech.length,
    text: transcriptLines.map((entry) => entry.text).join(" ").trim(),
    transcriptEntries: transcriptLines,
    transcriptStartedAt: transcriptLines[0]?.timestampMs
      ?? null,
    playbackStartedAt: playbackStarts[0]?.timestampMs ?? null,
  };
}

function statePersistenceTimestamp(consoleTrace, turnStartedAt) {
  return consoleEventEntries(consoleTrace)
    .filter((entry) => (
      (entry.logger === "gpt_live_state_persisted"
        || entry.logger === "gpt_live_state_persistence_succeeded")
      && (turnStartedAt === null
        || number(entry.timestampMs, 0) >= number(turnStartedAt, 0))
    ))
    .map((entry) => number(entry.timestampMs, null))
    .find((timestamp) => timestamp !== null) ?? null;
}

function parseJsonObject(value) {
  if (value && typeof value === "object") return value;
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

function loggerEntriesAfter(consoleTrace, logger, turnStartedAt) {
  return consoleEventEntries(consoleTrace)
    .filter((entry) => entry.logger === logger)
    .filter((entry) => turnStartedAt === null
      || number(entry.timestampMs, 0) >= number(turnStartedAt, 0))
    .sort((left, right) => number(left.timestampMs, 0) - number(right.timestampMs, 0));
}

export function liveStateUpdateLifecycle({ consoleTrace = [], networkLog = [], turnStartedAt = null } = {}) {
  const prepared = loggerEntriesAfter(
    consoleTrace,
    "gpt_live_state_update_prepared",
    turnStartedAt,
  ).at(-1) ?? null;
  const preparedDetails = prepared?.details ?? {};
  const delegationCreated = loggerEntriesAfter(
    consoleTrace,
    "gpt_live_delegation_created",
    turnStartedAt,
  ).at(-1) ?? null;
  const delegationDetails = delegationCreated?.details ?? {};
  const requestCandidates = (networkLog ?? [])
    .filter((entry) => entry.direction === "request")
    .filter((entry) => /\/api\/live\/state-updates(?:\?|$)/.test(entry.url ?? ""))
    .filter((entry) => turnStartedAt === null
      || number(entry.timestampMs, 0) >= number(turnStartedAt, 0))
    .sort((left, right) => number(left.timestampMs, 0) - number(right.timestampMs, 0));
  const requestEntry = [...requestCandidates].reverse().find((entry) => {
    const body = parseJsonObject(entry.postData);
    if (!body) return false;
    if (preparedDetails.call_id && body.call_id !== preparedDetails.call_id) return false;
    if (preparedDetails.delegation_id && body.delegation_id !== preparedDetails.delegation_id) return false;
    return true;
  }) ?? requestCandidates.at(-1) ?? null;
  const requestBody = parseJsonObject(requestEntry?.postData) ?? {};
  const responseCandidates = (networkLog ?? [])
    .filter((entry) => entry.direction === "response")
    .filter((entry) => /\/api\/live\/state-updates(?:\?|$)/.test(entry.url ?? ""))
    .filter((entry) => turnStartedAt === null
      || number(entry.timestampMs, 0) >= number(turnStartedAt, 0))
    .sort((left, right) => number(left.timestampMs, 0) - number(right.timestampMs, 0));
  const responseEntry = [...responseCandidates].reverse().find((entry) => (
    requestEntry === null || number(entry.timestampMs, 0) >= number(requestEntry.timestampMs, 0)
  )) ?? null;
  const responseBody = parseJsonObject(responseEntry?.responseBody) ?? {};
  const persistence = loggerEntriesAfter(
    consoleTrace,
    "gpt_live_state_persistence_succeeded",
    turnStartedAt,
  ).at(-1) ?? null;
  const persistenceDetails = persistence?.details ?? {};
  const delegationId = requestBody.delegation_id
    ?? preparedDetails.delegation_id
    ?? delegationDetails.delegation_id
    ?? null;
  return {
    delegationId,
    callId: requestBody.call_id ?? preparedDetails.call_id ?? null,
    turnId: requestBody.turn_id ?? preparedDetails.turn_id ?? null,
    request: {
      turnId: requestBody.turn_id ?? preparedDetails.turn_id ?? null,
      questionId: requestBody.question_id ?? preparedDetails.question_id ?? null,
      candidateAnswer: requestBody.candidate_answer ?? null,
      recordAnswer: requestBody.record_answer ?? null,
      answerState: requestBody.answer_state ?? null,
      answerStatus: requestBody.answer_status ?? null,
      expectedStateVersion: requestBody.base_state_version
        ?? preparedDetails.base_state_version
        ?? null,
      coveredAspects: requestBody.covered_aspects ?? [],
      missingAspects: requestBody.missing_aspects ?? [],
      evidence: requestBody.evidence ?? [],
      transcript: requestBody.transcript ?? null,
      timestampMs: requestEntry?.timestampMs ?? prepared?.timestampMs ?? null,
    },
    response: {
      status: responseBody.status ?? persistenceDetails.status ?? null,
      stateVersion: responseBody.stateVersion ?? persistenceDetails.state_version ?? null,
      currentQuestionId: responseBody.currentQuestionId
        ?? persistenceDetails.current_question_id
        ?? null,
      timestampMs: responseEntry?.timestampMs ?? persistence?.timestampMs ?? null,
    },
  };
}

function latestInterviewStateApplied(consoleTrace, turnStartedAt) {
  const entry = loggerEntriesAfter(
    consoleTrace,
    "interview_state_applied",
    turnStartedAt,
  ).at(-1) ?? null;
  return {
    stateVersion: entry?.details?.state_version ?? null,
    currentQuestionId: entry?.details?.current_question_id ?? null,
    recordId: entry?.details?.record_id ?? null,
    timestampMs: entry?.timestampMs ?? null,
  };
}

function fieldAnswerIsComplete(interview, field) {
  const fieldState = interview?.fieldStates?.[field.id] ?? {};
  const questionAnswer = Object.values(interview?.questionAnswers ?? {}).find(
    (answer) => answer?.fieldId === field.id,
  ) ?? {};
  return new Set(
    Array.isArray(interview?.completedFieldIds) ? interview.completedFieldIds : [],
  ).has(field.id)
    || [fieldState.answerState, questionAnswer.answerStatus]
      .some((status) => ["CONFIRMED", "ANSWERED"].includes(String(status ?? "").toUpperCase()));
}

function nextUnansweredField(context, state) {
  const interview = interviewState(state);
  const currentFieldId = String(interview.currentQuestionId ?? "").replace(/^live-field-/, "");
  const currentField = sortedFields(context).find((field) => field.id === currentFieldId) ?? null;
  // GPT-Live keeps currentQuestionId as the answer target until the next
  // user turn is assessed. Do not treat a later checklist item as the next
  // question while that target is still incomplete.
  if (currentField && !fieldAnswerIsComplete(interview, currentField)) {
    return null;
  }
  return sortedFields(context).find((field) => (
    !fieldAnswerIsComplete(interview, field)
    && field.id !== currentFieldId
  )) ?? null;
}

export function assistantAfterBoundary(assistant, boundaryAt) {
  if (boundaryAt === null) return assistant;
  const starts = assistant.starts.filter((entry) => (
    number(entry.timestampMs, 0) >= boundaryAt
  ));
  const backchannels = (assistant.backchannels ?? []).filter((entry) => (
    number(entry.timestampMs, 0) >= boundaryAt
  ));
  const transcriptEntries = (assistant.transcriptEntries ?? []).filter((entry) => (
    number(entry.timestampMs, 0) >= boundaryAt
  ));
  const playbackStartedAt = assistant.playbackStartedAt !== null
    && number(assistant.playbackStartedAt, 0) >= boundaryAt
    ? assistant.playbackStartedAt
    : null;
  return {
    ...assistant,
    starts,
    backchannels,
    ackCount: backchannels.length,
    mainResponseCount: starts.length,
    text: transcriptEntries.map((entry) => entry.text).join(" ").trim(),
    transcriptEntries,
    transcriptStartedAt: transcriptEntries[0]?.timestampMs ?? null,
    playbackStartedAt,
  };
}

function inboundAudioBytes(statsEntry) {
  return (statsEntry?.inboundRtp ?? []).reduce((total, item) => (
    total + Math.max(0, number(item?.bytesReceived, 0))
  ), 0);
}

/**
 * GPT-Live has no per-response audio-complete event.  The browser receives a
 * continuous WebRTC MediaStream, so the only useful test boundary is an
 * observed period in which both the output caption and inbound audio stop
 * advancing after a response has started.  This is deliberately based on
 * transport observations, not a fixed post-turn sleep or response text.
 */
export function gptLiveOutputPlaybackIdleStatus({
  dataTrace = [],
  audioTrace = { events: [], stats: [] },
  boundaryAt = null,
  idleMs = DEFAULT_GPT_LIVE_OUTPUT_IDLE_MS,
  nowMs = Date.now(),
} = {}) {
  const boundary = boundaryAt === null ? null : number(boundaryAt, null);
  const outputEntries = (dataTrace ?? [])
    .filter((entry) => entry.direction === "server")
    .filter((entry) => dataChannelEventType(entry) === "session.output_transcript.delta")
    .filter((entry) => boundary === null || number(entry.timestampMs, 0) >= boundary)
    .sort((left, right) => number(left.timestampMs, 0) - number(right.timestampMs, 0));
  const stats = [...(audioTrace?.stats ?? [])]
    .filter((entry) => boundary === null || number(entry.timestampMs, 0) >= boundary)
    .sort((left, right) => number(left.timestampMs, 0) - number(right.timestampMs, 0));
  const allStats = [...(audioTrace?.stats ?? [])]
    .sort((left, right) => number(left.timestampMs, 0) - number(right.timestampMs, 0));
  const firstOutputAt = outputEntries[0]?.timestampMs ?? null;
  const lastOutputAt = outputEntries.at(-1)?.timestampMs ?? null;
  let previousBytes = 0;
  let firstAudioAt = null;
  let lastAudioActivityAt = null;
  const audioBoundary = firstOutputAt ?? boundary;
  for (const entry of allStats) {
    const bytes = inboundAudioBytes(entry);
    const timestamp = number(entry.timestampMs, null);
    if (timestamp === null) continue;
    const advanced = bytes > previousBytes;
    if (timestamp >= (audioBoundary ?? Number.NEGATIVE_INFINITY) && advanced) {
      firstAudioAt ??= timestamp;
      lastAudioActivityAt = timestamp;
    }
    previousBytes = Math.max(previousBytes, bytes);
  }
  // The remote WebRTC track sends RTP/comfort-noise continuously while the
  // session is connected. It is useful as proof that media arrived after the
  // current Live output started, but it is not an end-of-speech signal. Use
  // the output transcript boundary for the bounded drain instead of waiting
  // for RTP bytes to stop.
  const lastActivityAt = lastOutputAt;
  const outputStarted = firstOutputAt !== null || firstAudioAt !== null;
  const currentTime = number(nowMs, Date.now());
  const idle = Number(idleMs);
  const settled = outputStarted
    && firstAudioAt !== null
    && lastOutputAt !== null
    && lastActivityAt !== null
    && Number.isFinite(idle)
    && idle >= 0
    && currentTime - lastActivityAt >= idle;
  return {
    settled,
    outputStarted,
    firstOutputAt,
    firstAudioAt,
    audioBoundaryAt: audioBoundary,
    lastOutputAt,
    lastAudioActivityAt,
    lastActivityAt,
    idleMs: idle,
    idleForMs: lastActivityAt === null ? null : Math.max(0, currentTime - lastActivityAt),
    settlementSource: settled ? "output_transcript_idle_with_post_output_rtp" : null,
    statsCount: stats.length,
    outputTranscriptDeltaCount: outputEntries.length,
  };
}

export function extractTiming({
  provider,
  dataTrace,
  consoleTrace,
  transcript,
  assistant,
  stateObservedAt,
  turnStartedAt = null,
  stateVersionAdvanced = false,
  audioTrace = null,
  responseBoundaryAt = null,
}) {
  const isCurrentTurn = (entry) => (
    turnStartedAt === null
      || number(entry.timestampMs, 0) >= number(turnStartedAt, 0)
  );
  const runtime = eventEntries(dataTrace, provider, { includeClient: true })
    .filter(isCurrentTurn);
  const consoleEvents = consoleEventEntries(consoleTrace).filter(isCurrentTurn);
  const audioEvents = (audioTrace?.events ?? [])
    .filter(isCurrentTurn)
    .map((entry) => ({
      timestampMs: entry.timestampMs,
      type: entry.kind ?? null,
      logger: "browser_audio_trace",
      details: entry.details ?? {},
      provider,
    }));
  const inboundAudioStats = (audioTrace?.stats ?? [])
    .filter(isCurrentTurn)
    .filter((entry) => (entry.inboundRtp ?? []).some((item) => (
      number(item?.packetsReceived, 0) > 0 || number(item?.bytesReceived, 0) > 0
    )))
    .map((entry) => ({
      timestampMs: entry.timestampMs,
      type: "inbound_audio_rtp",
      logger: "browser_audio_trace",
      details: { inboundRtp: entry.inboundRtp },
      provider,
    }));
  const combined = [...runtime, ...consoleEvents, ...audioEvents, ...inboundAudioStats].sort((left, right) => (
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
      || entry.logger === "gpt_live_user_speech_end"
      || entry.type === "session.input_audio_buffer.speech_stopped"
      || entry.type === "input_audio_buffer.speech_stopped"
  ));
  const final = transcript.finalEntries.find((entry) => (
    speechEnd?.timestampMs === undefined
      || number(entry.timestampMs, 0) >= number(speechEnd.timestampMs, 0)
  )) ?? findFirst(combined, (entry) => (
    (entry.type === "user_transcript_final"
      || /input_(?:audio_)?transcript\.(?:completed|done)$/.test(entry.type ?? ""))
      && (speechEnd?.timestampMs === undefined
        || number(entry.timestampMs, 0) >= number(speechEnd.timestampMs, 0))
  ));
  const finalAt = final?.timestampMs ?? null;
  const speechEndAt = speechEnd?.timestampMs ?? null;
  const assistantStart = assistant.starts[0] ?? null;
  const assistantStartAt = assistantStart?.timestampMs ?? null;
  const audioEvidenceBoundaryAt = assistantStartAt ?? responseBoundaryAt ?? speechEndAt;
  const firstAudio = findFirst(combined, (entry) => (
    ((entry.logger === "gpt_live_first_audio"
      || entry.type === "assistant_playback_started"
      || entry.type === "audio_playing_started"
      || (entry.logger === "voice_startup_latency"
        && entry.details?.event === "audio_playing_started"))
      || (entry.type === "inbound_audio_rtp"
        && (audioEvidenceBoundaryAt === null
          || number(entry.timestampMs, 0) >= audioEvidenceBoundaryAt)))
      && (speechEndAt === null || number(entry.timestampMs, 0) >= speechEndAt)
      && (responseBoundaryAt === null || number(entry.timestampMs, 0) >= responseBoundaryAt)
  ));
  const firstAudioAt = firstAudio?.timestampMs ?? null;
  // The initial greeting can itself be delegated and persisted before the
  // first user turn. Only a persistence event after this turn's speech end
  // belongs to the turn being measured; otherwise that greeting is reported
  // as TURN_COMMITTED_TOO_EARLY for the first answer.
  const persistedAt = consoleEvents.find((entry) => (
    entry.logger === "gpt_live_state_persisted"
      && (speechEndAt === null || number(entry.timestampMs, 0) >= speechEndAt)
  ))?.timestampMs
    ?? null;
  const commitAt = persistedAt
    ?? (stateVersionAdvanced ? stateObservedAt : null);
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
    firstAudio: firstAudioAt,
    firstAudioSource: firstAudio?.type ?? firstAudio?.logger ?? null,
    assistantSpeechEnded: findFirst(combined, (entry) => (
      entry.type === "assistant_speech_ended"
        || entry.logger === "gpt_live_response_done"
        || entry.logger === "gpt_live_live_audio_completed"
        || entry.logger === "gpt_live_live_output_completed"
    ))?.timestampMs ?? null,
    speechEndToTranscriptFinalMs: speechEndAt !== null && finalAt !== null
      ? Math.max(0, finalAt - speechEndAt) : null,
    transcriptFinalToAssistantStartMs: finalAt !== null && assistantStartAt !== null
      ? Math.max(0, assistantStartAt - finalAt) : null,
    speechEndToAssistantStartMs: speechEndAt !== null && assistantStartAt !== null
      ? Math.max(0, assistantStartAt - speechEndAt) : null,
    speechEndToFirstAudioMs: speechEndAt !== null && firstAudioAt !== null
      ? Math.max(0, firstAudioAt - speechEndAt) : null,
    segmentFinalToTurnCommitMs: finalAt !== null && commitAt !== null
      ? Math.max(0, commitAt - finalAt) : null,
    turnCommitToAckMs: commitAt !== null && ackAt !== null
      ? Math.max(0, ackAt - commitAt) : null,
    ackToMainResponseMs: ackAt !== null && mainAfterAck?.timestampMs !== undefined
      ? Math.max(0, mainAfterAck.timestampMs - ackAt) : null,
    fullTurnLatencyMs: speechEndAt !== null && firstAudioAt !== null
      ? Math.max(0, firstAudioAt - speechEndAt) : null,
  };
}

async function waitForTrace(page, predicate, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await page.evaluate(() => ({
      live: window.__kikioriVoiceLiveTrace?.entries ?? [],
      channel: window.__kikioriVoiceDataChannelTrace?.entries ?? [],
      audio: window.__kikioriVoiceAudioTrace ?? { events: [], stats: [] },
    })).catch(() => ({ live: [], channel: [] }));
    if (predicate(value)) return value;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return page.evaluate(() => ({
    live: window.__kikioriVoiceLiveTrace?.entries ?? [],
    channel: window.__kikioriVoiceDataChannelTrace?.entries ?? [],
    audio: window.__kikioriVoiceAudioTrace ?? { events: [], stats: [] },
  })).catch(() => ({ live: [], channel: [] }));
}

async function waitForGPTLiveOutputPlaybackIdle(
  page,
  { boundaryAt, timeoutMs, idleMs },
) {
  const startedAt = Date.now();
  let latest = { live: [], channel: [], audio: { events: [], stats: [] } };
  let status = gptLiveOutputPlaybackIdleStatus({
    dataTrace: latest.channel,
    audioTrace: latest.audio,
    boundaryAt,
    idleMs,
    nowMs: startedAt,
  });
  while (Date.now() - startedAt < timeoutMs) {
    latest = await page.evaluate(() => ({
      live: window.__kikioriVoiceLiveTrace?.entries ?? [],
      channel: window.__kikioriVoiceDataChannelTrace?.entries ?? [],
      audio: window.__kikioriVoiceAudioTrace ?? { events: [], stats: [] },
    })).catch(() => latest);
    status = gptLiveOutputPlaybackIdleStatus({
      dataTrace: latest.channel,
      audioTrace: latest.audio,
      boundaryAt,
      idleMs,
      nowMs: Date.now(),
    });
    if (status.settled) return { ...latest, status };
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  latest = await page.evaluate(() => ({
    live: window.__kikioriVoiceLiveTrace?.entries ?? [],
    channel: window.__kikioriVoiceDataChannelTrace?.entries ?? [],
    audio: window.__kikioriVoiceAudioTrace ?? { events: [], stats: [] },
  })).catch(() => latest);
  status = gptLiveOutputPlaybackIdleStatus({
    dataTrace: latest.channel,
    audioTrace: latest.audio,
    boundaryAt,
    idleMs,
    nowMs: Date.now(),
  });
  return { ...latest, status };
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
    const payload = dataChannelEventPayload(entry);
    const type = dataChannelEventType(entry);
    if (type !== "assistant_speech_started" && type !== "response.created") continue;
    const id = payload.responseId ?? payload.response_id ?? payload.response?.id;
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

function consoleHasEvent(consoleTrace, eventName) {
  return consoleTrace.some((entry) => (
    entry.args?.[0] === eventName
      || (entry.args?.[0] === "gpt_live_event" && entry.args?.[1]?.type === eventName)
  ));
}

function audioActuallySent({ audioTrace }) {
  return (audioTrace?.stats ?? []).some((sample) => (
    (sample.outboundRtp ?? []).some((stream) => (
      Number(stream.packetsSent) > 0 || Number(stream.bytesSent) > 0
    ))
  ));
}

function microphoneTrackReady(audioTrace) {
  const readyEvent = (audioTrace?.events ?? []).find((entry) => (
    entry.kind === "get_user_media_ready"
      && (entry.details?.tracks ?? []).some((track) => track.readyState === "live")
  ));
  if (readyEvent) return true;
  return (audioTrace?.stats ?? []).some((sample) => (
    (sample.tracks ?? []).some((track) => track.kind === "audio" && track.readyState === "live")
  ));
}

function providerSpeechBoundaries({ dataTrace = [], consoleTrace = [] } = {}) {
  const providerTypes = [
    ...dataTrace.map((entry) => dataChannelEventType(entry)).filter(Boolean),
    ...consoleTrace
      .filter((entry) => entry.args?.[0] === "gpt_live_event")
      .map((entry) => entry.args?.[1]?.type)
      .filter(Boolean),
  ];
  return {
    started: providerTypes.some((type) => (
      type === "session.input_audio_buffer.speech_started"
        || type === "input_audio_buffer.speech_started"
        || type === "session.input_speech_started"
        || type === "input_speech_started"
    )),
    ended: providerTypes.some((type) => (
      type === "session.input_audio_buffer.speech_stopped"
        || type === "input_audio_buffer.speech_stopped"
        || type === "session.input_speech_stopped"
        || type === "input_speech_stopped"
    )),
  };
}

export function harnessValidity({
  engine,
  audioTrace,
  dataTrace,
  consoleTrace,
  transcript,
  assistant,
  timing,
  stateVersionAdvanced,
}) {
  const runtime = eventEntries(dataTrace, engine);
  const sessionEstablished = engine === ROBUSTNESS_ENGINE.gpt_live
    ? consoleHasEvent(consoleTrace, "session.started")
      || consoleHasEvent(consoleTrace, "live_session_started")
      || runtime.some((entry) => entry.type === "session.started")
    : consoleHasEvent(consoleTrace, "voice_runtime_ready")
      || runtime.some((entry) => entry.type === "runtime_ready");
  const providerSpeech = engine === ROBUSTNESS_ENGINE.gpt_live
    ? providerSpeechBoundaries({ dataTrace, consoleTrace })
    : null;
  const transcriptReceived = hasText(transcript.finalText) || hasText(transcript.partialText);
  const applicationSpeechBoundaryObserved = timing.speechEnd !== null
    || consoleHasEvent(consoleTrace, "gpt_live_input_transcript_final");
  // GPT-Live supplies transcript deltas but does not guarantee a completed
  // turn or server VAD boundary event. The application groups those deltas at
  // an idle boundary; audio RTP plus provider transcript plus that marker are
  // the evidence that the test turn reached the runtime. Keep provider VAD
  // observations diagnostic instead of rejecting a valid GPT-Live session for
  // an event the protocol does not promise.
  const speechWasDetected = engine === ROBUSTNESS_ENGINE.gpt_live
    ? Boolean(transcriptReceived && applicationSpeechBoundaryObserved)
    : timing.speechStart !== null || timing.speechEnd !== null;
  const atLeastOneTurnCommitted = stateVersionAdvanced
    || consoleHasEvent(consoleTrace, "gpt_live_state_persisted")
    || runtime.some((entry) => ["user_turn_committed", "turn_committed"].includes(entry.type));
  const assistantMainResponseReceived = Boolean(
    timing.speechEnd !== null && assistant.mainResponseCount > 0,
  );
  const checks = {
    sessionEstablished,
    microphoneTrackReady: microphoneTrackReady(audioTrace),
    audioActuallySent: audioActuallySent({ audioTrace }),
    speechDetected: speechWasDetected,
    transcriptReceived,
    atLeastOneTurnCommitted,
    assistantMainResponseReceived,
  };
  const missing = Object.entries(checks)
    .filter(([key, value]) => key !== "speechDetected" && !value)
    .map(([key]) => key);
  if (!speechWasDetected) missing.push("speechDetected");
  return {
    status: missing.length ? "INVALID" : "VALID",
    classification: missing.length ? "TEST_INFRA_FAILURE" : null,
    checks,
    observations: providerSpeech ? {
      providerSpeechStarted: providerSpeech.started,
      providerSpeechEnded: providerSpeech.ended,
      applicationSpeechBoundaryObserved,
    } : { applicationSpeechBoundaryObserved },
    missing,
  };
}

function errorTypes(dataTrace, consoleTrace, networkErrors = []) {
  const runtime = eventEntries(dataTrace, "gpt_live");
  const values = [
    ...runtime.map((entry) => `${entry.type ?? ""} ${entry.event?.error?.code ?? ""} ${entry.event?.error?.message ?? ""}`),
    ...consoleTrace.map((entry) => `${entry.args?.[0] ?? ""} ${entry.args?.[1]?.type ?? ""} ${entry.args?.[1]?.code ?? ""} ${entry.args?.[1]?.message ?? ""}`),
    ...networkErrors.map((entry) => `${entry?.failure ?? ""} ${entry?.status ?? ""}`),
  ];
  return values.filter((value) => /error|unknown_parameter|5\d\d|websocket.*(?:closed|error)|data.?channel.*(?:closed|error)|transport\.closed/i.test(value));
}

export function failureAnalysis({
  engine,
  harness,
  transcript,
  timing,
  persisted,
  advanced,
  questionAdvanceExpected = true,
  assistant,
  dataTrace,
  consoleTrace,
  networkErrors,
  dataChannelErrors = [],
}) {
  const errors = [
    ...errorTypes(dataTrace, consoleTrace, networkErrors),
    ...dataChannelErrors.map((entry) => `${entry?.type ?? ""} ${entry?.message ?? ""}`),
  ];
  let rootFailure = null;
  if (!harness.checks.sessionEstablished) rootFailure = "SESSION_NOT_ESTABLISHED";
  else if (!harness.checks.microphoneTrackReady) rootFailure = "MICROPHONE_TRACK_NOT_READY";
  else if (!harness.checks.audioActuallySent) rootFailure = "AUDIO_NOT_SENT";
  else if (!harness.checks.speechDetected) rootFailure = "UTTERANCE_END_NOT_DETECTED";
  else if (errors.some((value) => /unknown_parameter/i.test(value))) {
    rootFailure = "GPT_LIVE_PROTOCOL_ERROR";
  } else if (errors.some((value) => /5\d\d/i.test(value))) {
    rootFailure = "API_5XX";
  } else if (errors.some((value) => /websocket/i.test(value))) {
    rootFailure = "WEBSOCKET_ERROR";
  } else if (errors.some((value) => /data.?channel/i.test(value))) {
    rootFailure = "DATACHANNEL_ERROR";
  } else if (!hasText(transcript.finalText)) rootFailure = "TRANSCRIPT_FINAL_MISSING";
  else if (!persisted) rootFailure = "ANSWER_NOT_PERSISTED";
  else if (questionAdvanceExpected && !advanced) rootFailure = "QUESTION_NOT_ADVANCED";
  else if (assistant.mainResponseCount === 0) rootFailure = "ASSISTANT_RESPONSE_MISSING";

  const observedFailures = [];
  if (!hasText(transcript.finalText)) observedFailures.push("TRANSCRIPT_FINAL_MISSING");
  if (!persisted) observedFailures.push("ANSWER_NOT_PERSISTED");
  if (questionAdvanceExpected && !advanced) observedFailures.push("QUESTION_NOT_ADVANCED");
  if (assistant.mainResponseCount === 0) observedFailures.push("RESPONSE_TIMEOUT");
  if (timing.turnCommit !== null && timing.transcriptFinal !== null
    && timing.turnCommit < timing.transcriptFinal) {
    observedFailures.push("TURN_COMMITTED_TOO_EARLY");
  }
  const cascadeFailures = [...new Set(observedFailures.filter((failure) => failure !== rootFailure))];
  return {
    classification: harness.classification || (rootFailure ? "PRODUCT_OR_RUNTIME_FAILURE" : null),
    rootFailure,
    cascadeFailures,
    errorEvidence: errors,
  };
}

export function questionAdvanceExpected({ expectations, answer }) {
  if (expectations.questionAdvance !== "required") return false;

  // A final transcript can be persisted while the current field still needs
  // clarification. In that state, keeping the same question is the expected
  // product behavior; the runner must not misclassify it as a transition bug.
  const answerState = String(answer?.answerState ?? "").toUpperCase();
  if (["UNANSWERED", "CANDIDATE_PENDING", "AWAITING_CONFIRMATION"].includes(answerState)) {
    return false;
  }
  return answerState === "CONFIRMED"
    || answerState === "ANSWERED"
    || (answer?.answerState == null && hasText(answer?.confirmed));
}

/**
 * In GPT-Live, currentQuestionId identifies the basic question whose answer
 * was most recently assessed.  Live may already be speaking the next basic
 * question while the State Store still points at that answer target.  Require
 * progression on the next semantic update only when the previous target was
 * already complete; do not treat a same-turn persisted ID as the next spoken
 * question.
 */
export function questionAdvanceRequiredForTurn({
  expectations,
  beforeAnswer,
  beforeQuestionId,
  beforeState,
  engine = null,
}) {
  if (expectations?.questionAdvance !== "required" || !beforeQuestionId) return false;
  // GPT-Live persists the question that the current transcript answers. The
  // interviewer owns the next spoken question, and that question is written
  // to currentQuestionId only when the next user answer is assessed. A
  // same-turn ID change is therefore neither required nor evidence of a
  // missing transition for this runtime.
  if (engine === ROBUSTNESS_ENGINE.gpt_live) return false;
  const state = beforeState ?? {};
  if (state.status === "completed" || state.currentTurnType === "PROBE") return false;
  const answerState = String(beforeAnswer?.answerState ?? "").toUpperCase();
  return answerState === "CONFIRMED"
    || answerState === "ANSWERED"
    || (beforeAnswer?.answerState == null
      && hasText(beforeAnswer?.confirmed)
      && !hasText(beforeAnswer?.candidate));
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

export function questionAdvanced({
  before,
  after,
  requestedQuestionId,
  expectedQuestionId,
}) {
  const beforeInterview = interviewState(before);
  const afterInterview = interviewState(after);
  const expectedId = String(expectedQuestionId ?? "").trim();
  const selectedQuestionId = String(requestedQuestionId ?? "").trim();
  if (!beforeInterview.currentQuestionId || !expectedId || !selectedQuestionId) return false;
  if (selectedQuestionId !== expectedId || expectedId === beforeInterview.currentQuestionId) {
    return false;
  }
  return afterInterview.currentQuestionId === expectedId;
}

/**
 * GPT-Live exposes the next basic question to the user before that question
 * becomes the persisted answer target. The following turn's state-update
 * request is the first deterministic record of which question the user was
 * actually answering. Use that boundary to verify progression instead of
 * requiring currentQuestionId to change during the preceding turn.
 */
export function verifyLiveQuestionProgression({ previousTurn, currentTurn }) {
  const previousLifecycle = previousTurn?.interview?.questionLifecycle ?? {};
  const expectedQuestionId = previousLifecycle.nextQuestionDetermination
    ?.expectedNextQuestionId ?? null;
  if (!expectedQuestionId) {
    return {
      status: "NOT_REQUIRED",
      expectedQuestionId: null,
      observedQuestionId: currentTurn?.interview?.questionLifecycle
        ?.statePersistenceRequest?.questionId ?? null,
      reason: "previous_answer_target_not_complete_or_no_following_question",
    };
  }
  const observedQuestionId = currentTurn?.interview?.questionLifecycle
    ?.statePersistenceRequest?.questionId ?? null;
  if (!observedQuestionId) {
    return {
      status: "PENDING",
      expectedQuestionId,
      observedQuestionId: null,
      reason: "following_turn_has_no_persisted_answer_target_yet",
    };
  }
  return {
    status: observedQuestionId === expectedQuestionId ? "VERIFIED" : "MISMATCH",
    expectedQuestionId,
    observedQuestionId,
    reason: observedQuestionId === expectedQuestionId
      ? "following_turn_answer_target_matches_live_spoken_question"
      : "following_turn_answer_target_does_not_match_expected_next_question",
  };
}

function sessionRunToken(value) {
  return String(value ?? "")
    .trim()
    .replace(/[^a-zA-Z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function robustnessSessionId({ engine, seed, index, behavior, variant, runId = null }) {
  const runToken = sessionRunToken(runId);
  return `robust-${engine}-${seed}${runToken ? `-${runToken}` : ""}-session-${index}-${behavior.toLowerCase()}-${variant + 1}`;
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
  const fixtureSequence = resolveRealAudioFixtureSequence({
    config,
    behavior: normalizedBehavior,
    variant,
  });
  const fixturePath = fixtureSequence[0];
  const sessionId = robustnessSessionId({
    engine,
    seed: config.seed,
    index: sessionIndex,
    behavior: normalizedBehavior,
    variant,
    runId: config.runId,
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
  let turnStartedAt = null;
  let before = null;
  let after = null;
  let contextData = null;
  let hardGate = null;
  let finalTrace = { live: [], channel: [], audio: { events: [], stats: [] } };
  let evidenceWritten = false;
  let tracingStarted = false;
  let turn = 0;
  let lastTurnRecord = null;
  let lastHardGate = null;

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
    await installWavFixtureMicrophone(page, fixtureSequence);
    await installVoiceAudioTrace(page);
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

    const audioMetadata = readWavMetadata(fixturePath);
    await page.waitForFunction(
      () => window.__kikioriVoiceFixtureMicrophone?.ready === true
        || window.__kikioriVoiceFixtureMicrophone?.mode === "browser_fallback"
        || window.__kikioriVoiceFixtureMicrophone?.mode === "unavailable",
      null,
      { timeout: 30_000 },
    );
    const configuredInitialResponseIdleMs = Number.parseInt(
      process.env.VOICE_TEST_REAL_INITIAL_RESPONSE_IDLE_MS
        || String(DEFAULT_GPT_LIVE_INITIAL_RESPONSE_IDLE_MS),
      10,
    );
    const initialResponseIdleMs = Number.isFinite(configuredInitialResponseIdleMs)
      && configuredInitialResponseIdleMs >= 0
      ? configuredInitialResponseIdleMs
      : DEFAULT_GPT_LIVE_INITIAL_RESPONSE_IDLE_MS;
    const initialQuestionReadyPredicate = (value) => {
      const runtime = eventEntries(value.channel, engine);
      if (engine === ROBUSTNESS_ENGINE.transcribe_polly) {
        return runtime.some((entry) => (
          entry.type === "input_state_changed"
          && entry.event?.inputState === "ANSWER_LISTENING"
        ));
      }
      return isGPTLiveInitialResponseReady(value.live, {
        idleMs: initialResponseIdleMs,
      });
    };
    await waitForTrace(page, initialQuestionReadyPredicate, Math.max(30_000, Number.parseInt(
      process.env.VOICE_TEST_REAL_STARTUP_TIMEOUT_MS || "30000",
      10,
    )));
    const initialQuestionReady = initialQuestionReadyPredicate(await page.evaluate(() => ({
      live: window.__kikioriVoiceLiveTrace?.entries ?? [],
      channel: window.__kikioriVoiceDataChannelTrace?.entries ?? [],
      audio: window.__kikioriVoiceAudioTrace ?? { events: [], stats: [] },
    })).catch(() => ({ live: [], channel: [], audio: { events: [], stats: [] } })));
    const microphoneStart = await startWavFixtureMicrophone(page);
    telemetry.event("audio_fixture_ready", {
      engine,
      microphoneMode: microphoneStart.mode,
      initialQuestionReady,
      initialResponseIdleMs: engine === ROBUSTNESS_ENGINE.gpt_live
        ? initialResponseIdleMs
        : null,
    });
    if (!microphoneStart.started && microphoneStart.mode !== "browser_fallback") {
      throw new Error(
        "real_voice_fixture_microphone_start_failed: "
          + String(microphoneStart.error || microphoneStart.mode),
      );
    }

    const maxTurns = Math.max(1, Number(config.maxTurnsPerSession) || 1);
    for (turn = 1; turn <= maxTurns; turn += 1) {
      after = null;
      const turnFixturePath = fixtureSequence[(turn - 1) % fixtureSequence.length];
      const turnAudioMetadata = readWavMetadata(turnFixturePath);
      if (turn > 1) {
        before = await fetchInterviewState(request).catch(() => before);
        currentField = fieldForQuestion(contextData, interviewState(before).currentQuestionId);
        currentQuestionId = currentField ? liveQuestionId(currentField) : null;
        currentQuestion = currentField ? questionText(currentField) : null;
        const replay = await replayWavFixtureMicrophone(
          page,
          (turn - 1) % fixtureSequence.length,
        );
        telemetry.event("audio_fixture_replayed", {
          turn,
          fixturePath: turnFixturePath,
          microphoneMode: replay.mode,
          playCount: replay.playCount,
        });
        if (!replay.started && replay.mode !== "browser_fallback") {
          throw new Error(
            "real_voice_fixture_replay_failed: "
              + String(replay.error || replay.mode),
          );
        }
      }
    const turnBaseline = await page.evaluate(() => ({
      live: window.__kikioriVoiceLiveTrace?.entries ?? [],
      channel: window.__kikioriVoiceDataChannelTrace?.entries ?? [],
      audio: window.__kikioriVoiceAudioTrace ?? { events: [], stats: [] },
    })).catch(() => ({ live: [], channel: [], audio: { events: [], stats: [] } }));
    const baselineTranscriptCount = collectTranscript(
      turnBaseline.channel,
      turnBaseline.live,
      engine,
    ).finalCount;
    const baselineAssistantResponseCount = collectAssistant(
      turnBaseline.channel,
      turnBaseline.live,
      engine,
    ).mainResponseCount;
    turnStartedAt = Date.now();
    guard.startTurn(audioMetadata.durationSeconds);
    telemetry.event("audio_fixture_started", {
      fixturePath: turnFixturePath,
      metadata: turnAudioMetadata,
      engine,
      microphoneMode: microphoneStart.mode,
    });

    const timeoutMs = Number.parseInt(
      process.env.VOICE_TEST_REAL_TURN_TIMEOUT_MS || String(DEFAULT_TURN_TIMEOUT_MS),
      10,
    );
    const trace = await waitForTrace(page, (value) => {
      const runtime = eventEntries(value.channel, engine);
      const hasFinal = engine === ROBUSTNESS_ENGINE.transcribe_polly
        ? runtime.filter((entry) => entry.type === "user_transcript_final").length
          > baselineTranscriptCount
        : collectTranscript(value.channel, value.live, engine).finalCount > baselineTranscriptCount;
      return normalizedBehavior === "SILENCE" ? Date.now() - turnStartedAt > 8_000 : hasFinal;
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
      : engine === ROBUSTNESS_ENGINE.gpt_live
        ? Math.min(timeoutMs, 5_000)
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

    // State persistence and assistant audio are separate asynchronous stages.
    // Do not take the State Store observation as proof that the user-facing
    // response started; otherwise a delayed Polly/Live response is reported
    // as a false immediate failure and its actual response window is lost.
    const responseWaitBudgetMs = Math.max(0, timeoutMs - (Date.now() - turnStartedAt));
    let outputPlaybackStatus = null;
    if (preliminaryTranscript.finalCount > 0 && responseWaitBudgetMs > 0) {
      const responseWaitStartedAt = Date.now();
      const observedStatePersistenceAt = engine === ROBUSTNESS_ENGINE.gpt_live
        ? statePersistenceTimestamp(finalTrace.live, turnStartedAt)
        : null;
      const responseBoundaryAt = observedStatePersistenceAt ?? stateObservedAt;
      telemetry.event("assistant_response_wait_started", {
        budgetMs: responseWaitBudgetMs,
        responseBoundaryAt,
      });
      const responseTrace = await waitForTrace(page, (value) => {
        const assistant = collectAssistant(value.channel, value.live, engine);
        if (engine === ROBUSTNESS_ENGINE.gpt_live) {
          const stateAt = statePersistenceTimestamp(value.live, turnStartedAt)
            ?? responseBoundaryAt;
          return stateAt !== null && assistant.starts.some((entry) => (
            number(entry.timestampMs, 0) >= stateAt
          ));
        }
        return assistant.mainResponseCount > baselineAssistantResponseCount;
      }, responseWaitBudgetMs);
      finalTrace = responseTrace;
      telemetry.event("assistant_response_wait_completed", {
        started: engine === ROBUSTNESS_ENGINE.gpt_live
          ? collectAssistant(responseTrace.channel, responseTrace.live, engine).starts.some((entry) => (
            number(entry.timestampMs, 0) >= (statePersistenceTimestamp(responseTrace.live, turnStartedAt)
              ?? stateObservedAt)
          ))
          : collectAssistant(responseTrace.channel, responseTrace.live, engine).mainResponseCount > 0,
        elapsedMs: Math.max(0, Date.now() - responseWaitStartedAt),
      });
      if (engine === ROBUSTNESS_ENGINE.gpt_live) {
        const drainBoundaryAt = statePersistenceTimestamp(responseTrace.live, turnStartedAt)
          ?? responseBoundaryAt;
        const configuredOutputIdleMs = Number.parseInt(
          process.env.VOICE_TEST_REAL_OUTPUT_IDLE_MS || String(DEFAULT_GPT_LIVE_OUTPUT_IDLE_MS),
          10,
        );
        const outputIdleMs = Number.isFinite(configuredOutputIdleMs)
          && configuredOutputIdleMs >= 0
          ? configuredOutputIdleMs
          : DEFAULT_GPT_LIVE_OUTPUT_IDLE_MS;
        const configuredDrainTimeoutMs = Number.parseInt(
          process.env.VOICE_TEST_REAL_OUTPUT_DRAIN_TIMEOUT_MS || "15000",
          10,
        );
        const drainTimeoutMs = Number.isFinite(configuredDrainTimeoutMs)
          && configuredDrainTimeoutMs > 0
          ? configuredDrainTimeoutMs
          : 15000;
        const drainBudgetMs = Math.max(0, Math.min(
          drainTimeoutMs,
          timeoutMs - (Date.now() - turnStartedAt),
        ));
        const drainTrace = drainBudgetMs > 0
          ? await waitForGPTLiveOutputPlaybackIdle(page, {
            boundaryAt: drainBoundaryAt,
            timeoutMs: drainBudgetMs,
            idleMs: outputIdleMs,
          })
          : {
            ...responseTrace,
            status: {
              settled: false,
              outputStarted: false,
              idleMs: outputIdleMs,
              idleForMs: 0,
              reason: "turn_timeout_budget_exhausted",
            },
          };
        finalTrace = drainTrace;
        outputPlaybackStatus = drainTrace.status ?? null;
        telemetry.event("assistant_response_drain_completed", {
          completed: Boolean(drainTrace.status?.settled),
          source: drainTrace.status?.settled
            ? (drainTrace.status?.settlementSource
              ?? "output_transcript_idle_with_post_output_rtp")
            : "not_observed",
          outputStarted: Boolean(drainTrace.status?.outputStarted),
          firstAudioAt: drainTrace.status?.firstAudioAt ?? null,
          lastActivityAt: drainTrace.status?.lastActivityAt ?? null,
          idleForMs: drainTrace.status?.idleForMs ?? null,
          idleMs: outputIdleMs,
          budgetMs: drainBudgetMs,
          boundaryAt: drainBoundaryAt,
        });
      }
    }

    // The Live state-update response is the persistence acknowledgement, but
    // the separate snapshot endpoint may become visible later. Do not block
    // Live's response on that read. Once the response has drained, give the
    // snapshot read the remainder of the turn budget to catch up so that the
    // answer assertion still uses the durable state rather than a stale read.
    const preliminaryStateUpdateLifecycle = engine === ROBUSTNESS_ENGINE.gpt_live
      ? liveStateUpdateLifecycle({
        consoleTrace: finalTrace.live,
        networkLog: telemetry.networkLog,
        turnStartedAt,
      })
      : null;
    const persistedStateVersion = number(
      preliminaryStateUpdateLifecycle?.response?.stateVersion,
      null,
    );
    if (engine === ROBUSTNESS_ENGINE.gpt_live
      && preliminaryTranscript.finalCount > 0
      && (!after || stateVersion(after) < (persistedStateVersion ?? stateVersion(before) + 1))) {
      const stateRefreshDeadline = turnStartedAt + timeoutMs;
      while (Date.now() < stateRefreshDeadline) {
        const candidateState = await fetchInterviewState(request).catch(() => null);
        const minimumVersion = persistedStateVersion ?? stateVersion(before) + 1;
        if (candidateState && stateVersion(candidateState) >= minimumVersion) {
          after = candidateState;
          stateObservedAt = Date.now();
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
    }

    const afterTrace = await page.evaluate(() => ({
      live: window.__kikioriVoiceLiveTrace?.entries ?? [],
      channel: window.__kikioriVoiceDataChannelTrace?.entries ?? [],
      audio: window.__kikioriVoiceAudioTrace ?? { events: [], stats: [] },
    })).catch(() => finalTrace);
    finalTrace = afterTrace;
    telemetry.setLiveTrace(afterTrace.live);
    telemetry.setDataChannelTrace(afterTrace.channel);
    telemetry.setAudioTrace(afterTrace.audio);
    const transcript = collectTranscript(afterTrace.channel, afterTrace.live, engine, {
      boundaryAt: turnStartedAt,
    });
    const responseBoundaryAt = engine === ROBUSTNESS_ENGINE.gpt_live
      ? statePersistenceTimestamp(afterTrace.live, turnStartedAt) ?? stateObservedAt
      : null;
    const assistant = assistantAfterBoundary(
      collectAssistant(afterTrace.channel, afterTrace.live, engine, {
        boundaryAt: turnStartedAt,
        inputEndAt: responseBoundaryAt,
      }),
      responseBoundaryAt,
    );
    const timing = extractTiming({
      provider: engine,
      dataTrace: afterTrace.channel,
      consoleTrace: afterTrace.live,
      transcript,
      assistant,
      stateObservedAt,
      turnStartedAt,
      stateVersionAdvanced: stateVersion(after) > stateVersion(before),
      audioTrace: afterTrace.audio,
      responseBoundaryAt,
    });
    const stateUpdateLifecycle = engine === ROBUSTNESS_ENGINE.gpt_live
      ? liveStateUpdateLifecycle({
        consoleTrace: afterTrace.live,
        networkLog: telemetry.networkLog,
        turnStartedAt,
      })
      : null;
    const stateUpdateStatus = String(stateUpdateLifecycle?.response?.status ?? "").toLowerCase();
    const stateUpdateReceiptPersisted = Boolean(
      stateUpdateLifecycle?.request?.turnId
        && ["applied", "updated", "accepted"].includes(stateUpdateStatus),
    );
    const stateVersionAfterTurn = Math.max(
      stateVersion(after),
      number(stateUpdateLifecycle?.response?.stateVersion, 0),
    );
    const stateVersionAdvancedForTurn = stateVersionAfterTurn > stateVersion(before);
    const requestedQuestionId = stateUpdateLifecycle?.request?.questionId
      || interviewState(after).currentQuestionId
      || currentQuestionId;
    const answerField = fieldForQuestion(contextData, requestedQuestionId) ?? currentField;
    const answer = extractFieldAnswer(
      interviewState(after),
      answerField?.id,
    );
    const beforeAnswer = extractFieldAnswer(
      interviewState(before),
      currentField?.id,
    );
    const beforeQuestionId = interviewState(before).currentQuestionId ?? currentQuestionId;
    const expectedNextFieldBefore = nextUnansweredField(contextData, before);
    const sameTurnExpectedNextQuestionId = expectedNextFieldBefore
      ? liveQuestionId(expectedNextFieldBefore)
      : null;
    const expectedNextFieldAfter = nextUnansweredField(contextData, after);
    const expectedNextQuestionId = expectedNextFieldAfter
      ? liveQuestionId(expectedNextFieldAfter)
      : null;
    const shouldAdvance = questionAdvanceRequiredForTurn({
      expectations,
      beforeAnswer,
      beforeQuestionId,
      beforeState: interviewState(before),
      engine,
    }) && Boolean(sameTurnExpectedNextQuestionId);
    const effectiveExpectations = shouldAdvance || expectations.questionAdvance !== "required"
      ? expectations
      : { ...expectations, questionAdvance: "optional" };
    const advanced = questionAdvanced({
      before,
      after,
      requestedQuestionId,
      expectedQuestionId: sameTurnExpectedNextQuestionId,
    });
    const responseIdsValue = responseIds(afterTrace.channel, afterTrace.live);
    const runtimeEvents = eventTypes(afterTrace.channel, afterTrace.live);
    const persisted = Boolean(
      stateUpdateReceiptPersisted
      ||
      hasText(answer.candidate)
      || hasText(answer.confirmed)
      || ["CONFIRMED", "ANSWERED"].includes(answer.answerState),
    );
    const hardGateBase = evaluateHardGate({
      turn,
      // Reaching the configured final turn is a successful bounded stop. The
      // supervisor hard gate is evaluated after a turn, so give its stop
      // threshold one extra slot for this final in-session observation.
      maxTurns: turn >= maxTurns ? maxTurns + 1 : maxTurns,
      jevCalls: 0,
      maxJevCalls: undefined,
      state: interviewState(after),
      fieldId: answerField?.id,
      expectedQuestionId: beforeQuestionId,
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
      questionShouldAdvance: shouldAdvance,
      questionAdvanced: advanced,
      expectAnswerPersistence: expectations.answerPersistence === "required",
      expectations: effectiveExpectations,
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
    if (engine === ROBUSTNESS_ENGINE.gpt_live
      && preliminaryTranscript.finalCount > 0
      && outputPlaybackStatus
      && !outputPlaybackStatus.settled) {
      extraFailures.push("ASSISTANT_OUTPUT_COMPLETION_UNOBSERVED");
    }
    const harness = harnessValidity({
      engine,
      audioTrace: afterTrace.audio,
      networkLog: telemetry.networkLog,
      dataTrace: afterTrace.channel,
      consoleTrace: afterTrace.live,
      transcript,
      assistant,
      timing,
      stateVersionAdvanced: stateVersionAdvancedForTurn,
    });
    const analysis = failureAnalysis({
      engine,
      harness,
      transcript,
      timing,
      persisted,
      advanced,
      questionAdvanceExpected: shouldAdvance,
      assistant,
      dataTrace: afterTrace.channel,
      consoleTrace: afterTrace.live,
      networkErrors,
      dataChannelErrors: telemetry.dataChannelErrors,
    });
    const harnessCodes = harness.status === "INVALID"
      ? [analysis.rootFailure, ...analysis.cascadeFailures].filter(Boolean)
      : [];
    hardGate = {
      ...hardGateBase,
      status: extraFailures.length || harnessCodes.length ? "FAIL" : hardGateBase.status,
      passed: !extraFailures.length && !harnessCodes.length && hardGateBase.passed,
      shouldCallJev: false,
      codes: [...new Set([...hardGateBase.codes, ...extraFailures, ...harnessCodes])],
      anomalies: [...new Set([...(hardGateBase.anomalies ?? []), ...extraFailures, ...harnessCodes])],
      reason: extraFailures.length || harnessCodes.length
        ? "real_voice_robustness_failure"
        : hardGateBase.reason,
    };
    telemetry.event("hard_gate", {
      status: hardGate.status,
      codes: hardGate.codes,
      expectations: effectiveExpectations,
      observed: hardGate.observed,
    });
    telemetry.event("harness_validity", harness);
    telemetry.event("failure_analysis", analysis);
    const frontendStateApplied = latestInterviewStateApplied(afterTrace.live, turnStartedAt);
    const followingQuestionField = expectedNextFieldAfter;
    const persistedResponse = stateUpdateLifecycle?.response
      ? {
        ...stateUpdateLifecycle.response,
        answerState: answer.answerState ?? null,
        candidateAnswerExists: hasText(answer.candidate),
        confirmedAnswerExists: hasText(answer.confirmed),
      }
      : null;
    const questionLifecycle = {
      before: {
        questionId: beforeQuestionId,
        questionText: currentQuestion,
        answerState: beforeAnswer.answerState ?? null,
        candidate: beforeAnswer.candidate ?? null,
        confirmed: beforeAnswer.confirmed ?? null,
        stateVersion: stateVersion(before),
      },
      userTranscriptFinal: transcript.finalText || null,
      delegationId: stateUpdateLifecycle?.delegationId ?? null,
      statePersistenceRequest: stateUpdateLifecycle?.request ?? null,
      statePersistenceResponse: persistedResponse,
      backendSelectedQuestionId: null,
      backendSelectedQuestionText: null,
      nextQuestionDetermination: {
        owner: "gpt_live",
        reason: "application_backend_persists_answer_target_only",
        answerTargetQuestionId: requestedQuestionId ?? null,
        selectedForThisTurnQuestionId: requestedQuestionId ?? null,
        selectedForThisTurnQuestionText: answerField ? questionText(answerField) : null,
        sameTurnExpectedNextQuestionId: sameTurnExpectedNextQuestionId,
        sameTurnExpectedNextQuestionText: expectedNextFieldBefore
          ? questionText(expectedNextFieldBefore)
          : null,
        expectedNextQuestionId: expectedNextQuestionId,
        expectedNextQuestionText: expectedNextFieldAfter
          ? questionText(expectedNextFieldAfter)
          : null,
        selectedQuestionMatchesExpected: shouldAdvance
          ? requestedQuestionId === sameTurnExpectedNextQuestionId
          : null,
        progressionVerification: "PENDING_NEXT_TURN",
        spokenText: assistant.text || null,
      },
      frontendStateApplied,
      after: {
        questionId: stateUpdateLifecycle?.response?.currentQuestionId
          ?? interviewState(after).currentQuestionId
          ?? null,
        questionText: fieldForQuestion(
          contextData,
          stateUpdateLifecycle?.response?.currentQuestionId
            ?? interviewState(after).currentQuestionId,
        ) ? questionText(fieldForQuestion(
          contextData,
          stateUpdateLifecycle?.response?.currentQuestionId
            ?? interviewState(after).currentQuestionId,
        )) : null,
        answerState: answer.answerState ?? null,
        candidate: answer.candidate ?? null,
        confirmed: answer.confirmed ?? null,
        stateVersion: stateVersionAfterTurn,
      },
    };
    telemetry.event("question_lifecycle", questionLifecycle);
    const timeline = realRuntimeTimeline({
      consoleEntries: afterTrace.live,
      dataChannelEntries: afterTrace.channel,
      audioTrace: afterTrace.audio,
      networkEntries: telemetry.networkLog,
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
          fixtureMetadata: turnAudioMetadata,
      },
      transcription: {
        partialCount: transcript.partialCount,
        partialText: transcript.partialText,
        finalText: transcript.finalText,
        finalCount: transcript.finalCount,
        source: engine,
      },
      expectations: effectiveExpectations,
      requestedExpectations: expectations,
      questionAdvanceExpected: shouldAdvance,
      observed: {
        transcriptFinal: hasText(transcript.finalText),
        transcriptFinalCount: transcript.finalCount,
        candidateAnswerExists: hasText(answer.candidate),
        confirmedAnswerExists: hasText(answer.confirmed),
        answerState: answer.answerState,
        stateVersionAdvanced: stateVersionAdvancedForTurn,
        stateUpdateReceiptPersisted,
        questionAdvanced: advanced,
        assistantResponseCount: assistant.mainResponseCount,
        assistantPlaybackSettled: Boolean(outputPlaybackStatus?.settled),
        assistantPlaybackCompletionSource: outputPlaybackStatus?.settled
          ? (outputPlaybackStatus.settlementSource
            ?? "output_transcript_idle_with_post_output_rtp")
          : null,
        ackCount: assistant.ackCount,
        harnessValidity: harness,
        failureAnalysis: analysis,
      },
      expectedConditions: [],
      expectationResult: hardGate.expectationResult,
      expectationViolations: hardGate.expectationViolations,
      interview: {
        questionId: requestedQuestionId ?? currentQuestionId,
        currentQuestionText: currentQuestion,
        candidate: answer.candidate,
        confirmed: answer.confirmed,
        answerState: answer.answerState,
        stateVersionBefore: stateVersion(before),
        stateVersionAfter: stateVersionAfterTurn,
        currentQuestionBefore: interviewState(before).currentQuestionId ?? null,
        currentQuestionAfter: interviewState(after).currentQuestionId ?? null,
        nextQuestionId: expectedNextFieldAfter ? liveQuestionId(expectedNextFieldAfter) : null,
        nextQuestionText: expectedNextFieldAfter ? questionText(expectedNextFieldAfter) : null,
        followingQuestionId: followingQuestionField ? liveQuestionId(followingQuestionField) : null,
        followingQuestionText: followingQuestionField ? questionText(followingQuestionField) : null,
        questionLifecycle,
      },
      response: {
        assistantSpeechStarted: assistant.mainResponseCount > 0,
        assistantSpeechEnded: timing.assistantSpeechEnded !== null,
        assistantPlaybackSettled: Boolean(outputPlaybackStatus?.settled),
        assistantPlaybackCompletionSource: outputPlaybackStatus?.settled
          ? (outputPlaybackStatus.settlementSource
            ?? "output_transcript_idle_with_post_output_rtp")
          : null,
        assistantTranscript: assistant.text || null,
        firstAudioLatencyMs: timing.speechEndToFirstAudioMs,
        firstAudioAt: timing.firstAudio,
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
      realTiming: timing,
      harnessGate: harness,
      failureAnalysis: analysis,
      hardGate,
      timeline,
      conversation: {
        engine: engineLabel(engine),
        question: currentQuestion,
        user: transcript.finalText,
        assistant: assistant.text,
      },
      transcript: transcript.finalText,
      anomalies: [...new Set([
        ...(hardGate.anomalies ?? hardGate.codes),
        analysis.rootFailure,
        ...analysis.cascadeFailures,
      ].filter(Boolean))],
      errors: telemetry.errors,
    };
    telemetry.transcriptFinal({
      turn,
      source: engine,
      finalText: transcript.finalText,
      sourceTimestampMs: timing.transcriptFinal,
    });

    // The prior Live response is verified against the answer target of this
    // turn. This is the first point at which the application has a durable,
    // question-specific signal for the question that Live asked next.
    const previousStoredTurn = telemetry.turns.at(-1) ?? null;
    if (engine === ROBUSTNESS_ENGINE.gpt_live && previousStoredTurn) {
      const progression = verifyLiveQuestionProgression({
        previousTurn: previousStoredTurn,
        currentTurn: turnRecord,
      });
      const previousDetermination = previousStoredTurn.interview?.questionLifecycle
        ?.nextQuestionDetermination;
      if (previousDetermination) {
        previousDetermination.progressionVerification = progression.status;
        previousDetermination.nextTurnAnswerTargetQuestionId = progression.observedQuestionId;
        previousDetermination.progressionVerificationReason = progression.reason;
      }
      telemetry.event("question_progression_verification", {
        previousTurn: previousStoredTurn.turn,
        currentTurn: turn,
        ...progression,
      });
      if (progression.status === "MISMATCH") {
        hardGate = {
          ...hardGate,
          status: "FAIL",
          passed: false,
          codes: [...new Set([...(hardGate.codes ?? []), "QUESTION_NOT_ADVANCED"])]
            .filter(Boolean),
          anomalies: [...new Set([...(hardGate.anomalies ?? []), "QUESTION_NOT_ADVANCED"])]
            .filter(Boolean),
          reason: "live_next_turn_answer_target_mismatch",
        };
        turnRecord.hardGate = hardGate;
        turnRecord.anomalies = [...new Set([
          ...(turnRecord.anomalies ?? []),
          "QUESTION_NOT_ADVANCED",
        ])];
      }
    }
    telemetry.recordTurn(turnRecord);
    fs.writeFileSync(path.join(sessionDir, "telemetry.json"), JSON.stringify(telemetry.snapshot(), null, 2));
    fs.writeFileSync(path.join(sessionDir, "interview-state.json"), JSON.stringify(after, null, 2));
    if (hardGate.status === "FAIL" || harness.status === "INVALID") {
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
      return {
        sessionId,
        index: sessionIndex,
        engine,
        provider: engine,
        behavior: normalizedBehavior,
        variant: variant + 1,
        fixturePath,
        status: "FAIL",
        turn: turnRecord,
        hardGate,
        before,
        state: after,
        networkErrors,
        telemetry,
        evidenceWritten,
      };
    }

    lastTurnRecord = turnRecord;
    lastHardGate = hardGate;
    if (turn < maxTurns) {
      // WebRTC does not expose a per-response audio-complete DataChannel
      // event. Give the just-started response a short drain window before
      // replaying the next fixture so the next user turn cannot overlap the
      // current assistant response.
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
    }

    const lastStoredTurn = telemetry.turns.at(-1);
    const lastDetermination = lastStoredTurn?.interview?.questionLifecycle
      ?.nextQuestionDetermination;
    if (lastDetermination?.progressionVerification === "PENDING_NEXT_TURN") {
      lastDetermination.progressionVerification = "NOT_OBSERVED_WITHIN_BOUNDED_SESSION";
      lastDetermination.progressionVerificationReason = "session_reached_max_turns";
      telemetry.event("question_progression_verification", {
        previousTurn: lastStoredTurn.turn,
        currentTurn: null,
        status: "NOT_OBSERVED_WITHIN_BOUNDED_SESSION",
        expectedQuestionId: lastDetermination.expectedNextQuestionId ?? null,
        observedQuestionId: null,
        reason: "session_reached_max_turns",
      });
    }
    fs.writeFileSync(path.join(sessionDir, "telemetry.json"), JSON.stringify(telemetry.snapshot(), null, 2));

    if (tracingStarted) {
      await context.tracing.stop();
      tracingStarted = false;
    }
    return {
      sessionId,
      index: sessionIndex,
      engine,
      provider: engine,
      behavior: normalizedBehavior,
      variant: variant + 1,
      fixturePath,
      status: lastHardGate?.status === "FAIL" ? "FAIL" : "PASS",
      turn: lastTurnRecord,
      hardGate: lastHardGate,
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
      audio: window.__kikioriVoiceAudioTrace ?? { events: [], stats: [] },
    })).catch(() => finalTrace);
    finalTrace = trace;
    telemetry.setLiveTrace(trace.live);
    telemetry.setDataChannelTrace(trace.channel);
    telemetry.setAudioTrace(trace.audio);
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
    + "VOICE_TEST_RUN_ID=" + (config.runId ?? "replay") + " "
    + "VOICE_TEST_RUN_MODE=" + (config.runMode ?? "robustness") + " "
    + "pnpm exec playwright test tests/e2e/voice/live/conversation-robustness.spec.mjs "
    + "--project=chromium --workers=1 --retries=0";
}
