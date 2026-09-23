import fs from "node:fs";
import path from "node:path";

export const SELF_HEALING_STATUS = Object.freeze({
  DISCOVERED: "DISCOVERED",
  LEVEL3_PENDING: "LEVEL3_PENDING",
  CONFIRMED_FAIL: "CONFIRMED_FAIL",
  REPAIRING: "REPAIRING",
  REPLAYING: "REPLAYING",
  FIX_VERIFIED: "FIX_VERIFIED",
  REGRESSION_FAILED: "REGRESSION_FAILED",
  MANUAL_REVIEW_REQUIRED: "MANUAL_REVIEW_REQUIRED",
});

export const DEFAULT_REPAIR_LIMITS = Object.freeze({
  maxRepairAttempts: 3,
  maxLevel3ReplaysPerFailure: 2,
  jevSessions: 10,
  jevTurnsPerSession: 8,
  jevCalls: 80,
});

export const REQUIRED_EVIDENCE_FILES = Object.freeze([
  "promotion-candidate.json",
  "comparison.json",
  "telemetry.json",
  "interview-state.json",
  "transcript.json",
  "timeline.json",
  "state-timeline.json",
  "console-log.json",
  "console-errors.json",
  "network-log.json",
  "network-errors.json",
  "data-channel-errors.json",
  "assistant-response.json",
  "audio-fixture-path.txt",
  "replay-command.txt",
  "playwright-trace-path.txt",
  "live-trace.json",
  "screenshot.png",
]);

const REQUIRED_ANALYSIS_FIELDS = Object.freeze([
  "symptom",
  "rootCauseHypothesis",
  "evidenceSupportingHypothesis",
  "relatedCode",
  "expectedFix",
  "regressionRisk",
]);

function hasText(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return null;
  }
}

function readText(filePath) {
  try {
    return fs.readFileSync(filePath, "utf8").trim();
  } catch {
    return "";
  }
}

