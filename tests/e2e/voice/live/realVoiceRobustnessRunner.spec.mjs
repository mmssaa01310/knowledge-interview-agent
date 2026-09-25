import { expect, test } from "@playwright/test";

import {
  collectAssistant,
  collectTranscript,
  extractTiming,
  failureAnalysis,
  harnessValidity,
  isGPTLiveInitialResponseReady,
  questionAdvanceExpected,
} from "./realVoiceRobustnessRunner.mjs";

function validHarnessInputs() {
  const audioTrace = {
    events: [{
      kind: "get_user_media_ready",
      details: { tracks: [{ kind: "audio", readyState: "live" }] },
    }],
    stats: [{ outboundRtp: [{ kind: "audio", packetsSent: 12, bytesSent: 1200 }] }],
  };
  const consoleTrace = [
    { args: ["gpt_live_event", { type: "session.started" }] },
    { args: ["gpt_live_event", { type: "input_audio_buffer.speech_started" }] },
    { args: ["gpt_live_event", { type: "input_audio_buffer.speech_stopped" }] },
    { args: ["gpt_live_user_speech_end", { turn_id: "turn-1" }] },
    { args: ["gpt_live_state_persisted", { state_version: 2 }] },
    { args: ["gpt_live_response_started", { turn_id: "turn-1" }] },
  ];
  return {
    engine: "gpt_live",
    audioTrace,
    dataTrace: [],
    consoleTrace,
    transcript: { finalText: "回答です", partialText: "回答です" },
    assistant: { mainResponseCount: 1 },
    timing: {
      speechStart: 100,
      speechEnd: 200,
      transcriptFinal: 250,
      turnCommit: 300,
    },
    stateVersionAdvanced: true,
  };
}

test("harness validity requires real audio and the main response", () => {
  const valid = harnessValidity(validHarnessInputs());
  expect(valid).toMatchObject({
    status: "VALID",
    classification: null,
    checks: {
      sessionEstablished: true,
      microphoneTrackReady: true,
      audioActuallySent: true,
      speechDetected: true,
      transcriptReceived: true,
      atLeastOneTurnCommitted: true,
      assistantMainResponseReceived: true,
    },
  });

  const invalid = harnessValidity({
    ...validHarnessInputs(),
    audioTrace: {
      events: [{
        kind: "get_user_media_ready",
        details: { tracks: [{ kind: "audio", readyState: "live" }] },
      }],
      stats: [],
    },
    consoleTrace: validHarnessInputs().consoleTrace.filter(
      (entry) => entry.args?.[0] !== "gpt_live_state_persisted"
        && !(entry.args?.[0] === "gpt_live_event"
          && ["input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped"]
            .includes(entry.args?.[1]?.type)),
    ),
    transcript: { finalText: "", partialText: "" },
    assistant: { mainResponseCount: 0 },
    timing: { speechStart: null, speechEnd: null, transcriptFinal: null, turnCommit: null },
    stateVersionAdvanced: false,
  });
  expect(invalid.status).toBe("INVALID");
  expect(invalid.classification).toBe("TEST_INFRA_FAILURE");
  expect(invalid.missing).toEqual(expect.arrayContaining([
    "audioActuallySent",
    "transcriptReceived",
    "atLeastOneTurnCommitted",
    "assistantMainResponseReceived",
    "speechDetected",
  ]));
});

test("root failure is separated from cascaded conversation symptoms", () => {
  const harness = harnessValidity({
    ...validHarnessInputs(),
    audioTrace: {
      events: [{
        kind: "get_user_media_ready",
        details: { tracks: [{ kind: "audio", readyState: "live" }] },
      }],
      stats: [],
    },
  });
  const analysis = failureAnalysis({
    engine: "gpt_live",
    harness,
    transcript: { finalText: "", partialText: "" },
    timing: { speechStart: null, speechEnd: null, transcriptFinal: null, turnCommit: null },
    persisted: false,
    advanced: false,
    assistant: { mainResponseCount: 0 },
    dataTrace: [],
    consoleTrace: [],
    networkErrors: [],
  });
  expect(analysis).toMatchObject({
    classification: "TEST_INFRA_FAILURE",
    rootFailure: "AUDIO_NOT_SENT",
  });
  expect(analysis.cascadeFailures).toEqual(expect.arrayContaining([
    "TRANSCRIPT_FINAL_MISSING",
    "ANSWER_NOT_PERSISTED",
    "QUESTION_NOT_ADVANCED",
    "RESPONSE_TIMEOUT",
  ]));
});

test("GPT-Live transcript grouping remains valid without provider VAD boundary events", () => {
  const valid = validHarnessInputs();
  const fallbackOnly = harnessValidity({
    ...valid,
    consoleTrace: valid.consoleTrace.filter((entry) => (
      entry.args?.[0] !== "gpt_live_event"
        || !["input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped"]
          .includes(entry.args?.[1]?.type)
    )),
  });

  expect(fallbackOnly.status).toBe("VALID");
  expect(fallbackOnly.classification).toBeNull();
  expect(fallbackOnly.checks.speechDetected).toBe(true);
  expect(fallbackOnly.observations).toMatchObject({
    providerSpeechStarted: false,
    providerSpeechEnded: false,
    applicationSpeechBoundaryObserved: true,
  });
});

test("Responses delegation response.created events are not counted as Live speech", () => {
  const assistant = collectAssistant([
    {
      direction: "server",
      timestampMs: 100,
      event: {
        type: "response.event",
        delegation_id: "delegation-1",
        event: {
          type: "response.created",
          response: { id: "responses-backend-1" },
        },
      },
    },
  ], [
    {
      timestampMs: 120,
      args: ["gpt_live_response_started", { turn_id: "turn-1" }],
    },
  ], "gpt_live");

  expect(assistant.mainResponseCount).toBe(1);
});

