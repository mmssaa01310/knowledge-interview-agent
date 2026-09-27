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
const eventUtilsSource = await readFile(new URL(
  "../src/features/realtime-voice/runtime/gptLiveEventUtils.ts", import.meta.url),
  "utf8",
);
const eventUtilsCode = ts.transpileModule(eventUtilsSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

function setup(refresh = () => {}, stateUpdate = () => Promise.resolve({
  status: "applied", stateVersion: 2,
  currentQuestionId: "question-2", coveredAspects: [], missingAspects: [],
})) {
  const sent = [];
  const requests = [];
  const polls = [];
  const stateRequests = [];
  const states = [];
  let receive;
  const timers = new Map();
  let nextTimer = 0;
  let nextHookTimer = 0;
  let hookNow = 0;
  const hookTimers = new Map();
  const hookSetTimeout = (callback, delay = 0) => {
    const id = ++nextHookTimer;
    hookTimers.set(id, { callback, due: hookNow + delay });
    return id;
  };
  const hookClearTimeout = (id) => hookTimers.delete(id);
  const captureExports = {};
  const eventUtilsExports = {};
  const pageListeners = new Map();
  vm.runInNewContext(captureCode, {
    exports: captureExports, console, TextEncoder,
    window: {
      addEventListener: (type, listener) => pageListeners.set(type, listener),
      removeEventListener: (type) => pageListeners.delete(type),
    },
    setTimeout: (callback) => { const id = ++nextTimer; timers.set(id, callback); return id; },
    clearTimeout: (id) => timers.delete(id),
  });
  vm.runInNewContext(eventUtilsCode, {
    exports: eventUtilsExports,
  });
  const exports = {};
  vm.runInNewContext(code, {
    exports, AbortController, DOMException, performance, crypto: { randomUUID },
    window: { setTimeout: hookSetTimeout, clearTimeout: hookClearTimeout },
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
      if (name.endsWith("voiceTelemetry")) return { logVoiceStartupEvent() {} };
      if (name.endsWith("liveTranscriptCapture")) return captureExports;
      if (name.endsWith("gptLiveEventUtils")) return eventUtilsExports;
      if (name.endsWith("realtimeVoiceClient")) return {
        getGPTLiveCaptureStatus: () => new Promise((resolve, reject) => polls.push({ resolve, reject })),
        submitGPTLiveCapture: (...args) => new Promise((resolve, reject) => {
          requests.push({ args, resolve, reject });
        }),
        submitGPTLiveStateUpdate: (...args) => {
          stateRequests.push({ args });
          return stateUpdate(...args);
        },
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
  return { hook, sent, requests, stateRequests, polls, states, pageListeners,
    receive: (event) => receive(event),
    tick: () => { const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach((fn) => fn()); },
    advance: (milliseconds) => {
      hookNow += milliseconds;
      let progressed = true;
      while (progressed) {
        progressed = false;
        const due = [...hookTimers.entries()]
          .filter(([, timer]) => timer.due <= hookNow)
          .sort(([, left], [, right]) => left.due - right.due);
        for (const [id, timer] of due) {
          if (!hookTimers.delete(id)) continue;
          progressed = true;
          timer.callback();
        }
      }
    },
  };
}

test("Live transcripts continue while the Responses state update and UI refresh are pending", async () => {
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
  assert.equal(
    app.sent.some((event) => event.type === "session.thinking.append"),
    false,
    "GPT Live state is updated through Responses Delegation, not thinking.append",
  );
  app.tick();
  assert.equal(app.requests.length, 2, "UI refresh must not hold the save queue");
  assert.equal(app.requests[1].args[2], 2);
  assert.equal(app.requests[1].args[3][0].role, "assistant");
  assert.equal(app.requests[1].args[3][1].text, "開発部です。");
  app.hook.stop();
  app.requests[1].resolve({ status: "updated", checklist: [] });
  await flush();
});

test("restarting the same GPT Live record keeps the visible transcript history", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "前の回答です。" });
  app.receive({ type: "session.output_transcript.delta", delta: "ありがとうございます。" });
  const beforeRestart = app.states.find(({ value }) => Array.isArray(value)
    && value.some((line) => line.text === "前の回答です。"));
  assert.ok(beforeRestart);

  app.hook.stop();
  await app.hook.start();

  assert.deepEqual(
    app.states.find(({ value }) => Array.isArray(value)
      && value.some((line) => line.text === "前の回答です。"))?.value,
    beforeRestart.value,
  );
  app.hook.stop();
});

test("durable receipt releases the next batch while server organization is pending", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "最初の回答" });
  app.tick();
  app.requests[0].resolve({ status: "accepted", revision: 1 });
  await flush();
  app.receive({ type: "session.input_transcript.delta", delta: "追加回答" });
  app.tick();
  assert.equal(app.requests.length, 2);
  assert.equal(app.polls.length, 1);
  app.polls[0].resolve({ status: "updated", checklist: [], processing: false, interviewState: { status: "completed" } });
  await flush();
  assert.notEqual(app.states[0].value, "completed", "unsaved speech prevents stale completion");
  app.requests[1].resolve({ status: "accepted", revision: 2 });
  await flush();
  app.tick();
  app.polls[1].resolve({ status: "updated", checklist: [], processing: false, interviewState: { status: "completed" } });
  await flush();
  assert.equal(app.states[0].value, "completed");
});

test("manual stop shows organization until a persisted result arrives", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "最後の回答" });
  app.hook.stop();
  assert.equal(app.states[0].value, "processing");
  app.requests[0].resolve({ status: "accepted", revision: 1 });
  await flush();
  app.tick();
  app.polls[0].resolve({ status: "updated", checklist: [], processing: false, interviewState: { status: "completed" } });
  await flush();
  assert.equal(app.states[0].value, "completed");
});

test("capture status does not inject a second conversation decision into Live", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "回答" });
  app.tick();
  app.requests[0].resolve({ status: "accepted", revision: 1 });
  await flush();
  app.tick();
  app.polls[0].resolve({
    status: "updated", processing: false,
    interviewState: { status: "in_progress" },
    completion: {
      complete: false, closingRequired: true,
      missingRequiredTargets: [{ targetId: "profile" }],
      pendingConfirmationTargets: [], unknownApplicabilityTopics: [], unresolvedContradictionIds: [],
    },
    checklist: [{
      id: "profile", label: "基本プロフィール", answer_state: "CANDIDATE_PENDING",
      answer_resolution: "TENTATIVE", candidate_answer: "候補", needs_confirmation: true,
      missing_required_items: [],
    }],
  });
  await flush();
  assert.equal(app.sent.some((event) => event.type === "session.thinking.append"), false);
  assert.equal(app.sent.some((event) => event.type === "session.commentary.append"), false);
  assert.equal(app.sent.some((event) => event.content.includes("必須項目は保存済みです")), false);
});

test("response watchdog emits one wait notice and one continuation at most", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "input_audio_buffer.speech_started" });
  app.receive({ type: "session.input_transcript.delta", delta: "回答です。" });
  app.receive({ type: "input_audio_buffer.speech_stopped" });

  app.advance(2500);
  assert.equal(app.sent.filter((event) => event.type === "session.commentary.append"
    && event.event_id?.includes("wait_notice")).length, 1);
  app.advance(2500);
  app.advance(10000);
  app.advance(5000);
  app.advance(5000);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("continuation")).length, 1);
  app.hook.stop();
});

test("watchdog gives one bounded delegation grace after sending a wait notice", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "待機案内後に遅れて評価される回答です。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });

  app.advance(2500);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("wait_notice")).length, 1);

  // Two normal delegation waits are followed by one extra interval only
  // because the Live wait notice has already been sent.
  app.advance(2500);
  app.advance(5000);
  app.advance(5000);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("continuation")).length, 0);

  app.advance(5000);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("continuation")).length, 1);
  app.hook.stop();
});

test("wait notice output does not suppress a late delegation continuation", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "待機案内後に評価される回答です。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });

  app.advance(2500);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("wait_notice")).length, 1);
  app.receive({
    type: "session.output_transcript.delta",
    delta: "少々お待ちください。",
  });

  // The wait notice is not the interview response, so the watchdog must keep
  // waiting for the Responses delegation instead of treating it as complete.
  app.advance(2500);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("continuation")).length, 0);
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-after-wait", target: "responses" },
    response_id: "response-after-wait",
  });
  app.advance(5000);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("continuation")).length, 0);
  app.hook.stop();
});

