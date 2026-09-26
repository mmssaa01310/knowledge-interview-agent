import { expect, test } from "@playwright/test";

import {
  assistantAfterBoundary,
  collectAssistant,
  collectTranscript,
  extractTiming,
  failureAnalysis,
  gptLiveOutputPlaybackIdleStatus,
  harnessValidity,
  isGPTLiveInitialResponseReady,
  realBehaviorPlan,
  questionAdvanced,
  questionAdvanceRequiredForTurn,
  questionAdvanceExpected,
  verifyLiveQuestionProgression,
} from "./realVoiceRobustnessRunner.mjs";

test("repeated NORMAL sessions restart the ordered fixture sequence", () => {
  expect(realBehaviorPlan({
    engine: "gpt_live",
    behaviors: ["NORMAL"],
    sessions: 3,
  })).toEqual([
    expect.objectContaining({ behavior: "NORMAL", variant: 0, ordinal: 1 }),
    expect.objectContaining({ behavior: "NORMAL", variant: 0, ordinal: 2 }),
    expect.objectContaining({ behavior: "NORMAL", variant: 0, ordinal: 3 }),
  ]);
});

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

test("direct Responses delegation response.created events are not counted as Live speech", () => {
  const assistant = collectAssistant([
    {
      direction: "server",
      timestampMs: 100,
      event: {
        type: "response.created",
        delegation_id: "delegation-1",
        response: { id: "responses-backend-1", model: "gpt-5.6-luna" },
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

test("GPT-Live assistant output is bounded to the current turn", () => {
  const assistant = collectAssistant([
    {
      direction: "server",
      timestampMs: 110,
      event: { type: "session.input_transcript.delta", delta: "今の回答" },
    },
    {
      direction: "server",
      timestampMs: 300,
      event: { type: "session.input_transcript.delta", delta: "次の回答" },
    },
    {
      direction: "server",
      timestampMs: 410,
      event: { type: "session.output_transcript.delta", delta: "次の質問" },
    },
  ], [
    { timestampMs: 200, args: ["gpt_live_response_started", { turn_id: "turn-1" }] },
    { timestampMs: 400, args: ["gpt_live_response_started", { turn_id: "turn-2" }] },
  ], "gpt_live", {
    boundaryAt: 100,
    inputEndAt: 150,
  });

  expect(assistant).toMatchObject({
    mainResponseCount: 1,
    starts: [{ timestampMs: 200 }],
  });
});

test("Transcribe processing backchannels are not counted as duplicate main responses", () => {
  const assistant = collectAssistant([
    {
      direction: "server",
      timestampMs: 100,
      event: {
        type: "assistant_backchannel",
        responseId: "processing-1",
      },
    },
    {
      direction: "server",
      timestampMs: 100,
      event: {
        type: "assistant_speech_started",
        responseId: "processing-1",
      },
    },
    {
      direction: "server",
      timestampMs: 200,
      event: {
        type: "assistant_speech_started",
        responseId: "main-1",
      },
    },
  ], [], "transcribe_polly");

  expect(assistant).toMatchObject({
    mainResponseCount: 1,
    ackCount: 1,
  });
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

test("per-turn GPT-Live transcript telemetry excludes earlier user turns", () => {
  const transcript = collectTranscript([
    {
      direction: "server",
      timestampMs: 100,
      event: { type: "session.input_transcript.delta", delta: "前の回答" },
    },
    {
      direction: "server",
      timestampMs: 300,
      event: { type: "session.input_transcript.delta", delta: "今の回答" },
    },
  ], [
    {
      timestampMs: 400,
      args: ["gpt_live_input_transcript_final", {
        source: "transcript_idle",
        transcript_revision: 2,
      }],
    },
  ], "gpt_live", { boundaryAt: 250 });

  expect(transcript).toMatchObject({
    finalText: "今の回答",
    partialText: "今の回答",
    partialCount: 1,
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

test("real turn latency uses inbound WebRTC audio when no DataChannel audio event exists", () => {
  const timing = extractTiming({
    provider: "gpt_live",
    dataTrace: [],
    consoleTrace: [
      { timestampMs: 100, args: ["gpt_live_user_speech_end", { turn_id: "turn-1" }] },
      { timestampMs: 400, args: ["gpt_live_response_started", { turn_id: "turn-1" }] },
    ],
    transcript: {
      finalEntries: [{ timestampMs: 200, args: ["gpt_live_input_transcript_final", {
        transcript: "回答です",
      }] }],
    },
    assistant: { starts: [{ timestampMs: 400 }], backchannels: [] },
    stateObservedAt: 300,
    audioTrace: {
      events: [],
      stats: [{
        timestampMs: 650,
        inboundRtp: [{ kind: "audio", packetsReceived: 1, bytesReceived: 160 }],
      }],
    },
  });

  expect(timing).toMatchObject({
    firstAudio: 650,
    speechEndToFirstAudioMs: 550,
    fullTurnLatencyMs: 550,
  });
});

test("real turn timing records the explicit Live audio completion event", () => {
  const timing = extractTiming({
    provider: "gpt_live",
    dataTrace: [],
    consoleTrace: [
      { timestampMs: 100, args: ["gpt_live_user_speech_end", { turn_id: "turn-1" }] },
      { timestampMs: 300, args: ["gpt_live_first_audio", { turn_id: "turn-1" }] },
      { timestampMs: 900, args: ["gpt_live_live_audio_completed", {
        turn_id: "turn-1", type: "response.output_audio.done",
      }] },
    ],
    transcript: { finalEntries: [] },
    assistant: { starts: [], backchannels: [] },
    stateObservedAt: 400,
  });

  expect(timing).toMatchObject({
    firstAudio: 300,
    assistantSpeechEnded: 900,
  });
});

test("turn timing ignores a delegated initial greeting persisted before user speech", () => {
  const timing = extractTiming({
    provider: "gpt_live",
    dataTrace: [],
    consoleTrace: [
      { timestampMs: 100, args: ["gpt_live_state_persisted", { delegation_id: "greeting" }] },
      { timestampMs: 200, args: ["gpt_live_user_speech_end", { turn_id: "turn-1" }] },
      { timestampMs: 240, args: ["gpt_live_input_transcript_final", { transcript: "回答です" }] },
      { timestampMs: 320, args: ["gpt_live_state_persisted", { delegation_id: "answer" }] },
    ],
    transcript: {
      finalEntries: [{
        timestampMs: 240,
        args: ["gpt_live_input_transcript_final", { transcript: "回答です" }],
      }],
    },
    assistant: { starts: [], backchannels: [] },
    stateObservedAt: 330,
  });

  expect(timing).toMatchObject({
    speechEnd: 200,
    transcriptFinal: 240,
    turnCommit: 320,
    segmentFinalToTurnCommitMs: 80,
  });
});

test("turn timing does not attribute an earlier turn commit to a later replay", () => {
  const timing = extractTiming({
    provider: "gpt_live",
    dataTrace: [],
    consoleTrace: [
      { timestampMs: 100, args: ["gpt_live_user_speech_end", { turn_id: "turn-1" }] },
      { timestampMs: 120, args: ["gpt_live_state_persisted", { delegation_id: "turn-1" }] },
      { timestampMs: 1_015, args: ["gpt_live_input_transcript_final", { transcript: "同じ回答" }] },
      { timestampMs: 1_010, args: ["gpt_live_user_speech_end", { turn_id: "turn-2" }] },
      { timestampMs: 1_020, args: ["gpt_live_response_started", { turn_id: "turn-2" }] },
    ],
    transcript: {
      finalEntries: [{
        timestampMs: 1_015,
        args: ["gpt_live_input_transcript_final", { transcript: "同じ回答" }],
      }],
    },
    assistant: { starts: [{ timestampMs: 1_020 }], backchannels: [] },
    stateObservedAt: 1_030,
    turnStartedAt: 1_000,
    stateVersionAdvanced: false,
  });

  expect(timing).toMatchObject({
    speechEnd: 1_010,
    transcriptFinal: 1_015,
    turnCommit: null,
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

test("assistant output is correlated after the State persistence boundary", () => {
  const assistant = collectAssistant([
    {
      direction: "server",
      timestampMs: 100,
      event: { type: "session.output_transcript.delta", delta: "前ターンの残り" },
    },
    {
      direction: "server",
      timestampMs: 200,
      event: { type: "session.output_transcript.delta", delta: "今ターンの質問" },
    },
  ], [
    { timestampMs: 90, args: ["gpt_live_response_started", { turn_id: "old" }] },
    { timestampMs: 190, args: ["gpt_live_response_started", { turn_id: "new" }] },
  ], "gpt_live");

  expect(assistantAfterBoundary(assistant, 150)).toMatchObject({
    mainResponseCount: 1,
    text: "今ターンの質問",
    transcriptStartedAt: 200,
  });
});

test("same-turn answer target is not mistaken for the next question", () => {
  expect(questionAdvanced({
    before: {
      interviewState: {
        currentQuestionId: "live-field-q1",
        stateVersion: 3,
      },
    },
    after: {
      interviewState: {
        currentQuestionId: "live-field-q1",
        stateVersion: 4,
      },
    },
    requestedQuestionId: "live-field-q1",
    expectedQuestionId: "live-field-q2",
  })).toBe(false);

  expect(questionAdvanced({
    before: {
      interviewState: {
        currentQuestionId: "live-field-q1",
        stateVersion: 3,
      },
    },
    after: {
      interviewState: {
        currentQuestionId: "live-field-q2",
        stateVersion: 4,
      },
    },
    requestedQuestionId: "live-field-q2",
    expectedQuestionId: "live-field-q2",
  })).toBe(true);
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

test("GPT-Live output drain uses output-transcript idle while treating RTP as media evidence", () => {
  const audioTrace = {
    events: [],
    stats: [
      { timestampMs: 1_000, inboundRtp: [{ bytesReceived: 0, packetsReceived: 0 }] },
      { timestampMs: 1_500, inboundRtp: [{ bytesReceived: 100, packetsReceived: 1 }] },
      { timestampMs: 1_800, inboundRtp: [{ bytesReceived: 200, packetsReceived: 2 }] },
    ],
  };
  const dataTrace = [{
    direction: "server",
    timestampMs: 1_200,
    event: { type: "session.output_transcript.delta", delta: "次の質問です" },
  }, {
    direction: "server",
    timestampMs: 1_800,
    event: { type: "session.output_transcript.delta", delta: "続きです" },
  }];

  expect(gptLiveOutputPlaybackIdleStatus({
    dataTrace,
    audioTrace,
    boundaryAt: 1_000,
    idleMs: 500,
    nowMs: 2_100,
  })).toMatchObject({
    outputStarted: true,
    firstAudioAt: 1_500,
    lastAudioActivityAt: 1_800,
    settled: false,
  });
  expect(gptLiveOutputPlaybackIdleStatus({
    dataTrace,
    audioTrace,
    boundaryAt: 1_000,
    idleMs: 500,
    nowMs: 2_400,
  })).toMatchObject({
    settled: true,
    idleForMs: 600,
    settlementSource: "output_transcript_idle_with_post_output_rtp",
  });
});

test("GPT-Live first audio does not use RTP received before the Live response", () => {
  const timing = extractTiming({
    provider: "gpt_live",
    dataTrace: [],
    consoleTrace: [
      { timestampMs: 100, args: ["gpt_live_user_speech_end", { turn_id: "turn-1" }] },
      { timestampMs: 400, args: ["gpt_live_response_started", { turn_id: "turn-1" }] },
    ],
    transcript: { finalEntries: [] },
    assistant: { starts: [{ timestampMs: 400 }], backchannels: [] },
    stateObservedAt: 300,
    audioTrace: {
      events: [],
      stats: [
        { timestampMs: 300, inboundRtp: [{ packetsReceived: 10, bytesReceived: 1_000 }] },
        { timestampMs: 500, inboundRtp: [{ packetsReceived: 20, bytesReceived: 2_000 }] },
      ],
    },
  });

  expect(timing).toMatchObject({
    firstAudio: 500,
    firstAudioSource: "inbound_audio_rtp",
  });
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

test("question progression is required only when the previous target was already complete", () => {
  const expectations = { questionAdvance: "required" };
  expect(questionAdvanceRequiredForTurn({
    expectations,
    beforeQuestionId: "live-field-q1",
    beforeAnswer: { answerState: "CANDIDATE_PENDING", candidate: "回答" },
    beforeState: {},
  })).toBe(false);
  expect(questionAdvanceRequiredForTurn({
    expectations,
    beforeQuestionId: "live-field-q1",
    beforeAnswer: { answerState: "CONFIRMED", confirmed: "回答" },
    beforeState: {},
  })).toBe(true);
  expect(questionAdvanceRequiredForTurn({
    expectations,
    beforeQuestionId: "live-field-q1",
    beforeAnswer: { answerState: "CONFIRMED", confirmed: "回答" },
    beforeState: { currentTurnType: "PROBE" },
  })).toBe(false);
});

test("GPT-Live does not require a same-turn currentQuestionId transition", () => {
  expect(questionAdvanceRequiredForTurn({
    expectations: { questionAdvance: "required" },
    engine: "gpt_live",
    beforeQuestionId: "live-field-q1",
    beforeAnswer: { answerState: "CONFIRMED", confirmed: "回答" },
    beforeState: {},
  })).toBe(false);
});

test("GPT-Live verifies the next question at the following answer-target boundary", () => {
  const previousTurn = {
    turn: 2,
    interview: {
      questionLifecycle: {
        nextQuestionDetermination: {
          expectedNextQuestionId: "live-field-q2",
        },
      },
    },
  };
  expect(verifyLiveQuestionProgression({
    previousTurn,
    currentTurn: {
      interview: {
        questionLifecycle: {
          statePersistenceRequest: { questionId: "live-field-q2" },
        },
      },
    },
  })).toMatchObject({
    status: "VERIFIED",
    expectedQuestionId: "live-field-q2",
    observedQuestionId: "live-field-q2",
  });
  expect(verifyLiveQuestionProgression({
    previousTurn,
    currentTurn: {
      interview: {
        questionLifecycle: {
          statePersistenceRequest: { questionId: "live-field-q1" },
        },
      },
    },
  })).toMatchObject({
    status: "MISMATCH",
    expectedQuestionId: "live-field-q2",
    observedQuestionId: "live-field-q1",
  });
});
