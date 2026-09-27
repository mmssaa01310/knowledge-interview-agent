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

// Real-provider robustness inputs. These are test fixtures, not application
// conversation rules. The runner resolves the corresponding prerecorded WAV
// by behavior and variant so the same utterance is never the only evidence
// for a behavior.
export const REAL_VOICE_BEHAVIORS = Object.freeze([
  "NORMAL",
  "HESITATION",
  "SELF_CORRECTION",
  "MULTI_ANSWER",
  "AMBIGUOUS",
  "ASK_CLARIFICATION",
  "SILENCE",
  "OFF_TOPIC",
  "CONTRADICTION",
  "CHANGE_PREVIOUS_ANSWER",
  "DONT_KNOW",
  "PARTIAL_ANSWER",
  "LONG_ANSWER",
  "ANSWER_FUTURE_QUESTION",
  "USER_INTERRUPTION",
  "SPLIT_UTTERANCE",
  "SPLIT_UTTERANCE_LONG_PAUSE",
  "MULTIPLE_TRANSCRIBE_FINALS",
  "USER_RESUMES_BEFORE_COMMIT",
  "SLOW_RESPONSE_WITH_ACK",
  "FAST_RESPONSE_WITHOUT_ACK",
  "USER_INTERRUPTS_ACK",
]);

export const REAL_VOICE_BEHAVIORS_BY_ENGINE = Object.freeze({
  gpt_live: Object.freeze([
    "NORMAL",
    "HESITATION",
    "SELF_CORRECTION",
    "MULTI_ANSWER",
    "AMBIGUOUS",
    "ASK_CLARIFICATION",
    "SILENCE",
    "OFF_TOPIC",
    "CONTRADICTION",
    "CHANGE_PREVIOUS_ANSWER",
    "DONT_KNOW",
    "PARTIAL_ANSWER",
    "LONG_ANSWER",
    "ANSWER_FUTURE_QUESTION",
    "USER_INTERRUPTION",
  ]),
  transcribe_polly: REAL_VOICE_BEHAVIORS,
});