test("new transcript interrupts an active Live output once before the next turn", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "最初の回答です。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({ type: "session.output_transcript.delta", delta: "次の質問です。" });

  // The provider speech boundary interrupts the stale response before the
  // new Responses handoff returns to Live. A transcript delta alone is only
  // an observation and must not add a second application boundary.
  app.receive({ type: "input_audio_buffer.speech_started" });
  app.receive({ type: "session.input_transcript.delta", delta: "次の回答です。" });
  app.receive({ type: "session.input_transcript.delta", delta: "続きです。" });

  const interruptions = app.sent.filter((event) => event.type === "session.instructions.append"
    && event.event_id?.startsWith("gpt_live_response_interrupt_"));
  assert.equal(interruptions.length, 1);
  assert.equal(interruptions[0].delegation_id, null);
  assert.match(interruptions[0].event_id, /^gpt_live_response_interrupt_/);
  app.hook.stop();
});

test("a late PARTIAL handoff resumes without a second interruption boundary", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "前の回答です。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({ type: "session.output_transcript.delta", delta: "前の質問です。" });
  app.receive({ type: "response.done" });

  // The previous turn has ended, but its activeTurn record is still present.
  // The next input must detach that record before a late Responses delegation
  // can produce Live output for the new transcript revision.
  app.receive({ type: "session.input_transcript.delta", delta: "宮崎です。" });
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-partial-new-turn", target: "responses" },
    response_id: "response-partial-new-turn",
  });
  app.receive({ type: "session.output_transcript.delta", delta: "はい、承知しました。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });

  const liveInterrupts = app.sent.filter((event) => event.type === "session.instructions.append"
    && event.event_id?.startsWith("gpt_live_response_interrupt_"));
  assert.equal(
    liveInterrupts.length,
    0,
    "a completed prior Live response does not need a new interruption boundary",
  );

  app.receive({
    type: "response.event",
    delegation_id: "delegation-partial-new-turn",
    event: {
      type: "response.output_item.done",
      item: {
        type: "function_call",
        name: "update_interview_state",
        call_id: "call-partial-new-turn",
        arguments: JSON.stringify({
          turn_id: "turn-partial-new-turn", base_state_version: 1,
          question_id: "question-1", answer_status: "PARTIAL",
          covered_aspects: [{ aspect_id: "name", evidence: "宮崎です" }],
          missing_aspects: ["department"], evidence: [],
        }),
      },
    },
  });
  await flush();
  app.receive({
    type: "response.event",
    delegation_id: "delegation-partial-new-turn",
    response_id: "response-partial-new-turn-continuation",
    event: {
      type: "response.created",
      response: { id: "response-partial-new-turn-continuation" },
    },
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-partial-new-turn",
    response_id: "response-partial-new-turn-continuation",
    event: {
      type: "response.completed",
      response: { id: "response-partial-new-turn-continuation" },
    },
  });
  app.advance(0);
  app.advance(1000);

  assert.equal(
    app.sent.filter((event) => event.type === "session.instructions.append"
      && event.event_id === "gpt_live_state_resume_call-partial-new-turn_interrupt").length,
    0,
    "the persisted Responses result must not add a second handoff boundary",
  );
  assert.equal(
    app.sent.filter((event) => event.type === "session.thinking.append"
      && event.event_id === "gpt_live_state_resume_call-partial-new-turn_state").length,
    1,
  );
  assert.equal(
    app.sent.filter((event) => event.type === "session.commentary.append"
      && event.event_id === "gpt_live_state_resume_call-partial-new-turn").length,
    1,
  );
  app.hook.stop();
});

test("a Live acknowledgement is allowed to finish while Responses delegation runs", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "Responses評価待ちの回答です。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({ type: "session.output_transcript.delta", delta: "回答を確認しています。" });
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-after-output", target: "responses" },
    response_id: "response-after-output",
  });

  const interruptions = app.sent.filter((event) => event.type === "session.instructions.append"
    && event.event_id?.startsWith("gpt_live_response_interrupt_"));
  assert.equal(interruptions.length, 0);
  app.receive({ type: "session.output_transcript.delta", delta: "古い応答の続きです。" });
  assert.equal(
    app.sent.filter((event) => event.type === "session.instructions.append"
      && event.event_id?.startsWith("gpt_live_response_interrupt_")).length,
    0,
  );
  app.hook.stop();
});

test("the first user transcript does not add an application boundary to the initial greeting", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.output_transcript.delta", delta: "最初のご案内です。" });
  app.receive({ type: "input_audio_buffer.speech_started" });
  app.receive({ type: "session.input_transcript.delta", delta: "最初の回答です。" });

  const interruptions = app.sent.filter((event) => (
    event.type === "session.instructions.append"
      && event.event_id?.startsWith("gpt_live_response_interrupt_")
  ));
  assert.equal(interruptions.length, 0);
  app.hook.stop();
});

test("an input interruption is followed by one State resume for a PARTIAL handoff", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "最初の回答です。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({ type: "session.output_transcript.delta", delta: "次の質問です。" });
  app.receive({ type: "input_audio_buffer.speech_started" });
  app.receive({ type: "session.input_transcript.delta", delta: "宮崎です。" });

  const inputInterrupt = app.sent.find((event) => (
    event.type === "session.instructions.append"
      && event.event_id?.startsWith("gpt_live_response_interrupt_")
  ));
  assert.ok(inputInterrupt);
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-next-partial", target: "responses" },
    response_id: "response-next-partial",
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-next-partial",
    event: {
      type: "response.output_item.done",
      item: {
        type: "function_call",
        name: "update_interview_state",
        call_id: "call-next-partial",
        arguments: JSON.stringify({
          turn_id: "turn-next-partial", base_state_version: 1,
          question_id: "question-1", answer_status: "PARTIAL",
          covered_aspects: [{ aspect_id: "name", evidence: "宮崎です" }],
          missing_aspects: ["department"], evidence: [],
        }),
      },
    },
  });
  await flush();
  assert.equal(app.stateRequests.length, 1);
  assert.equal(app.stateRequests[0].args[1].answer_status, "PARTIAL");
  app.receive({
    type: "response.event",
    delegation_id: "delegation-next-partial",
    response_id: "response-next-partial-continuation",
    event: {
      type: "response.created",
      response: { id: "response-next-partial-continuation" },
    },
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-next-partial",
    response_id: "response-next-partial-continuation",
    event: {
      type: "response.completed",
      response: { id: "response-next-partial-continuation" },
    },
  });
  app.advance(0);

  assert.equal(
    app.sent.filter((event) => (
      (event.type === "session.thinking.append"
        && event.event_id === "gpt_live_state_resume_call-next-partial_state")
      || (event.type === "session.commentary.append"
        && event.event_id === "gpt_live_state_resume_call-next-partial")
    )).length,
    0,
    "State resume waits for the requested input boundary ACK",
  );
  assert.equal(
    app.sent.filter((event) => event.type === "session.instructions.append"
      && event.event_id === "gpt_live_state_resume_call-next-partial_interrupt").length,
    0,
    "State resume waits for the input boundary ACK",
  );
  app.receive({
    type: "session.instructions.appended",
    client_event_id: inputInterrupt.event_id,
  });
  app.advance(1000);
  assert.equal(
    app.sent.filter((event) => event.type === "session.instructions.append"
      && event.event_id === "gpt_live_state_resume_call-next-partial_interrupt").length,
    0,
    "the State handoff does not add a second interruption boundary",
  );
  assert.equal(
    app.sent.filter((event) => event.type === "session.thinking.append"
      && event.event_id === "gpt_live_state_resume_call-next-partial_state").length,
    1,
    "State context is sent after the input boundary ACK",
  );
  assert.equal(
    app.sent.filter((event) => event.type === "session.commentary.append"
      && event.event_id === "gpt_live_state_resume_call-next-partial").length,
    1,
  );
  app.hook.stop();
});

