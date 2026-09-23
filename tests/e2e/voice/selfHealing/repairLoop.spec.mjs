import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { test, expect } from "@playwright/test";
import {
  DEFAULT_REPAIR_LIMITS,
  EvidenceDrivenRepairLoop,
  SELF_HEALING_STATUS,
  allocateFailureId,
  formatFailureId,
  loadEvidencePackage,
} from "./repairLoop.mjs";

function makeEvidence({ level2Failure = true, level3Failure = true } = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "kikiori-evidence-"));
  const tracePath = path.join(directory, "trace.zip");
  fs.writeFileSync(tracePath, "trace");
  const jsonFiles = {
    "promotion-candidate.json": {
      seed: 38291,
      behavior: "SELF_CORRECT",
      level2: {
        expectationResult: level2Failure ? "FAIL" : "PASS",
        hardGateStatus: level2Failure ? "FAIL" : "PASS",
        hardGateCodes: level2Failure ? ["QUESTION_NOT_ADVANCED"] : [],
        expectationViolations: level2Failure ? ["QUESTION_NOT_ADVANCED"] : [],
      },
    },
    "comparison.json": { level2Failure, level3Failure },
    "telemetry.json": {
      seed: 38291,
      scenario: "SELF_CORRECT",
      errors: level3Failure ? [{ message: "simulated" }] : [],
      consoleErrors: [],
      dataChannelErrors: [],
      turns: level3Failure
        ? [{ expectationResult: "FAIL", hardGate: { status: "FAIL" }, anomalies: [] }]
        : [{ expectationResult: "PASS", hardGate: { status: "PASS" }, anomalies: [] }],
    },
    "interview-state.json": { interviewState: { stateVersion: 12, currentQuestionId: "q-2" } },
    "network-errors.json": [],
  };
  for (const fileName of [
    "transcript.json",
    "timeline.json",
    "state-timeline.json",
    "console-log.json",
    "console-errors.json",
    "network-log.json",
    "data-channel-errors.json",
    "assistant-response.json",
    "live-trace.json",
  ]) jsonFiles[fileName] = [];
  for (const [fileName, value] of Object.entries(jsonFiles)) {
    fs.writeFileSync(path.join(directory, fileName), JSON.stringify(value));
  }
  fs.writeFileSync(path.join(directory, "audio-fixture-path.txt"), "/tmp/fixture.wav\n");
  fs.writeFileSync(path.join(directory, "replay-command.txt"), "replay\n");
  fs.writeFileSync(path.join(directory, "playwright-trace-path.txt"), tracePath + "\n");
  fs.writeFileSync(path.join(directory, "screenshot.png"), "png");
  return directory;
}

function analysis(changedFiles = ["app/web/src/example.ts"]) {
  return {
    hypothesisConfirmed: true,
    symptom: "assistant did not advance",
    rootCauseHypothesis: "state update was committed before the final transcript",
    evidenceSupportingHypothesis: "timeline shows commit before transcript final",
    relatedCode: "app/web/src/example.ts",
    expectedFix: "wait for final transcript before commit",
    regressionRisk: "turn ordering and duplicate response handling",
    changedFiles,
  };
}

function allPassingCallbacks(order, { regression = true, reexploration = true } = {}) {
  return {
    repair: async () => {
      order.push("repair");
      return analysis();
    },
    unit: async () => { order.push("unit"); return { passed: true }; },
    integration: async () => { order.push("integration"); return { passed: true }; },
    level2Replay: async () => { order.push("level2"); return { passed: true }; },
    level3Replay: async () => { order.push("level3"); return { passed: true, apiUsage: { gptLiveCalls: 1 } }; },
    regression: async () => { order.push("regression"); return { passed: regression }; },
    jevExplore: reexploration
      ? async ({ limits }) => {
          order.push("jev");
          expect(limits).toEqual({ maxSessions: 10, maxTurnsPerSession: 8, maxJevCallsTotal: 80, maxRetries: 0 });
          return { passed: true, apiUsage: { jevCalls: 80 } };
        }
      : null,
  };
}

test("Level 2-only failure is not repairable before Level 3 confirmation", async () => {
  const evidenceDir = makeEvidence({ level2Failure: true, level3Failure: false });
  const historyPath = path.join(evidenceDir, "history.json");
  let repairCalled = false;
  const loop = new EvidenceDrivenRepairLoop({
    historyPath,
    repair: async () => { repairCalled = true; return analysis(); },
  });
  const result = await loop.run(evidenceDir);
  expect(result.status).toBe(SELF_HEALING_STATUS.LEVEL3_PENDING);
  expect(repairCalled).toBe(false);
  expect(result.record.failureId).toBe("VF-0001");
});

