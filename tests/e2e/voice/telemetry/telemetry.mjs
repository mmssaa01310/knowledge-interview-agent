import fs from "node:fs";
import path from "node:path";

export class VoiceTelemetry {
  constructor({
    outputDir,
    sessionId,
    scenario,
    seed,
    costGuard = null,
    audioInputMode = "unknown",
    transcriptSource = "unknown",
    liveApiUsed = false,
    level = null,
    audioFixturePath = null,
    promotionCandidate = null,
  }) {
    this.outputDir = outputDir;
    this.sessionId = sessionId;
    this.scenario = scenario;
    this.seed = seed;
    this.costGuard = costGuard;
    this.audioInputMode = audioInputMode;
    this.transcriptSource = transcriptSource;
    this.liveApiUsed = liveApiUsed;
    this.level = level;
    this.audioFixturePath = audioFixturePath;
    this.promotionCandidate = promotionCandidate;
    this.timeline = [];
    this.turns = [];
    this.transcripts = [];
    this.errors = [];
    this.consoleErrors = [];
    this.consoleLogs = [];
    this.networkLog = [];
    this.dataChannelErrors = [];
    fs.mkdirSync(outputDir, { recursive: true });
    this.jsonlPath = path.join(outputDir, "telemetry.jsonl");
    fs.writeFileSync(this.jsonlPath, "", "utf8");
  }

  event(name, detail = {}) {
    return this.eventAt(name, Date.now(), detail);
  }

  eventAt(name, timestampMs, detail = {}) {
    const item = { timestampMs, event: name, ...detail };
    this.timeline.push(item);
    fs.appendFileSync(this.jsonlPath, JSON.stringify(item) + "\n", "utf8");
    return item;
  }

  setLiveTrace(entries) {
    this.liveTrace = Array.isArray(entries) ? entries : [];
  }

  error(error) {
    const item = { message: error instanceof Error ? error.message : String(error) };
    this.errors.push(item);
    this.event("error", item);
  }

  consoleError(message) {
    const item = { message: String(message) };
    this.consoleErrors.push(item);
    this.event("console_error", item);
  }

  consoleLog(entry) {
    const item = {
      type: entry?.type ?? "log",
      text: String(entry?.text ?? ""),
      location: entry?.location ?? null,
      timestampMs: entry?.timestampMs ?? Date.now(),
    };
    this.consoleLogs.push(item);
    this.event("console_log", item);
    return item;
  }

  networkEvent(entry) {
    const item = {
      direction: entry?.direction ?? "unknown",
      method: entry?.method ?? null,
      url: entry?.url ?? null,
      resourceType: entry?.resourceType ?? null,
      status: entry?.status ?? null,
      failure: entry?.failure ?? null,
      expectedCancellation: Boolean(entry?.expectedCancellation),
      timestampMs: entry?.timestampMs ?? Date.now(),
    };
    this.networkLog.push(item);
    this.event("network", item);
    return item;
  }

  dataChannelError(entry) {
    const item = {
      type: entry?.type ?? "data_channel_error",
      message: String(entry?.message ?? ""),
      timestampMs: entry?.timestampMs ?? Date.now(),
    };
    this.dataChannelErrors.push(item);
    this.event("data_channel_error", item);
    return item;
  }

  transcriptFinal(entry) {
    const item = {
      turn: entry?.turn ?? null,
      source: entry?.source ?? this.transcriptSource,
      finalText: String(entry?.finalText ?? ""),
      sourceTimestampMs: entry?.sourceTimestampMs ?? null,
      timestampMs: entry?.timestampMs ?? Date.now(),
    };
    this.transcripts.push(item);
    return item;
  }

  recordTurn(turn) {
    const item = {
      sessionId: this.sessionId,
      sessionIndex: turn.sessionIndex ?? null,
      turn: turn.turn,
      scenario: this.scenario,
      seed: this.seed,
      audio: {
        startMs: turn.audio?.startMs ?? null,
        endMs: turn.audio?.endMs ?? null,
        durationMs: turn.audio?.durationMs ?? null,
      },
      transcription: {
        partialCount: turn.transcription?.partialCount ?? 0,
        finalText: turn.transcription?.finalText ?? "",
        source: turn.transcription?.source ?? this.transcriptSource,
      },
      behavior: turn.behavior ?? null,
      expectations: turn.expectations ?? null,
      observed: turn.observed ?? null,
      expectedConditions: turn.expectedConditions ?? [],
      expectationResult: turn.expectationResult ?? null,
      expectationViolations: turn.expectationViolations ?? [],
      interview: {
        questionId: turn.interview?.questionId ?? null,
        candidate: turn.interview?.candidate ?? null,
        confirmed: turn.interview?.confirmed ?? null,
        answerState: turn.interview?.answerState ?? null,
        stateVersionBefore: turn.interview?.stateVersionBefore ?? null,
        stateVersionAfter: turn.interview?.stateVersionAfter ?? null,
        currentQuestionBefore: turn.interview?.currentQuestionBefore ?? null,
        currentQuestionAfter: turn.interview?.currentQuestionAfter ?? null,
        nextQuestionId: turn.interview?.nextQuestionId ?? null,
        nextQuestionText: turn.interview?.nextQuestionText ?? null,
      },
      response: {
        assistantSpeechStarted: turn.response?.assistantSpeechStarted ?? false,
        assistantSpeechEnded: turn.response?.assistantSpeechEnded ?? false,
        assistantTranscript: turn.response?.assistantTranscript ?? null,
        simulatedTurnLatencyMs: turn.response?.simulatedTurnLatencyMs ?? null,
        firstAudioLatencyMs: turn.response?.firstAudioLatencyMs ?? null,
        speechEndToTranscriptFinalMs: turn.response?.speechEndToTranscriptFinalMs ?? null,
        transcriptFinalToAssistantStartMs: turn.response?.transcriptFinalToAssistantStartMs ?? null,
        speechEndToAssistantStartMs: turn.response?.speechEndToAssistantStartMs ?? null,
      },
      realTiming: turn.realTiming ?? null,
      hardGate: {
        status: turn.hardGate?.status ?? null,
        codes: turn.hardGate?.codes ?? [],
        shouldCallJev: turn.hardGate?.shouldCallJev ?? false,
      },
      jev: {
        called: turn.jev?.called ?? false,
        sessionAction: turn.jev?.sessionAction ?? null,
        nextUserBehavior: turn.jev?.nextUserBehavior ?? null,
        confidence: turn.jev?.confidence ?? null,
        cacheHit: turn.jev?.cacheHit ?? false,
        callCount: turn.jev?.callCount ?? null,
        provider: turn.jev?.provider ?? null,
        model: turn.jev?.model ?? null,
        usage: turn.jev?.usage ?? null,
      },
      supervisorInput: turn.supervisorInput ?? null,
      timeline: turn.timeline ?? [],
      errors: turn.errors ?? [],
      anomalies: turn.anomalies ?? [],
    };
    this.turns.push(item);
    fs.appendFileSync(this.jsonlPath, JSON.stringify(item) + "\n", "utf8");
    return item;
  }