test("a transcript-only input boundary is followed by one State resume", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "最初の回答です。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({ type: "session.output_transcript.delta", delta: "前の質問です。" });

  // Some browser sessions omit speech_started. When an existing Live output
  // is still active, the first transcript delta supplies the one input-side
  // boundary so the next user turn can be processed.
  app.receive({ type: "session.input_transcript.delta", delta: "次の回答です。" });
  const inputInterrupt = app.sent.find((event) => event.type === "session.instructions.append"
    && event.event_id?.startsWith("gpt_live_response_interrupt_"));
  assert.ok(inputInterrupt);
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-transcript-only", target: "responses" },
    response_id: "response-transcript-only",
  });
  // A delayed fragment from the old Live response can arrive after the new
  // transcript starts. It must not create a second input-side boundary.
  app.receive({ type: "session.output_transcript.delta", delta: "遅延した前の応答です。" });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-transcript-only",
    event: {
      type: "response.output_item.done",
      item: {
        type: "function_call",
        name: "update_interview_state",
        call_id: "call-transcript-only",
        arguments: JSON.stringify({
          turn_id: "turn-transcript-only", base_state_version: 1,
          question_id: "question-1", answer_status: "PARTIAL",
          covered_aspects: [{ aspect_id: "name", evidence: "次の回答です" }],
          missing_aspects: ["department"], evidence: [],
        }),
      },
    },
  });
  await flush();
  app.receive({
    type: "response.event",
    delegation_id: "delegation-transcript-only",
    response_id: "response-transcript-only-continuation",
    event: {
      type: "response.created",
      response: { id: "response-transcript-only-continuation" },
    },
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-transcript-only",
    response_id: "response-transcript-only-continuation",
    event: {
      type: "response.completed",
      response: { id: "response-transcript-only-continuation" },
    },
  });
  app.advance(0);

  assert.equal(
    app.sent.filter((event) => event.type === "session.instructions.append"
      && event.event_id?.startsWith("gpt_live_response_interrupt_")).length,
    1,
    "the transcript boundary is sent once for the new user utterance",
  );
  assert.equal(
    app.sent.filter((event) => event.type === "session.instructions.append"
      && event.event_id === "gpt_live_state_resume_call-transcript-only_interrupt").length,
    0,
    "State resume does not add a second interruption boundary",
  );
  assert.equal(
    app.sent.filter((event) => event.type === "session.commentary.append"
      && event.event_id === "gpt_live_state_resume_call-transcript-only").length,
    0,
  );
  app.receive({
    type: "session.instructions.appended",
    client_event_id: inputInterrupt.event_id,
  });
  app.advance(1000);
  assert.equal(
    app.sent.filter((event) => event.type === "session.instructions.append"
      && event.event_id === "gpt_live_state_resume_call-transcript-only_interrupt").length,
    0,
    "State resume keeps the single transcript boundary",
  );
  assert.equal(
    app.sent.filter((event) => event.type === "session.commentary.append"
      && event.event_id === "gpt_live_state_resume_call-transcript-only").length,
    1,
  );
  app.hook.stop();
});

test("a pre-delegation Live acknowledgement is drained before persisted-state resume", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "先行応答が残っている回答です。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({ type: "session.output_transcript.delta", delta: "回答を確認しています。" });
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-pre-output", target: "responses" },
    response_id: "response-pre-output",
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-pre-output",
    event: {
      type: "response.output_item.done",
      item: {
        type: "function_call",
        name: "update_interview_state",
        call_id: "call-pre-output",
        arguments: JSON.stringify({
          turn_id: "turn-pre-output", base_state_version: 1,
          question_id: "question-1", answer_status: "ANSWERED",
          covered_aspects: [], missing_aspects: [], evidence: [],
        }),
      },
    },
  });
  await flush();
  app.receive({
    type: "response.event",
    delegation_id: "delegation-pre-output",
    response_id: "response-pre-output-continuation",
    event: {
      type: "response.created",
      response: { id: "response-pre-output-continuation" },
    },
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-pre-output",
    response_id: "response-pre-output-continuation",
    event: {
      type: "response.completed",
      response: { id: "response-pre-output-continuation" },
    },
  });
  app.advance(0);
  assert.equal(
    app.sent.filter((event) => event.type === "session.commentary.append"
      && event.event_id === "gpt_live_state_resume_call-pre-output").length,
    0,
  );
  app.advance(1000);

  assert.equal(
    app.sent.some((event) => (
      event.type === "session.instructions.append"
        && event.event_id === "gpt_live_state_resume_call-pre-output_interrupt"
    )),
    false,
    "an unfinished Live response does not get a second handoff boundary",
  );
  assert.equal(
    app.sent.filter((event) => event.type === "session.commentary.append"
      && event.event_id === "gpt_live_state_resume_call-pre-output").length,
    1,
  );
  app.hook.stop();
});

test("late output during delegation keeps the State resume correlated", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "中断後に遅延出力が届く回答です。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-stale-output", target: "responses" },
    response_id: "response-stale-output",
  });

  // These are Live output fragments that arrive while the Responses
  // delegation is pending. They are not a new user utterance, so they must
  // not cause an input-side interruption by themselves.
  app.receive({ type: "session.output_transcript.delta", delta: "確認中です。" });
  app.receive({ type: "session.output_transcript.delta", delta: "遅延した古い出力です。" });
  assert.equal(
    app.sent.filter((event) => event.type === "session.instructions.append"
      && event.event_id?.startsWith("gpt_live_response_interrupt_")).length,
    0,
  );

  app.receive({
    type: "response.event",
    delegation_id: "delegation-stale-output",
    event: {
      type: "response.output_item.done",
      item: {
        type: "function_call",
        name: "update_interview_state",
        call_id: "call-stale-output",
        arguments: JSON.stringify({
          turn_id: "turn-stale-output", base_state_version: 1,
          question_id: "question-1", answer_status: "PARTIAL",
          covered_aspects: [{ aspect_id: "name", evidence: "宮崎です" }],
          missing_aspects: ["department"], evidence: [],
        }),
      },
    },
  });
  await flush();
  app.receive({
    type: "response.event",
    delegation_id: "delegation-stale-output",
    response_id: "response-stale-output-continuation",
    event: {
      type: "response.created",
      response: { id: "response-stale-output-continuation" },
    },
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-stale-output",
    response_id: "response-stale-output-continuation",
    event: {
      type: "response.completed",
      response: { id: "response-stale-output-continuation" },
    },
  });
  app.advance(0);
  app.advance(1000);

  assert.equal(
    app.sent.filter((event) => event.type === "session.instructions.append"
      && event.event_id === "gpt_live_state_resume_call-stale-output_interrupt").length,
    0,
    "the state handoff does not add a second boundary for the still-active response",
  );
  assert.equal(
    app.sent.filter((event) => event.type === "session.commentary.append"
      && event.event_id === "gpt_live_state_resume_call-stale-output").length,
    1,
  );
  app.hook.stop();
});

test("post-delegation Live output does not discard the pending State resume", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "不足情報を確認してください。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-post-output", target: "responses" },
    response_id: "response-post-output",
  });
  app.receive({ type: "session.output_transcript.delta", delta: "確認中です。" });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-post-output",
    event: {
      type: "response.output_item.done",
      item: {
        type: "function_call",
        name: "update_interview_state",
        call_id: "call-post-output",
        arguments: JSON.stringify({
          turn_id: "turn-post-output", base_state_version: 1,
          question_id: "question-1", answer_status: "PARTIAL",
          covered_aspects: [{ aspect_id: "name", evidence: "宮崎です" }],
          missing_aspects: ["department"], evidence: [],
        }),
      },
    },
  });
  await flush();
  app.receive({
    type: "response.event",
    delegation_id: "delegation-post-output",
    response_id: "response-post-output-continuation",
    event: {
      type: "response.created",
      response: { id: "response-post-output-continuation" },
    },
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-post-output",
    response_id: "response-post-output-continuation",
    event: {
      type: "response.completed",
      response: { id: "response-post-output-continuation" },
    },
  });
  app.advance(0);
  app.receive({
    type: "session.output_transcript.delta",
    delta: "保存後の短い接続表現です。",
  });
  app.advance(1000);

  assert.equal(
    app.sent.filter((event) => event.type === "session.thinking.append"
      && event.event_id === "gpt_live_state_resume_call-post-output_state").length,
    1,
  );
  assert.equal(
    app.sent.filter((event) => event.type === "session.commentary.append"
      && event.event_id === "gpt_live_state_resume_call-post-output").length,
    1,
  );
  app.hook.stop();
});