test("the application transcript boundary promotes GPT-Live deltas to a final transcript", () => {
  const transcript = collectTranscript([], [
    {
      timestampMs: 100,
      args: ["gpt_live_event", { type: "session.input_transcript.delta", delta: "回答です" }],
    },
    {
      timestampMs: 2_700,
      args: ["gpt_live_input_transcript_final", {
        source: "transcript_idle",
        transcript_revision: 1,
      }],
    },
  ], "gpt_live");

  expect(transcript).toMatchObject({
    finalText: "回答です",
    finalCount: 1,
    partialText: "回答です",
  });
});

test("real turn latency uses the first audio event, not only response creation", () => {
  const timing = extractTiming({
    provider: "gpt_live",
    dataTrace: [],
    consoleTrace: [
      { timestampMs: 100, args: ["gpt_live_event", { type: "input_audio_buffer.speech_started" }] },
      { timestampMs: 300, args: ["gpt_live_user_speech_end", { turn_id: "turn-1" }] },
      {
        timestampMs: 350,
        args: ["gpt_live_event", {
          type: "input_audio_transcription.completed",
          transcript: "回答です",
        }],
      },
      { timestampMs: 400, args: ["gpt_live_response_started", { turn_id: "turn-1" }] },
      { timestampMs: 500, args: ["gpt_live_first_audio", { turn_id: "turn-1" }] },
    ],
    transcript: {
      finalEntries: [{ timestampMs: 350, args: ["gpt_live_event", {
        type: "input_audio_transcription.completed",
        transcript: "回答です",
      }] }],
    },
    assistant: {
      starts: [{ timestampMs: 400 }],
      backchannels: [],
    },
    stateObservedAt: 375,
  });

  expect(timing).toMatchObject({
    speechEnd: 300,
    speechEndToAssistantStartMs: 100,
    firstAudio: 500,
    speechEndToFirstAudioMs: 200,
    fullTurnLatencyMs: 200,
  });
});

test("uses a Transcribe playback-start event from the client side of the DataChannel", () => {
  const timing = extractTiming({
    provider: "transcribe_polly",
    dataTrace: [
      { direction: "server", timestampMs: 100, event: { type: "user_speech_ended" } },
      { direction: "server", timestampMs: 130, event: { type: "user_transcript_final", text: "回答です" } },
      { direction: "server", timestampMs: 200, event: { type: "assistant_speech_started" } },
      { direction: "client", timestampMs: 240, event: { type: "assistant_playback_started" } },
    ],
    consoleTrace: [],
    transcript: {
      finalEntries: [{ timestampMs: 130, event: { type: "user_transcript_final", text: "回答です" } }],
    },
    assistant: { starts: [{ timestampMs: 200 }], backchannels: [] },
    stateObservedAt: 150,
  });

  expect(timing).toMatchObject({
    firstAudio: 240,
    speechEndToFirstAudioMs: 140,
    fullTurnLatencyMs: 140,
  });
});

test("does not turn GPT output transcript into a response-created event", () => {
  const assistant = collectAssistant([
    {
      direction: "server",
      timestampMs: 100,
      event: { type: "session.input_transcript.delta", delta: "回答です" },
    },
    {
      direction: "server",
      timestampMs: 160,
      event: { type: "session.output_transcript.delta", delta: "次の質問です" },
    },
  ], [], "gpt_live");

  expect(assistant).toMatchObject({
    mainResponseCount: 0,
    transcriptStartedAt: 160,
    text: "次の質問です",
  });
});

test("GPT-Live initial readiness uses output transcript idle, not response.done", () => {
  const output = [
    { timestampMs: 1_000, args: ["gpt_live_event", {
      type: "session.output_transcript.delta",
      delta: "最初の質問",
    }] },
  ];

  expect(isGPTLiveInitialResponseReady(output, { nowMs: 4_000, idleMs: 2_500 })).toBe(true);
  expect(isGPTLiveInitialResponseReady(output, { nowMs: 3_000, idleMs: 2_500 })).toBe(false);
  expect(isGPTLiveInitialResponseReady([
    { timestampMs: 1_000, args: ["gpt_live_event", { type: "response.done" }] },
  ], { nowMs: 4_000, idleMs: 2_500 })).toBe(false);
});

test("GPT-Live initial readiness requires an observed output transcript", () => {
  expect(isGPTLiveInitialResponseReady([], { nowMs: 4_000, idleMs: 2_500 })).toBe(false);
});

test("does not require a question transition while the current answer is pending", () => {
  expect(questionAdvanceExpected({
    expectations: { questionAdvance: "required" },
    answer: { answerState: "CANDIDATE_PENDING", candidate: "宮崎、プロジェクトリーダー" },
  })).toBe(false);

  expect(questionAdvanceExpected({
    expectations: { questionAdvance: "required" },
    answer: { answerState: "CONFIRMED", confirmed: "宮崎、プロジェクトリーダー" },
  })).toBe(true);

  const analysis = failureAnalysis({
    engine: "transcribe_polly",
    harness: {
      classification: null,
      checks: {
        sessionEstablished: true,
        microphoneTrackReady: true,
        audioActuallySent: true,
        speechDetected: true,
      },
    },
    transcript: { finalText: "回答です" },
    timing: { turnCommit: 300, transcriptFinal: 250 },
    persisted: true,
    advanced: false,
    questionAdvanceExpected: false,
    assistant: { mainResponseCount: 1 },
    dataTrace: [],
    consoleTrace: [],
    networkErrors: [],
  });
  expect(analysis.rootFailure).toBeNull();
  expect(analysis.cascadeFailures).not.toContain("QUESTION_NOT_ADVANCED");
});
