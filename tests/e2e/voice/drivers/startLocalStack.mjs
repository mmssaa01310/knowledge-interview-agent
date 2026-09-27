import { spawn } from "node:child_process";
import {
  DEFAULT_VOICE_TEST_API_BASE_URL,
  DEFAULT_VOICE_TEST_BASE_URL,
} from "../config.mjs";

const root = process.cwd();
const apiBaseURL = String(
  process.env.VOICE_TEST_API_BASE_URL || DEFAULT_VOICE_TEST_API_BASE_URL,
).replace(/\/+$/, "");
const webBaseURL = String(
  process.env.VOICE_TEST_BASE_URL || DEFAULT_VOICE_TEST_BASE_URL,
).replace(/\/+$/, "");
const voiceBaseURL = String(
  process.env.VOICE_TEST_VOICE_API_BASE_URL || "http://127.0.0.1:18010",
).replace(/\/+$/, "");

function portFrom(url, fallback) {
  try {
    const parsed = new URL(url);
    return parsed.port || String(fallback);
  } catch {
    return String(fallback);
  }
}

const apiPort = portFrom(apiBaseURL, 18001);
const webPort = portFrom(webBaseURL, 15173);
const voicePort = portFrom(voiceBaseURL, 18010);
const realApiEnabled = ["1", "true", "yes", "on"].includes(
  String(process.env.VOICE_TEST_REAL_API_ENABLED || "").toLowerCase(),
);
const requestedProvider = process.env.VOICE_TEST_REAL_PROVIDER || "gpt_live";
const realProvider = realApiEnabled && ["gpt_live", "transcribe_polly"].includes(requestedProvider)
  ? requestedProvider
  : "gpt_live";
const environment = {
  ...process.env,
  APP_ENV: "test",
  DATABASE_URL: "memory://test",
  DEV_AUTO_SEED_VOICE_DEMO: "true",
  DOCUMENT_KNOWLEDGE_BACKEND: "postgres",
  BEDROCK_ENABLED: "false",
  OPENAI_SECRET_KEY: process.env.OPENAI_SECRET_KEY || "voice-test-disabled",
  VITE_API_PROXY_TARGET: apiBaseURL,
  VITE_VOICE_PROXY_TARGET: voiceBaseURL,
  VITE_VOICE_RUNTIME_PROVIDER: realProvider,
  VITE_DEV_TOKEN: process.env.VOICE_TEST_DEV_TOKEN || "dev-manager",
};

const children = [
  spawn(
    "uv",
    [
      "run",
      "--project",
      "app/api",
      "uvicorn",
      "ai_interviewer_api.main:app",
      "--host",
      "127.0.0.1",
      "--port",
      apiPort,
    ],
    {
      cwd: root,
      env: environment,
      stdio: process.env.VOICE_TEST_VERBOSE ? "inherit" : "ignore",
    },
  ),
  spawn("pnpm", ["--dir", "app/web", "dev", "--host", "127.0.0.1", "--port", webPort], {
    cwd: root,
    env: environment,
    stdio: process.env.VOICE_TEST_VERBOSE ? "inherit" : "ignore",
  }),
];

if (realProvider === "transcribe_polly") {
  children.push(spawn(
    "uv",
    [
      "run",
      "--project",
      "app/voice",
      "uvicorn",
      "ai_interviewer_voice.main:app",
      "--host",
      "127.0.0.1",
      "--port",
      voicePort,
    ],
    {
      cwd: root,
      env: {
        ...environment,
        VOICE_API_BASE_URL: apiBaseURL,
        VOICE_RUNTIME_PROVIDER: realProvider,
      },
      stdio: process.env.VOICE_TEST_VERBOSE ? "inherit" : "ignore",
    },
  ));
}

async function waitFor(url, timeoutMs = 90_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      // The child process may still be importing Python or starting Vite.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("Local voice E2E stack did not start: " + url);
}

function stop() {
  for (const child of children) child.kill("SIGTERM");
}

process.on("SIGINT", stop);
process.on("SIGTERM", stop);
process.on("exit", stop);

try {
  await waitFor(apiBaseURL + "/api/health");
  await waitFor(webBaseURL + "/");
  if (realProvider === "transcribe_polly") {
    await waitFor(voiceBaseURL + "/voice/health");
  }
  await new Promise(() => {});
} finally {
  stop();
}