test("delegation-created boundary keeps the later Live resume on the same turn", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "発話終了イベントより先に評価される回答です。" });
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-before-speech-end", target: "responses" },
    response_id: "response-before-speech-end",
  });
  app.advance(2500);
  assert.equal(
    app.sent.filter((event) => event.event_id?.includes("wait_notice")).length,
    0,
    "an in-flight Delegation is not a slow Live response",
  );
  app.receive({
    type: "response.event",
    delegation_id: "delegation-before-speech-end",
    response_id: "response-before-speech-end",
    event: {
      type: "response.output_item.done",
      item: {
        type: "function_call",
        name: "update_interview_state",
        call_id: "call-before-speech-end",
        arguments: JSON.stringify({
          turn_id: "turn-before-speech-end", base_state_version: 1,
          question_id: "question-1", answer_status: "ANSWERED",
          covered_aspects: [], missing_aspects: [], evidence: [],
        }),
      },
    },
  });
  await flush();
  app.receive({
    type: "response.event",
    delegation_id: "delegation-before-speech-end",
    response_id: "response-before-speech-end-continuation",
    event: {
      type: "response.created",
      response: { id: "response-before-speech-end-continuation" },
    },
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-before-speech-end",
    response_id: "response-before-speech-end-continuation",
    event: {
      type: "response.completed",
      response: { id: "response-before-speech-end-continuation" },
    },
  });
  app.advance(0);

  const resume = app.sent.find((event) => (
    event.type === "session.commentary.append"
      && event.event_id === "gpt_live_state_resume_call-before-speech-end"
  ));
  assert.ok(resume, "the persisted result must resume Live on the same turn");
  assert.equal(resume.delegation_id, null);
  app.hook.stop();
});

test("watchdog waits for a late Responses delegation before creating a competing response", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "Delegationが遅れて届く回答です。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });

  app.advance(5000);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("continuation")).length, 0);
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-late", target: "responses" },
    response_id: "response-late",
  });
  app.advance(5000);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("continuation")).length, 0);
  app.hook.stop();
});

test("watchdog keeps the second delegation wait from racing a late handoff", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "さらに遅れてDelegationが届く回答です。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });

  app.advance(5000);
  app.advance(5000);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("continuation")).length, 0);

  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-later", target: "responses" },
    response_id: "response-later",
  });
  app.advance(5000);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("continuation")).length, 0);

  app.receive({
    type: "response.event",
    delegation_id: "delegation-later",
    response_id: "response-later",
    event: {
      type: "response.completed",
      response: { id: "response-later" },
    },
  });
  app.advance(5000);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("continuation")).length, 1);
  app.hook.stop();
});

test("a late PARTIAL Delegation keeps the watchdog bounded and sends one Live recovery", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "宮崎です" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });

  // Reproduce the race: the wait notice is spoken first, the Responses
  // delegation is still late, and the bounded watchdog starts one Live-only
  // recovery before the delegation is created. It must not use response.create
  // because that command is for the Responses backend, not Live speech.
  app.advance(2500);
  app.receive({
    type: "session.output_transcript.delta",
    delta: "少々お待ちください。",
  });
  app.advance(2500);
  app.advance(5000);
  app.advance(5000);
  app.advance(5000);
  const initialContinuation = app.sent.find((event) => (
    event.type === "session.commentary.append"
      && event.event_id?.startsWith("gpt_live_continuation_")
  ));
  assert.ok(initialContinuation);
  assert.equal(
    app.sent.filter((event) => event.type === "response.create"
      && event.event_id?.startsWith("gpt_live_continuation_")).length,
    0,
  );

  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-late-recovery", target: "responses" },
    response_id: "response-late-recovery",
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-late-recovery",
    event: {
      type: "response.output_item.done",
      item: {
        type: "function_call",
        name: "update_interview_state",
        call_id: "call-late-recovery",
        arguments: JSON.stringify({
          turn_id: "turn-late-recovery", base_state_version: 1,
          question_id: "question-1", answer_status: "PARTIAL",
          covered_aspects: [{ aspect_id: "name", evidence: "宮崎です" }],
          missing_aspects: ["department"],
          evidence: [{ aspect_id: "name", text: "宮崎です" }],
        }),
      },
    },
  });
  await flush();
  app.receive({
    type: "response.event",
    delegation_id: "delegation-late-recovery",
    response_id: "response-late-recovery-continuation",
    event: {
      type: "response.created",
      response: { id: "response-late-recovery-continuation" },
    },
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-late-recovery",
    response_id: "response-late-recovery-continuation",
    event: {
      type: "response.completed",
      response: { id: "response-late-recovery-continuation" },
    },
  });
  app.advance(0);

  const interrupt = app.sent.filter((event) => (
    event.type === "session.instructions.append"
      && event.event_id === "gpt_live_state_resume_call-late-recovery_interrupt"
  ));
  assert.equal(
    interrupt.length,
    0,
    "a wait notice is not an active interview response and needs no second boundary",
  );
  app.advance(0);
  const resume = app.sent.filter((event) => (
    event.type === "session.commentary.append"
      && event.event_id === "gpt_live_state_resume_call-late-recovery"
  ));
  assert.equal(resume.length, 1);
  assert.equal(app.stateRequests[0].args[1].answer_status, "PARTIAL");
  assert.equal(
    app.sent.filter((event) => event.type === "response.create"
      && event.event_id?.startsWith("gpt_live_state_resume_call-late-recovery")).length,
    0,
    "a late watchdog race must not create a second semantic response",
  );
  app.hook.stop();
});

test("a watchdog Live recovery is not treated as Live output before a Live delta", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "保存待ちのPARTIAL回答です。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });

  // The bounded watchdog uses a Live commentary recovery when no delegation
  // has been observed. Sending response.create here would start/continue the
  // Responses backend instead of requesting a spoken Live response.
  app.advance(2500);
  app.advance(2500);
  app.advance(5000);
  app.advance(5000);
  app.advance(5000);
  assert.ok(app.sent.some((event) => (
    event.type === "session.commentary.append"
      && event.event_id?.startsWith("gpt_live_continuation_")
  )));
  assert.equal(app.sent.filter((event) => (
    event.type === "response.create"
      && event.event_id?.startsWith("gpt_live_continuation_")
  )).length, 0);

  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-watchdog-only", target: "responses" },
    response_id: "response-watchdog-only",
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-watchdog-only",
    event: {
      type: "response.output_item.done",
      item: {
        type: "function_call",
        name: "update_interview_state",
        call_id: "call-watchdog-only",
        arguments: JSON.stringify({
          turn_id: "turn-watchdog-only", base_state_version: 1,
          question_id: "question-1", answer_status: "PARTIAL",
          covered_aspects: [{ aspect_id: "name", evidence: "宮崎です" }],
          missing_aspects: ["department"], evidence: [],
        }),
      },
    },
  });
  await flush();
  app.receive({
    type: "response.event",
    delegation_id: "delegation-watchdog-only",
    response_id: "response-watchdog-only-continuation",
    event: {
      type: "response.created",
      response: { id: "response-watchdog-only-continuation" },
    },
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-watchdog-only",
    response_id: "response-watchdog-only-continuation",
    event: {
      type: "response.completed",
      response: { id: "response-watchdog-only-continuation" },
    },
  });
  app.advance(0);

  assert.equal(
    app.sent.filter((event) => event.type === "session.instructions.append"
      && event.event_id === "gpt_live_state_resume_call-watchdog-only_interrupt").length,
    0,
    "a Responses-only event must not create a fake Live interruption",
  );
  assert.ok(app.sent.some((event) => (
    event.type === "session.commentary.append"
      && event.event_id === "gpt_live_state_resume_call-watchdog-only"
  )));
  app.hook.stop();
});

