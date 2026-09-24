import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { expect, test } from "@playwright/test";

import { generateVoiceReportForRun } from "./voiceReportGenerator.mjs";

function writeJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(value, null, 2), "utf8");
}

test("generates a self-contained report that renders from file://", async ({ page }) => {
  const runDir = fs.mkdtempSync(path.join(os.tmpdir(), "kikiori-voice-report-"));
  const evidenceDir = path.join(runDir, "artifacts", "session-s2", "evidence");
  try {
    writeJson(path.join(runDir, "summary.json"), {
      runId: "report-fixture-1",
      startedAt: "2026-09-24T00:00:00.000Z",
      sessions: [
        {
          sessionId: "s1",
          index: 1,
          level: 2,
          status: "PASS",
          turns: [
            {
              turn: 1,
              behavior: "NORMAL",
              status: "PASS",
              transcription: { finalText: "プロジェクトリーダーです" },
              interview: {
                questionId: "q-001",
                confirmed: "プロジェクトリーダーです",
                stateVersionBefore: 1,
                stateVersionAfter: 2,
              },
              response: {
                speechEndToTranscriptFinalMs: 120,
                transcriptFinalToAssistantStartMs: 480,
                speechEndToAssistantStartMs: 600,
                firstAudioLatencyMs: 600,
              },
            },
          ],
        },
        {
          sessionId: "s2",
          index: 2,
          level: 3,
          status: "FAIL",
          turns: [
            {
              turn: 1,
              behavior: "SELF_CORRECTION",
              status: "FAIL",
              aiQuestion: "現在の役割を教えてください",
              userTranscript: "プロジェクトマネージャー、いや、プロジェクトリーダーです",
              aiResponse: "もう少し教えてください",
              expected: { confirmed: true },
              actual: { confirmed: null },
              interviewState: { stateVersion: 3 },
              anomalies: ["SELF_CORRECTION_FAILED"],
              timeline: [{ event: "transcript_final", timestampMs: 1000 }],
            },
          ],
        },
      ],
    });
    writeJson(path.join(runDir, "metrics.json"), {
      conversationQuality: { coherent: [true], correctionHandled: [false] },
    });
    writeJson(path.join(evidenceDir, "telemetry.json"), { sessionId: "s2" });
    writeJson(path.join(evidenceDir, "interview-state.json"), { stateVersion: 3 });
    fs.writeFileSync(path.join(evidenceDir, "screenshot.png"), "fixture", "utf8");
    fs.writeFileSync(path.join(evidenceDir, "trace.zip"), "fixture", "utf8");

    const reportPath = generateVoiceReportForRun({
      runDir,
      runId: "report-fixture-1",
      testStatus: "failed",
    });
    expect(fs.existsSync(reportPath)).toBe(true);
    expect(fs.existsSync(path.join(runDir, "sessions.jsonl"))).toBe(true);
    expect(fs.existsSync(path.join(runDir, "failures.json"))).toBe(true);

    const html = fs.readFileSync(reportPath, "utf8");
    expect(html).toContain("KIKIORI 音声インタビューテストレポート");
    expect(html).toContain("失敗の詳細");
    expect(html).toContain("自己訂正を反映できない");
    expect(html).toContain("artifacts/session-s2/evidence/screenshot.png");
    expect(html).not.toMatch(/<script\s+src=/i);

    await page.goto(pathToFileURL(reportPath).href);
    await expect(page.getByRole("heading", { name: "KIKIORI 音声インタビューテストレポート" })).toBeVisible();
    await expect(page.getByText("自己訂正を反映できない").first()).toBeVisible();
    await expect(page.getByText("Level 3（実音声API）").first()).toBeVisible();
  } finally {
    fs.rmSync(runDir, { recursive: true, force: true });
  }
});

test("joins one telemetry stream to supervisor sessions by sessionIndex", () => {
  const runDir = fs.mkdtempSync(path.join(os.tmpdir(), "kikiori-voice-report-"));
  try {
    writeJson(path.join(runDir, "exploration-summary.json"), {
      runId: "multi-session-fixture",
      sessions: [
        { sessionId: "session-1", index: 1, status: "PASS", turns: [] },
        { sessionId: "session-2", index: 2, status: "PASS", turns: [] },
      ],
    });
    fs.writeFileSync(path.join(runDir, "telemetry.jsonl"), [
      JSON.stringify({
        sessionId: "telemetry-container",
        sessionIndex: 1,
        turn: 1,
        behavior: "NORMAL",
        transcription: { finalText: "一つ目" },
        hardGate: { status: "PASS" },
      }),
      JSON.stringify({
        sessionId: "telemetry-container",
        sessionIndex: 2,
        turn: 1,
        behavior: "HESITATE",
        transcription: { finalText: "二つ目" },
        hardGate: { status: "PASS" },
      }),
      "",
    ].join("\n"), "utf8");

    const reportPath = generateVoiceReportForRun({ runDir, runId: "multi-session-fixture" });
    const sessions = fs.readFileSync(path.join(runDir, "sessions.jsonl"), "utf8")
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line));
    expect(sessions).toHaveLength(2);
    expect(sessions.map((session) => session.sessionId)).toEqual(["session-1", "session-2"]);
    expect(sessions.map((session) => session.turns.length)).toEqual([1, 1]);
    expect(fs.existsSync(reportPath)).toBe(true);
  } finally {
    fs.rmSync(runDir, { recursive: true, force: true });
  }
});
