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
  REAL_VOICE_FIXTURE_TARGETS,
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

function diagnosticDataChannelPolicy(engine) {
  if (engine !== ROBUSTNESS_ENGINE.gpt_live) {
    return {};
  }
  const suppressTypes = String(process.env.VOICE_TEST_GPT_LIVE_DIAGNOSTIC_SUPPRESS_TYPES ?? "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
  const prependInstruction = String(
    process.env.VOICE_TEST_GPT_LIVE_DIAGNOSTIC_PREPEND_INSTRUCTION ?? "",
  ).toLowerCase() === "true";
  const gateAppendsAfterInstruction = String(
    process.env.VOICE_TEST_GPT_LIVE_DIAGNOSTIC_GATE_AFTER_INSTRUCTION ?? "",
  ).toLowerCase() === "true";
  const instructionOnDelegation = String(
    process.env.VOICE_TEST_GPT_LIVE_DIAGNOSTIC_INSTRUCTION_ON_DELEGATION ?? "",
  ).toLowerCase() === "true";
  const prependInstructionDelayMs = Number(process.env.VOICE_TEST_GPT_LIVE_DIAGNOSTIC_PREPEND_DELAY_MS);
  let sessionUpdate = null;
  try {
    const rawSessionUpdate = String(
      process.env.VOICE_TEST_GPT_LIVE_DIAGNOSTIC_SESSION_UPDATE_JSON ?? "",
    ).trim();
    if (rawSessionUpdate) {
      const parsed = JSON.parse(rawSessionUpdate);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        sessionUpdate = parsed;
      }
    }
  } catch {
    sessionUpdate = null;
  }
  return {
    suppressTypes,
    prependInstructionBeforeTypes: prependInstruction
      ? ["session.thinking.append"]
      : [],
    prependInstructionOnServerTypes: instructionOnDelegation
      ? ["session.delegation.created"]
      : [],
    gateAppendsAfterInstruction,
    prependInstructionDelayMs: Number.isFinite(prependInstructionDelayMs)
      ? Math.max(0, prependInstructionDelayMs)
      : 0,
    sessionUpdateAfterServerTypes: sessionUpdate ? ["session.started"] : [],
    sessionUpdate,
  };
}

function stateVersion(state) {
  return number(state?.interviewState?.stateVersion, 0);
}

function interviewState(state) {
  return state?.interviewState ?? state ?? {};
}

export function questionGenerationPending(state) {
  return interviewState(state).questionGenerationPending === true;
}

export function settledTurnBaselineState(interruptedState, previousSettledState) {
  if (!interruptedState) return previousSettledState;
  if (!previousSettledState) return interruptedState;
  return stateVersion(previousSettledState) >= stateVersion(interruptedState)
    ? previousSettledState
    : interruptedState;
}

function sortedFields(context) {
  return [...(context?.fields ?? [])].sort(
    (left, right) => number(left.displayOrder, 0) - number(right.displayOrder, 0),
  );
}

