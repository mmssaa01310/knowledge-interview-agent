import fs from "node:fs";
import path from "node:path";

const DEFAULT_OUTPUT_DIR = process.env.VOICE_TEST_OUTPUT_DIR
  || path.resolve(process.cwd(), "test-results", "voice-e2e");
const RUN_COUNTERS = new Map();

function booleanEnv(value, fallback = false) {
  if (value === undefined || value === null || value === "") return fallback;
  return ["1", "true", "yes", "on"].includes(String(value).trim().toLowerCase());
}

function integerEnv(value, fallback) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
}

export function readVoiceTestConfig(env = process.env) {
  const requestedProvider = env.VOICE_TEST_REAL_PROVIDER || "gpt_live";
  const agentMode = String(env.TEST_AGENT_MODE || "").trim().toLowerCase() === "jev"
    ? "jev"
    : "deterministic";
  const jevMode = agentMode === "jev";
  const configuredJevCalls = integerEnv(env.VOICE_TEST_MAX_JEV_CALLS, jevMode ? 80 : 20);
  const maxJevCallsTotal = integerEnv(
    env.VOICE_TEST_MAX_JEV_CALLS_TOTAL,
    configuredJevCalls,
  );
  const config = {
    realApiEnabled: booleanEnv(env.VOICE_TEST_REAL_API_ENABLED, false),
    realProvider: ["gpt_live", "transcribe_polly"].includes(requestedProvider)
      ? requestedProvider
      : "gpt_live",
    maxSessions: integerEnv(env.VOICE_TEST_MAX_SESSIONS, jevMode ? 10 : 3),
    maxTurnsPerSession: integerEnv(env.VOICE_TEST_MAX_TURNS_PER_SESSION, 8),
    maxAudioSecondsPerSession: integerEnv(env.VOICE_TEST_MAX_AUDIO_SECONDS_PER_SESSION, 120),
    maxJevCalls: maxJevCallsTotal,
    maxJevCallsTotal,
    maxLlmJudgeCalls: integerEnv(env.VOICE_TEST_MAX_LLM_JUDGE_CALLS, 5),
    maxRetries: integerEnv(env.VOICE_TEST_MAX_RETRIES, jevMode ? 0 : 1),
    seed: integerEnv(env.VOICE_TEST_SEED, 38291),
    agentMode,
    scenario: env.VOICE_TEST_SCENARIO || "NORMAL",
    baseURL: env.VOICE_TEST_BASE_URL || "http://127.0.0.1:5173",
    apiBaseURL: env.VOICE_TEST_API_BASE_URL || "http://127.0.0.1:8001",
    outputDir: path.resolve(env.VOICE_TEST_OUTPUT_DIR || DEFAULT_OUTPUT_DIR),
    audioFixturePath: env.VOICE_TEST_AUDIO_FIXTURE
      ? path.resolve(env.VOICE_TEST_AUDIO_FIXTURE)
      : null,
    startLocalStack: booleanEnv(env.VOICE_TEST_START_LOCAL_STACK, false),
    runBrowserAudio: booleanEnv(env.VOICE_TEST_RUN_BROWSER_AUDIO, true),
  };
  fs.mkdirSync(config.outputDir, { recursive: true });
  return Object.freeze(config);
}

export class VoiceCostGuard {
  constructor(config = readVoiceTestConfig()) {
    this.config = config;
    const counterKey = config.outputDir;
    if (!RUN_COUNTERS.has(counterKey)) {
      RUN_COUNTERS.set(counterKey, {
        sessions: 0,
        turns: 0,
        audioSeconds: 0,
        jevCalls: 0,
        llmJudgeCalls: 0,
        retries: 0,
      });
    }
    this.counters = RUN_COUNTERS.get(counterKey);
    this.currentSession = { turns: 0, audioSeconds: 0 };
    this.sessionActive = false;
  }

  requireRealApiOptIn() {
    if (!this.config.realApiEnabled) {
      throw new Error("Real voice API tests are disabled. Set VOICE_TEST_REAL_API_ENABLED=true explicitly.");
    }
  }

  startSession() {
    this.counters.sessions += 1;
    this.#assertWithin("sessions", this.config.maxSessions);
    this.currentSession = { turns: 0, audioSeconds: 0 };
    this.sessionActive = true;
    return this.snapshot();
  }

  startTurn(audioSeconds = 0) {
    if (!this.sessionActive) {
      throw new Error("Voice test cost guard requires startSession() before startTurn()");
    }
    this.counters.turns += 1;
    const seconds = Math.max(0, Number(audioSeconds) || 0);
    this.counters.audioSeconds += seconds;
    this.currentSession.turns += 1;
    this.currentSession.audioSeconds += seconds;
    if (this.currentSession.turns > this.config.maxTurnsPerSession) {
      throw new Error("Voice test cost guard exceeded: turns="
        + this.currentSession.turns + " limit=" + this.config.maxTurnsPerSession
        + " for the current session");
    }
    if (this.currentSession.audioSeconds > this.config.maxAudioSecondsPerSession) {
      throw new Error("Voice test cost guard exceeded: audioSeconds="
        + this.currentSession.audioSeconds + " limit="
        + this.config.maxAudioSecondsPerSession + " for the current session");
    }
    return this.snapshot();
  }

  recordJevCall() {
    this.counters.jevCalls += 1;
    this.#assertWithin(
      "jevCalls",
      this.config.maxJevCallsTotal ?? this.config.maxJevCalls,
    );
  }

  recordLlmJudgeCall() {
    this.counters.llmJudgeCalls += 1;
    this.#assertWithin("llmJudgeCalls", this.config.maxLlmJudgeCalls);
  }

  recordRetry() {
    this.counters.retries += 1;
    this.#assertWithin("retries", this.config.maxRetries);
  }

  snapshot() {
    return {
      ...this.counters,
      currentSession: { ...this.currentSession },
      sessionActive: this.sessionActive,
      limits: {
        maxSessions: this.config.maxSessions,
        maxTurnsPerSession: this.config.maxTurnsPerSession,
        maxAudioSecondsPerSession: this.config.maxAudioSecondsPerSession,
        maxJevCalls: this.config.maxJevCalls,
        maxJevCallsTotal: this.config.maxJevCallsTotal ?? this.config.maxJevCalls,
        maxLlmJudgeCalls: this.config.maxLlmJudgeCalls,
        maxRetries: this.config.maxRetries,
      },
    };
  }

  #assertWithin(counter, limit) {
    if (this.counters[counter] > limit) {
      throw new Error("Voice test cost guard exceeded: " + counter + "="
        + this.counters[counter] + " limit=" + limit);
    }
  }
}
