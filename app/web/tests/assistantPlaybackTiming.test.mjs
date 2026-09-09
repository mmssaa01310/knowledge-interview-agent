import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

const source = await readFile(new URL("../src/features/realtime-voice/webrtc/assistantPlaybackTracker.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } });
const { AssistantPlaybackTracker } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

// Characterization, not an acceptance claim about physical speaker playback.
// A MediaStream stays live during silence, so audio.onended is not a turn end.
test("measure existing drain guard separately from PCM generation duration", () => {
  const originalWindow = globalThis.window;
  const originalPerformance = globalThis.performance;
  let now = 0;
  const timers = [];
  globalThis.performance = { now: () => now };
  globalThis.window = { setTimeout: (callback, delay) => { timers.push({ callback, delay }); return timers.length; }, clearTimeout: () => {} };
  try {
    for (const [duration, expected] of [[1000, 1000], [undefined, 4000]]) {
      const sent = [];
      const tracker = new AssistantPlaybackTracker({ voiceSessionId: "timing-test", sendDataChannelMessage: (event) => { sent.push(JSON.parse(event)); return true; }, onEvent: () => {} });
      now = 0;
      tracker.handleDataChannelEvent({ type: "assistant_speech_started", responseId: "r1", generation: 1 }, { playbackInitialized: true, remoteAudioPaused: false });
      now = 1000;
      tracker.handleDataChannelEvent({ type: "assistant_speech_ended", responseId: "r1", generation: 1, audioDurationMs: duration }, { playbackInitialized: true, remoteAudioPaused: false });
      const timer = timers.at(-1);
      assert.equal(timer.delay, expected);
      assert.equal(sent.filter((event) => event.type === "assistant_playback_drained").length, 0);
      now += timer.delay;
      timer.callback();
      assert.equal(sent.filter((event) => event.type === "assistant_playback_drained").length, 1);
      console.info(`drain_characterization known_duration=${duration !== undefined} end_to_ack_ms=${timer.delay}`);
      tracker.stop();
    }
  } finally {
    globalThis.window = originalWindow;
    globalThis.performance = originalPerformance;
  }
});
