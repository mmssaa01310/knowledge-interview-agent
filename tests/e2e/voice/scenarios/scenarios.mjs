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

// These are test inputs only. Jev selects a behavior label; it never writes
// the transcript itself. Each label has fixed variants so a replay remains
// deterministic and RETRY_CURRENT can use a different fixture.
export const JEV_BEHAVIOR_FIXTURES = Object.freeze({
  HESITATE: Object.freeze([
    "えーっと……プロジェクトリーダーを担当しています。",
    "えっと、現在はプロジェクトリーダーです。",
  ]),
  SELF_CORRECT: Object.freeze([
    "プロジェクトマネージャー……いや、プロジェクトリーダーをしています。",
    "マネージャーではなく、プロジェクトリーダーを担当しています。",
  ]),
  AMBIGUOUS: Object.freeze([
    "まあ、そのあたりを色々やっています。",
    "関係する業務を一通り担当しています。",
  ]),
  ASK_CLARIFICATION: Object.freeze([
    "この質問では、何を答えればよいですか？",
    "質問の意図をもう少し教えてください。",
  ]),
  SILENCE: Object.freeze([""]),
  MULTI_ANSWER: Object.freeze([
    "プロジェクトリーダーで、進捗管理をしています。今後は生成AIも学びたいです。",
    "現在はプロジェクトリーダーとして開発を進め、これから生成AIも学びたいです。",
  ]),
});

// Expectations describe what the application should do for a behavior. They
// are intentionally separate from the fixture text: an empty SILENCE fixture
// is a valid test input, not a missing test case.
export const JEV_BEHAVIOR_EXPECTATIONS = Object.freeze({
  NORMAL: Object.freeze({
    transcriptFinal: "required",
    answerPersistence: "required",
    questionAdvance: "required",
    assistantRecovery: "required",
  }),
  HESITATE: Object.freeze({
    transcriptFinal: "required",
    answerPersistence: "required",
    questionAdvance: "required",
    assistantRecovery: "required",
  }),
  SELF_CORRECT: Object.freeze({
    transcriptFinal: "required",
    answerPersistence: "required",
    questionAdvance: "required",
    correction: "corrected_value_required",
    assistantRecovery: "required",
  }),
  MULTI_ANSWER: Object.freeze({
    transcriptFinal: "required",
    answerPersistence: "required",
    questionAdvance: "optional",
    informationPreservation: "required",
    assistantRecovery: "required",
  }),
  AMBIGUOUS: Object.freeze({
    transcriptFinal: "required",
    answerPersistence: "optional",
    questionAdvance: "optional",
    assistantRecovery: "required",
  }),
  ASK_CLARIFICATION: Object.freeze({
    transcriptFinal: "required",
    answerPersistence: "not_required",
    questionAdvance: "not_required",
    assistantRecovery: "required",
  }),
  SILENCE: Object.freeze({
    transcriptFinal: "not_required",
    answerPersistence: "must_not",
    questionAdvance: "must_not",
    assistantRecovery: "observe",
  }),
});

export const JEV_BEHAVIOR_OUTCOMES = Object.freeze({
  NORMAL: Object.freeze({
    answerStatus: "ANSWERED",
    expectedQuestionTransition: true,
    expectations: JEV_BEHAVIOR_EXPECTATIONS.NORMAL,
  }),
  HESITATE: Object.freeze({
    answerStatus: "ANSWERED",
    expectedQuestionTransition: true,
    expectations: JEV_BEHAVIOR_EXPECTATIONS.HESITATE,
  }),
  SELF_CORRECT: Object.freeze({
    answerStatus: "ANSWERED",
    expectedQuestionTransition: true,
    expectations: JEV_BEHAVIOR_EXPECTATIONS.SELF_CORRECT,
  }),
  AMBIGUOUS: Object.freeze({
    answerStatus: "PARTIAL",
    expectedQuestionTransition: false,
    expectations: JEV_BEHAVIOR_EXPECTATIONS.AMBIGUOUS,
  }),
  ASK_CLARIFICATION: Object.freeze({
    answerStatus: "NOT_ANSWERED",
    expectedQuestionTransition: false,
    expectAnswerPersistence: false,
    expectations: JEV_BEHAVIOR_EXPECTATIONS.ASK_CLARIFICATION,
  }),
  SILENCE: Object.freeze({
    answerStatus: "NOT_ANSWERED",
    expectedQuestionTransition: false,
    expectAnswerPersistence: false,
    expectations: JEV_BEHAVIOR_EXPECTATIONS.SILENCE,
  }),
  MULTI_ANSWER: Object.freeze({
    answerStatus: "ANSWERED",
    expectedQuestionTransition: true,
    expectations: JEV_BEHAVIOR_EXPECTATIONS.MULTI_ANSWER,
  }),
});

const RETRY_NORMAL_FIXTURES = Object.freeze([
  "宮崎と申します。スマート技術開発部でシニアエンジニアを担当しています。",
  "プロジェクトリーダーとして、メンバーのタスクと進捗を管理しています。",
  "顧客の要望を整理して開発を進め、手戻りを減らす方法を学びました。",
  "要件と優先順位を整理して実装し、予定通りリリースしました。",
  "要望変更の影響範囲を確認し、計画を更新しています。",
]);

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

export function fixtureForBehavior({
  behavior = "NORMAL",
  scenario = VOICE_SCENARIOS.NORMAL,
  turnIndex = 0,
  retryCount = 0,
}) {
  const normalized = String(behavior).trim().toUpperCase();
  const outcome = JEV_BEHAVIOR_OUTCOMES[normalized];
  if (!outcome) throw new Error("Unsupported Jev user behavior: " + behavior);
  if (normalized === "NORMAL") {
    const transcript = retryCount > 0
      ? RETRY_NORMAL_FIXTURES[Math.min(turnIndex, RETRY_NORMAL_FIXTURES.length - 1)]
      : scenario.turns[Math.min(turnIndex, scenario.turns.length - 1)];
    return { behavior: normalized, transcript: transcript ?? "", ...outcome };
  }
  const variants = JEV_BEHAVIOR_FIXTURES[normalized];
  const transcript = variants[Math.min(retryCount, variants.length - 1)];
  return { behavior: normalized, transcript, ...outcome };
}

export function chooseNextAction({ scenario, turn, config }) {
  if (config.agentMode === "jev") {
    throw new Error("Jev mode uses JevSupervisor; chooseNextAction is deterministic-only");
  }
  return scenario.turns[turn] ?? null;
}
