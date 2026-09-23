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

function decisionResult(decision, { cacheHit, callCount }) {
  return {
    called: true,
    sessionAction: decision.sessionAction,
    nextUserBehavior: decision.nextUserBehavior,
    confidence: decision.confidence,
    cacheHit,
    callCount,
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
    apiUrl = envValue("JEV_API_URL"),
    apiKey = envValue("JEV_API_KEY"),
    timeoutMs = Number(process.env.JEV_API_TIMEOUT_MS || 15_000),
  }) {
    this.config = config;
    this.guard = guard;
    this.sessionId = sessionId;
    this.fetchImpl = fetchImpl;
    this.apiUrl = apiUrl;
    this.apiKey = apiKey;
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
      interviewState: interviewState ?? null,
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
        skippedReason: "max_jev_calls",
      };
    }
    if (!hasOwnText(this.apiUrl)) {
      throw new Error("TEST_AGENT_MODE=jev requires JEV_API_URL");
    }

    const cacheInput = Object.fromEntries(
      Object.entries(input ?? {}).filter(([key]) => (
        key !== "remainingTurns"
        && key !== "remainingJevCalls"
        && key !== "telemetry"
      )),
    );
    const cacheKey = cacheKeyFor(cacheInput);
    const cached = JEV_CACHE.get(cacheKey);
    if (cached) {
      return decisionResult(cached, {
        cacheHit: true,
        callCount: current.jevCalls,
      });
    }
    const inFlight = JEV_IN_FLIGHT.get(cacheKey);
    if (inFlight) {
      const decision = await inFlight;
      return decisionResult(decision, {
        cacheHit: true,
        callCount: this.guard.snapshot().jevCalls,
      });
    }

    this.guard.recordJevCall();
    const headers = {
      "content-type": "application/json",
      "x-voice-test-cache-key": cacheKey,
    };
    if (hasOwnText(this.apiKey)) headers.authorization = "Bearer " + this.apiKey;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    const request = (async () => {
      let response;
      try {
        response = await this.fetchImpl(this.apiUrl, {
          method: "POST",
          headers,
          body: JSON.stringify(input),
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timeout);
      }
      if (!response?.ok) {
        throw new Error("Jev request failed: " + (response?.status ?? "unknown"));
      }
      const payload = await response.json();
      return normalizeDecision(payload);
    })();
    JEV_IN_FLIGHT.set(cacheKey, request);
    try {
      const decision = await request;
      JEV_CACHE.set(cacheKey, decision);
      return decisionResult(decision, {
        cacheHit: false,
        callCount: this.guard.snapshot().jevCalls,
      });
    } finally {
      if (JEV_IN_FLIGHT.get(cacheKey) === request) JEV_IN_FLIGHT.delete(cacheKey);
    }
  }
}
