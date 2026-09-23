/**
 * Semantic judging is an exceptional path. Deterministic assertions should
 * finish first; a caller must explicitly enable this adapter or provide an
 * anomaly before it consumes an LLM budget.
 */
export async function runSemanticJudge({
  enabled = false,
  anomalyDetected = false,
  guard,
  evaluator,
  input,
}) {
  if (!enabled && !anomalyDetected) {
    return { status: "skipped", reason: "deterministic_checks_passed" };
  }
  if (typeof evaluator !== "function") {
    return { status: "skipped", reason: "judge_adapter_not_configured" };
  }
  guard.recordLlmJudgeCall();
  return { status: "evaluated", result: await evaluator(input) };
}