test("a no-state delegation resumes behind one Live boundary when an old output is active", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "評価結果を待っている回答です。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-without-state-tool", target: "responses" },
    response_id: "response-without-state-tool",
  });
  app.receive({ type: "session.output_transcript.delta", delta: "確認中です。" });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-without-state-tool",
    response_id: "response-without-state-tool",
    event: {
      type: "response.completed",
      response: { id: "response-without-state-tool" },
    },
  });

  const boundary = app.sent.filter((event) => (
    event.type === "session.instructions.append"
      && event.event_id === "gpt_live_continuation_gpt-live-turn-1-1_interrupt"
  ));
  assert.equal(boundary.length, 1);
  assert.equal(
    app.sent.filter((event) => event.type === "session.commentary.append"
      && event.event_id === "gpt_live_continuation_gpt-live-turn-1-1").length,
    0,
    "the speakable continuation waits for the boundary ACK",
  );
  app.advance(1000);
  assert.equal(
    app.sent.filter((event) => event.type === "session.commentary.append"
      && event.event_id === "gpt_live_continuation_gpt-live-turn-1-1").length,
    1,
    "a missing Live boundary ACK must not leave a no-state delegation silent",
  );
  assert.equal(app.sent.filter((event) => event.type === "response.create").length, 0);

  app.receive({
    type: "session.instructions.appended",
    client_event_id: "gpt_live_continuation_gpt-live-turn-1-1_interrupt",
  });
  assert.equal(
    app.sent.filter((event) => event.type === "session.commentary.append"
      && event.event_id === "gpt_live_continuation_gpt-live-turn-1-1").length,
    1,
  );
  app.hook.stop();
});

test("a protocol response without user-facing output does not block no-state recovery", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "まだ音声が始まっていない回答です。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({
    type: "response.created",
    response: { id: "live-response-created-only" },
  });
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-created-without-state", target: "responses" },
    response_id: "responses-without-state",
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-created-without-state",
    response_id: "responses-without-state",
    event: {
      type: "response.completed",
      response: { id: "responses-without-state" },
    },
  });

  const boundary = app.sent.filter((event) => (
    event.type === "session.instructions.append"
      && event.event_id === "gpt_live_continuation_gpt-live-turn-1-1_interrupt"
  ));
  assert.equal(boundary.length, 1);
  assert.equal(
    app.sent.filter((event) => event.type === "session.commentary.append"
      && event.event_id === "gpt_live_continuation_gpt-live-turn-1-1").length,
    0,
    "the recovery waits for the single Live boundary ACK",
  );

  app.receive({
    type: "session.instructions.appended",
    client_event_id: "gpt_live_continuation_gpt-live-turn-1-1_interrupt",
  });
  assert.equal(
    app.sent.filter((event) => event.type === "session.commentary.append"
      && event.event_id === "gpt_live_continuation_gpt-live-turn-1-1").length,
    1,
  );
  app.hook.stop();
});

test("transcript idle boundary starts the watchdog when Live omits speech-end events", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "発話終了イベントのない回答です。" });

  app.advance(2499);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("continuation")).length, 0);

  // The idle boundary creates the same response watchdog as the provider's
  // speech-stopped event. It does not immediately emit a second response.
  app.advance(1);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("wait_notice")).length, 0);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("continuation")).length, 0);

  app.advance(2500);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("wait_notice")).length, 1);
  app.advance(2500);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("continuation")).length, 0);
  app.advance(10000);
  app.advance(5000);
  app.advance(5000);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("continuation")).length, 1);
  app.hook.stop();
});

test("an already observed Live response suppresses the duplicate wait notice but keeps recovery bounded", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "回答後の状態保存が必要です。" });
  app.receive({ type: "session.output_transcript.delta", delta: "次の質問です。" });

  app.advance(2500);
  app.advance(2500);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("wait_notice")).length, 0);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("continuation")).length, 0);

  app.advance(2500);
  app.advance(5000);
  app.advance(5000);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("continuation")).length, 1);
  app.advance(10000);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("continuation")).length, 1);
  app.hook.stop();
});

test("response start cancels the watchdog before it can send a second prompt", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "input_audio_buffer.speech_stopped" });
  app.receive({
    type: "response.created",
    response_id: "response-1",
  });
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-1", target: "responses" },
    response_id: "response-1",
  });
  app.receive({ type: "response.output_audio.delta", delta: "audio" });
  app.advance(10000);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("wait_notice")).length, 0);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("continuation")).length, 0);
  app.hook.stop();
});

test("watchdog starts a new turn after a previous Live response", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "最初の回答です。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({ type: "response.created", response_id: "response-1" });

  app.receive({ type: "input_audio_buffer.speech_started" });
  app.receive({ type: "session.input_transcript.delta", delta: "次の回答です。" });
  const inputInterrupt = app.sent.find((event) => (
    event.type === "session.instructions.append"
      && event.event_id?.startsWith("gpt_live_response_interrupt_")
  ));
  assert.ok(inputInterrupt);
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({
    type: "session.instructions.appended",
    client_event_id: inputInterrupt.event_id,
  });
  app.advance(5000);
  app.advance(5000);
  app.advance(5000);
  app.advance(5000);
  assert.equal(app.sent.filter((event) => event.event_id?.includes("continuation")).length, 1);
  app.hook.stop();
});

test("duplicate speech-end events do not create another turn without new transcript", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "回答です。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({ type: "response.created", response_id: "response-1" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.advance(5000);

  assert.equal(app.sent.filter((event) => event.event_id?.includes("continuation")).length, 0);
  app.hook.stop();
});

test("Responses function call is persisted and resumed without backend question generation", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "顧客の要望を聞いて開発しました。" });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-1",
    event: {
      type: "response.output_item.done",
      item: {
        type: "function_call",
        name: "update_interview_state",
        call_id: "call-1",
        arguments: JSON.stringify({
          turn_id: "turn-1", base_state_version: 1, question_id: "question-1",
          answer_status: "PARTIAL",
          covered_aspects: [{ aspect_id: "trigger_experience", evidence: "顧客の要望を聞いて開発した" }],
          missing_aspects: ["impact_or_change"],
          evidence: [{ aspect_id: "trigger_experience", text: "顧客の要望を聞いて開発した" }],
        }),
      },
    },
  });
  await flush();
  assert.equal(app.stateRequests.length, 1);
  assert.equal(app.stateRequests[0].args[1].question_id, "question-1");
  assert.equal(app.sent.filter((event) => event.type === "response.item.create").length, 1);
  const continuation = app.sent.find((event) => event.type === "response.create");
  assert.ok(continuation);
  assert.equal(continuation.response, undefined);
  assert.equal(
    app.sent.some((event) => event.event_id === "gpt_live_state_resume_call-1"),
    false,
    "the Live steering event must wait until the Responses handoff is complete",
  );
  app.hook.stop();
});

test("a persisted Responses result steers Live without duplicating the backend continuation", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "保存後も会話を続けてください。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-resume", target: "responses" },
    response_id: "response-resume",
  });
  // Live may produce a short acknowledgement before the Responses evaluator
  // has returned the persisted State. It must not suppress the post-handoff
  // steering event that resumes the actual interview response.
  app.receive({
    type: "session.output_transcript.delta",
    delta: "保存前の接続表現です。",
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-resume",
    event: {
      type: "response.output_item.done",
      item: {
        type: "function_call",
        name: "update_interview_state",
        call_id: "call-resume",
        arguments: JSON.stringify({
          turn_id: "turn-resume", base_state_version: 1, question_id: "question-1",
          answer_status: "ANSWERED", covered_aspects: [], missing_aspects: [], evidence: [],
        }),
      },
    },
  });
  await flush();

  assert.equal(app.sent.filter((event) => event.type === "response.create").length, 1);
  assert.equal(app.sent.filter((event) => event.event_id === "gpt_live_state_resume_call-resume").length, 0);

  // The Responses continuation is still running. The Live watchdog must not
  // start another Responses request for the same turn.
  app.advance(5000);
  assert.equal(app.sent.filter((event) => event.type === "response.create").length, 1);

  app.receive({
    type: "response.event",
    delegation_id: "delegation-resume",
    event: {
      type: "response.created",
      response: { id: "response-resume-continuation" },
    },
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-resume",
    event: {
      type: "response.completed",
      response: { id: "response-resume-continuation" },
    },
  });
  app.advance(0);
  assert.equal(app.sent.filter((event) => event.type === "response.create").length, 1);
  assert.equal(app.sent.filter((event) => event.event_id?.startsWith("gpt_live_continuation_")).length, 0);
  assert.equal(
    app.sent.filter((event) => event.type === "session.instructions.append"
      && event.event_id === "gpt_live_state_resume_call-resume_interrupt").length,
    0,
    "the persisted-state handoff uses one direct Live update",
  );
  assert.equal(
    app.sent.find((event) => event.event_id === "gpt_live_state_resume_call-resume")?.type,
    "session.commentary.append",
  );
  app.hook.stop();
});

