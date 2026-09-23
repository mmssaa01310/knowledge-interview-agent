import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

export const JEV_SESSION_ACTIONS = Object.freeze([
  "CONTINUE",
  "STRESS_NEXT",
  "RETRY_CURRENT",
  "END_SUCCESS",
]);

export const JEV_USER_BEHAVIORS = Object.freeze([
  "NORMAL",
  "HESITATE",
  "SELF_CORRECT",
  "AMBIGUOUS",
  "ASK_CLARIFICATION",
  "SILENCE",
  "MULTI_ANSWER",
]);

export const TYPESAFE_DEFAULT_BASE_URL = "https://api.typesafe.ai";
export const TYPESAFE_DEFAULT_MODEL = "jev-latest";

export const JEV_SUPERVISOR_CRITERIA = Object.freeze([
  "The purpose is to find useful untested failure modes, not to make the test pass.",
  "Prefer a behavior that exercises an untested transition or state.",
  "Avoid repeating a behavior already present in testedBehaviors unless it is necessary to probe a different state.",
  "Return only the structured decision fields; never generate user transcript text.",
  "Use next_user_behavior only for STRESS_NEXT or RETRY_CURRENT.",
  "Respect remainingTurns and remainingJevCalls.",
]);

const JEV_CACHE = new Map();
const JEV_IN_FLIGHT = new Map();

const TYPESAFE_ACTION_CRITERIA = Object.freeze({
  CONTINUE: "Continue with a normal answer fixture when no untested stress case is more valuable.",
  STRESS_NEXT: "Advance to the next basic question and apply the selected unusual user behavior.",
  RETRY_CURRENT: "Keep the current question and apply a different fixture to test the same state boundary.",
  END_SUCCESS: "End this session because its useful exploration is complete or the budget is nearly exhausted.",
});

const TYPESAFE_BEHAVIOR_CRITERIA = Object.freeze({
  NORMAL: "A clear, ordinary answer to the current interview question.",
  HESITATE: "An answer containing a brief hesitation before a meaningful response.",
  SELF_CORRECT: "An answer that corrects an initial interpretation or role.",
  AMBIGUOUS: "A vague answer that is related but leaves semantic coverage uncertain.",
  ASK_CLARIFICATION: "The user asks what the question means instead of answering it.",
  SILENCE: "The user produces no usable final transcript; this is a transport or timeout stress case.",
  MULTI_ANSWER: "The user answers multiple nearby interview aspects in one utterance.",
});

function stableStringify(value) {
  if (Array.isArray(value)) return "[" + value.map(stableStringify).join(",") + "]";
  if (value && typeof value === "object") {
    return "{" + Object.keys(value).sort().map((key) => (
      JSON.stringify(key) + ":" + stableStringify(value[key])
    )).join(",") + "}";
  }
  return JSON.stringify(value);
}

function cacheKeyFor(state) {
  return crypto.createHash("sha256").update(stableStringify(state)).digest("hex");
}

function parseDotEnvValue(raw) {
  const value = String(raw ?? "").trim();
  if (!value) return "";
  if ((value.startsWith("\"") && value.endsWith("\""))
    || (value.startsWith("'") && value.endsWith("'"))) {
    return value.slice(1, -1);
  }
  return value;
}

function readDotEnvValue(name) {
  const candidates = [
    path.resolve(process.cwd(), ".env"),
    path.resolve(process.cwd(), "..", ".env"),
  ];
  for (const filePath of candidates) {
    try {
      const content = fs.readFileSync(filePath, "utf8");
      for (const line of content.split(/\r?\n/)) {
        const match = line.match(new RegExp("^\\s*(?:export\\s+)?" + name + "\\s*=\\s*(.*)$"));
        if (match) return parseDotEnvValue(match[1]);
      }
    } catch {
      // The process environment remains the source of truth when .env is absent.
    }
  }
  return "";
}

function envValue(name) {
  const processValue = String(process.env[name] ?? "").trim();
  return processValue || readDotEnvValue(name);
}