export function fieldForQuestion(context, questionId, state = null) {
  const fields = sortedFields(context);
  const questionIdText = String(questionId ?? "");
  const normalized = questionIdText.replace(/^live-field-/, "");
  const interview = interviewState(state);
  const askedQuestion = (interview.askedQuestions ?? []).find((question) => (
    question?.questionId === questionIdText
  ));
  const stateFieldId = askedQuestion?.fieldId
    ?? (interview.currentQuestionId === questionIdText ? interview.currentFieldId : null);
  return fields.find((field) => field.id === stateFieldId)
    ?? fields.find((field) => field.id === normalized || `live-field-${field.id}` === questionIdText)
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

/**
 * Build a bounded conversation rather than replaying one behavior in every
 * turn.  A robustness case needs a recovery boundary: the interviewer should
 * receive an ordinary answer, the stressed answer, and then another ordinary
 * answer so that we can observe whether the session returns to its normal
 * context.  The fixtures remain deterministic; this function only arranges
 * them into a session plan.
 */
export function realConversationPlan({
  engine = null,
  behaviors = engine
    ? REAL_VOICE_BEHAVIORS_BY_ENGINE[engine] ?? REAL_VOICE_BEHAVIORS
    : REAL_VOICE_BEHAVIORS,
  sessions = 0,
  maxTurns = 7,
} = {}) {
  const selected = [...new Set(behaviors.map(normalizeBehavior))]
    .filter((behavior) => REAL_VOICE_FIXTURE_TEXTS[behavior]);
  if (!selected.length) return [];
  const requested = Number(sessions);
  const count = Number.isFinite(requested) && requested > 0
    ? Math.floor(requested)
    : selected.length;
  const turnLimit = Math.max(1, Number(maxTurns) || 1);
  const targetCapacity = Math.max(1, Math.floor((turnLimit - 1) / 2));
  const targets = selected.filter((behavior) => behavior !== "NORMAL");
  const buckets = Array.from({ length: count }, () => []);
  const occurrences = new Map();
  for (const [index, behavior] of targets.entries()) {
    const bucket = buckets[index % count];
    if (bucket.length >= targetCapacity) continue;
    const variant = occurrences.get(behavior) ?? 0;
    occurrences.set(behavior, variant + 1);
    bucket.push({ behavior, variant: variant % 3 });
  }

  return buckets.map((bucket, index) => {
    const normalVariant = index % 3;
    const turns = [{ behavior: "NORMAL", variant: normalVariant }];
    for (const target of bucket) {
      if (turns.length + 2 > turnLimit) break;
      turns.push(target);
      turns.push({ behavior: "NORMAL", variant: (normalVariant + turns.length) % 3 });
    }
    // Keep a target-behavior session alive through the configured turn limit
    // so the harness can observe recovery, subsequent normal answers, and
    // later state transitions instead of stopping after the first recovery
    // turn. If the caller selected only NORMAL, retain the smaller bounded
    // smoke conversation without manufacturing a target behavior.
    const conversationLimit = bucket.length === 0
      ? Math.min(turnLimit, 3)
      : turnLimit;
    while (turns.length < conversationLimit) {
      turns.push({ behavior: "NORMAL", variant: (normalVariant + turns.length) % 3 });
    }
    return {
      behavior: bucket[0]?.behavior ?? "NORMAL",
      variant: bucket[0]?.variant ?? normalVariant,
      ordinal: index + 1,
      turns,
      fixtureText: turns.map((turn) => realVoiceFixtureText(turn.behavior, turn.variant)),
    };
  });
}

/**
 * Acceptance-only sequence for the Live partial-answer recovery path. The
 * clarification is produced by GPT-Live between turns 2 and 3; it is not a
 * synthetic user fixture. The target metadata is checked separately before a
 * Live session is opened.
 */
export function partialAcceptancePlan({ sessions = 2, maxTurns = 5 } = {}) {
  const sessionCount = Math.max(1, Math.floor(Number(sessions) || 1));
  const turnLimit = Math.max(4, Math.floor(Number(maxTurns) || 5));
  const plans = [];
  for (let index = 0; index < sessionCount; index += 1) {
    const variant = index % 3;
    const turns = [
      { behavior: "NORMAL", variant },
      { behavior: "PARTIAL_ANSWER", variant },
      { behavior: "PARTIAL_COMPLETION", variant },
      { behavior: "RECOVERY_NORMAL", variant },
    ].slice(0, turnLimit);
    plans.push({
      behavior: "PARTIAL_ANSWER",
      variant,
      ordinal: index + 1,
      turns,
      fixtureText: turns.map((turn) => realVoiceFixtureText(turn.behavior, turn.variant)),
    });
  }
  return plans;
}

function requiredAspectIds(field) {
  return (field?.questionPlan?.requiredItems ?? [])
    .map((item) => item?.itemId)
    .filter((itemId) => typeof itemId === "string" && itemId.trim());
}

function nextFieldAfter(context, field) {
  const fields = sortedFields(context);
  const index = fields.findIndex((candidate) => candidate?.id === field?.id);
  return index >= 0 ? fields[index + 1] ?? null : null;
}

/**
 * Check the semantic relationship between the first five acceptance inputs
 * and the dev voice-demo question order before a real Live session is
 * started. This is test-fixture validation, not a production answer router.
 */
export function buildFixtureCorrelationPreflight({
  context,
  state,
  plannedTurns = [],
  fixtureRecords = [],
} = {}) {
  const fields = sortedFields(context);
  const fieldById = new Map(fields.map((field) => [field.id, field]));
  const recordsByKey = new Map(fixtureRecords.map((record) => [
    `${record.behavior}:${record.variant}`,
    record,
  ]));
  const initialField = fieldForQuestion(context, interviewState(state).currentQuestionId, state)
    ?? fields[0]
    ?? null;
  const remaining = new Map(fields.map((field) => [field.id, requiredAspectIds(field)]));
  const rows = [];
  const violations = [];
  let currentField = initialField;

  if (!initialField) violations.push("current question field is unavailable");
  if (interviewState(state).currentQuestionId
    && initialField
    && interviewState(state).currentQuestionId !== liveQuestionId(initialField)) {
    violations.push("persisted currentQuestionId does not resolve to the current field");
  }

  for (const [index, turn] of plannedTurns.entries()) {
    const behavior = normalizeBehavior(turn.behavior);
    const variant = Number(turn.variant) || 0;
    const record = recordsByKey.get(`${behavior}:${variant}`);
    const targetField = record?.targetField ? fieldById.get(record.targetField) : null;
    const currentQuestionId = currentField ? liveQuestionId(currentField) : null;
    const currentQuestion = currentField ? questionText(currentField) : null;
    const missingAspects = currentField ? [...(remaining.get(currentField.id) ?? [])] : [];
    const expectedMissingAfterFixture = currentField
      ? missingAspects.filter((aspectId) => !(record?.targetAspects ?? []).includes(aspectId))
      : [];
    const row = {
      turn: index + 1,
      behavior,
      variant,
      currentQuestionId,
      currentQuestion,
      missingAspects,
      fixtureText: record?.text ?? realVoiceFixtureText(behavior, variant),
      fixtureTargetField: record?.targetField ?? null,
      fixtureTargetAspects: record?.targetAspects ?? [],
      expectedMissingAspectsAfterFixture: expectedMissingAfterFixture,
      expectedMissingAspectsFromMetadata: record?.expectedMissingAspects ?? null,
    };
    rows.push(row);

    if (!record) {
      violations.push(`turn ${index + 1}: fixture metadata is missing for ${behavior}:${variant}`);
    }
    if (!targetField) {
      violations.push(`turn ${index + 1}: fixture target field is not present in question context`);
    }
    if (!currentField || !currentQuestion) {
      violations.push(`turn ${index + 1}: current question is unavailable`);
    }
    if (record && currentField && record.targetField !== currentField.id) {
      violations.push(
        `turn ${index + 1}: fixture target ${record.targetField} does not match ${currentField.id}`,
      );
    }
    if (record
      && JSON.stringify(record.expectedMissingAspects ?? [])
        !== JSON.stringify(expectedMissingAfterFixture)) {
      violations.push(`turn ${index + 1}: fixture missing-aspect expectation is inconsistent`);
    }

    if (currentField) {
      const nextRemaining = expectedMissingAfterFixture;
      remaining.set(currentField.id, nextRemaining);
      currentField = nextRemaining.length === 0
        ? nextFieldAfter(context, currentField)
        : currentField;
    }
  }

  return {
    status: violations.length ? "FAIL" : "PASS",
    failureType: violations.length ? "TEST_INFRA_FAILURE" : null,
    currentState: stateSnapshot(state),
    rows,
    violations,
    fixtureTargets: REAL_VOICE_FIXTURE_TARGETS,
  };
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

function normalizedExpectations(expectations) {
  return expectations ?? {
    transcriptFinal: "required",
    answerPersistence: "required",
    questionAdvance: "required",
    assistantRecovery: "required",
  };
}

function eventEntries(dataTrace, provider, { includeClient = false } = {}) {
  return dataTrace.filter((entry) => includeClient || entry.direction === "server").map((entry) => ({
    timestampMs: entry.timestampMs,
    direction: entry.direction,
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

function observedModels(dataTrace = [], consoleTrace = []) {
  const models = new Set();
  const visit = (value) => {
    if (Array.isArray(value)) {
      value.forEach(visit);
      return;
    }
    if (!value || typeof value !== "object") return;
    for (const [key, child] of Object.entries(value)) {
      if (key === "model" && typeof child === "string" && child.trim()) {
        models.add(child.trim());
      }
      visit(child);
    }
  };
  visit(dataTrace);
  visit(consoleTrace);
  return {
    liveModels: [...models].filter((model) => model === "gpt-live-1"),
    responsesModels: [...models].filter((model) => model !== "gpt-live-1"),
  };
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

function traceDelegationId(entry) {
  return entry?.event?.delegation_id
    ?? entry?.event?.delegation?.id
    ?? entry?.envelope?.delegation_id
    ?? entry?.details?.delegation_id
    ?? null;
}

/**
 * Keep the two no-state cases observable. A Responses delegation can finish
 * without calling update_interview_state; that is different from Live never
 * creating a delegation at all, and the latter must not be inferred from an
 * empty State Store update record.
 */
export function delegationLifecycle({
  dataTrace = [],
  consoleTrace = [],
  turnStartedAt = null,
} = {}) {
  const isAfterBoundary = (entry) => turnStartedAt === null
    || number(entry.timestampMs, 0) >= number(turnStartedAt, 0);
  const runtime = eventEntries(dataTrace, ROBUSTNESS_ENGINE.gpt_live)
    .filter(isAfterBoundary);
  const consoleEntries = consoleEventEntries(consoleTrace).filter(isAfterBoundary);
  const created = [
    ...runtime.filter((entry) => entry.type === "session.delegation.created"),
    ...consoleEntries.filter((entry) => entry.logger === "gpt_live_delegation_created"),
  ];
  const functionCalls = [
    ...runtime.filter((entry) => (
      entry.type === "response.output_item.done"
      && entry.event?.item?.type === "function_call"
    )),
    ...consoleEntries.filter((entry) => entry.logger === "gpt_live_responses_function_call"),
  ];
  const stateToolCalls = functionCalls.filter((entry) => (
    entry.event?.item?.name === "update_interview_state"
    || entry.details?.name === "update_interview_state"
    // The application only emits gpt_live_responses_function_call after it
    // has accepted the update_interview_state tool. The item name is not
    // present in that diagnostic logger, so retain it as a transport-level
    // state-tool observation.
    || entry.logger === "gpt_live_responses_function_call"
  ));
  const resultSent = consoleEntries.filter((entry) => (
    entry.logger === "gpt_live_delegation_result_sent"
  ));
  const responseCreateSent = resultSent.filter((entry) => (
    entry.details?.type === "response.create"
  ));
  const backendCompleted = consoleEntries.filter((entry) => (
    entry.logger === "gpt_live_responses_backend_completed"
      || entry.logger === "gpt_live_responses_initial_backend_completed"
  ));
  const delegationIds = [...new Set([
    ...created,
    ...functionCalls,
    ...resultSent,
    ...backendCompleted,
  ].map(traceDelegationId).filter((value) => typeof value === "string" && value))];
  const observed = created.length > 0;
  const stateToolCalled = stateToolCalls.length > 0;
  const responseCreated = runtime.filter((entry) => entry.type === "response.created");
  const responseCompleted = runtime.filter((entry) => entry.type === "response.completed");
  return {
    status: !observed
      ? "NOT_OBSERVED"
      : stateToolCalled
        ? "OBSERVED_WITH_STATE_TOOL"
        : "OBSERVED_WITHOUT_STATE_TOOL",
    observed,
    createdCount: created.length,
    delegationIds,
    stateToolCalled,
    stateToolCallCount: stateToolCalls.length,
    functionResultSent: resultSent.some((entry) => entry.details?.type === "response.item.create"),
    responsesContinuationSent: responseCreateSent.length > 0,
    backendCompleted: backendCompleted.length > 0,
    responseCreatedCount: responseCreated.length,
    responseCompletedCount: responseCompleted.length,
    responseCreated: responseCreated.map((entry) => ({
      timestampMs: entry.timestampMs,
      responseId: entry.event?.response?.id ?? entry.event?.id ?? null,
    })),
    responseCompleted: responseCompleted.map((entry) => ({
      timestampMs: entry.timestampMs,
      responseId: entry.event?.response?.id ?? entry.event?.id ?? null,
      outputCount: Array.isArray(entry.event?.response?.output)
        ? entry.event.response.output.length
        : Array.isArray(entry.event?.output) ? entry.event.output.length : null,
    })),
    firstCreatedAt: created[0]?.timestampMs ?? null,
    firstStateToolCallAt: stateToolCalls[0]?.timestampMs ?? null,
    firstResultSentAt: resultSent[0]?.timestampMs ?? null,
    firstResponseCreateAt: responseCreateSent[0]?.timestampMs ?? null,
    completedAt: backendCompleted.at(-1)?.timestampMs ?? null,
  };
}

export function liveUpdateLifecycle({
  dataTrace = [],
  consoleTrace = [],
  turnStartedAt = null,
} = {}) {
  const afterBoundary = (entry) => turnStartedAt === null
    || number(entry.timestampMs, 0) >= number(turnStartedAt, 0);
  const runtime = dataTrace.filter(afterBoundary);
  const consoleEntries = consoleTrace
    .map((entry) => ({
      timestampMs: entry.timestampMs,
      logger: entry.args?.[0] ?? null,
      details: entry.args?.[1] ?? {},
      eventType: entry.args?.[0] === "gpt_live_event"
        ? entry.args?.[1]?.type ?? null
        : null,
    }))
    .filter(afterBoundary);
  const clientTypeCount = (type) => runtime.filter((entry) => (
    entry.direction === "client" && dataChannelEventType(entry) === type
  )).length;
  const sentCount = (type) => {
    const logged = consoleEntries.filter((entry) => (
      [
        "gpt_live_live_update_sent",
        "gpt_live_live_update_retry_sent",
      ].includes(entry.logger)
      && entry.details?.type === type
    )).length;
    return logged || clientTypeCount(type);
  };
  const ackTypeCount = (type) => {
    const loggerCount = consoleEntries.filter((entry) => (
      entry.logger === "gpt_live_live_update_acknowledged"
      && entry.details?.type === `${type}ed`
    )).length;
    if (loggerCount) return loggerCount;
    return consoleEntries.filter((entry) => entry.eventType === `${type}ed`).length;
  };
  return {
    interruptionBoundaryCount: consoleEntries.filter((entry) => (
      entry.logger === "gpt_live_response_interrupt_requested"
      || entry.logger === "gpt_live_live_continuation_boundary_sent"
    )).length || clientTypeCount("session.instructions.append"),
    thinkingAppendCount: sentCount("session.thinking.append"),
    commentaryAppendCount: sentCount("session.commentary.append"),
    commentaryAckCount: ackTypeCount("session.commentary.append"),
    thinkingAckCount: ackTypeCount("session.thinking.append"),
    instructionAckCount: ackTypeCount("session.instructions.append"),
  };
}

function findFirst(entries, predicate) {
  return entries.find(predicate) ?? null;
}

export function collectTranscript(
  dataEntries,
  consoleEntries,
  provider,
  { boundaryAt = null, endAt = null } = {},
) {
  const isAfterBoundary = (entry) => (
    (boundaryAt === null || number(entry.timestampMs, 0) >= number(boundaryAt, 0))
      && (endAt === null || number(entry.timestampMs, 0) < number(endAt, 0))
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
    lastFinalTimestampMs: allFinals.at(-1)?.timestampMs ?? null,
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

export function gptLiveResponseBoundary({
  statePersistenceAt = null,
  turnStartedAt = null,
} = {}) {
  return statePersistenceAt ?? turnStartedAt;
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

export function nextUnansweredField(context, state) {
  const interview = interviewState(state);
  const askedQuestion = (interview.askedQuestions ?? []).find((question) => (
    question?.questionId === interview.currentQuestionId
  ));
  const currentFieldId = String(
    interview.currentFieldId
      ?? askedQuestion?.fieldId
      ?? interview.currentQuestionId
      ?? "",
  ).replace(/^live-field-/, "");
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
  const finalLastAt = transcript.lastFinalTimestampMs ?? finalAt;
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
    transcriptFinalLast: finalLastAt,
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
    segmentFinalToTurnCommitMs: finalLastAt !== null && commitAt !== null
      ? Math.max(0, commitAt - finalLastAt) : null,
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

async function interruptAssistantWithNextFixture({
  page,
  request,
  engine,
  turnStartedAt,
  turn,
  fixtureIndex,
  fixturePath,
  timeoutMs,
}) {
  const trace = await waitForTrace(page, (value) => eventEntries(
    value.channel,
    engine,
    { includeClient: true },
  ).some((entry) => (
    entry.direction !== "client"
    && entry.type === "assistant_speech_started"
    && number(entry.timestampMs, 0) >= turnStartedAt
  )), timeoutMs);
  const entries = eventEntries(trace.channel, engine, { includeClient: true })
    .filter((entry) => number(entry.timestampMs, 0) >= turnStartedAt);
  const assistantStartedAt = entries.find((entry) => (
    entry.direction !== "client" && entry.type === "assistant_speech_started"
  ))?.timestampMs ?? null;
  const assistantAlreadyDrained = assistantStartedAt !== null && entries.some((entry) => (
    entry.direction === "client"
    && entry.type === "assistant_playback_drained"
    && number(entry.timestampMs, 0) >= number(assistantStartedAt, 0)
  ));
  if (assistantStartedAt === null || assistantAlreadyDrained) {
    return {
      observed: false,
      inputResumed: false,
      assistantStartedAt,
      microphoneReplayAt: null,
      preplayedTurn: null,
      error: assistantStartedAt === null
        ? "ASSISTANT_SPEECH_START_NOT_OBSERVED"
        : "BARGE_IN_WINDOW_ALREADY_DRAINED",
    };
  }

  const nextBaselineTranscriptCount = collectTranscript(
    trace.channel,
    trace.live,
    engine,
  ).finalCount;
  const nextBaselineAssistantResponseCount = collectAssistant(
    trace.channel,
    trace.live,
    engine,
  ).mainResponseCount;
  const beforeStatePromise = fetchInterviewState(request).catch(() => null);
  const beforeReplayTrace = await page.evaluate(() => ({
    channel: window.__kikioriVoiceDataChannelTrace?.entries ?? [],
  })).catch(() => ({ channel: [] }));
  const drainedBeforeReplay = eventEntries(
    beforeReplayTrace.channel,
    engine,
    { includeClient: true },
  ).some((entry) => (
    entry.direction === "client"
    && entry.type === "assistant_playback_drained"
    && number(entry.timestampMs, 0) >= number(assistantStartedAt, 0)
  ));
  if (drainedBeforeReplay) {
    return {
      observed: false,
      inputResumed: false,
      assistantStartedAt,
      microphoneReplayAt: null,
      preplayedTurn: null,
      error: "BARGE_IN_WINDOW_ALREADY_DRAINED",
    };
  }

  const microphoneReplayAt = Date.now();
  const replayPromise = replayWavFixtureMicrophone(page, fixtureIndex);
  const [beforeState, replay] = await Promise.all([beforeStatePromise, replayPromise]);
  if (!beforeState) {
    return {
      observed: false,
      inputResumed: false,
      assistantStartedAt,
      microphoneReplayAt,
      preplayedTurn: null,
      error: "PRE_BARGE_IN_STATE_SNAPSHOT_MISSING",
    };
  }
  if (!replay.started && replay.mode !== "browser_fallback") {
    return {
      observed: false,
      inputResumed: false,
      assistantStartedAt,
      microphoneReplayAt,
      preplayedTurn: null,
      error: `BARGE_IN_FIXTURE_START_FAILED:${String(replay.error || replay.mode)}`,
    };
  }
  const preplayedTurn = {
    startedAt: microphoneReplayAt,
    baselineTranscriptCount: nextBaselineTranscriptCount,
    baselineAssistantResponseCount: nextBaselineAssistantResponseCount,
    beforeState,
  };
  const interruptedTrace = await waitForTrace(page, (value) => {
    const observedEntries = eventEntries(value.channel, engine, { includeClient: true })
      .filter((entry) => number(entry.timestampMs, 0) >= microphoneReplayAt);
    const interrupted = observedEntries.some((entry) => (
      entry.direction !== "client" && entry.type === "assistant_interrupted"
    ));
    const inputResumed = observedEntries.some((entry) => (
      entry.direction !== "client"
      && entry.type === "input_state_changed"
      && ["ANSWER_LISTENING", "CONFIRMATION_LISTENING"].includes(entry.event?.inputState)
    ));
    return interrupted && inputResumed;
  }, Math.min(8_000, Math.max(1_000, timeoutMs)));
  const interruptedEntries = eventEntries(
    interruptedTrace.channel,
    engine,
    { includeClient: true },
  ).filter((entry) => number(entry.timestampMs, 0) >= microphoneReplayAt);
  const observed = interruptedEntries.some((entry) => (
    entry.direction !== "client" && entry.type === "assistant_interrupted"
  ));
  const inputResumed = interruptedEntries.some((entry) => (
    entry.direction !== "client"
    && entry.type === "input_state_changed"
    && ["ANSWER_LISTENING", "CONFIRMATION_LISTENING"].includes(entry.event?.inputState)
  ));
  return {
    observed,
    inputResumed,
    assistantStartedAt,
    microphoneReplayAt,
    preplayedTurn,
    turn: turn + 1,
    fixturePath,
    error: observed && inputResumed ? null : "REAL_BARGE_IN_NOT_OBSERVED",
  };
}

export function transcribePlaybackLifecycle(dataTrace, boundaryAt) {
  const entries = eventEntries(dataTrace, ROBUSTNESS_ENGINE.transcribe_polly, {
    includeClient: true,
  }).filter((entry) => number(entry.timestampMs, 0) >= number(boundaryAt, 0));
  const outputEndedAt = entries
    .filter((entry) => entry.direction !== "client" && entry.type === "assistant_speech_ended")
    .map((entry) => number(entry.timestampMs, null))
    .filter((timestamp) => timestamp !== null)
    .at(-1) ?? null;
  const browserDrainedAt = entries
    .filter((entry) => entry.direction === "client" && entry.type === "assistant_playback_drained")
    .map((entry) => number(entry.timestampMs, null))
    .filter((timestamp) => timestamp !== null)
    .at(-1) ?? null;
  const outputEnded = outputEndedAt !== null;
  const browserDrained = browserDrainedAt !== null;
  const playbackEndedAt = outputEnded && browserDrained
    ? Math.max(outputEndedAt, browserDrainedAt)
    : null;
  const inputResumedAt = playbackEndedAt === null ? null : entries
    .filter((entry) => (
      entry.direction !== "client"
      && entry.type === "input_state_changed"
      && ["ANSWER_LISTENING", "CONFIRMATION_LISTENING"].includes(entry.event?.inputState)
      && number(entry.timestampMs, 0) > playbackEndedAt
    ))
    .map((entry) => number(entry.timestampMs, null))
    .filter((timestamp) => timestamp !== null)[0] ?? null;
  const inputResumed = inputResumedAt !== null;
  return {
    settled: outputEnded && browserDrained && inputResumed,
    outputEnded,
    browserDrained,
    inputResumed,
    outputEndedAt,
    browserDrainedAt,
    inputResumedAt,
    settlementSource: outputEnded && browserDrained && inputResumed
      ? "assistant_speech_ended_and_browser_drained_then_input_resumed"
      : null,
  };
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
  expectations = null,
}) {
  const effectiveExpectations = normalizedExpectations(expectations);
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
  const transcriptExpected = effectiveExpectations.transcriptFinal !== "not_required";
  const commitExpected = effectiveExpectations.answerPersistence !== "must_not";
  const assistantExpected = effectiveExpectations.assistantRecovery !== "observe"
    && effectiveExpectations.assistantRecovery !== "not_required";
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
    transcriptReceived: transcriptExpected ? transcriptReceived : true,
    atLeastOneTurnCommitted: commitExpected ? atLeastOneTurnCommitted : true,
    assistantMainResponseReceived: assistantExpected ? assistantMainResponseReceived : true,
  };
  const missing = Object.entries(checks)
    .filter(([key, value]) => key !== "speechDetected" && !value)
    .map(([key]) => key);
  if (!speechWasDetected && transcriptExpected) missing.push("speechDetected");
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
  expectations = null,
}) {
  const effectiveExpectations = normalizedExpectations(expectations);
  const errors = [
    ...errorTypes(dataTrace, consoleTrace, networkErrors),
    ...dataChannelErrors.map((entry) => `${entry?.type ?? ""} ${entry?.message ?? ""}`),
  ];
  let rootFailure = null;
  const transcriptRequired = effectiveExpectations.transcriptFinal !== "not_required";
  const persistenceRequired = effectiveExpectations.answerPersistence === "required";
  const responseRequired = effectiveExpectations.assistantRecovery !== "observe"
    && effectiveExpectations.assistantRecovery !== "not_required";
  if (!harness.checks.sessionEstablished) rootFailure = "SESSION_NOT_ESTABLISHED";
  else if (!harness.checks.microphoneTrackReady) rootFailure = "MICROPHONE_TRACK_NOT_READY";
  else if (!harness.checks.audioActuallySent) rootFailure = "AUDIO_NOT_SENT";
  else if (transcriptRequired && !harness.checks.speechDetected) rootFailure = "UTTERANCE_END_NOT_DETECTED";
  else if (errors.some((value) => /unknown_parameter/i.test(value))) {
    rootFailure = "GPT_LIVE_PROTOCOL_ERROR";
  } else if (errors.some((value) => /5\d\d/i.test(value))) {
    rootFailure = "API_5XX";
  } else if (errors.some((value) => /websocket/i.test(value))) {
    rootFailure = "WEBSOCKET_ERROR";
  } else if (errors.some((value) => /data.?channel/i.test(value))) {
    rootFailure = "DATACHANNEL_ERROR";
  } else if (transcriptRequired && !hasText(transcript.finalText)) {
    rootFailure = "TRANSCRIPT_FINAL_MISSING";
  } else if (persistenceRequired && !persisted) rootFailure = "ANSWER_NOT_PERSISTED";
  else if (questionAdvanceExpected && !advanced) rootFailure = "QUESTION_NOT_ADVANCED";
  else if (responseRequired && assistant.mainResponseCount === 0) rootFailure = "ASSISTANT_RESPONSE_MISSING";

  const observedFailures = [];
  if (transcriptRequired && !hasText(transcript.finalText)) observedFailures.push("TRANSCRIPT_FINAL_MISSING");
  if (persistenceRequired && !persisted) observedFailures.push("ANSWER_NOT_PERSISTED");
  if (questionAdvanceExpected && !advanced) observedFailures.push("QUESTION_NOT_ADVANCED");
  if (responseRequired && assistant.mainResponseCount === 0) observedFailures.push("RESPONSE_TIMEOUT");
  if (timing.turnCommit !== null && timing.transcriptFinal !== null
    && timing.turnCommit < (timing.transcriptFinalLast ?? timing.transcriptFinal)) {
    observedFailures.push("TURN_COMMITTED_TOO_EARLY");
  }
  const cascadeFailures = [...new Set(observedFailures.filter((failure) => failure !== rootFailure))];
  const transportPrerequisitesSatisfied = Boolean(
    harness.checks.sessionEstablished
      && harness.checks.microphoneTrackReady
      && harness.checks.audioActuallySent
      && harness.checks.speechDetected
      && harness.checks.transcriptReceived
      && harness.checks.atLeastOneTurnCommitted,
  );
  const classification = harness.classification === "TEST_INFRA_FAILURE"
    && rootFailure === "ASSISTANT_RESPONSE_MISSING"
    && transportPrerequisitesSatisfied
    ? "PRODUCT_FAILURE"
    : harness.classification || (rootFailure ? "PRODUCT_OR_RUNTIME_FAILURE" : null);
  return {
    classification,
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
  conversationTurns = null,
  strictTranscribePlayback = false,
}) {
  const normalizedBehavior = normalizeBehavior(behavior);
  const plannedTurns = Array.isArray(conversationTurns) && conversationTurns.length
    ? conversationTurns.map((item) => ({
      behavior: normalizeBehavior(item.behavior),
      variant: Number.isFinite(Number(item.variant)) ? Number(item.variant) : 0,
      fixtureRecord: item.fixtureRecord ?? null,
      interruptAssistant: item.interruptAssistant === true,
    }))
    : null;
  const fixtureSequence = plannedTurns
    ? plannedTurns.map((item) => resolveRealAudioFixture({
      config,
      behavior: item.behavior,
      variant: item.variant,
    }))
    : resolveRealAudioFixtureSequence({
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
  const preplayedTurns = new Map();

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
    await installVoiceDataChannelTrace(page, diagnosticDataChannelPolicy(engine));
    await installWavFixtureMicrophone(page, fixtureSequence);
    await installVoiceAudioTrace(page);
    await resetVoiceDemo(request);
    contextData = await fetchInterviewContext(request);
    before = await fetchInterviewState(request);
    const fields = sortedFields(contextData);
    let currentField = fieldForQuestion(
      contextData,
      interviewState(before).currentQuestionId,
      before,
    );
    let currentQuestionId = currentField ? liveQuestionId(currentField) : null;
    let currentQuestion = currentField ? questionText(currentField) : null;
    telemetry.event("robustness_session_started", {
      engine,
      provider: engine,
      behavior: normalizedBehavior,
      variant: variant + 1,
      fixturePath,
      fixtureText: realVoiceFixtureText(normalizedBehavior, variant),
      conversationTurns: plannedTurns ?? fixtureSequence.map((_, index) => ({
        behavior: normalizedBehavior,
        variant: (variant + index) % 3,
      })),
      currentQuestionId,
      currentQuestion,
      baseline: stateSnapshot(before),
    });
    await page.addInitScript(() => {
      window.localStorage.setItem("ai-interviewer-dev-token", "dev-manager");
    });
    const initialReplyBoundaryAt = Date.now();
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
        currentField = fieldForQuestion(
          contextData,
          interviewState(before).currentQuestionId,
          before,
        );
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
      if (engine === ROBUSTNESS_ENGINE.transcribe_polly) {
        return transcribePlaybackLifecycle(value.channel, initialReplyBoundaryAt).settled;
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
    if (strictTranscribePlayback && !initialQuestionReady) {
      throw new Error("TRANSCRIBE_POLLY_INITIAL_QUESTION_PLAYBACK_NOT_SETTLED");
    }
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

    const maxTurns = Math.min(
      Math.max(1, Number(config.maxTurnsPerSession) || 1),
      fixtureSequence.length,
    );
    for (turn = 1; turn <= maxTurns; turn += 1) {
      const previousSettledState = after;
      after = null;
      const turnFixturePath = fixtureSequence[(turn - 1) % fixtureSequence.length];
      const turnPlan = plannedTurns?.[turn - 1] ?? {
        behavior: normalizedBehavior,
        variant: (variant + turn - 1) % 3,
      };
      const turnBehavior = normalizeBehavior(turnPlan.behavior);
      const nextTurnPlan = plannedTurns?.[turn] ?? null;
      const nextTurnInterruptsAssistant = nextTurnPlan?.interruptAssistant === true;
      const preplayedTurn = preplayedTurns.get(turn) ?? null;
      const expectations = behaviorExpectations(turnBehavior, engine);
      const turnAudioMetadata = readWavMetadata(turnFixturePath);
      const turnFixtureText = turnPlan.fixtureRecord?.text
        ?? realVoiceFixtureText(turnBehavior, turnPlan.variant);
      if (turn > 1) {
        before = preplayedTurn
          ? (settledTurnBaselineState(
            preplayedTurn.beforeState,
            previousSettledState,
          ) ?? await fetchInterviewState(request).catch(() => before))
          : await fetchInterviewState(request).catch(() => before);
        currentField = fieldForQuestion(
          contextData,
          interviewState(before).currentQuestionId,
          before,
        );
        currentQuestionId = currentField ? liveQuestionId(currentField) : null;
        currentQuestion = currentField ? questionText(currentField) : null;
        if (!preplayedTurn) {
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
      }
    const turnBaseline = await page.evaluate(() => ({
      live: window.__kikioriVoiceLiveTrace?.entries ?? [],
      channel: window.__kikioriVoiceDataChannelTrace?.entries ?? [],
      audio: window.__kikioriVoiceAudioTrace ?? { events: [], stats: [] },
    })).catch(() => ({ live: [], channel: [], audio: { events: [], stats: [] } }));
    const baselineTranscriptCount = preplayedTurn?.baselineTranscriptCount
      ?? collectTranscript(turnBaseline.channel, turnBaseline.live, engine).finalCount;
    const baselineAssistantResponseCount = preplayedTurn?.baselineAssistantResponseCount
      ?? collectAssistant(turnBaseline.channel, turnBaseline.live, engine).mainResponseCount;
    turnStartedAt = preplayedTurn?.startedAt ?? Date.now();
    guard.startTurn(turnAudioMetadata.durationSeconds);
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
      return turnBehavior === "SILENCE" ? Date.now() - turnStartedAt > 8_000 : hasFinal;
    }, timeoutMs);
    finalTrace = trace;
    const preliminaryTranscript = collectTranscript(trace.channel, trace.live, engine, {
      boundaryAt: turnStartedAt,
    });
    if (preliminaryTranscript.finalCount > 0 || turnBehavior === "SILENCE") {
      telemetry.event("transcript_final", {
        finalText: preliminaryTranscript.finalText,
        finalCount: preliminaryTranscript.finalCount,
        partialCount: preliminaryTranscript.partialCount,
        engine,
      });
    }

    // Start the following fixture as soon as Polly begins speaking. Waiting
    // until the current turn's persistence and telemetry checks finish misses
    // the actual VAD interruption window.
    let bargeInPromise = null;
    if (
      strictTranscribePlayback
      && engine === ROBUSTNESS_ENGINE.transcribe_polly
      && preliminaryTranscript.finalCount > 0
      && nextTurnInterruptsAssistant
    ) {
      const remainingTurnMs = Math.max(1_000, timeoutMs - (Date.now() - turnStartedAt));
      bargeInPromise = interruptAssistantWithNextFixture({
        page,
        request,
        engine,
        turnStartedAt,
        turn,
        fixtureIndex: turn,
        fixturePath: fixtureSequence[turn],
        timeoutMs: remainingTurnMs,
      }).catch((error) => ({
        observed: false,
        inputResumed: false,
        assistantStartedAt: null,
        microphoneReplayAt: null,
        preplayedTurn: null,
        error: error instanceof Error ? error.message : String(error),
      }));
    }

    // If the provider never emits a final transcript, there is no useful
    // reason to keep polling the State Store for the full turn timeout. Keep
    // the failure evidence bounded and let the hard gate classify it.
    const stateWaitMs = turnBehavior === "SILENCE"
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
      const questionStillGenerating = questionGenerationPending(candidateState);
      if (candidateState && (
        turnBehavior === "SILENCE"
        || (stateChanged && !questionStillGenerating && (
          answerReady
          || questionChanged
          || NO_ADVANCE_BEHAVIORS.has(turnBehavior)
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
      // A state-tool result is a useful boundary for separating a short
      // pre-handoff acknowledgement from the post-handoff interview reply.
      // When no State Tool was called, however, there is no such boundary;
      // using the snapshot-read timestamp would hide a valid Live response
      // that already started after the user's turn. In that case count Live
      // output from the user-turn boundary instead.
      const responseBoundaryAt = engine === ROBUSTNESS_ENGINE.gpt_live
        ? gptLiveResponseBoundary({
          statePersistenceAt: observedStatePersistenceAt,
          turnStartedAt,
        })
        : stateObservedAt;
      telemetry.event("assistant_response_wait_started", {
        budgetMs: responseWaitBudgetMs,
        responseBoundaryAt,
      });
      const responseTrace = await waitForTrace(page, (value) => {
        const assistant = collectAssistant(value.channel, value.live, engine);
        if (engine === ROBUSTNESS_ENGINE.gpt_live) {
          const stateAt = statePersistenceTimestamp(value.live, turnStartedAt)
            ?? gptLiveResponseBoundary({ turnStartedAt });
          return assistant.starts.some((entry) => (
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
              ?? gptLiveResponseBoundary({ turnStartedAt }))
          ))
          : collectAssistant(responseTrace.channel, responseTrace.live, engine).mainResponseCount > 0,
        elapsedMs: Math.max(0, Date.now() - responseWaitStartedAt),
      });
      if (engine === ROBUSTNESS_ENGINE.gpt_live) {
        const drainBoundaryAt = statePersistenceTimestamp(responseTrace.live, turnStartedAt)
          ?? gptLiveResponseBoundary({ turnStartedAt });
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

    if (
      strictTranscribePlayback
      && engine === ROBUSTNESS_ENGINE.transcribe_polly
      && preliminaryTranscript.finalCount > 0
      && !nextTurnInterruptsAssistant
    ) {
      const playbackTimeoutMs = Number.parseInt(
        process.env.VOICE_TEST_REAL_POLLY_DRAIN_TIMEOUT_MS || "20000",
        10,
      );
      const playbackTrace = await waitForTrace(page, (value) => (
        transcribePlaybackLifecycle(value.channel, turnStartedAt).settled
      ), Math.max(1_000, playbackTimeoutMs));
      finalTrace = playbackTrace;
      outputPlaybackStatus = transcribePlaybackLifecycle(
        playbackTrace.channel,
        turnStartedAt,
      );
      telemetry.event("transcribe_polly_playback_drain_completed", {
        ...outputPlaybackStatus,
        turn,
        timeoutMs: playbackTimeoutMs,
      });
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

    const bargeInResult = bargeInPromise ? await bargeInPromise : null;
    if (bargeInResult?.preplayedTurn) {
      preplayedTurns.set(turn + 1, bargeInResult.preplayedTurn);
    }
    if (bargeInResult) {
      telemetry.event("real_barge_in_observed", {
        turn: turn + 1,
        observed: bargeInResult.observed,
        inputResumed: bargeInResult.inputResumed,
        assistantStartedAt: bargeInResult.assistantStartedAt,
        microphoneReplayAt: bargeInResult.microphoneReplayAt,
        fixturePath: bargeInResult.fixturePath ?? fixtureSequence[turn],
        error: bargeInResult.error,
      });
    }

    const acceptanceFieldForState = (state) => {
      const answer = currentField
        ? extractFieldAnswer(interviewState(state), currentField.id)
        : null;
      return NO_ADVANCE_BEHAVIORS.has(turnBehavior)
        || !questionAdvanceExpected({ expectations, answer })
        ? currentField
        : nextFieldAfter(contextData, currentField);
    };
    let expectedAcceptanceField = acceptanceFieldForState(after);
    if (
      strictTranscribePlayback
      && engine === ROBUSTNESS_ENGINE.transcribe_polly
      && preliminaryTranscript.finalCount > 0
      && !nextTurnInterruptsAssistant
    ) {
      const stateRefreshDeadline = Date.now() + Math.min(timeoutMs, 8_000);
      let expectedStateObserved = false;
      while (Date.now() < stateRefreshDeadline) {
        const candidateState = await fetchInterviewState(request).catch(() => null);
        if (candidateState && stateVersion(candidateState) >= stateVersion(after)) {
          after = candidateState;
          expectedAcceptanceField = acceptanceFieldForState(candidateState);
          const actualField = fieldForQuestion(
            contextData,
            interviewState(candidateState).currentQuestionId,
            candidateState,
          );
          const targetMatches = expectedAcceptanceField
            ? actualField?.id === expectedAcceptanceField.id
            : interviewState(candidateState).status === "completed";
          const answer = currentField
            ? extractFieldAnswer(interviewState(candidateState), currentField.id)
            : {};
          const partialCandidateStored = turnBehavior !== "PARTIAL_ANSWER"
            || (hasText(answer.candidate)
              && !["CONFIRMED", "ANSWERED"].includes(
                String(answer.answerState ?? "").toUpperCase(),
              ));
          if (targetMatches && partialCandidateStored) {
            expectedStateObserved = true;
            break;
          }
        }
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      stateObservedAt = Date.now();
      telemetry.event("interview_state_after_response_observed", {
        expectedFieldId: expectedAcceptanceField?.id ?? null,
        actualFieldId: fieldForQuestion(
          contextData,
          interviewState(after).currentQuestionId,
          after,
        )?.id ?? null,
        expectedStateObserved,
        state: stateSnapshot(after),
      });
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
    const modelObservation = observedModels(afterTrace.channel, afterTrace.live);
    telemetry.setModelObservation({
      liveModel: modelObservation.liveModels[0] ?? null,
      responsesModels: modelObservation.responsesModels,
    });
    telemetry.event("models_observed", modelObservation);
    const bargeInAssistantStartAt = bargeInResult?.assistantStartedAt ?? null;
    const transcript = collectTranscript(afterTrace.channel, afterTrace.live, engine, {
      boundaryAt: turnStartedAt,
      // The following fixture is sent while this assistant response is
      // playing. Keep its transcript deltas/final out of the current turn's
      // evidence, even if Transcribe finishes quickly.
      endAt: nextTurnInterruptsAssistant ? bargeInAssistantStartAt : null,
    });
    const responseBoundaryAt = engine === ROBUSTNESS_ENGINE.gpt_live
      ? gptLiveResponseBoundary({
        statePersistenceAt: statePersistenceTimestamp(afterTrace.live, turnStartedAt),
        turnStartedAt,
      })
      : null;
    const assistant = assistantAfterBoundary(
      collectAssistant(afterTrace.channel, afterTrace.live, engine, {
        boundaryAt: turnStartedAt,
        // The response boundary is where Live output may be counted. It is
        // not the end of the current user input: for a no-state delegation it
        // can be earlier than the input transcript itself. Use the current
        // final transcript as the lower bound for finding a *later* input
        // transcript, so the current turn's own input cannot hide its Live
        // response from the evidence collector.
        inputEndAt: transcript.lastFinalTimestampMs ?? responseBoundaryAt,
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
    const delegationLifecycleState = engine === ROBUSTNESS_ENGINE.gpt_live
      ? delegationLifecycle({
        dataTrace: afterTrace.channel,
        consoleTrace: afterTrace.live,
        turnStartedAt,
      })
      : null;
    const liveUpdateLifecycleState = engine === ROBUSTNESS_ENGINE.gpt_live
      ? liveUpdateLifecycle({
        dataTrace: afterTrace.channel,
        consoleTrace: afterTrace.live,
        turnStartedAt,
      })
      : null;
    if (delegationLifecycleState) {
      telemetry.event("delegation_lifecycle", delegationLifecycleState);
    }
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
    const transcriptQuestionId = transcript.finalEntries
      .map((entry) => entry.event?.questionId ?? entry.event?.question_id)
      .find(hasText);
    const fallbackQuestionId = engine === ROBUSTNESS_ENGINE.transcribe_polly
      ? interviewState(before).currentQuestionId
      : interviewState(after).currentQuestionId;
    const requestedQuestionId = stateUpdateLifecycle?.request?.questionId
      || transcriptQuestionId
      || fallbackQuestionId
      || currentQuestionId;
    const answerField = fieldForQuestion(contextData, requestedQuestionId, before) ?? currentField;
    const answer = extractFieldAnswer(
      interviewState(after),
      answerField?.id,
    );
    const beforeAnswer = extractFieldAnswer(
      interviewState(before),
      currentField?.id,
    );
    const answerStateMutationObserved = ["answerState", "candidate", "confirmed"].some(
      (key) => beforeAnswer?.[key] !== answer?.[key],
    );
    const beforeQuestionId = interviewState(before).currentQuestionId ?? currentQuestionId;
    const currentFieldAfter = fieldForQuestion(
      contextData,
      interviewState(after).currentQuestionId,
      after,
    );
    const currentFieldBeforeId = currentField?.id ?? null;
    const currentFieldAfterId = currentFieldAfter?.id ?? null;
    const expectedFieldAfterId = expectedAcceptanceField?.id ?? null;
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
    const advanced = strictTranscribePlayback
      && engine === ROBUSTNESS_ENGINE.transcribe_polly
      ? currentFieldAfterId !== currentFieldBeforeId
      : questionAdvanced({
        before,
        after,
        requestedQuestionId,
        expectedQuestionId: sameTurnExpectedNextQuestionId,
      });
    // In GPT-Live, a changed currentQuestionId on this record means that the
    // current transcript was routed to the next answer target. It is not a
    // same-turn question-advance assertion: Live has already spoken the next
    // question, and the following state update may legitimately be PARTIAL
    // while keeping that target. Passing this value to the hard gate as an
    // observed advance would classify a valid PARTIAL turn as
    // UNEXPECTED_QUESTION_ADVANCE.
    const questionAdvancedForHardGate = engine === ROBUSTNESS_ENGINE.gpt_live
      || (strictTranscribePlayback && engine === ROBUSTNESS_ENGINE.transcribe_polly)
      ? null
      : advanced;
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
        && (timing.transcriptFinalLast ?? timing.transcriptFinal) !== null
        && timing.turnCommit < (timing.transcriptFinalLast ?? timing.transcriptFinal),
      apiStatuses: telemetry.networkLog
        .filter((item) => item.direction === "response")
        .map((item) => item.status),
      dataChannelErrors: telemetry.dataChannelErrors,
      questionShouldAdvance: shouldAdvance,
      questionAdvanced: questionAdvancedForHardGate,
      expectAnswerPersistence: expectations.answerPersistence === "required",
      expectations: effectiveExpectations,
      answerPersistedBefore: false,
      answerPersistedThisTurn: persisted,
    });
    const extraFailures = failureCodes({
      behavior: turnBehavior,
      engine,
      hardGate: hardGateBase,
      transcript,
      assistant,
      dataTrace: afterTrace.channel,
      consoleTrace: afterTrace.live,
    });
    if (
      strictTranscribePlayback
      && engine === ROBUSTNESS_ENGINE.transcribe_polly
      && expectations.transcriptFinal === "required"
      && transcript.partialCount < 1
    ) {
      extraFailures.push("TRANSCRIBE_PARTIAL_RESULT_MISSING");
    }
    if (
      strictTranscribePlayback
      && engine === ROBUSTNESS_ENGINE.transcribe_polly
      && preliminaryTranscript.finalCount > 0
      && !nextTurnInterruptsAssistant
      && !outputPlaybackStatus?.settled
    ) {
      extraFailures.push("POLLY_PLAYBACK_DRAIN_NOT_CONFIRMED");
    }
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
      expectations: effectiveExpectations,
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
      expectations: effectiveExpectations,
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
        fieldId: currentFieldBeforeId,
        questionText: currentQuestion,
        answerState: beforeAnswer.answerState ?? null,
        candidate: beforeAnswer.candidate ?? null,
        confirmed: beforeAnswer.confirmed ?? null,
        stateVersion: stateVersion(before),
      },
      userTranscriptFinal: transcript.finalText || null,
      delegationId: stateUpdateLifecycle?.delegationId ?? null,
      delegationObservation: delegationLifecycleState,
      statePersistenceRequest: stateUpdateLifecycle?.request ?? null,
      statePersistenceResponse: persistedResponse,
      backendSelectedQuestionId: null,
      backendSelectedQuestionText: null,
      nextQuestionDetermination: {
        owner: engine === ROBUSTNESS_ENGINE.gpt_live ? "gpt_live" : "application_backend",
        reason: engine === ROBUSTNESS_ENGINE.gpt_live
          ? "application_backend_persists_answer_target_only"
          : "backend_state_tracks_answer_target_and_progression",
        answerTargetQuestionId: requestedQuestionId ?? null,
        selectedForThisTurnQuestionId: requestedQuestionId ?? null,
        selectedForThisTurnQuestionText: answerField ? questionText(answerField) : null,
        sameTurnExpectedNextQuestionId: sameTurnExpectedNextQuestionId,
        sameTurnExpectedNextQuestionText: expectedNextFieldBefore
          ? questionText(expectedNextFieldBefore)
          : null,
        expectedNextQuestionId: expectedAcceptanceField
          ? liveQuestionId(expectedAcceptanceField)
          : expectedNextQuestionId,
        expectedNextQuestionText: expectedAcceptanceField
          ? questionText(expectedAcceptanceField)
          : expectedNextFieldAfter
            ? questionText(expectedNextFieldAfter)
            : null,
        selectedQuestionMatchesExpected: shouldAdvance
          ? requestedQuestionId === sameTurnExpectedNextQuestionId
          : null,
        progressionVerification: strictTranscribePlayback
          && engine === ROBUSTNESS_ENGINE.transcribe_polly
          ? currentFieldAfterId === expectedFieldAfterId ? "VERIFIED" : "MISMATCH"
          : "PENDING_NEXT_TURN",
        spokenText: assistant.text || null,
      },
      frontendStateApplied,
      after: {
        questionId: stateUpdateLifecycle?.response?.currentQuestionId
          ?? interviewState(after).currentQuestionId
          ?? null,
        fieldId: currentFieldAfterId,
        questionText: currentFieldAfter ? questionText(currentFieldAfter) : null,
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
      behavior: turnBehavior,
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
        stateMutationOccurred: answerStateMutationObserved,
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
        currentFieldBeforeId,
        currentFieldAfterId,
        expectedFieldAfterId,
        candidate: answer.candidate,
        confirmed: answer.confirmed,
        answerState: answer.answerState,
        answerStatus: stateUpdateLifecycle?.request?.answerStatus ?? null,
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
      delegation: delegationLifecycleState,
      liveUpdates: liveUpdateLifecycleState,
      response: {
        assistantSpeechStarted: assistant.mainResponseCount > 0,
        liveOutputStarted: assistant.mainResponseCount > 0,
        assistantAudioStarted: assistant.playbackStartedAt !== null
          || afterTrace.live.some((entry) => (
            entry.args?.[0] === "gpt_live_live_audio_started"
            && number(entry.timestampMs, 0) >= number(turnStartedAt, 0)
          )),
        assistantSpeechEnded: timing.assistantSpeechEnded !== null,
        assistantPlaybackSettled: Boolean(outputPlaybackStatus?.settled),
        assistantPlaybackCompletionSource: outputPlaybackStatus?.settled
          ? (outputPlaybackStatus.settlementSource
            ?? "output_transcript_idle_with_post_output_rtp")
          : null,
        assistantTranscript: assistant.text || null,
        firstAudioLatencyMs: timing.speechEndToFirstAudioMs,
        firstAudioAt: timing.firstAudio,
        transcriptFinalLastAt: timing.transcriptFinalLast,
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
        liveModel: telemetry.liveModel ?? null,
        responsesModels: telemetry.responsesModels ?? [],
      },
      fixtureCorrelation: {
        fixtureText: turnFixtureText,
        fixtureTargetField: turnPlan.fixtureRecord?.targetField ?? null,
        fixtureTargetAspects: turnPlan.fixtureRecord?.targetAspects ?? [],
        currentQuestionId: beforeQuestionId,
        currentQuestionText: currentQuestion,
        missingAspects: stateUpdateLifecycle?.request?.missingAspects ?? [],
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
    if (turn < maxTurns && !nextTurnInterruptsAssistant) {
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
