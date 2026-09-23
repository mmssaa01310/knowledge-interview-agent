import { test, expect } from "@playwright/test";
import { extractRealTurnTrace } from "./realLiveTrace.mjs";

function event(type, timestampMs) {
  return {
    timestampMs,
    performanceMs: timestampMs,
    args: ["gpt_live_event", { type }],
  };
}

test("real GPT Live timing is derived from speech and response events", () => {
  const trace = [
    event("input_audio_buffer.speech_started", 1000),
    { timestampMs: 1400, args: ["gpt_live_user_speech_end", { turn_id: "turn-1" }] },
    event("conversation.item.input_audio_transcription.completed", 1550),
    { timestampMs: 1600, args: ["gpt_live_state_persisted", { status: "ok" }] },
    event("assistant_speech_started", 1800),
    event("assistant_speech_ended", 2600),
  ];
  expect(extractRealTurnTrace(trace)).toMatchObject({
    speechStart: 1000,
    speechEnd: 1400,
    transcriptFinal: 1550,
    turnCommit: 1600,
    assistantSpeechStarted: 1800,
    assistantSpeechEnded: 2600,
    speechEndToTranscriptFinalMs: 150,
    transcriptFinalToAssistantStartMs: 250,
    speechEndToAssistantStartMs: 400,
  });
});

test("initial greeting response is excluded from post-user turn timing", () => {
  const trace = [
    event("assistant_speech_started", 500),
    { timestampMs: 1000, args: ["gpt_live_user_speech_end", { turn_id: "turn-1" }] },
    event("conversation.item.input_audio_transcription.completed", 1100),
    event("assistant_speech_started", 1300),
  ];
  expect(extractRealTurnTrace(trace).assistantSpeechStarted).toBe(1300);
});

