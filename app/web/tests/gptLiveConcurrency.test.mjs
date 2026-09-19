import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import vm from "node:vm";
import { randomUUID } from "node:crypto";

const require = createRequire(new URL("../package.json", import.meta.url));
const ts = require("typescript");
const source = await readFile(new URL(
  "../src/features/realtime-voice/hooks/useGPTLiveVoiceConversation.ts", import.meta.url,
), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const flush = () => new Promise((resolve) => setImmediate(resolve));
const captureSource = await readFile(new URL(
  "../src/features/realtime-voice/utils/liveTranscriptCapture.ts", import.meta.url,
), "utf8");
const captureCode = ts.transpileModule(captureSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

function setup(refresh = () => {}) {
  const sent = [];
  const requests = [];
  const states = [];
  let receive;
  const timers = new Map();
  let nextTimer = 0;
  const captureExports = {};
  vm.runInNewContext(captureCode, {
    exports: captureExports, console,
    setTimeout: (callback) => { const id = ++nextTimer; timers.set(id, callback); return id; },
    clearTimeout: (id) => timers.delete(id),
  });
  const exports = {};
  vm.runInNewContext(code, {
    exports, AbortController, DOMException, performance, crypto: { randomUUID },
    window: { setTimeout, clearTimeout },
    console: { info() {}, warn() {} },
    require(name) {
      if (name === "react") return {
        useCallback: (fn) => fn,
        useEffect() {},
        useRef: (current) => ({ current }),
        useState(initial) {
          const state = { value: initial };
          states.push(state);
          return [initial, (value) => {
            state.value = typeof value === "function" ? value(state.value) : value;
          }];
        },
      };
      if (name.endsWith("i18n")) return { useI18n: () => ({ t: (key) => key }) };
      if (name.endsWith("voiceErrors")) return { toStartErrorMessage: () => "error" };
      if (name.endsWith("liveTranscriptCapture")) return captureExports;
      if (name.endsWith("realtimeVoiceClient")) return {
        submitGPTLiveCapture: (...args) => new Promise((resolve, reject) => {
          requests.push({ args, resolve, reject });
        }),
      };
      if (name.endsWith("gptLivePeerConnection")) return {
        async createGPTLivePeerConnection(options) {
          receive = options.onEvent;
          receive({ type: "session.started", session: { id: "live_test" } });
          return { sessionId: "live_test", stop() {}, sendEvent(event) { sent.push(event); return true; } };
        },
      };
      throw new Error(name);
    },
  });
  const hook = exports.useGPTLiveVoiceConversation({
    enabled: true, recordId: "record", remoteAudioRef: { current: null },
    onInterviewStateChanged: refresh,
  });
  return { hook, sent, requests, states, receive: (event) => receive(event),
    tick: () => { const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach((fn) => fn()); },
  };
}

test("without delegation, Live transcripts continue while background LLM and UI refresh are pending", async () => {
  const app = setup(() => new Promise(() => {}));
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "田中です。" });
  app.tick();
  await flush();
  app.receive({ type: "session.output_transcript.delta", delta: "所属を教えてください。" });
  assert.ok(app.states.some(({ value }) => Array.isArray(value)
    && value.some((line) => line.text === "所属を教えてください。")));
  app.receive({ type: "session.input_transcript.delta", delta: "開発部です。" });
  app.tick();
  assert.equal(app.requests.length, 1);
  app.requests[0].resolve({ status: "updated", stateVersion: 1, checklist: [
    { label: "プロフィール", answer_state: "CANDIDATE_PENDING", missing_required_items: ["役職"] },
  ] });
  await flush();
  assert.ok(app.sent.some((event) => event.type === "session.thinking.append" && event.content.includes("役職")));
  app.tick();
  assert.equal(app.requests.length, 2, "UI refresh must not hold the save queue");
  assert.equal(app.requests[1].args[2], 2);
  assert.equal(app.requests[1].args[3][0].role, "assistant");
  assert.equal(app.requests[1].args[3][1].text, "開発部です。");
  app.hook.stop();
  app.requests[1].resolve({ status: "updated", checklist: [] });
  await flush();
});

test("a disconnected session cannot send late results into the next session", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "回答" });
  app.receive({ type: "session.delegation.created", delegation_id: "old" });
  await flush();
  app.hook.stop();
  await app.hook.start();
  const before = app.sent.length;
  app.requests[0].resolve({ status: "updated", checklist: [{ label: "古い結果", answer_state: "CONFIRMED", missing_required_items: [] }] });
  await flush();
  assert.equal(app.sent.length, before);
  app.hook.stop();
});

test("failed saving automatically retries the identical batch before trailing fragments", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "田中です。" });
  app.receive({ type: "session.delegation.created", delegation_id: "failed" });
  await flush();
  app.requests[0].reject(new Error("unavailable"));
  await flush();
  assert.ok(app.states.some(({ value }) => typeof value === "string" && value.includes("再試行")));
  app.receive({ type: "session.input_transcript.delta", delta: "開発部です。" });
  app.tick();
  await flush();
  assert.deepEqual(app.requests[1].args, app.requests[0].args);
  app.hook.stop();
  app.requests[1].resolve({ status: "updated", checklist: [] });
  await flush();
  app.tick();
  assert.equal(app.requests[2].args[3][0].text, "開発部です。");
  app.requests[2].resolve({ status: "updated", checklist: [] });
  await flush();
});

test("delegation before transcript does not lose late fragments; stop flushes final speech", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.delegation.created", delegation_id: "early" });
  assert.equal(app.requests.length, 0);
  app.receive({ type: "session.input_transcript.delta", delta: "最後の回答です。", start_ms: 10, end_ms: 100 });
  app.hook.stop();
  assert.equal(app.requests.length, 1);
  assert.equal(app.requests[0].args[3][0].text, "最後の回答です。");
  assert.equal(app.requests[0].args[3][0].end_ms, 100);
  app.requests[0].resolve({ status: "updated", checklist: [] });
  await flush();
});

test("long continuous speech is drained in bounded, ordered batches without loss", async () => {
  const app = setup();
  await app.hook.start();
  for (let index = 0; index < 150; index += 1) {
    app.receive({ type: "session.input_transcript.delta", delta: String(index) });
  }
  app.tick();
  assert.equal(app.requests[0].args[3].length, 128);
  app.hook.stop();
  app.requests[0].resolve({ status: "updated", checklist: [] });
  await flush();
  app.tick();
  assert.equal(app.requests[1].args[3].length, 22);
  assert.equal(app.requests[1].args[3][0].text, "128");
  assert.equal(app.requests[1].args[3][21].text, "149");
  app.requests[1].resolve({ status: "updated", checklist: [] });
  await flush();
});
