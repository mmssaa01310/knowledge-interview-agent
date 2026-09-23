import fs from "node:fs";
import path from "node:path";

import {
  JEV_SESSION_ACTIONS,
  JEV_USER_BEHAVIORS,
} from "./jevSupervisor.mjs";
import { ANOMALIES } from "../assertions/invariants.mjs";
import { buildLevel3Candidates } from "./level3Promotion.mjs";

const HARD_GATE_CODES = Object.freeze([
  ANOMALIES.ANSWER_NOT_PERSISTED,
  ANOMALIES.QUESTION_NOT_ADVANCED,
  ANOMALIES.REPEATED_QUESTION,
  ANOMALIES.DUPLICATE_RESPONSE,
  ANOMALIES.TRANSCRIPT_FINAL_MISSING,
  ANOMALIES.RESPONSE_TIMEOUT,
  ANOMALIES.API_5XX,
  ANOMALIES.WEBSOCKET_ERROR,
  ANOMALIES.DATACHANNEL_ERROR,
  ANOMALIES.TURN_COMMITTED_TOO_EARLY,
  ANOMALIES.UNEXPECTED_ANSWER_PERSISTED,
  ANOMALIES.UNEXPECTED_QUESTION_ADVANCE,
]);

function zeroCounts(keys) {
  return Object.fromEntries(keys.map((key) => [key, 0]));
}

export function createExplorationSummary({
  seed,
  limits,
  provider = null,
  model = null,
}) {
  return {
    seed,
    provider,
    model,
    limits: { ...limits },
    totals: {
      sessions: 0,
      turns: 0,
      jevApiCalls: 0,
      cacheHits: 0,
      cacheMisses: 0,
    },
    behaviorCounts: zeroCounts(JEV_USER_BEHAVIORS),
    expectationResultCounts: { PASS: 0, FAIL: 0 },
    expectationViolationCounts: {},
    sessionActionCounts: zeroCounts(JEV_SESSION_ACTIONS),
    hardGateAnomalyCounts: zeroCounts(HARD_GATE_CODES),
    hardGateStopCounts: {},
    sessions: [],
    level3Candidates: [],
  };
}

export function recordExplorationTurn(summary, {
  session,
  behavior,
  jev,
  hardGate,
  turnRecord = null,
}) {
  summary.totals.turns += 1;
  if (Object.hasOwn(summary.behaviorCounts, behavior)) {
    summary.behaviorCounts[behavior] += 1;
  }
  if (jev?.called) {
    summary.totals.jevApiCalls += jev.cacheHit ? 0 : 1;
    if (jev.cacheHit) summary.totals.cacheHits += 1;
    else summary.totals.cacheMisses += 1;
    if (Object.hasOwn(summary.sessionActionCounts, jev.sessionAction)) {
      summary.sessionActionCounts[jev.sessionAction] += 1;
    }
  }
  for (const anomaly of new Set([
    ...(hardGate?.anomalies ?? []),
    ...(hardGate?.codes ?? []),
  ])) {
    if (Object.hasOwn(summary.hardGateAnomalyCounts, anomaly)) {
      summary.hardGateAnomalyCounts[anomaly] += 1;
    }
  }
  const expectationResult = hardGate?.expectationResult ?? null;
  if (expectationResult && Object.hasOwn(summary.expectationResultCounts, expectationResult)) {
    summary.expectationResultCounts[expectationResult] += 1;
  }
  for (const violation of hardGate?.expectationViolations ?? []) {
    summary.expectationViolationCounts[violation] = (
      summary.expectationViolationCounts[violation] ?? 0
    ) + 1;
  }
  for (const code of hardGate?.codes ?? []) {
    if (code.startsWith("MAX_")) {
      summary.hardGateStopCounts[code] = (summary.hardGateStopCounts[code] ?? 0) + 1;
    }
  }
  session.turns.push({
    turn: session.turns.length + 1,
    behavior,
    sessionAction: jev?.sessionAction ?? null,
    nextUserBehavior: jev?.nextUserBehavior ?? null,
    cacheHit: jev?.cacheHit ?? false,
    hardGateStatus: hardGate?.status ?? null,
    hardGateCodes: hardGate?.codes ?? [],
    expectations: hardGate?.expectations ?? null,
    observed: hardGate?.observed ?? null,
    expectedConditions: hardGate?.expectedConditions ?? [],
    expectationResult,
    expectationViolations: hardGate?.expectationViolations ?? [],
    transcript: turnRecord?.transcription?.finalText ?? "",
    interview: turnRecord?.interview ?? null,
    response: turnRecord?.response ?? null,
  });
}

export function recordExplorationSession(summary, session) {
  summary.totals.sessions += 1;
  summary.sessions.push({
    sessionId: session.sessionId,
    index: session.index,
    status: session.status,
    turns: session.turns,
    path: session.path,
    testedBehaviors: [...new Set(session.testedBehaviors ?? [])],
    error: session.error ?? null,
  });
}

export function writeExplorationSummary({ outputDir, summary, costGuard, maxLevel3Candidates = 5 }) {
  summary.level3Candidates = buildLevel3Candidates(summary, {
    maxCandidates: maxLevel3Candidates,
  });
  const snapshot = {
    ...summary,
    costGuard: costGuard?.snapshot() ?? null,
  };
  const summaryPath = path.join(outputDir, "exploration-summary.json");
  fs.writeFileSync(summaryPath, JSON.stringify(snapshot, null, 2), "utf8");
  return summaryPath;
}