test("post-delegation resume includes the latest checklist state for Live progression", async () => {
  const app = setup(() => {}, () => Promise.resolve({
    status: "applied",
    stateVersion: 3,
    currentQuestionId: "question-1",
    coveredAspects: ["name", "department", "role"],
    missingAspects: [],
    checklist: [
      {
        id: "field-1",
        question_id: "question-1",
        label: "基本プロフィール",
        description: "お名前、所属、役職を教えてください。",
        answer_state: "CONFIRMED",
        answer_resolution: "CONFIRMED",
        needs_confirmation: false,
        captured_items: ["氏名", "所属部署", "役職"],
        missing_required_items: [],
      },
      {
        id: "field-2",
        question_id: "question-2",
        label: "現在の役割",
        description: "現在の役割と責任を教えてください。",
        answer_state: "UNANSWERED",
        answer_resolution: null,
        needs_confirmation: true,
        captured_items: [],
        missing_required_items: ["役割"],
      },
    ],
    current: {
      field_id: "field-1",
      question_id: "question-1",
      state_version: 3,
      answer_evaluation: "ANSWERED",
      clarification_kind: "NONE",
      clarification_target: null,
    },
  }));
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "プロフィールへの回答です。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-checklist", target: "responses" },
    response_id: "response-checklist",
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-checklist",
    event: {
      type: "response.output_item.done",
      item: {
        type: "function_call",
        name: "update_interview_state",
        call_id: "call-checklist",
        arguments: JSON.stringify({
          turn_id: "turn-checklist", base_state_version: 2, question_id: "question-1",
          answer_status: "ANSWERED", covered_aspects: [], missing_aspects: [], evidence: [],
        }),
      },
    },
  });
  await flush();
  app.advance(5000);
  app.receive({
    type: "response.event",
    delegation_id: "delegation-checklist",
    event: {
      type: "response.created",
      response: { id: "response-checklist-continuation" },
    },
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-checklist",
    event: {
      type: "response.completed",
      response: { id: "response-checklist-continuation" },
    },
  });
  app.advance(0);

  const stateContext = app.sent.find((event) => event.type === "session.thinking.append");
  assert.ok(stateContext);
  assert.match(stateContext.content, /question-1/);
  assert.match(stateContext.content, /state_version=3/);
  assert.match(stateContext.content, /covered_aspects=\["name","department","role"\]/);
  assert.match(stateContext.content, /missing_aspects=\[\]/);
  assert.doesNotMatch(stateContext.content, /checklist=/);
  assert.ok(stateContext.content.length < 1000, "Live state context stays within the append payload budget");
  app.hook.stop();
});

test("a missing Live steering acknowledgement is retried once without duplicating the Responses continuation", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "保存後の応答を再送してください。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-retry", target: "responses" },
    response_id: "response-retry",
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-retry",
    event: {
      type: "response.output_item.done",
      item: {
        type: "function_call",
        name: "update_interview_state",
        call_id: "call-retry",
        arguments: JSON.stringify({
          turn_id: "turn-retry", base_state_version: 1, question_id: "question-1",
          answer_status: "ANSWERED", covered_aspects: [], missing_aspects: [], evidence: [],
        }),
      },
    },
  });
  await flush();
  app.receive({
    type: "response.event",
    delegation_id: "delegation-retry",
    event: {
      type: "response.created",
      response: { id: "response-retry-continuation" },
    },
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-retry",
    event: {
      type: "response.completed",
      response: { id: "response-retry-continuation" },
    },
  });
  app.advance(0);
  const resumeEvents = () => app.sent.filter((event) => (
    event.type === "session.commentary.append"
      && event.event_id?.startsWith("gpt_live_state_resume_call-retry")
  ));
  assert.equal(resumeEvents().length, 1);
  app.advance(999);
  assert.equal(resumeEvents().length, 1);
  app.advance(1);
  assert.equal(resumeEvents().length, 2);
  assert.equal(
    app.sent.filter((event) => event.type === "session.thinking.append"
      && event.event_id?.startsWith("gpt_live_state_resume_call-retry_state")).length,
    2,
    "the private State context is retried together with the spoken continuation",
  );
  assert.equal(app.sent.filter((event) => event.type === "response.create").length, 1);

  app.receive({
    type: "session.commentary.appended",
    client_event_id: "gpt_live_state_resume_call-retry_retry",
  });
  app.advance(1000);
  assert.equal(resumeEvents().length, 2);
  app.hook.stop();
});

test("a Live steering acknowledgement does not suppress retry before user-facing output starts", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "保存後の本応答を確認します。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-ack-no-output", target: "responses" },
    response_id: "response-ack-no-output",
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-ack-no-output",
    event: {
      type: "response.output_item.done",
      item: {
        type: "function_call",
        name: "update_interview_state",
        call_id: "call-ack-no-output",
        arguments: JSON.stringify({
          turn_id: "turn-ack-no-output", base_state_version: 1,
          question_id: "question-1", answer_status: "ANSWERED",
          covered_aspects: [], missing_aspects: [], evidence: [],
        }),
      },
    },
  });
  await flush();
  app.receive({
    type: "response.event",
    delegation_id: "delegation-ack-no-output",
    event: {
      type: "response.created",
      response: { id: "response-ack-no-output-continuation" },
    },
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-ack-no-output",
    event: {
      type: "response.completed",
      response: { id: "response-ack-no-output-continuation" },
    },
  });
  app.advance(0);
  const resumeEvents = () => app.sent.filter((event) => (
    event.type === "session.commentary.append"
      && event.event_id?.startsWith("gpt_live_state_resume_call-ack-no-output")
  ));
  assert.equal(resumeEvents().length, 1);

  // A delayed output fragment before the commentary ACK is still ambiguous;
  // it must not cancel the bounded retry or count as the post-handoff answer.
  app.receive({ type: "session.output_transcript.delta", delta: "古い応答の遅延断片です。" });
  app.advance(999);
  assert.equal(resumeEvents().length, 1);
  app.advance(1);
  assert.equal(resumeEvents().length, 2);

  app.receive({
    type: "session.commentary.appended",
    client_event_id: "gpt_live_state_resume_call-ack-no-output",
  });
  assert.equal(app.sent.filter((event) => event.type === "response.create").length, 1);

  // Only an actual Live response start cancels the bounded recovery retry.
  app.receive({
    type: "response.created",
    response: { id: "live-user-facing-response" },
  });
  app.advance(1000);
  assert.equal(resumeEvents().length, 2);
  app.hook.stop();
});

test("the original Responses handoff completion does not release the continuation watchdog", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "元のhandoff完了を受け取ります。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-original-terminal", target: "responses" },
    response_id: "response-original",
  });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-original-terminal",
    event: {
      type: "response.output_item.done",
      item: {
        type: "function_call",
        name: "update_interview_state",
        call_id: "call-original-terminal",
        arguments: JSON.stringify({
          turn_id: "turn-original-terminal", base_state_version: 1,
          question_id: "question-1", answer_status: "ANSWERED",
          covered_aspects: [], missing_aspects: [], evidence: [],
        }),
      },
    },
  });
  await flush();
  app.receive({
    type: "response.event",
    delegation_id: "delegation-original-terminal",
    event: {
      type: "response.completed",
      response: { id: "response-original" },
    },
  });
  assert.equal(
    app.sent.filter((event) => event.type === "session.commentary.append").length,
    0,
    "the original handoff completion is not the continuation completion",
  );
  app.hook.stop();
});