function hasOwnText(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function compactInterviewState(state) {
  if (!state || typeof state !== "object") return state ?? null;
  const currentQuestionId = state.currentQuestionId ?? null;
  const fieldEntries = Object.entries(state.fieldStates ?? {});
  const fieldStates = Object.fromEntries(fieldEntries.map(([fieldId, field]) => {
    return [fieldId, {
      fieldId: field?.fieldId ?? fieldId,
      status: field?.status ?? null,
      answerState: field?.answerState ?? null,
      answerStatus: field?.answerStatus ?? null,
      probeStatus: field?.probeStatus ?? null,
      probeCount: field?.probeCount ?? 0,
      missingRequiredItemIds: field?.missingRequiredItemIds ?? [],
      missingInformation: field?.missingInformation ?? [],
      hasCandidateAnswer: Boolean(field?.candidateAnswer),
      hasConfirmedAnswer: Boolean(field?.recordAnswer ?? field?.answer),
      answer: field?.recordAnswer ?? field?.answer ?? field?.candidateAnswer ?? null,
    }];
  }));
  return {
    id: state.id ?? null,
    status: state.status ?? null,
    stateVersion: state.stateVersion ?? null,
    currentQuestionId,
    openIssues: state.openIssues ?? [],
    fieldStates,
  };
}

function typeSafeEndpoint(baseUrl) {
  const normalized = String(baseUrl || TYPESAFE_DEFAULT_BASE_URL).replace(/\/+$/, "");
  if (/\/v1\/systemone$/i.test(normalized)) return normalized;
  if (/\/v1$/i.test(normalized)) return normalized + "/systemone";
  return normalized + "/v1/systemone";
}

function buildTypeSafeRequest(input, model) {
  return {
    model,
    state: input,
    questions: {
      session_action: {
        type: "choice",
        instructions: (
          "Choose the next test-session action from the allowed values. "
          + "Optimize for finding untested, state-sensitive KIKIORI failures, "
          + "not for making the test pass. Consider testedBehaviors, the current "
          + "Interview State, and remainingTurns/remainingJevCalls."
        ),
        criteria: TYPESAFE_ACTION_CRITERIA,
      },
      next_user_behavior: {
        type: "choice",
        instructions: (
          "Choose the most useful fixed user behavior for STRESS_NEXT or "
          + "RETRY_CURRENT. Prefer a behavior not in testedBehaviors and use "
          + "the current state boundary to decide. This is a label only; do not "
          + "write transcript text."
        ),
        criteria: TYPESAFE_BEHAVIOR_CRITERIA,
      },
    },
  };
}

function normalizeDecision(payload) {
  const source = payload?.decision && typeof payload.decision === "object"
    ? payload.decision
    : payload;
  const sessionAction = String(source?.session_action ?? "").trim().toUpperCase();
  if (!JEV_SESSION_ACTIONS.includes(sessionAction)) {
    throw new Error(
      "Jev response must contain session_action in " + JEV_SESSION_ACTIONS.join(", "),
    );
  }

  let nextUserBehavior = source?.next_user_behavior == null
    ? null
    : String(source.next_user_behavior).trim().toUpperCase();
  if (sessionAction === "STRESS_NEXT" || sessionAction === "RETRY_CURRENT") {
    if (!JEV_USER_BEHAVIORS.includes(nextUserBehavior)) {
      throw new Error(
        sessionAction + " requires next_user_behavior in " + JEV_USER_BEHAVIORS.join(", "),
      );
    }
  } else {
    nextUserBehavior = null;
  }

  const confidence = source?.confidence == null ? null : Number(source.confidence);
  if (confidence !== null && (!Number.isFinite(confidence) || confidence < 0 || confidence > 1)) {
    throw new Error("Jev response confidence must be between 0 and 1");
  }
  return {
    sessionAction,
    nextUserBehavior,
    confidence,
  };
}

function normalizeTypeSafeDecision(payload) {
  const actionAnswer = payload?.answers?.session_action;
  const behaviorAnswer = payload?.answers?.next_user_behavior;
  if (!actionAnswer || typeof actionAnswer.choice !== "string") {
    throw new Error("TypeSafe response must contain answers.session_action.choice");
  }
  const decision = normalizeDecision({
    session_action: actionAnswer.choice,
    next_user_behavior: behaviorAnswer?.choice ?? null,
    confidence: actionAnswer.confidence,
  });
  return {
    ...decision,
    usage: payload?.usage ?? null,
    model: payload?.model ?? null,
  };
}

function decisionResult(decision, { cacheHit, callCount, provider, model }) {
  return {
    called: true,
    sessionAction: decision.sessionAction,
    nextUserBehavior: decision.nextUserBehavior,
    confidence: decision.confidence,
    cacheHit,
    callCount,
    provider: provider ?? null,
    model: model ?? decision.model ?? null,
    usage: decision.usage ?? null,
  };
}

export function clearJevSupervisorCache() {
  JEV_CACHE.clear();
  JEV_IN_FLIGHT.clear();
}

export class JevSupervisor {
  constructor({
    config,
    guard,
    sessionId,
    fetchImpl = fetch,
    apiUrl,
    apiKey,
    typeSafeApiKey,
    typeSafeBaseUrl,
    typeSafeModel,
    timeoutMs = Number(process.env.JEV_API_TIMEOUT_MS || 15_000),
  }) {
    const explicitLegacyEndpoint = apiUrl !== undefined && apiUrl !== null;
    this.config = config;
    this.guard = guard;
    this.sessionId = sessionId;
    this.fetchImpl = fetchImpl;
    this.apiUrl = apiUrl ?? envValue("JEV_API_URL");
    this.apiKey = apiKey ?? envValue("JEV_API_KEY");
    this.typeSafeApiKey = typeSafeApiKey ?? envValue("TYPESAFE_API_KEY");
    this.typeSafeBaseUrl = hasOwnText(typeSafeBaseUrl)
      ? typeSafeBaseUrl
      : envValue("TYPESAFE_BASE_URL") || TYPESAFE_DEFAULT_BASE_URL;
    this.typeSafeModel = hasOwnText(typeSafeModel)
      ? typeSafeModel
      : envValue("TYPESAFE_DEFAULT_MODEL") || TYPESAFE_DEFAULT_MODEL;
    this.provider = explicitLegacyEndpoint
      ? "jev_http"
      : hasOwnText(this.typeSafeApiKey)
        ? "typesafe"
        : hasOwnText(this.apiUrl)
          ? "jev_http"
          : null;
    this.timeoutMs = Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : 15_000;
  }

  buildInput({
    turn,
    currentQuestionId,
    currentQuestion,
    lastTranscript,
    answerState,
    candidateAnswerExists,
    recordAnswerExists,
    stateVersionAdvanced,
    questionAdvanced,
    previousUserBehavior,
    testedBehaviors,
    remainingTurns,
    remainingJevCalls,
    errors = [],
    interviewState,
    telemetry,
    expectations = null,
    observed = null,
    expectedConditions = [],
    expectationResult = null,
    expectationViolations = [],
  }) {
    return {
      task: "kikiori_voice_test_supervisor",
      session_id: this.sessionId,
      seed: this.config.seed,
      turn,
      currentQuestionId,
      currentQuestion,
      lastTranscript,
      answerState,
      candidateAnswerExists: Boolean(candidateAnswerExists),
      recordAnswerExists: Boolean(recordAnswerExists),
      stateVersionAdvanced: Boolean(stateVersionAdvanced),
      questionAdvanced: Boolean(questionAdvanced),
      previousUserBehavior: previousUserBehavior ?? null,
      testedBehaviors: [...new Set(testedBehaviors ?? [])],
      remainingTurns,
      remainingJevCalls,
      errors: [...errors],
      expectations: expectations ?? null,
      observed: observed ?? null,
      expectedConditions: [...expectedConditions],
      expectationResult: expectationResult ?? null,
      expectationViolations: [...expectationViolations],
      interviewState: compactInterviewState(interviewState),
      telemetry: telemetry ?? null,
      criteria: [...JEV_SUPERVISOR_CRITERIA],
      output_schema: {
        session_action: JEV_SESSION_ACTIONS,
        next_user_behavior: JEV_USER_BEHAVIORS,
        confidence: "number between 0 and 1 or null",
      },
    };
  }

  async decideAfterTurn({ hardGate, input }) {
    if (!hardGate?.shouldCallJev || hardGate.status !== "PASS") {
      return {
        called: false,
        sessionAction: null,
        nextUserBehavior: null,
        confidence: null,
        cacheHit: false,
        callCount: this.guard.snapshot().jevCalls,
        provider: this.provider,
        skippedReason: "hard_gate_" + String(hardGate?.status || "unknown").toLowerCase(),
      };
    }
    if (this.config.agentMode !== "jev") {
      return {
        called: false,
        sessionAction: null,
        nextUserBehavior: null,
        confidence: null,
        cacheHit: false,
        callCount: this.guard.snapshot().jevCalls,
        provider: this.provider,
        skippedReason: "agent_mode_not_jev",
      };
    }

    const current = this.guard.snapshot();
    if (current.jevCalls >= this.config.maxJevCalls) {
      return {
        called: false,
        sessionAction: null,
        nextUserBehavior: null,
        confidence: null,
        cacheHit: false,
        callCount: current.jevCalls,
        provider: this.provider,
        skippedReason: "max_jev_calls",
      };
    }
    if (!this.provider) {
      throw new Error(
        "TEST_AGENT_MODE=jev requires TYPESAFE_API_KEY "
        + "or the legacy JEV_API_URL configuration",
      );
    }

    const cacheInput = Object.fromEntries(
      Object.entries(input ?? {}).filter(([key]) => (
        key !== "remainingTurns"
        && key !== "remainingJevCalls"
        && key !== "telemetry"
      )),
    );
    const cacheKey = cacheKeyFor({
      provider: this.provider,
      model: this.provider === "typesafe" ? this.typeSafeModel : null,
      ...cacheInput,
    });
    const cached = JEV_CACHE.get(cacheKey);
    if (cached) {
      return decisionResult(cached, {
        cacheHit: true,
        callCount: current.jevCalls,
        provider: this.provider,
        model: this.provider === "typesafe" ? this.typeSafeModel : null,
      });
    }
    const inFlight = JEV_IN_FLIGHT.get(cacheKey);
    if (inFlight) {
      const decision = await inFlight;
      return decisionResult(decision, {
        cacheHit: true,
        callCount: this.guard.snapshot().jevCalls,
        provider: this.provider,
        model: this.provider === "typesafe" ? this.typeSafeModel : null,
      });
    }

    this.guard.recordJevCall();
    const headers = {
      "content-type": "application/json",
      "x-voice-test-cache-key": cacheKey,
      "x-voice-test-provider": this.provider,
    };
    const authorization = this.provider === "typesafe"
      ? this.typeSafeApiKey
      : this.apiKey;
    if (hasOwnText(authorization)) headers.authorization = "Bearer " + authorization;
    const requestUrl = this.provider === "typesafe"
      ? typeSafeEndpoint(this.typeSafeBaseUrl)
      : this.apiUrl;
    const requestBody = this.provider === "typesafe"
      ? buildTypeSafeRequest(input, this.typeSafeModel)
      : input;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    const request = (async () => {
      let response;
      try {
        response = await this.fetchImpl(requestUrl, {
          method: "POST",
          headers,
          body: JSON.stringify(requestBody),
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timeout);
      }
      if (!response?.ok) {
        throw new Error("Jev request failed: " + (response?.status ?? "unknown"));
      }
      const payload = await response.json();
      return this.provider === "typesafe"
        ? normalizeTypeSafeDecision(payload)
        : normalizeDecision(payload);
    })();
    JEV_IN_FLIGHT.set(cacheKey, request);
    try {
      const decision = await request;
      JEV_CACHE.set(cacheKey, decision);
      return decisionResult(decision, {
        cacheHit: false,
        callCount: this.guard.snapshot().jevCalls,
        provider: this.provider,
        model: this.provider === "typesafe" ? this.typeSafeModel : null,
      });
    } finally {
      if (JEV_IN_FLIGHT.get(cacheKey) === request) JEV_IN_FLIGHT.delete(cacheKey);
    }
  }
}
