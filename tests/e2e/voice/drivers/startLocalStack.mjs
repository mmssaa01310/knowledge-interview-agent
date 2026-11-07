import os from "node:os";
import path from "node:path";
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
const environment = {
  ...process.env,
  APP_ENV: "test",
  UV_CACHE_DIR: process.env.UV_CACHE_DIR || path.join(os.tmpdir(), "kikiori-uv-cache"),
  DATABASE_URL: "memory://test",
  DEV_AUTO_SEED_VOICE_DEMO: "true",
  DOCUMENT_KNOWLEDGE_BACKEND: "postgres",
  VITE_API_PROXY_TARGET: apiBaseURL,
  VITE_DEV_TOKEN: process.env.VOICE_TEST_DEV_TOKEN || "dev-manager",
};
const stdio = process.env.VOICE_TEST_VERBOSE ? "inherit" : "ignore";
const children = [];

function startService(name, command, args) {
  const child = spawn(command, args, { cwd: root, env: environment, stdio });
  child.voiceTestServiceName = name;
  child.once("error", (error) => {
    child.voiceTestSpawnError = error instanceof Error ? error.message : String(error);
  });
  children.push(child);
  return child;
}

startService("api", "uv", [
  "run", "--project", "app/api", "uvicorn", "ai_interviewer_api.main:app",
  "--host", "127.0.0.1", "--port", apiPort,
]);
startService("web", "pnpm", [
  "--dir", "app/web", "dev", "--host", "127.0.0.1", "--port", webPort,
]);

async function waitFor(url, timeoutMs = 90_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const exitedChild = children.find((child) => (
      child.voiceTestSpawnError || child.exitCode !== null || child.signalCode !== null
    ));
    if (exitedChild) {
      const details = exitedChild.voiceTestSpawnError
        || `exit=${exitedChild.exitCode} signal=${exitedChild.signalCode}`;
      throw new Error(`Local voice E2E ${exitedChild.voiceTestServiceName} service exited before readiness: ${details}`);
    }
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      // The child process may still be importing Python or starting Vite.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Local voice E2E stack did not start: ${url}`);
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
  await new Promise(() => {});
} finally {
  stop();
}