  snapshot() {
    return {
      sessionId: this.sessionId,
      scenario: this.scenario,
      seed: this.seed,
      audioInputMode: this.audioInputMode,
      transcriptSource: this.transcriptSource,
      liveApiUsed: this.liveApiUsed,
      level: this.level,
      audioFixturePath: this.audioFixturePath,
      promotionCandidate: this.promotionCandidate,
      turns: this.turns,
      transcripts: this.transcripts,
      timeline: this.timeline,
      errors: this.errors,
      consoleErrors: this.consoleErrors,
      consoleLogs: this.consoleLogs,
      networkLog: this.networkLog,
      dataChannelErrors: this.dataChannelErrors,
      liveTrace: this.liveTrace ?? [],
      costGuard: this.costGuard?.snapshot() ?? null,
      jsonlPath: this.jsonlPath,
    };
  }
}

export async function writeEvidencePackage({
  testInfo,
  page,
  telemetry,
  state,
  networkErrors = [],
  audioFixturePath = telemetry?.audioFixturePath ?? null,
  comparison = null,
  assistantResponse = null,
  replayCommand = null,
  candidate = telemetry?.promotionCandidate ?? null,
  evidenceDir: requestedEvidenceDir = null,
}) {
  const evidenceDir = requestedEvidenceDir || testInfo.outputPath("evidence");
  fs.mkdirSync(evidenceDir, { recursive: true });
  const snapshot = telemetry.snapshot();
  fs.writeFileSync(path.join(evidenceDir, "telemetry.json"), JSON.stringify(snapshot, null, 2));
  fs.writeFileSync(path.join(evidenceDir, "interview-state.json"), JSON.stringify(state ?? null, null, 2));
  fs.writeFileSync(path.join(evidenceDir, "promotion-candidate.json"), JSON.stringify(candidate, null, 2));
  fs.writeFileSync(path.join(evidenceDir, "comparison.json"), JSON.stringify(comparison, null, 2));
  fs.writeFileSync(path.join(evidenceDir, "assistant-response.json"), JSON.stringify(assistantResponse, null, 2));
  fs.writeFileSync(path.join(evidenceDir, "audio-fixture-path.txt"), String(audioFixturePath ?? "") + "\n");
  fs.writeFileSync(path.join(evidenceDir, "network-errors.json"), JSON.stringify(networkErrors, null, 2));
  fs.writeFileSync(path.join(evidenceDir, "network-log.json"), JSON.stringify(snapshot.networkLog, null, 2));
  fs.writeFileSync(path.join(evidenceDir, "console-errors.json"), JSON.stringify(snapshot.consoleErrors, null, 2));
  fs.writeFileSync(path.join(evidenceDir, "console-log.json"), JSON.stringify(snapshot.consoleLogs, null, 2));
  fs.writeFileSync(
    path.join(evidenceDir, "data-channel-errors.json"),
    JSON.stringify(snapshot.dataChannelErrors, null, 2),
  );
  fs.writeFileSync(
    path.join(evidenceDir, "transcript.json"),
    JSON.stringify(
      snapshot.transcripts.length > 0
        ? snapshot.transcripts
        : snapshot.turns.map((turn) => ({
            turn: turn.turn,
            source: turn.transcription.source,
            finalText: turn.transcription.finalText,
          })),
      null,
      2,
    ),
  );
  fs.writeFileSync(path.join(evidenceDir, "timeline.json"), JSON.stringify(snapshot.timeline, null, 2));
  fs.writeFileSync(path.join(evidenceDir, "live-trace.json"), JSON.stringify(snapshot.liveTrace ?? [], null, 2));
  fs.writeFileSync(
    path.join(evidenceDir, "state-timeline.json"),
    JSON.stringify(snapshot.timeline.filter((item) => /state|question|answer/i.test(item.event)), null, 2),
  );
  fs.writeFileSync(
    path.join(evidenceDir, "playwright-trace-path.txt"),
    path.join(testInfo.outputDir, "trace.zip") + "\n",
  );
  fs.writeFileSync(
    path.join(evidenceDir, "replay-command.txt"),
    (replayCommand || ("pnpm voice-test -- --replay " + telemetry.seed)) + "\n",
  );
  if (page && !page.isClosed()) {
    await page.screenshot({
      path: path.join(evidenceDir, "screenshot.png"),
      fullPage: true,
    }).catch(() => undefined);
  }
  return evidenceDir;
}