test("incomplete Evidence Package cannot become CONFIRMED_FAIL", async () => {
  const evidenceDir = makeEvidence();
  fs.rmSync(path.join(evidenceDir, "screenshot.png"));
  const evidence = loadEvidencePackage(evidenceDir);
  expect(evidence.confirmedFail).toBe(false);
  expect(evidence.missingFiles).toContain("screenshot.png");
});

test("successful repair follows the required order and restarts Jev only after regression", async () => {
  const evidenceDir = makeEvidence();
  const order = [];
  const historyPath = path.join(evidenceDir, "history.json");
  const loop = new EvidenceDrivenRepairLoop({
    historyPath,
    ...allPassingCallbacks(order),
  });
  const result = await loop.run(evidenceDir);
  expect(result.status).toBe(SELF_HEALING_STATUS.FIX_VERIFIED);
  expect(order).toEqual(["repair", "unit", "integration", "level2", "level3", "regression", "jev"]);
  expect(result.record.repairAttempts).toBe(1);
  expect(result.record.level3ReplayCount).toBe(1);
  expect(result.record.changedFiles).toEqual(["app/web/src/example.ts"]);
  expect(result.record.apiUsage).toEqual({ gptLiveCalls: 1, jevCalls: 80, llmJudgeCalls: 0 });
});

test("Level 3 replay is capped and unresolved failures require manual review", async () => {
  const evidenceDir = makeEvidence();
  let repairCalls = 0;
  let level3Calls = 0;
  const loop = new EvidenceDrivenRepairLoop({
    historyPath: path.join(evidenceDir, "history.json"),
    repair: async () => { repairCalls += 1; return analysis(); },
    unit: async () => ({ passed: true }),
    integration: async () => ({ passed: true }),
    level2Replay: async () => ({ passed: true }),
    level3Replay: async () => { level3Calls += 1; return { passed: false }; },
    regression: async () => ({ passed: true }),
  });
  const result = await loop.run(evidenceDir);
  expect(result.status).toBe(SELF_HEALING_STATUS.MANUAL_REVIEW_REQUIRED);
  expect(repairCalls).toBe(DEFAULT_REPAIR_LIMITS.maxRepairAttempts);
  expect(level3Calls).toBe(DEFAULT_REPAIR_LIMITS.maxLevel3ReplaysPerFailure);
  expect(result.record.level3ReplayCount).toBe(2);
});

test("regression failure stops before Jev restart", async () => {
  const evidenceDir = makeEvidence();
  const order = [];
  const loop = new EvidenceDrivenRepairLoop({
    historyPath: path.join(evidenceDir, "history.json"),
    ...allPassingCallbacks(order, { regression: false }),
  });
  const result = await loop.run(evidenceDir);
  expect(result.status).toBe(SELF_HEALING_STATUS.REGRESSION_FAILED);
  expect(order).not.toContain("jev");
});

test("a new Jev failure receives a separate Failure ID", async () => {
  const evidenceDir = makeEvidence();
  const callbacks = allPassingCallbacks([]);
  callbacks.jevExplore = async () => ({
    passed: false,
    seed: 38291,
    behavior: "AMBIGUOUS",
    scenario: "NORMAL",
    evidenceDir: "/tmp/new-evidence",
  });
  const loop = new EvidenceDrivenRepairLoop({
    historyPath: path.join(evidenceDir, "history.json"),
    ...callbacks,
  });
  const result = await loop.run(evidenceDir);
  expect(result.status).toBe(SELF_HEALING_STATUS.FIX_VERIFIED);
  expect(result.newFailure.failureId).toBe("VF-0002");
  const history = JSON.parse(fs.readFileSync(path.join(evidenceDir, "history.json"), "utf8"));
  expect(history.failures.map((entry) => entry.failureId)).toEqual(["VF-0001", "VF-0002"]);
});

test("unconfirmed code hypothesis stops without editing", async () => {
  const evidenceDir = makeEvidence();
  const order = [];
  const loop = new EvidenceDrivenRepairLoop({
    historyPath: path.join(evidenceDir, "history.json"),
    repair: async () => {
      order.push("repair");
      return { hypothesisConfirmed: false };
    },
  });
  const result = await loop.run(evidenceDir);
  expect(result.status).toBe(SELF_HEALING_STATUS.MANUAL_REVIEW_REQUIRED);
  expect(order).toEqual(["repair"]);
  expect(result.record.changedFiles).toEqual([]);
});

test("Failure IDs are monotonic and formatted", () => {
  expect(formatFailureId(1)).toBe("VF-0001");
  expect(formatFailureId(42)).toBe("VF-0042");
  const historyPath = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "kikiori-failure-id-")), "history.json");
  fs.writeFileSync(historyPath, JSON.stringify({ failures: [{ failureId: "VF-0007" }] }));
  expect(allocateFailureId(historyPath)).toBe("VF-0008");
});
