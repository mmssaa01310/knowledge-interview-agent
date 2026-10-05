import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import vm from "node:vm";

const require = createRequire(new URL("../package.json", import.meta.url));
const ts = require("typescript");
const source = await readFile(new URL(
  "../src/features/realtime-voice/runtime/startVoiceSession.ts", import.meta.url,
), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

test("voice session creation allows time for the API to generate its first interview question", async () => {
  let createSessionSignal;
  let timeout;
  const stream = { getTracks: () => [{ stop() {} }], getAudioTracks: () => [{ readyState: "live" }] };
  const exports = {};
  vm.runInNewContext(code, {
    exports,
    AbortController,
    ApiError: class ApiError extends Error {},
    navigator: { mediaDevices: { getUserMedia: async () => stream } },
    performance,
    window: {
      setTimeout(callback, delay) {
        timeout = { callback, delay };
        return 1;
      },
      clearTimeout() {},
    },
    console: { info() {}, warn() {} },
    require(name) {
      if (name.endsWith("lib/api")) return { ApiError: class ApiError extends Error {} };
      if (name.endsWith("voiceErrors")) return { toStartErrorMessage: () => "start failed" };
      if (name.endsWith("voiceTelemetry")) return {
        VOICE_SIGNALING_TIMEOUT_MS: 8000,
        VOICE_SESSION_START_TIMEOUT_MS: 30000,
        createVoiceStartupTrace: () => ({}),
        logVoiceStartupEvent() {},
      };
      if (name.endsWith("realtimeVoiceClient")) return {
        createVoiceSession(_recordId, _provider, signal) {
          createSessionSignal = signal;
          return new Promise((_resolve, reject) => {
            signal.addEventListener("abort", () => reject(new Error("request aborted")), { once: true });
          });
        },
      };
      if (name.endsWith("openaiRealtimePeerConnection")) return {};
      if (name.endsWith("voicePeerConnection")) return {};
      throw new Error(`Unexpected import: ${name}`);
    },
  });

  const noOp = () => {};
  const sessionRef = { current: null };
  const startingRef = { current: false };
  const pendingStart = exports.startVoiceSession({
    recordId: "record",
    provider: "transcribe_polly",
    hasQuestions: true,
    remoteAudioRef: { current: null },
    voiceSessionRef: sessionRef,
    peerRef: { current: null },
    microphoneStreamRef: { current: null },
    startingRef,
    resetConversationTracking: noOp,
    setStatus: noOp,
    setMessage: noOp,
    setInitialReplyActive: noOp,
    setRequiresManualPlayback: noOp,
    setConnectionState: noOp,
    setStats: noOp,
    onMessageRef: { current: noOp },
    handleEvent: noOp,
    handleOpenAIEvent: noOp,
    hasPendingInitialReply: () => false,
    t: (key) => key,
  });

  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(timeout.delay, 30000);
  assert.equal(createSessionSignal.aborted, false);

  timeout.callback();
  await pendingStart;
  assert.equal(createSessionSignal.aborted, true);
  assert.equal(startingRef.current, false);
});