test("standard function-call completion events update the interview state", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "所属と役職です。" });
  app.receive({
    type: "response.output_item.done",
    delegation_id: "delegation-standard-call",
    item: {
      type: "function_call",
      name: "update_interview_state",
      call_id: "call-standard-call",
      arguments: JSON.stringify({
        turn_id: "turn-standard-call", base_state_version: 1, question_id: "question-1",
        answer_status: "ANSWERED", covered_aspects: [], missing_aspects: [], evidence: [],
      }),
    },
  });
  await flush();

  assert.equal(app.stateRequests.length, 1);
  assert.equal(app.stateRequests[0].args[1].call_id, "call-standard-call");
  assert.equal(app.sent.filter((event) => event.type === "response.item.create").length, 1);
  app.hook.stop();
});

test("function-call arguments completion alone does not persist before the output item is complete", async () => {
  const app = setup();
  await app.hook.start();
  const argumentsValue = JSON.stringify({
    turn_id: "turn-output-item-boundary", base_state_version: 1,
    question_id: "question-1", answer_status: "ANSWERED",
    covered_aspects: [], missing_aspects: [], evidence: [],
  });

  app.receive({
    type: "response.function_call_arguments.done",
    delegation_id: "delegation-output-item-boundary",
    name: "update_interview_state",
    call_id: "call-output-item-boundary",
    arguments: argumentsValue,
  });
  await flush();
  assert.equal(app.stateRequests.length, 0);

  app.receive({
    type: "response.output_item.done",
    delegation_id: "delegation-output-item-boundary",
    item: {
      type: "function_call",
      name: "update_interview_state",
      call_id: "call-output-item-boundary",
      arguments: argumentsValue,
    },
  });
  await flush();
  assert.equal(app.stateRequests.length, 1);
  assert.equal(app.sent.filter((event) => event.type === "response.create").length, 1);
  app.hook.stop();
});

test("each Live assessment keeps the evaluator state version as its concurrency boundary", async () => {
  const app = setup(undefined, (...args) => Promise.resolve({
    status: "applied",
    stateVersion: args[1].call_id === "call-version-1" ? 3 : 4,
    currentQuestionId: "question-1",
    coveredAspects: [],
    missingAspects: [],
  }));
  await app.hook.start();
  app.receive({
    type: "response.output_item.done",
    delegation_id: "delegation-version-1",
    item: {
      type: "function_call",
      name: "update_interview_state",
      call_id: "call-version-1",
      arguments: JSON.stringify({
        turn_id: "turn-version-1", base_state_version: 0, question_id: "question-1",
        answer_status: "PARTIAL", covered_aspects: [], missing_aspects: [], evidence: [],
      }),
    },
  });
  await flush();
  app.receive({
    type: "response.output_item.done",
    delegation_id: "delegation-version-2",
    item: {
      type: "function_call",
      name: "update_interview_state",
      call_id: "call-version-2",
      arguments: JSON.stringify({
        turn_id: "turn-version-2", base_state_version: 0, question_id: "question-1",
        answer_status: "ANSWERED", covered_aspects: [], missing_aspects: [], evidence: [],
      }),
    },
  });
  await flush();

  assert.equal(app.stateRequests[0].args[1].base_state_version, 0);
  assert.equal(app.stateRequests[1].args[1].base_state_version, 0);
  app.hook.stop();
});

test("a stale state response is returned to Live without rebasing the old assessment", async () => {
  let attempts = 0;
  const app = setup(undefined, (...args) => {
    attempts += 1;
    return Promise.resolve(attempts === 1
      ? { status: "stale", stateVersion: 5, currentQuestionId: "question-1" }
      : {
          status: "applied", stateVersion: 6, currentQuestionId: "question-1",
          coveredAspects: [], missingAspects: [],
        });
  });
  await app.hook.start();
  app.receive({
    type: "response.output_item.done",
    delegation_id: "delegation-stale-retry",
    item: {
      type: "function_call",
      name: "update_interview_state",
      call_id: "call-stale-retry",
      arguments: JSON.stringify({
        turn_id: "turn-stale-retry", base_state_version: 0, question_id: "question-1",
        answer_status: "ANSWERED", covered_aspects: [], missing_aspects: [], evidence: [],
      }),
    },
  });
  await flush();

  assert.equal(attempts, 1);
  assert.equal(app.stateRequests[0].args[1].base_state_version, 0);
  assert.equal(app.sent.filter((event) => event.event_id === "gpt_live_state_tool_continue_call-stale-retry").length, 1);
  app.hook.stop();
});

test("an in-flight Live state update cannot write into a restarted session", async () => {
  let resolveUpdate;
  const app = setup(undefined, () => new Promise((resolve) => {
    resolveUpdate = resolve;
  }));
  await app.hook.start();
  app.receive({
    type: "response.output_item.done",
    delegation_id: "delegation-old-session",
    item: {
      type: "function_call",
      name: "update_interview_state",
      call_id: "call-old-session",
      arguments: JSON.stringify({
        turn_id: "turn-old-session", base_state_version: 0, question_id: "question-1",
        answer_status: "ANSWERED", covered_aspects: [], missing_aspects: [], evidence: [],
      }),
    },
  });
  await flush();
  assert.equal(app.stateRequests.length, 1);

  app.hook.stop();
  await app.hook.start();
  const sentAfterRestart = app.sent.length;
  resolveUpdate({
    status: "applied", stateVersion: 1, currentQuestionId: "question-1",
    coveredAspects: [], missingAspects: [],
  });
  await flush();

  assert.equal(app.sent.length, sentAfterRestart);
  assert.equal(app.states.some(({ value }) => value === "completed"), false);
  app.hook.stop();
});

test("standard function-call events correlate by response id when delegation id is absent", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-correlated", target: "responses" },
    response_id: "response-correlated",
  });
  app.receive({
    type: "response.output_item.done",
    response_id: "response-correlated",
    item: {
      type: "function_call",
      name: "update_interview_state",
      call_id: "call-correlated",
      arguments: JSON.stringify({
        turn_id: "turn-correlated", base_state_version: 1, question_id: "question-1",
        answer_status: "ANSWERED", covered_aspects: [], missing_aspects: [], evidence: [],
      }),
    },
  });
  await flush();

  app.receive({ type: "session.input_transcript.delta", delta: "次の回答です。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.advance(5000);
  app.advance(5000);
  app.advance(5000);
  app.advance(5000);
  assert.ok(app.sent.some((event) => event.event_id?.startsWith("gpt_live_continuation_")));
  app.hook.stop();
});

test("a transient state update failure retries the same idempotent tool call", async () => {
  let attempts = 0;
  const app = setup(undefined, (...args) => {
    attempts += 1;
    if (attempts === 1) {
      return Promise.reject(new Error("temporary network failure"));
    }
    return Promise.resolve({
      status: "applied", stateVersion: 2, currentQuestionId: "question-2",
      coveredAspects: [], missingAspects: [],
    });
  });
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "回答です。" });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-retry",
    event: {
      type: "response.output_item.done",
      item: {
        type: "function_call",
        name: "update_interview_state",
        call_id: "call-retry",
        arguments: JSON.stringify({
          turn_id: "turn-retry", base_state_version: 1, question_id: "question-1",
          answer_status: "ANSWERED", covered_aspects: [], missing_aspects: [], evidence: [],
        }),
      },
    },
  });
  await flush();

  assert.equal(attempts, 2);
  assert.equal(app.stateRequests.length, 2);
  assert.equal(app.stateRequests[0].args[1].turn_id, app.stateRequests[1].args[1].turn_id);
  app.hook.stop();
});

test("a state update error resumes Live without advancing the checklist", async () => {
  const app = setup(undefined, () => Promise.reject(new Error("state unavailable")));
  await app.hook.start();
  app.receive({
    type: "response.output_item.done",
    delegation_id: "delegation-state-error",
    item: {
      type: "function_call",
      name: "update_interview_state",
      call_id: "call-state-error",
      arguments: JSON.stringify({
        turn_id: "turn-state-error", base_state_version: 0, question_id: "question-1",
        answer_status: "ANSWERED", covered_aspects: [], missing_aspects: [], evidence: [],
      }),
    },
  });
  await flush();

  const continuation = app.sent.find(
    (event) => event.event_id === "gpt_live_state_tool_continue_call-state-error",
  );
  assert.ok(continuation);
  assert.equal(continuation.response, undefined);
  assert.equal(app.stateRequests.length, 2, "network failures may still use the bounded retry");
  app.hook.stop();
});

