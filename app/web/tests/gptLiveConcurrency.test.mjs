import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import vm from "node:vm";

const require = createRequire(new URL("../package.json", import.meta.url));
const ts = require("typescript");
const source = await readFile(new URL(
  "../src/features/realtime-voice/hooks/useGPTLiveVoiceConversation.ts", import.meta.url,
), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const flush = () => new Promise((resolve) => setImmediate(resolve));

function setup(refresh = () => {}) {
  const sent = [];
  const requests = [];
  const states = [];
  let receive;
  const exports = {};
  vm.runInNewContext(code, {
    exports, AbortController, DOMException, performance,
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
      if (name.endsWith("realtimeVoiceClient")) return {
        submitGPTLiveDelegation: (...args) => new Promise((resolve, reject) => {
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
  return { hook, sent, requests, states, receive: (event) => receive(event) };
}

test("Live transcripts continue while saving is pending; fragments are reserved once", async () => {
  const app = setup(() => new Promise(() => {}));
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "田中です。" });
  app.receive({ type: "session.delegation.created", delegation_id: "d1" });
  await flush();
  app.receive({ type: "session.output_transcript.delta", delta: "所属を教えてください。" });
  assert.ok(app.states.some(({ value }) => Array.isArray(value)
    && value.some((line) => line.text === "所属を教えてください。")));
  app.receive({ type: "session.input_transcript.delta", delta: "開発部です。" });
  app.receive({ type: "session.delegation.created", delegation_id: "d2" });
  assert.equal(app.requests.length, 1);
  app.requests[0].resolve({ status: "updated", stateVersion: 1 });
  await flush();
  assert.ok(app.sent.some((event) => event.delegation_id === "d1"));
  assert.equal(app.requests.length, 2, "UI refresh must not hold the save queue");
  assert.equal(app.requests[1].args[2], "開発部です。");
  app.hook.stop();
  app.requests[1].resolve({ status: "updated" });
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
  app.requests[0].resolve({ status: "completed" });
  await flush();
  assert.ok(!app.sent.some((event) => event.delegation_id === "old"));
  app.hook.stop();
});

test("failed saving retains the answer for a later model delegation", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "田中です。" });
  app.receive({ type: "session.delegation.created", delegation_id: "failed" });
  await flush();
  app.requests[0].reject(new Error("unavailable"));
  await flush();
  assert.ok(app.sent.some((event) => event.delegation_id === "failed"
    && event.content.includes("saving failed")));
  app.receive({ type: "session.input_transcript.delta", delta: "開発部です。" });
  app.receive({ type: "session.delegation.created", delegation_id: "retry" });
  await flush();
  assert.equal(app.requests[1].args[2], "田中です。\n開発部です。");
  app.hook.stop();
  app.requests[1].resolve({ status: "updated" });
  await flush();
});
