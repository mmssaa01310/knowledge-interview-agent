import fs from "node:fs";
import path from "node:path";

export class VoiceTelemetry {
  constructor({ outputDir, sessionId, scenario, seed, costGuard = null }) {
    this.outputDir = outputDir;
    this.sessionId = sessionId;
    this.scenario = scenario;
    this.seed = seed;
    this.costGuard = costGuard;
    this.timeline = [];
    this.turns = [];
    this.errors = [];
    this.consoleErrors = [];
    fs.mkdirSync(outputDir, { recursive: true });
    this.jsonlPath = path.join(outputDir, "telemetry.jsonl");
    fs.writeFileSync(this.jsonlPath, "", "utf8");
  }

  event(name, detail = {}) {
    const item = { timestampMs: Date.now(), event: name, ...detail };
    this.timeline.push(item);
    fs.appendFileSync(this.jsonlPath, JSON.stringify(item) + "\n", "utf8");
    return item;
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

  recordTurn(turn) {
    const item = {
      sessionId: this.sessionId,
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
      },
      interview: {
        questionId: turn.interview?.questionId ?? null,
        candidate: turn.interview?.candidate ?? null,
        confirmed: turn.interview?.confirmed ?? null,
        stateVersionBefore: turn.interview?.stateVersionBefore ?? null,
        stateVersionAfter: turn.interview?.stateVersionAfter ?? null,
      },
      response: { firstAudioLatencyMs: turn.response?.firstAudioLatencyMs ?? null },
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
      turns: this.turns,
      timeline: this.timeline,
      errors: this.errors,
      consoleErrors: this.consoleErrors,
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
}) {
  const evidenceDir = testInfo.outputPath("evidence");
  fs.mkdirSync(evidenceDir, { recursive: true });
  const snapshot = telemetry.snapshot();
  fs.writeFileSync(path.join(evidenceDir, "telemetry.json"), JSON.stringify(snapshot, null, 2));
  fs.writeFileSync(path.join(evidenceDir, "interview-state.json"), JSON.stringify(state ?? null, null, 2));
  fs.writeFileSync(path.join(evidenceDir, "network-errors.json"), JSON.stringify(networkErrors, null, 2));
  fs.writeFileSync(path.join(evidenceDir, "console-errors.json"), JSON.stringify(snapshot.consoleErrors, null, 2));
  fs.writeFileSync(path.join(evidenceDir, "timeline.json"), JSON.stringify(snapshot.timeline, null, 2));
  fs.writeFileSync(
    path.join(evidenceDir, "playwright-trace-path.txt"),
    path.join(testInfo.outputDir, "trace.zip") + "\n",
  );
  fs.writeFileSync(
    path.join(evidenceDir, "replay-command.txt"),
    "pnpm voice-test -- --replay " + telemetry.seed + "\n",
  );
  if (page && !page.isClosed()) {
    await page.screenshot({
      path: path.join(evidenceDir, "screenshot.png"),
      fullPage: true,
    }).catch(() => undefined);
  }
  return evidenceDir;
}