function fileExists(filePath) {
  try {
    return fs.existsSync(filePath);
  } catch {
    return false;
  }
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function level2Failure(candidate, comparison) {
  const level2 = candidate?.level2 ?? {};
  return Boolean(
    comparison?.level2Failure
    || level2.expectationResult === "FAIL"
    || level2.hardGateStatus === "FAIL"
    || asArray(level2.hardGateCodes).length > 0
    || asArray(level2.expectationViolations).length > 0,
  );
}

function level3Failure(comparison, telemetry, networkErrors) {
  const failedTurn = asArray(telemetry?.turns).some((turn) => (
    turn.expectationResult === "FAIL"
    || turn.hardGate?.status === "FAIL"
    || turn.expectationViolations?.length > 0
    || turn.anomalies?.length > 0
  ));
  return Boolean(
    comparison?.level3Failure
    || failedTurn
    || asArray(telemetry?.errors).length > 0
    || asArray(networkErrors).length > 0
    || asArray(telemetry?.consoleErrors).length > 0
    || asArray(telemetry?.dataChannelErrors).length > 0,
  );
}

function readTraceAvailability(evidenceDir) {
  const reference = readText(path.join(evidenceDir, "playwright-trace-path.txt"));
  if (!reference) return { reference, available: false };
  const tracePath = reference.split(/\r?\n/, 1)[0].trim();
  return {
    reference: tracePath,
    available: fileExists(tracePath) || fileExists(path.resolve(evidenceDir, tracePath)),
  };
}

function numericSeed(...values) {
  for (const value of values) {
    if (Number.isFinite(Number(value))) return Number(value);
    if (hasText(value)) return value;
  }
  return null;
}

/**
 * Read and validate one Level 3 Evidence Package. This function does not
 * inspect source code and never marks a Level 2-only failure as repairable.
 */
export function loadEvidencePackage(evidenceDir) {
  const resolvedDir = path.resolve(evidenceDir);
  const missingFiles = REQUIRED_EVIDENCE_FILES.filter((fileName) => (
    !fileExists(path.join(resolvedDir, fileName))
  ));
  const candidate = readJson(path.join(resolvedDir, "promotion-candidate.json"));
  const comparison = readJson(path.join(resolvedDir, "comparison.json"));
  const telemetry = readJson(path.join(resolvedDir, "telemetry.json"));
  const state = readJson(path.join(resolvedDir, "interview-state.json"));
  const networkErrors = readJson(path.join(resolvedDir, "network-errors.json"));
  const trace = readTraceAvailability(resolvedDir);
  const seed = numericSeed(
    candidate?.seed,
    telemetry?.seed,
    comparison?.seed,
  );
  const level2Failed = level2Failure(candidate, comparison);
  const level3Failed = level3Failure(comparison, telemetry, networkErrors);
  const hasSeed = seed !== null;
  const complete = missingFiles.length === 0 && trace.available;
  const confirmedFail = complete && level2Failed && level3Failed && hasSeed;
  const status = confirmedFail
    ? SELF_HEALING_STATUS.CONFIRMED_FAIL
    : complete && !level2Failed
      ? SELF_HEALING_STATUS.MANUAL_REVIEW_REQUIRED
      : SELF_HEALING_STATUS.LEVEL3_PENDING;
  return {
    evidenceDir: resolvedDir,
    candidate,
    comparison,
    telemetry,
    state,
    networkErrors: asArray(networkErrors),
    trace,
    seed,
    behavior: candidate?.behavior ?? telemetry?.scenario ?? null,
    scenario: candidate?.scenario ?? telemetry?.scenario ?? null,
    level2Failed,
    level3Failed,
    hasSeed,
    missingFiles,
    complete,
    confirmedFail,
    status,
    replayCommand: readText(path.join(resolvedDir, "replay-command.txt")),
  };
}

export function formatFailureId(sequence) {
  const numeric = Math.max(1, Number.parseInt(String(sequence), 10) || 1);
  return `VF-${String(numeric).padStart(4, "0")}`;
}

function readHistory(historyPath) {
  const parsed = readJson(historyPath);
  if (Array.isArray(parsed)) return parsed;
  if (Array.isArray(parsed?.failures)) return parsed.failures;
  return [];
}

export function allocateFailureId(historyPath) {
  const used = readHistory(historyPath)
    .map((entry) => String(entry?.failureId ?? "").match(/^VF-(\d+)$/)?.[1])
    .filter(Boolean)
    .map((value) => Number(value));
  return formatFailureId((used.length ? Math.max(...used) : 0) + 1);
}

function writeHistory(historyPath, entries) {
  fs.mkdirSync(path.dirname(historyPath), { recursive: true });
  fs.writeFileSync(historyPath, JSON.stringify({ failures: entries }, null, 2), "utf8");
}

function mergeUsage(target, usage) {
  for (const key of ["gptLiveCalls", "jevCalls", "llmJudgeCalls"]) {
    const value = Number(usage?.[key] ?? 0);
    if (Number.isFinite(value) && value >= 0) target[key] = (target[key] ?? 0) + value;
  }
  return target;
}

function passed(result) {
  return result?.passed === true || result?.status === "PASS" || result?.status === "passed";
}

function analysisIsActionable(analysis) {
  return Boolean(
    analysis
    && analysis.hypothesisConfirmed === true
    && REQUIRED_ANALYSIS_FIELDS.every((field) => hasText(analysis[field])),
  );
}

function baseFailureRecord(failureId, evidence, status) {
  return {
    failureId,
    seed: evidence.seed,
    behavior: evidence.behavior,
    scenario: evidence.scenario,
    status,
    repairAttempts: 0,
    level3ReplayCount: 0,
    changedFiles: [],
    regressionResult: null,
    evidenceDir: evidence.evidenceDir,
    apiUsage: { gptLiveCalls: 0, jevCalls: 0, llmJudgeCalls: 0 },
    updatedAt: new Date().toISOString(),
  };
}

/**
 * Evidence-driven repair orchestrator.
 *
 * Callbacks are deliberately injected: this module never edits files, runs a
 * shell command, creates a commit, or pushes a branch on its own. A Codex
 * repair callback must return the six-item analysis before any repair is
 * accepted.
 */
export class EvidenceDrivenRepairLoop {
  constructor({
    historyPath,
    limits = {},
    repair = null,
    unit = null,
    integration = null,
    level2Replay = null,
    level3Replay = null,
    regression = null,
    jevExplore = null,
    now = () => new Date().toISOString(),
  } = {}) {
    this.historyPath = path.resolve(historyPath || path.join(process.cwd(), "test-results", "voice-self-healing", "failure-history.json"));
    this.limits = {
      ...DEFAULT_REPAIR_LIMITS,
      ...limits,
    };
    this.callbacks = { repair, unit, integration, level2Replay, level3Replay, regression, jevExplore };
    this.now = now;
  }

  #persist(record, extra = {}) {
    const entries = readHistory(this.historyPath);
    const next = { ...record, ...extra, updatedAt: this.now() };
    const index = entries.findIndex((entry) => entry.failureId === record.failureId);
    if (index >= 0) entries[index] = next;
    else entries.push(next);
    writeHistory(this.historyPath, entries);
    return next;
  }

  async run(evidenceDir) {
    const evidence = loadEvidencePackage(evidenceDir);
    const failureId = allocateFailureId(this.historyPath);
    let record = baseFailureRecord(failureId, evidence, SELF_HEALING_STATUS.DISCOVERED);
    record = this.#persist(record, {
      status: SELF_HEALING_STATUS.DISCOVERED,
      evidenceValidation: {
        complete: evidence.complete,
        missingFiles: evidence.missingFiles,
        traceAvailable: evidence.trace.available,
      },
    });

    if (!evidence.confirmedFail) {
      const status = evidence.level2Failed && !evidence.level3Failed
        ? SELF_HEALING_STATUS.LEVEL3_PENDING
        : SELF_HEALING_STATUS.MANUAL_REVIEW_REQUIRED;
      record = this.#persist(record, {
        status,
        reason: evidence.missingFiles.length > 0
          ? "Evidence Package is incomplete"
          : "Level 2 and Level 3 reproducibility gate was not satisfied",
      });
      return { record, evidence, status, reexploration: { status: "NOT_RUN" } };
    }

    record = this.#persist(record, { status: SELF_HEALING_STATUS.CONFIRMED_FAIL });
    if (typeof this.callbacks.repair !== "function") {
      record = this.#persist(record, {
        status: SELF_HEALING_STATUS.MANUAL_REVIEW_REQUIRED,
        reason: "Codex repair callback is not configured; no automatic edit was attempted",
      });
      return { record, evidence, status: record.status, reexploration: { status: "NOT_RUN" } };
    }

    for (let attempt = 1; attempt <= this.limits.maxRepairAttempts; attempt += 1) {
      record = this.#persist(record, {
        status: SELF_HEALING_STATUS.REPAIRING,
        repairAttempts: attempt,
      });
      let analysis;
      try {
        analysis = await this.callbacks.repair({
          failureId,
          evidence,
          previousRecord: record,
          attempt,
          limits: this.limits,
        });
      } catch (error) {
        record = this.#persist(record, {
          reason: `repair callback failed: ${error instanceof Error ? error.message : String(error)}`,
        });
        continue;
      }
      if (!analysisIsActionable(analysis)) {
        record = this.#persist(record, {
          status: SELF_HEALING_STATUS.MANUAL_REVIEW_REQUIRED,
          reason: "Required root-cause analysis was not confirmed by Evidence and code inspection",
          analysis: analysis ?? null,
        });
        return { record, evidence, status: record.status, reexploration: { status: "NOT_RUN" } };
      }
      record = this.#persist(record, {
        analysis,
        changedFiles: [...new Set([...(record.changedFiles ?? []), ...(analysis.changedFiles ?? [])])],
      });

      const stages = [
        ["unit", this.callbacks.unit],
        ["integration", this.callbacks.integration],
        ["level2Replay", this.callbacks.level2Replay],
      ];
      let stageFailed = false;
      for (const [stage, callback] of stages) {
        if (typeof callback !== "function") {
          stageFailed = true;
          record = this.#persist(record, {
            status: SELF_HEALING_STATUS.MANUAL_REVIEW_REQUIRED,
            reason: `${stage} verification callback is not configured`,
          });
          break;
        }
        record = this.#persist(record, {
          status: stage === "level2Replay"
            ? SELF_HEALING_STATUS.REPLAYING
            : SELF_HEALING_STATUS.REPAIRING,
        });
        let result;
        try {
          result = await callback({
            failureId,
            seed: evidence.seed,
            behavior: evidence.behavior,
            scenario: evidence.scenario,
            evidence,
            analysis,
            attempt,
          });
        } catch (error) {
          result = { passed: false, error: error instanceof Error ? error.message : String(error) };
        }
        record = this.#persist(record, {
          [`${stage}Result`]: result,
          apiUsage: mergeUsage(record.apiUsage, result?.apiUsage),
        });
        if (!passed(result)) {
          stageFailed = true;
          break;
        }
      }
      if (stageFailed) continue;

      if (typeof this.callbacks.level3Replay !== "function") {
        record = this.#persist(record, {
          status: SELF_HEALING_STATUS.MANUAL_REVIEW_REQUIRED,
          reason: "Level 3 replay callback is not configured",
        });
        return { record, evidence, status: record.status, reexploration: { status: "NOT_RUN" } };
      }
      if (record.level3ReplayCount >= this.limits.maxLevel3ReplaysPerFailure) {
        record = this.#persist(record, {
          status: SELF_HEALING_STATUS.MANUAL_REVIEW_REQUIRED,
          reason: "maxLevel3ReplaysPerFailure reached",
        });
        return { record, evidence, status: record.status, reexploration: { status: "NOT_RUN" } };
      }
      record = this.#persist(record, {
        status: SELF_HEALING_STATUS.REPLAYING,
        level3ReplayCount: record.level3ReplayCount + 1,
      });
      let level3Result;
      try {
        level3Result = await this.callbacks.level3Replay({
          failureId,
          seed: evidence.seed,
          behavior: evidence.behavior,
          scenario: evidence.scenario,
          wavPath: readText(path.join(evidence.evidenceDir, "audio-fixture-path.txt")),
          evidence,
          analysis,
          attempt,
          replayIndex: record.level3ReplayCount,
        });
      } catch (error) {
        level3Result = { passed: false, error: error instanceof Error ? error.message : String(error) };
      }
      record = this.#persist(record, {
        level3Result,
        apiUsage: mergeUsage(record.apiUsage, level3Result?.apiUsage),
      });
      if (!passed(level3Result)) continue;

      if (typeof this.callbacks.regression !== "function") {
        record = this.#persist(record, {
          status: SELF_HEALING_STATUS.MANUAL_REVIEW_REQUIRED,
          reason: "full Level 1/2 regression callback is not configured",
        });
        return { record, evidence, status: record.status, reexploration: { status: "NOT_RUN" } };
      }
      let regressionResult;
      try {
        regressionResult = await this.callbacks.regression({
          failureId,
          seed: evidence.seed,
          evidence,
          analysis,
          attempt,
        });
      } catch (error) {
        regressionResult = { passed: false, error: error instanceof Error ? error.message : String(error) };
      }
      record = this.#persist(record, {
        regressionResult,
        apiUsage: mergeUsage(record.apiUsage, regressionResult?.apiUsage),
      });
      if (!passed(regressionResult)) {
        record = this.#persist(record, {
          status: SELF_HEALING_STATUS.REGRESSION_FAILED,
          reason: "Level 1/2 regression failed; Jev exploration was not restarted",
        });
        return { record, evidence, status: record.status, reexploration: { status: "NOT_RUN" } };
      }

      record = this.#persist(record, {
        status: SELF_HEALING_STATUS.FIX_VERIFIED,
        regressionResult: "PASS",
      });
      if (typeof this.callbacks.jevExplore !== "function") {
        return {
          record,
          evidence,
          status: record.status,
          reexploration: {
            status: "NOT_RUN",
            reason: "Jev exploration callback is not configured",
          },
        };
      }
      const reexploration = await this.callbacks.jevExplore({
        failureId,
        seed: evidence.seed,
        limits: {
          maxSessions: this.limits.jevSessions,
          maxTurnsPerSession: this.limits.jevTurnsPerSession,
          maxJevCallsTotal: this.limits.jevCalls,
          maxRetries: 0,
        },
      });
      record = this.#persist(record, {
        reexploration,
        apiUsage: mergeUsage(record.apiUsage, reexploration?.apiUsage),
      });
      if (!passed(reexploration)) {
        const newFailureId = allocateFailureId(this.historyPath);
        const newEvidence = {
          seed: reexploration?.seed ?? evidence.seed,
          behavior: reexploration?.behavior ?? reexploration?.scenario ?? "UNKNOWN",
          scenario: reexploration?.scenario ?? evidence.scenario,
          evidenceDir: reexploration?.evidenceDir ?? "",
        };
        const newFailure = baseFailureRecord(
          newFailureId,
          newEvidence,
          SELF_HEALING_STATUS.DISCOVERED,
        );
        this.#persist(newFailure, {
          parentFailureId: failureId,
          reason: "Jev re-exploration found a new failure; a new Evidence Package is required",
        });
        record = this.#persist(record, {
          status: SELF_HEALING_STATUS.FIX_VERIFIED,
          newFailureId,
          reason: "Original fix verified; new Jev failure was assigned a separate Failure ID",
        });
        return {
          record,
          evidence,
          status: record.status,
          newFailure: { failureId: newFailureId, ...newEvidence },
          reexploration,
        };
      }
      return { record, evidence, status: record.status, reexploration };
    }

    record = this.#persist(record, {
      status: SELF_HEALING_STATUS.MANUAL_REVIEW_REQUIRED,
      reason: "maxRepairAttempts reached",
    });
    return { record, evidence, status: record.status, reexploration: { status: "NOT_RUN" } };
  }
}
