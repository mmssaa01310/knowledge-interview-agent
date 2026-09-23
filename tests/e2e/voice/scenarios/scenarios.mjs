import crypto from "node:crypto";

export const VOICE_SCENARIOS = Object.freeze({
  NORMAL: Object.freeze({
    name: "NORMAL",
    turns: [
      "宮崎です。スマート技術開発部でシニアエンジニアをしています。",
      "現在の役割はプロジェクトリーダーで、メンバーのタスク管理と進捗管理を担当しています。",
      "顧客の要望を聞きながらシステム開発を進めた経験が、今の専門性につながっています。手戻りを減らす進め方を学びました。",
      "要件を整理し、関係者と優先順位を合わせてから実装することが私の強みです。予定通りリリースできました。",
      "課題は要望の変化が多いことです。影響範囲を確認して計画を更新しています。",
      "今後は生成AIを使った開発に取り組み、関連する知識を身につけたいです。必要な支援は特にありません。",
    ],
  }),
  HESITATION: Object.freeze({
    name: "HESITATION",
    turns: ["えーっと……", "プロジェクトリーダーをしています。"],
  }),
  SELF_CORRECTION: Object.freeze({
    name: "SELF_CORRECTION",
    turns: ["プロジェクトマネージャー……いや、プロジェクトリーダーをしています。"],
  }),
  MULTI_ANSWER: Object.freeze({
    name: "MULTI_ANSWER",
    turns: ["プロジェクトリーダーで、進捗管理をしています。今後は生成AIも学びたいです。"],
  }),
  AMBIGUOUS: Object.freeze({
    name: "AMBIGUOUS",
    turns: ["まあ、そのあたりを色々やっています。"],
  }),
});

export function resolveScenario(name = "NORMAL") {
  const scenario = VOICE_SCENARIOS[String(name).toUpperCase()];
  if (!scenario) throw new Error("Unknown voice test scenario: " + name);
  return scenario;
}

export function deterministicSeed(seed) {
  return Number.isFinite(Number(seed)) ? Number(seed) : 38291;
}

export function transcriptForAction(action, scenario, turn) {
  if (typeof action !== "string") return null;
  if (scenario.turns.includes(action)) return action;
  const actionScenario = {
    NORMAL: VOICE_SCENARIOS.NORMAL,
    HESITATE: VOICE_SCENARIOS.HESITATION,
    HESITATION: VOICE_SCENARIOS.HESITATION,
    SELF_CORRECT: VOICE_SCENARIOS.SELF_CORRECTION,
    SELF_CORRECTION: VOICE_SCENARIOS.SELF_CORRECTION,
    LONG_ANSWER: VOICE_SCENARIOS.MULTI_ANSWER,
    MULTI_ANSWER: VOICE_SCENARIOS.MULTI_ANSWER,
    AMBIGUOUS: VOICE_SCENARIOS.AMBIGUOUS,
    ASK_CLARIFICATION: VOICE_SCENARIOS.AMBIGUOUS,
  }[action.trim().toUpperCase()];
  if (action.trim().toUpperCase() === "SILENCE") return "";
  if (!actionScenario) throw new Error("Unsupported Jev scenario action: " + action);
  return actionScenario.turns[Math.min(turn, actionScenario.turns.length - 1)];
}

const JEV_ACTION_CACHE = new Map();

export async function chooseNextAction({
  scenario,
  turn,
  state,
  config,
  guard,
  fetchImpl = fetch,
}) {
  if (config.agentMode !== "jev") return scenario.turns[turn] ?? null;
  guard.recordJevCall();
  const url = process.env.JEV_API_URL;
  if (!url) throw new Error("TEST_AGENT_MODE=jev requires JEV_API_URL");
  const cacheKey = crypto.createHash("sha256").update(JSON.stringify({
    seed: config.seed,
    scenario: scenario.name,
    turn,
    state,
  })).digest("hex");
  if (JEV_ACTION_CACHE.has(cacheKey)) return JEV_ACTION_CACHE.get(cacheKey);
  const response = await fetchImpl(url, {
    method: "POST",
    headers: { "content-type": "application/json", "x-voice-test-cache-key": cacheKey },
    body: JSON.stringify({ scenario: scenario.name, turn, state, seed: config.seed }),
  });
  if (!response.ok) throw new Error("Jev request failed: " + response.status);
  const result = await response.json();
  const action = typeof result.action === "string" ? result.action : null;
  if (!action) throw new Error("Jev response must contain an action, not generated text");
  JEV_ACTION_CACHE.set(cacheKey, action);
  return action;
}
