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

const JUDGE_SCHEMA = Object.freeze({
  type: "object",
  additionalProperties: false,
  properties: {
    overall: { type: "string", enum: ["PASS", "FAIL"] },
    coherent: { type: "boolean" },
    correctionHandled: { type: "boolean" },
    contextPreserved: { type: "boolean" },
    appropriateClarification: { type: "boolean" },
    unnecessaryRepeatedQuestion: { type: "boolean" },
    reason: { type: "string" },
  },
  required: [
    "overall",
    "coherent",
    "correctionHandled",
    "contextPreserved",
    "appropriateClarification",
    "unnecessaryRepeatedQuestion",
    "reason",
  ],
});

function responseOutputText(response) {
  if (typeof response?.output_text === "string") return response.output_text;
  const chunks = [];
  for (const item of response?.output ?? []) {
    for (const content of item?.content ?? []) {
      if (typeof content?.text === "string") chunks.push(content.text);
    }
  }
  return chunks.join("\n");
}

function parseJudgeOutput(response) {
  const text = responseOutputText(response).trim();
  if (!text) throw new Error("LLM Judge returned no output");
  try {
    return JSON.parse(text);
  } catch {
    const objectText = text.match(/\{[\s\S]*\}/)?.[0];
    if (!objectText) throw new Error("LLM Judge output was not JSON");
    return JSON.parse(objectText);
  }
}

/**
 * Optional session-level semantic adapter. It is deliberately kept outside
 * the application runtime: deterministic turn gates remain authoritative and
 * this adapter only scores conversation quality after a session is complete.
 */
export function createResponsesJudgeEvaluator({
  apiKey = process.env.OPENAI_SECRET_KEY || process.env.OPENAI_API_KEY || "",
  model = process.env.VOICE_TEST_LLM_JUDGE_MODEL || "gpt-5.6-luna",
  fetchImpl = globalThis.fetch,
} = {}) {
  return async (input) => {
    if (!apiKey) throw new Error("LLM Judge requires OPENAI_SECRET_KEY or OPENAI_API_KEY");
    if (typeof fetchImpl !== "function") throw new Error("LLM Judge requires fetch");
    const response = await fetchImpl("https://api.openai.com/v1/responses", {
      method: "POST",
      headers: {
        authorization: `Bearer ${apiKey}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model,
        instructions: [
          "あなたは音声インタビューのセッション終了後だけに使う品質評価者です。",
          "決定論的な通信・保存エラーは判定せず、会話の意味的な成立だけを評価してください。",
          "ユーザー発話とAI応答が噛み合っているか、訂正・文脈・聞き返しが適切かを評価します。",
          "入力にない事実を補わず、JSONだけを返してください。",
        ].join("\n"),
        input: JSON.stringify(input),
        text: {
          format: {
            type: "json_schema",
            name: "voice_conversation_quality",
            strict: true,
            schema: JUDGE_SCHEMA,
          },
        },
      }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(`LLM Judge HTTP ${response.status}: ${JSON.stringify(body).slice(0, 500)}`);
    }
    return {
      ...parseJudgeOutput(body),
      model: body.model ?? model,
      usage: body.usage ?? null,
    };
  };
}