test("Responses state updates keep the transcript for their delegation when the next answer starts", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "input_audio_buffer.speech_started" });
  app.receive({ type: "session.input_transcript.delta", delta: "最初の回答です。" });
  app.receive({ type: "input_audio_buffer.speech_stopped" });
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-snapshot", target: "responses" },
    response_id: "response-snapshot",
  });

  // The next answer arrives before the Responses function call completes.
  app.receive({ type: "input_audio_buffer.speech_started" });
  app.receive({ type: "session.input_transcript.delta", delta: "次の回答です。" });
  app.receive({
    type: "response.event",
    delegation_id: "delegation-snapshot",
    event: {
      type: "response.output_item.done",
      item: {
        type: "function_call",
        name: "update_interview_state",
        call_id: "call-snapshot",
        arguments: JSON.stringify({
          turn_id: "turn-snapshot", base_state_version: 1, question_id: "question-1",
          answer_status: "ANSWERED", covered_aspects: [], missing_aspects: [], evidence: [],
        }),
      },
    },
  });
  await flush();

  assert.equal(app.stateRequests.length, 1);
  assert.equal(app.stateRequests[0].args[2], "最初の回答です。");
  app.hook.stop();
});

test("delegation in flight defers the watchdog continuation until its tool result", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "回答です。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "delegation-pending", target: "responses" },
    response_id: "response-pending",
  });

  app.advance(5000);
  assert.equal(app.sent.filter((event) => event.event_id?.startsWith("gpt_live_continuation_")).length, 0);

  app.receive({
    type: "response.event",
    delegation_id: "delegation-pending",
    event: {
      type: "response.output_item.done",
      item: {
        type: "function_call",
        name: "update_interview_state",
        call_id: "call-pending",
        arguments: JSON.stringify({
          turn_id: "turn-pending", base_state_version: 1, question_id: "question-1",
          answer_status: "ANSWERED", covered_aspects: [], missing_aspects: [], evidence: [],
        }),
      },
    },
  });
  await flush();
  assert.equal(app.sent.filter((event) => event.event_id === "gpt_live_state_tool_continue_call-pending").length, 1);
  assert.equal(app.sent.filter((event) => event.event_id?.startsWith("gpt_live_continuation_")).length, 0);
  app.hook.stop();
});

test("page exit flushes speech and keeps the unsaved warning after audio stop", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "保存前の回答" });
  app.pageListeners.get("pagehide")();
  assert.equal(app.requests.length, 1);
  app.hook.stop();
  let prevented = false;
  app.pageListeners.get("beforeunload")({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  app.requests[0].resolve({ status: "accepted", revision: 1 });
  await flush();
  assert.equal(app.pageListeners.has("beforeunload"), false, "only receipt acknowledgment releases the warning");
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

test("delegation snapshots keep every late transcript fragment until the next speech starts", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.delegation.created", delegation_id: "late-fragments" });
  app.receive({ type: "session.input_transcript.delta", delta: "遅延した前半" });
  app.receive({ type: "session.input_transcript.delta", delta: "と後半" });
  app.receive({
    type: "response.event",
    delegation_id: "late-fragments",
    event: {
      type: "response.output_item.done",
      item: {
        type: "function_call",
        name: "update_interview_state",
        call_id: "call-late-fragments",
        arguments: JSON.stringify({
          turn_id: "turn-late-fragments", base_state_version: 1, question_id: "question-1",
          answer_status: "ANSWERED", covered_aspects: [], missing_aspects: [], evidence: [],
        }),
      },
    },
  });
  await flush();

  assert.equal(app.stateRequests.length, 1);
  assert.equal(app.stateRequests[0].args[2], "遅延した前半と後半");
  app.hook.stop();
});

test("a completed delegation without a state tool does not block continuation", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "回答です。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "completed-without-tool", target: "responses" },
    response_id: "initial-without-tool",
  });
  app.receive({
    type: "response.event",
    delegation_id: "completed-without-tool",
    response_id: "initial-without-tool",
    event: {
      type: "response.completed",
      response: { id: "initial-without-tool" },
    },
  });

  app.advance(5000);
  const continuation = app.sent.filter((event) => event.event_id?.startsWith("gpt_live_continuation_"));
  assert.equal(continuation.length, 1);
  assert.equal(continuation[0].type, "session.commentary.append");
  assert.equal(continuation[0].delegation_id, null);
  assert.equal(app.sent.filter((event) => event.type === "response.create").length, 0);
  app.hook.stop();
});

test("a no-state delegation waits for transcript final before continuing Live", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "回答の途中です。" });
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "completed-before-transcript-final", target: "responses" },
    response_id: "initial-before-transcript-final",
  });
  app.receive({
    type: "response.event",
    delegation_id: "completed-before-transcript-final",
    response_id: "initial-before-transcript-final",
    event: {
      type: "response.completed",
      response: { id: "initial-before-transcript-final" },
    },
  });

  assert.equal(
    app.sent.filter((event) => event.event_id?.startsWith("gpt_live_continuation_")).length,
    0,
    "the no-state continuation must wait while the user transcript is active",
  );

  app.receive({ type: "session.input_transcript.done" });
  const continuation = app.sent.filter((event) => event.event_id?.startsWith("gpt_live_continuation_"));
  assert.equal(continuation.length, 1);
  assert.equal(continuation[0].type, "session.commentary.append");
  assert.equal(continuation[0].delegation_id, null);
  app.hook.stop();
});

test("a no-state delegation does not duplicate a Live response that starts while waiting", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "回答です。" });
  app.receive({
    type: "session.delegation.created",
    delegation: { id: "completed-while-speaking", target: "responses" },
    response_id: "initial-while-speaking",
  });
  app.receive({
    type: "response.event",
    delegation_id: "completed-while-speaking",
    response_id: "initial-while-speaking",
    event: {
      type: "response.completed",
      response: { id: "initial-while-speaking" },
    },
  });

  // The user-facing Live response can begin before the transcript grouping
  // marker arrives. That response is the continuation; do not append another
  // commentary request when the input is finalized.
  app.receive({ type: "session.output_transcript.delta", delta: "次の質問です。" });
  app.receive({ type: "session.input_transcript.done" });

  assert.equal(
    app.sent.filter((event) => event.event_id?.startsWith("gpt_live_continuation_")).length,
    0,
  );
  app.hook.stop();
});

test("a protocol-only Live response is recoverable when no delegation is observed", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "Delegationが届かない回答です。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({
    type: "response.created",
    response: { id: "live-response-created-without-delegation" },
  });

  app.advance(5000);
  app.advance(5000);
  app.advance(5000);
  app.advance(5000);

  const continuation = app.sent.filter((event) => event.event_id?.startsWith("gpt_live_continuation_"));
  assert.equal(continuation.length, 1);
  assert.equal(continuation[0].type, "session.commentary.append");
  assert.equal(continuation[0].delegation_id, null);
  assert.equal(app.sent.filter((event) => event.type === "response.create").length, 0);
  app.hook.stop();
});

test("a function call without call_id does not leave delegation pending forever", async () => {
  const app = setup();
  await app.hook.start();
  app.receive({ type: "session.input_transcript.delta", delta: "回答です。" });
  app.receive({ type: "session.input_audio_buffer.speech_stopped" });
  app.receive({ type: "session.delegation.created", delegation_id: "missing-call-id" });
  app.receive({
    type: "response.event",
    delegation_id: "missing-call-id",
    event: {
      type: "response.output_item.done",
      item: {
        type: "function_call",
        name: "update_interview_state",
        arguments: "{}",
      },
    },
  });

  app.advance(5000);
  assert.equal(app.sent.filter((event) => event.event_id?.startsWith("gpt_live_continuation_")).length, 1);
  app.hook.stop();
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
