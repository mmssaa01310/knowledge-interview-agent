import fs from "node:fs";
import path from "node:path";

const STRESS_BEHAVIORS = new Set([
  "AMBIGUOUS",
  "SELF_CORRECT",
  "MULTI_ANSWER",
  "ASK_CLARIFICATION",
  "HESITATE",
]);

const PRIORITY = Object.freeze({
  EXPECTATION_FAIL: 100,
  HARD_GATE_ANOMALY: 95,
  RETRY_CURRENT: 90,
  UNEXPECTED_TRANSITION: 85,
  STRESS_BEHAVIOR: 70,
  ANSWER_BOUNDARY: 65,
  ASSISTANT_RECOVERY: 55,
});

const UNEXPECTED_TRANSITION_CODES = new Set([
  "QUESTION_NOT_ADVANCED",
  "UNEXPECTED_QUESTION_ADVANCE",
  "REPEATED_QUESTION",
]);

function hasText(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function normalizedBehavior(value) {
  return String(value ?? "").trim().toUpperCase();
}

function normalizedReason(reason) {
  return String(reason ?? "").trim().toUpperCase();
}

function level2Snapshot(turn) {
  return {
    turn: turn.turn ?? null,
    behavior: normalizedBehavior(turn.behavior),
    transcript: turn.transcript ?? turn.transcription?.finalText ?? "",
    expectationResult: turn.expectationResult ?? null,
    expectationViolations: [...(turn.expectationViolations ?? [])],
    hardGateStatus: turn.hardGateStatus ?? null,
    hardGateCodes: [...(turn.hardGateCodes ?? [])],
    interview: turn.interview ?? null,
    response: turn.response ?? null,
    observed: turn.observed ?? null,
    sessionAction: turn.sessionAction ?? null,
    nextUserBehavior: turn.nextUserBehavior ?? null,
  };
}

function turnTriggers(turn) {
  const behavior = normalizedBehavior(turn.behavior);
  const reasons = [];
  const expectationResult = normalizedReason(turn.expectationResult);
  const hardGateCodes = new Set(turn.hardGateCodes ?? []);
  const anomalyCodes = [...hardGateCodes].filter((code) => !String(code).startsWith("MAX_"));

  if (expectationResult === "FAIL") {
    reasons.push({
      code: "EXPECTATION_FAIL",
      priority: PRIORITY.EXPECTATION_FAIL,
      detail: (turn.expectationViolations ?? []).join(", ") || "behavior expectation failed",
    });
  }
  if (turn.hardGateStatus === "FAIL" || anomalyCodes.length > 0) {
    const codes = anomalyCodes;
    reasons.push({
      code: "HARD_GATE_ANOMALY",
      priority: PRIORITY.HARD_GATE_ANOMALY,
      detail: codes.join(", ") || "hard gate failed",
    });
  }
  if (turn.sessionAction === "RETRY_CURRENT") {
    reasons.push({
      code: "RETRY_CURRENT",
      priority: PRIORITY.RETRY_CURRENT,
      detail: "Jev selected RETRY_CURRENT",
    });
  }
  if ([...(turn.hardGateCodes ?? [])].some((code) => UNEXPECTED_TRANSITION_CODES.has(code))) {
    reasons.push({
      code: "UNEXPECTED_TRANSITION",
      priority: PRIORITY.UNEXPECTED_TRANSITION,
      detail: (turn.hardGateCodes ?? []).filter((code) => UNEXPECTED_TRANSITION_CODES.has(code)).join(", "),
    });
  }
  if (STRESS_BEHAVIORS.has(behavior)) {
    reasons.push({
      code: "STRESS_BEHAVIOR",
      priority: PRIORITY.STRESS_BEHAVIOR,
      detail: `${behavior} behavior was selected by Jev`,
    });
  }

  const interview = turn.interview ?? {};
  const hasCandidate = hasText(interview.candidate);
  const hasConfirmed = hasText(interview.confirmed);
  if (hasCandidate || (behavior !== "NORMAL" && (hasConfirmed || interview.answerState))) {
    reasons.push({
      code: "ANSWER_BOUNDARY",
      priority: PRIORITY.ANSWER_BOUNDARY,
      detail: `answerState=${interview.answerState ?? "unknown"}`,
    });
  }

  if (behavior !== "NORMAL" && turn.response?.assistantSpeechStarted === true) {
    reasons.push({
      code: "ASSISTANT_RECOVERY",
      priority: PRIORITY.ASSISTANT_RECOVERY,
      detail: "assistant recovered with a response after a non-normal behavior",
    });
  }
  return reasons;
}

function mergeCandidate(existing, candidate) {
  const reasons = new Map((existing.reasons ?? []).map((reason) => [reason.code, reason]));
  for (const reason of candidate.reasons ?? []) reasons.set(reason.code, reason);
  const sortedReasons = [...reasons.values()].sort((left, right) => right.priority - left.priority);
  const representative = sortedReasons[0] ?? {
    code: "UNKNOWN",
    priority: 0,
    detail: "candidate trigger",
  };
  return {
    ...existing,
    reasons: sortedReasons,
    reason: representative.detail,
    reasonCode: representative.code,
    priority: representative.priority,
  };
}

/**
 * Convert Level 2 exploration evidence into a small, deterministic promotion
 * queue. A clean NORMAL-only pass intentionally produces no candidate.
 */
export function buildLevel3Candidates(summary, { maxCandidates = 5 } = {}) {
  const byBehavior = new Map();
  for (const session of summary?.sessions ?? []) {
    for (const turn of session.turns ?? []) {
      const reasons = turnTriggers(turn);
      if (reasons.length === 0) continue;
      const behavior = normalizedBehavior(turn.behavior) || "UNKNOWN";
      const candidate = {
        seed: summary.seed ?? null,
        session: {
          id: session.sessionId ?? null,
          index: session.index ?? null,
        },
        turn: turn.turn ?? null,
        behavior,
        reasonCode: reasons.sort((left, right) => right.priority - left.priority)[0]?.code ?? "UNKNOWN",
        reason: reasons.sort((left, right) => right.priority - left.priority)[0]?.detail
          ?? "candidate trigger",
        priority: Math.max(...reasons.map((reason) => reason.priority)),
        reasons,
        level2: level2Snapshot(turn),
      };
      const key = behavior;
      const existing = byBehavior.get(key);
      if (!existing) {
        byBehavior.set(key, candidate);
      } else {
        const preferred = candidate.priority > existing.priority
          || (candidate.priority === existing.priority
            && (candidate.session.index ?? Number.MAX_SAFE_INTEGER) < (existing.session.index ?? Number.MAX_SAFE_INTEGER));
        const merged = mergeCandidate(preferred ? candidate : existing, preferred ? existing : candidate);
        byBehavior.set(key, merged);
      }
    }
  }

  return [...byBehavior.values()]
    .sort((left, right) => (
      right.priority - left.priority
      || (left.session.index ?? Number.MAX_SAFE_INTEGER) - (right.session.index ?? Number.MAX_SAFE_INTEGER)
      || (left.turn ?? Number.MAX_SAFE_INTEGER) - (right.turn ?? Number.MAX_SAFE_INTEGER)
      || left.behavior.localeCompare(right.behavior)
    ))
    .slice(0, Math.max(0, Number(maxCandidates) || 0));
}

export function readExplorationSummary(summaryPath) {
  const resolvedPath = path.resolve(summaryPath);
  const summary = JSON.parse(fs.readFileSync(resolvedPath, "utf8"));
  return {
    path: resolvedPath,
    summary,
    candidates: Array.isArray(summary.level3Candidates)
      ? summary.level3Candidates
      : buildLevel3Candidates(summary),
  };
}

function normalizeTranscript(value) {
  return String(value ?? "")
    .normalize("NFKC")
    .replace(/\s+/g, "")
    .trim();
}

function isFailure(turn) {
  return turn?.expectationResult === "FAIL"
    || turn?.hardGateStatus === "FAIL"
    || (turn?.hardGate?.status === "FAIL")
    || (turn?.anomalies?.length ?? 0) > 0;
}

/** Compare structural results without making a semantic/LLM judgment. */
export function compareLevel2Level3(candidate, level3Turn) {
  const level2 = candidate?.level2 ?? {};
  const level3 = level3Turn ?? {};
  const level2Interview = level2.interview ?? {};
  const level3Interview = level3.interview ?? {};
  const level2Failure = isFailure(level2);
  const level3Failure = isFailure(level3);
  const comparison = {
    transcriptMatch: normalizeTranscript(level2.transcript) === normalizeTranscript(
      level3.transcript ?? level3.transcription?.finalText,
    ),
    stateTransitionMatch: (
      Boolean(level2Interview.currentQuestionAfter)
      && Boolean(level3Interview.currentQuestionAfter)
      && Boolean(level2Interview.currentQuestionAfter === level3Interview.currentQuestionAfter)
    ) || (
      level2Interview.answerState != null
      && level2Interview.answerState === level3Interview.answerState
    ),
    nextQuestionMatch: Boolean(
      level2Interview.nextQuestionId
      && level2Interview.nextQuestionId === level3Interview.nextQuestionId,
    ),
    expectationMatch: level2.expectationResult != null
      && level2.expectationResult === level3.expectationResult,
    level2Failure,
    level3Failure,
  };
  const classification = level2Failure && level3Failure
    ? "LEVEL2_FAIL_LEVEL3_FAIL"
    : !level2Failure && level3Failure
      ? "LEVEL2_PASS_LEVEL3_FAIL"
      : "MATCH_OR_DIFFERENT_TRANSCRIPT";
  return {
    ...comparison,
    classification,
    structuralMatch: comparison.stateTransitionMatch
      && comparison.nextQuestionMatch
      && comparison.expectationMatch,
  };
}