export const REAL_VOICE_FIXTURE_TEXTS = Object.freeze({
  NORMAL: Object.freeze([
    "宮崎です。スマート技術開発部に所属し、シニアエンジニアとしてシステム開発を担当しています。",
    "名前は宮崎です。所属はスマート技術開発部で、役職はシニアエンジニアです。",
    "宮崎と申します。スマート技術開発部のシニアエンジニアで、生成AIを活用したシステム開発を担当しています。",
  ]),
  HESITATION: Object.freeze([
    "えーっと、宮崎です。現在はシステム開発を担当しています。",
    "少し考えます。宮崎で、シニアエンジニアです。",
    "あの、名前は宮崎です。担当は開発です。",
  ]),
  SELF_CORRECTION: Object.freeze([
    "田中です。あ、すみません、宮崎です。",
    "担当は保全です……いや、今はシステム開発です。",
    "三年くらい……正確には五年、開発を担当しています。",
  ]),
  MULTI_ANSWER: Object.freeze([
    "宮崎です。開発部のシニアエンジニアで、プロジェクト管理も担当しています。",
    "名前は宮崎で、役職はシニアエンジニアです。専門は生成AIを使った開発です。",
    "宮崎です。部署は開発部、役割はリーダーで、今後はAIの知識も身につけたいです。",
  ]),
  AMBIGUOUS: Object.freeze([
    "まあ、そのあたりを色々やっています。",
    "関係する業務を一通り担当しています。",
    "いろいろな開発に関わっています。",
  ]),
  ASK_CLARIFICATION: Object.freeze([
    "この質問では、何を答えればよいですか？",
    "質問の意図をもう少し教えてください。",
    "どの範囲について話せばいいでしょうか？",
  ]),
  SILENCE: Object.freeze(["", "", ""]),
  OFF_TOPIC: Object.freeze([
    "週末は家で映画を見ることが多いです。",
    "最近は天気がよくて、散歩を楽しんでいます。",
    "昼食は近所の店で食べました。",
  ]),
  CONTRADICTION: Object.freeze([
    "先ほどは開発担当と言いましたが、実際は営業担当です。",
    "役職はリーダーと話しましたが、現在はメンバーです。",
    "東京勤務と答えましたが、勤務地は大阪でした。",
  ]),
  CHANGE_PREVIOUS_ANSWER: Object.freeze([
    "先ほどの回答を訂正します。部署は開発部ではなく企画部です。",
    "前の回答を変更します。担当は保全ではなく生産技術です。",
    "さきほどの年数は違いました。正しくは五年です。",
  ]),
  DONT_KNOW: Object.freeze([
    "分かりません。",
    "その点は今は答えられません。",
    "特に思い当たることはありません。",
  ]),
  PARTIAL_ANSWER: Object.freeze([
    "現在はプロジェクトリーダーをしています。",
    "プロジェクトリーダーとして開発を進めています。",
    "今の役割はプロジェクトリーダーです。",
  ]),
  PARTIAL_COMPLETION: Object.freeze([
    "プロジェクトリーダーとして、メンバーのタスクと進捗を管理しています。",
    "プロジェクトリーダーとして、進捗と品質を管理し、リスクがあれば早めに相談しています。",
    "プロジェクトリーダーとして、メンバーの状況を確認し、必要な調整を行っています。",
  ]),
  RECOVERY_NORMAL: Object.freeze([
    "顧客の要望を聞きながらシステム開発を進めた経験が転機になりました。要件を整理し、関係者と優先順位を合わせてから実装する進め方を身につけました。",
    "顧客との調整を経験したことで、先に要件を整理して認識を合わせる仕事の進め方に変わりました。今も開発の手戻りを減らすために活かしています。",
    "難しい案件で要望の優先順位を決めた経験から、影響範囲を確認してから判断するようになりました。現在の開発でも同じ進め方をしています。",
  ]),
  LONG_ANSWER: Object.freeze([
    "宮崎です。開発部でシニアエンジニアをしています。顧客の要望を整理し、関係者と優先順位を確認してから実装しています。問題が起きたときは影響範囲を調べ、計画を見直してチームに共有します。",
    "現在はシステム開発を担当しています。要件を聞き、関係者の認識を合わせ、実装と検証を進めます。リリース後の問い合わせにも対応し、次の改善につなげています。",
    "プロジェクトリーダーとして、進捗と品質を管理しています。メンバーの状況を確認し、リスクがあれば早めに相談します。顧客の期待と開発の現実を調整することを大切にしています。",
  ]),
  ANSWER_FUTURE_QUESTION: Object.freeze([
    "宮崎です。役職はシニアエンジニアで、今後は生成AIの活用にも取り組みたいです。",
    "開発部で働いています。課題は要望変更で、将来は自動化の知識を身につけたいです。",
    "現在はリーダーです。強みは調整力で、次はAIを使った開発を学びたいです。",
  ]),
  USER_INTERRUPTION: Object.freeze([
    "宮崎です。あ、部署は開発部で、役割はリーダーです。",
    "システム開発を担当していて、すみません、今の質問に戻ると宮崎です。",
    "プロジェクト管理です。いや、まず名前は宮崎です。",
  ]),
  SPLIT_UTTERANCE: Object.freeze([
    "名前は……|宮崎です。",
    "担当は……|AWS基盤です。",
    "原因は……|たぶんセンサーですね。",
  ]),
  SPLIT_UTTERANCE_LONG_PAUSE: Object.freeze([
    "そのときは……|まず設備を止めます。",
    "所属は……|スマート技術開発部です。",
    "役割は……|プロジェクトリーダーです。",
  ]),
  MULTIPLE_TRANSCRIBE_FINALS: Object.freeze([
    "最初に確認して……|その後で関係者へ共有します。",
    "原因を調べて……|再発防止策を決めます。",
    "要件を整理して……|優先順位を合わせます。",
  ]),
  USER_RESUMES_BEFORE_COMMIT: Object.freeze([
    "名前は……|宮崎です。",
    "担当は……|AWS基盤です。",
    "そのときは……|まず設備を止めます。",
  ]),
  SLOW_RESPONSE_WITH_ACK: Object.freeze([
    "関係者の要望を整理して、影響範囲を確認しながら対応しました。",
    "難しい案件でしたが、状況を整理してチームで判断しました。",
    "顧客と相談し、優先順位を決めてから開発を進めました。",
  ]),
  FAST_RESPONSE_WITHOUT_ACK: Object.freeze([
    "宮崎です。",
    "開発部です。",
    "シニアエンジニアです。",
  ]),
  USER_INTERRUPTS_ACK: Object.freeze([
    "はい、宮崎です。",
    "確認ありがとうございます。開発部です。",
    "そうですね、プロジェクトリーダーです。",
  ]),
});

// These are test-fixture semantics. They describe which dev voice-demo field
// a prerecorded utterance is intended to answer; they are never sent to the
// application or used as production conversation rules.
export const REAL_VOICE_FIXTURE_TARGETS = Object.freeze({
  NORMAL: Object.freeze({
    fieldId: "dev-voice-demo-field-self-introduction",
    aspectIds: ["name", "department", "role_or_domain"],
    expectedMissingAspectIds: [],
  }),
  PARTIAL_ANSWER: Object.freeze({
    fieldId: "dev-voice-demo-field-current-role",
    aspectIds: ["role"],
    expectedMissingAspectIds: ["responsibilities"],
  }),
  PARTIAL_COMPLETION: Object.freeze({
    fieldId: "dev-voice-demo-field-current-role",
    aspectIds: ["role", "responsibilities"],
    expectedMissingAspectIds: [],
  }),
  RECOVERY_NORMAL: Object.freeze({
    fieldId: "dev-voice-demo-field-experience",
    aspectIds: ["trigger_experience", "impact_or_change"],
    expectedMissingAspectIds: [],
  }),
});

export function realVoiceFixtureText(behavior, variant = 0) {
  const values = REAL_VOICE_FIXTURE_TEXTS[String(behavior).toUpperCase()];
  if (!values) throw new Error("Unknown real voice behavior: " + behavior);
  return values[Math.max(0, Number(variant) || 0) % values.length];
}

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
