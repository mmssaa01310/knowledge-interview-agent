import fs from "node:fs";
import path from "node:path";

export const REPORT_BEHAVIORS = Object.freeze([
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

export const REPORT_FAILURE_TYPES = Object.freeze([
  "ANSWER_NOT_PERSISTED",
  "QUESTION_NOT_ADVANCED",
  "REPEATED_QUESTION",
  "DUPLICATE_RESPONSE",
  "TRANSCRIPT_FINAL_MISSING",
  "RESPONSE_TIMEOUT",
  "TURN_COMMITTED_TOO_EARLY",
  "CONTEXT_LOSS",
  "SELF_CORRECTION_FAILED",
  "MULTI_ANSWER_INFORMATION_LOST",
  "inappropriate clarification",
  "API_5XX",
  "WebSocket/DataChannel error",
  "ACK_DUPLICATE",
]);

export const REPORT_BEHAVIOR_INFO = Object.freeze({
  NORMAL: { label: "通常回答", description: "質問に対して通常どおり回答したケース" },
  HESITATION: { label: "ためらい・言いよどみ", description: "考えながら、または言いよどみを含めて回答したケース" },
  SELF_CORRECTION: { label: "自己訂正", description: "回答途中で自分の発言を訂正したケース" },
  MULTI_ANSWER: { label: "複数項目を含む回答", description: "複数の質問項目に関係する情報を一度に回答したケース" },
  AMBIGUOUS: { label: "曖昧な回答", description: "内容が曖昧で追加確認が必要なケース" },
  ASK_CLARIFICATION: { label: "質問の意味を確認", description: "ユーザーが質問の意味や意図を確認したケース" },
  SILENCE: { label: "無発話", description: "ユーザーの発話がない、または無音だったケース" },
  OFF_TOPIC: { label: "質問と無関係な回答", description: "現在の質問とは異なる内容を回答したケース" },
  CONTRADICTION: { label: "矛盾する回答", description: "過去の回答と矛盾する内容を回答したケース" },
  CHANGE_PREVIOUS_ANSWER: { label: "過去回答の変更", description: "以前の回答内容を変更したケース" },
  DONT_KNOW: { label: "分からない・回答できない", description: "分からない、または回答を控えたケース" },
  PARTIAL_ANSWER: { label: "一部回答", description: "質問に含まれる観点の一部だけを回答したケース" },
  LONG_ANSWER: { label: "長い回答", description: "通常より長い発話で回答したケース" },
  ANSWER_FUTURE_QUESTION: { label: "後続質問への先回り回答", description: "後で尋ねる項目も先に回答したケース" },
  USER_INTERRUPTION: { label: "ユーザー割り込み", description: "AIの発話中にユーザーが割り込んだケース" },
  SPLIT_UTTERANCE: { label: "分割発話", description: "一つの回答を複数回の発話に分けたケース" },
  SPLIT_UTTERANCE_LONG_PAUSE: { label: "長い間を含む分割発話", description: "長い無音を挟んで一つの回答を続けたケース" },
  MULTIPLE_TRANSCRIBE_FINALS: { label: "複数の確定文字起こし", description: "一つの回答中に複数の文字起こし確定を受け取ったケース" },
  USER_RESUMES_BEFORE_COMMIT: { label: "保存前の再発話", description: "ターン保存前にユーザーが発話を再開したケース" },
  SLOW_RESPONSE_WITH_ACK: { label: "遅延時の応答案内", description: "応答が遅い場合に待機案内を含めて処理したケース" },
  FAST_RESPONSE_WITHOUT_ACK: { label: "高速応答", description: "待機案内を出さずに応答したケース" },
  USER_INTERRUPTS_ACK: { label: "待機案内への割り込み", description: "AIの待機案内中にユーザーが割り込んだケース" },
});

export const REPORT_FAILURE_INFO = Object.freeze({
  ANSWER_NOT_PERSISTED: {
    label: "回答が保存されていない",
    description: "確定した文字起こしはあるが、回答がcandidateまたはconfirmedとして保存されていません。",
  },
  QUESTION_NOT_ADVANCED: {
    label: "次の質問へ進まない",
    description: "回答処理後に、期待される次の質問へ進んでいません。",
  },
  REPEATED_QUESTION: {
    label: "同じ質問の不要な繰り返し",
    description: "回答済みの質問が、必要な理由なく再度提示されています。",
  },
  DUPLICATE_RESPONSE: {
    label: "AI応答が重複",
    description: "同じターンにAIの応答が複数回開始されています。",
  },
  TRANSCRIPT_FINAL_MISSING: {
    label: "確定文字起こしがない",
    description: "ユーザー発話のfinal transcriptを取得できていません。",
  },
  RESPONSE_TIMEOUT: {
    label: "AI応答のタイムアウト",
    description: "ユーザー発話後、制限時間内にAI応答が開始されていません。",
  },
  TURN_COMMITTED_TOO_EARLY: {
    label: "確定前にターンを保存",
    description: "final transcriptを受け取る前にターンが確定されています。",
  },
  CONTEXT_LOSS: {
    label: "会話コンテキストの欠落",
    description: "過去の回答や会話状態が次の処理へ引き継がれていません。",
  },
  SELF_CORRECTION_FAILED: {
    label: "自己訂正を反映できない",
    description: "訂正後の回答が保存またはInterview Stateへ正しく反映されていません。",
  },
  MULTI_ANSWER_INFORMATION_LOST: {
    label: "複数情報の一部が欠落",
    description: "一つの発話に含まれていた複数の回答情報の一部が失われています。",
  },
  "inappropriate clarification": {
    label: "不適切な聞き返し",
    description: "聞き取れている回答に対して、不要または対象が不明確な聞き返しが生成されています。",
  },
  API_5XX: {
    label: "APIサーバーエラー（5xx）",
    description: "バックエンドまたは外部APIが5xxエラーを返しています。",
  },
  "WebSocket/DataChannel error": {
    label: "音声通信チャネルエラー",
    description: "WebSocketまたはWebRTC DataChannelで通信エラーが発生しています。",
  },
  ACK_DUPLICATE: {
    label: "待機案内が重複",
    description: "同一ターンに待機案内が複数回発生しています。",
  },
  ERROR: {
    label: "未分類のエラー",
    description: "エラーは記録されていますが、既知の失敗分類に対応していません。",
  },
});

const REPORT_LATENCY_INFO = Object.freeze({
  speechEndToTranscriptFinalMs: "発話終了 → 文字起こし確定",
  transcriptFinalToAssistantStartMs: "文字起こし確定 → AI応答開始",
  speechEndToAssistantStartMs: "発話終了 → AI応答開始",
  fullTurnLatencyMs: "1ターン全体の応答時間",
  segmentFinalToTurnCommitMs: "文字起こしsegment確定 → ターン保存",
  turnCommitToAckMs: "ターン保存 → 待機案内",
  ackToMainResponseMs: "待機案内 → 本応答開始",
});

const REPORT_QUALITY_INFO = Object.freeze({
  coherent: "会話の自然さ",
  correctionHandled: "自己訂正の処理",
  contextPreserved: "会話コンテキストの維持",
  appropriateClarification: "聞き返しの適切さ",
  unnecessaryRepeatedQuestion: "不要な質問の繰り返しがない",
});

const LATENCY_METRICS = Object.freeze([
  "speechEndToTranscriptFinalMs",
  "transcriptFinalToAssistantStartMs",
  "speechEndToAssistantStartMs",
  "fullTurnLatencyMs",
  "segmentFinalToTurnCommitMs",
  "turnCommitToAckMs",
  "ackToMainResponseMs",
]);

const QUALITY_METRICS = Object.freeze([
  "coherent",
  "correctionHandled",
  "contextPreserved",
  "appropriateClarification",
  "unnecessaryRepeatedQuestion",
]);

const JSON_FILE_NAMES = new Set([
  "summary.json",
  "exploration-summary.json",
  "level3-summary.json",
  "metrics.json",
  "failures.json",
  "telemetry.json",
]);

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function array(value) {
  return Array.isArray(value) ? value : [];
}

function text(value, fallback = "") {
  if (value === null || value === undefined) return fallback;
  return String(value);
}

function number(value, fallback = null) {
  if (value === null || value === undefined || value === "") return fallback;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function boolean(value, fallback = false) {
  if (value === true || value === false) return value;
  if (typeof value === "string") {
    if (["true", "pass", "passed", "ok", "yes", "1"].includes(value.toLowerCase())) {
      return true;
    }
    if (["false", "fail", "failed", "no", "0"].includes(value.toLowerCase())) {
      return false;
    }
  }
  return fallback;
}

function unique(values) {
  return [...new Set(array(values).filter((value) => value !== null && value !== undefined))];
}

function firstText(...values) {
  return values.find((value) => typeof value === "string" && value.trim()) ?? null;
}

function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return null;
  }
}

function readJsonLines(filePath) {
  try {
    return fs.readFileSync(filePath, "utf8")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => JSON.parse(line))
      .filter(isObject);
  } catch {
    return [];
  }
}

function walkFiles(root) {
  const result = [];
  if (!fs.existsSync(root)) return result;
  const pending = [root];
  while (pending.length) {
    const current = pending.pop();
    let entries = [];
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) pending.push(fullPath);
      else result.push(fullPath);
    }
  }
  return result.sort();
}

function mergeObject(base, incoming) {
  const result = isObject(base) ? { ...base } : {};
  if (!isObject(incoming)) return result;
  for (const [key, value] of Object.entries(incoming)) {
    if (value === null || value === undefined || value === "") continue;
    if (isObject(value) && isObject(result[key])) result[key] = mergeObject(result[key], value);
    else if (Array.isArray(value) && value.length === 0 && Array.isArray(result[key])) continue;
    else result[key] = value;
  }
  return result;
}

function normalizeLevel(value, fallback = 2) {
  const parsed = number(value, null);
  return parsed === 3 || String(value).toLowerCase().includes("level3") ? 3 : fallback;
}

function levelFromPath(filePath) {
  return /(?:^|[/\\])level3(?:[/\\]|[-_])/i.test(filePath) ? 3 : 2;
}

function normalizeBehavior(value) {
  const normalized = text(value).trim().toUpperCase();
  if (normalized === "SELF_CORRECT") return "SELF_CORRECTION";
  if (normalized === "HESITATE") return "HESITATION";
  return normalized || null;
}

function issueCode(value) {
  const raw = text(
    isObject(value)
      ? value.code ?? value.type ?? value.issueType ?? value.message
      : value,
  ).trim();
  if (!raw) return null;
  if (/websocket|data.?channel/i.test(raw)) return "WebSocket/DataChannel error";
  if (/inappropriate.?clarification|clarification.*inappropriate/i.test(raw)) {
    return "inappropriate clarification";
  }
  return raw;
}

function turnIssues(turn) {
  const hardGate = isObject(turn?.hardGate) ? turn.hardGate : {};
  const sources = [
    turn?.anomalies,
    turn?.errors,
    turn?.issues,
    hardGate.codes,
    hardGate.anomalies,
    turn?.hardGateCodes,
    turn?.hardGateAnomalies,
    hardGate.expectationViolations,
    turn?.expectationViolations,
  ];
  return unique(sources.flatMap((items) => array(items).map(issueCode))).filter(Boolean);
}

function normalizeTurn(raw, {
  sessionId,
  level = 2,
  defaultSeed = null,
  defaultBehavior = null,
  defaultEngine = null,
} = {}) {
  const source = isObject(raw) ? raw : {};
  const interview = isObject(source.interview) ? source.interview : {};
  const response = isObject(source.response) ? source.response : {};
  const hardGate = isObject(source.hardGate) ? source.hardGate : {};
  const transcription = isObject(source.transcription) ? source.transcription : {};
  const behavior = normalizeBehavior(
    source.behavior ?? source.nextUserBehavior ?? defaultBehavior,
  );
  const issues = turnIssues(source);
  const explicitStatus = text(
    source.status
      ?? source.result
      ?? hardGate.status
      ?? source.hardGateStatus
      ?? source.expectationResult,
  ).toUpperCase();
  const status = explicitStatus === "FAIL" || explicitStatus === "ERROR" || issues.length
    ? "FAIL"
    : "PASS";
  const state = source.interviewState ?? source.state ?? source.observed?.interviewState ?? null;
  const aiQuestion = firstText(
    source.aiQuestion,
    source.currentQuestion,
    source.supervisorInput?.currentQuestion,
    interview.currentQuestionText,
    interview.currentQuestionBefore,
    interview.questionId,
  );
  const userTranscript = firstText(
    source.userTranscript,
    transcription.finalText,
    source.transcript,
    source.lastTranscript,
    "",
  ) ?? "";
  const aiResponse = firstText(
    source.aiResponse,
    source.assistantResponse,
    response.assistantTranscript,
    response.text,
    interview.nextQuestionText,
  );
  const expectations = source.expectations ?? hardGate.expectations ?? null;
  const observed = source.observed ?? hardGate.observed ?? null;
  const expected = source.expected ?? source.expectedConditions ?? hardGate.expectedConditions ?? expectations;
  const realTiming = source.realTiming ?? source.timing ?? source.latency ?? null;
  return {
    ...source,
    sessionId: source.sessionId ?? sessionId ?? null,
    turn: number(source.turn, null),
    level: normalizeLevel(source.level, level),
    engine: source.engine ?? source.provider ?? defaultEngine ?? null,
    provider: source.provider ?? source.engine ?? defaultEngine ?? null,
    seed: source.seed ?? defaultSeed,
    behavior,
    status,
    issues,
    aiQuestion,
    userTranscript,
    aiResponse,
    expected,
    actual: observed,
    interviewState: state,
    expectations,
    observed,
    hardGate,
    response,
    transcription,
    realTiming,
    conversation: source.conversation ?? null,
    judgeReason: firstText(
      source.judgeReason,
      source.llmJudge?.reason,
      source.semanticJudge?.reason,
      source.judge?.reason,
    ),
    timeline: array(source.timeline),
  };
}

function sessionStatus(session) {
  const explicit = text(session?.status).toUpperCase();
  if (explicit === "FAIL" || explicit === "ERROR") return "FAIL";
  if (session?.error) return "FAIL";
  return array(session?.turns).some((turn) => turn.status === "FAIL") ? "FAIL" : "PASS";
}

function evidenceForDirectory(root, evidenceDir) {
  const result = {};
  const direct = {
    screenshot: "screenshot.png",
    telemetry: "telemetry.json",
    trace: "trace.zip",
  };
  for (const [key, name] of Object.entries(direct)) {
    const filePath = path.join(evidenceDir, name);
    if (fs.existsSync(filePath)) result[key] = filePath;
  }
  const tracePathFile = path.join(evidenceDir, "playwright-trace-path.txt");
  if (!result.trace && fs.existsSync(tracePathFile)) {
    const candidate = text(fs.readFileSync(tracePathFile, "utf8")).trim();
    if (candidate && fs.existsSync(candidate)) result.trace = candidate;
  }
  if (!Object.keys(result).length) return null;
  return { ...result, directory: evidenceDir, root };
}

function findEvidence(root, files) {
  const bySession = new Map();
  for (const filePath of files) {
    if (path.basename(filePath) !== "telemetry.json") continue;
    if (!filePath.includes(`${path.sep}evidence${path.sep}`)) continue;
    const directory = path.dirname(filePath);
    const snapshot = readJson(filePath);
    const sessionId = snapshot?.sessionId;
    if (sessionId) bySession.set(sessionId, evidenceForDirectory(root, directory));
  }
  return bySession;
}

function mergeSessionTurn(session, rawTurn, defaults) {
  const normalized = normalizeTurn(rawTurn, {
    sessionId: session.sessionId,
    level: defaults.level ?? session.level ?? 2,
    defaultSeed: defaults.seed ?? session.seed ?? null,
    defaultBehavior: defaults.behavior ?? session.scenario ?? null,
    defaultEngine: defaults.engine ?? defaults.provider ?? session.engine ?? session.provider ?? null,
  });
  const key = normalized.turn === null ? `unknown-${session.turns.length}` : String(normalized.turn);
  const existingIndex = session.turns.findIndex((turn) => (
    (turn.turn === normalized.turn && normalized.turn !== null)
  ));
  if (existingIndex >= 0) {
    session.turns[existingIndex] = mergeObject(session.turns[existingIndex], normalized);
    session.turns[existingIndex].issues = unique([
      ...session.turns[existingIndex].issues,
      ...normalized.issues,
    ]);
    return session.turns[existingIndex];
  }
  session.turns.push({ ...normalized, _key: key });
  return session.turns.at(-1);
}

export function loadVoiceRunData({ runDir, runId = null } = {}) {
  const root = path.resolve(runDir || process.cwd());
  const files = walkFiles(root);
  const byName = (name) => files.filter((filePath) => path.basename(filePath) === name);
  const rootOrFirst = (name) => {
    const direct = path.join(root, name);
    return fs.existsSync(direct) ? direct : byName(name)[0] ?? null;
  };
  const summaryPath = rootOrFirst("summary.json")
    || rootOrFirst("exploration-summary.json");
  const summary = summaryPath ? readJson(summaryPath) ?? {} : {};
  const metricsPath = rootOrFirst("metrics.json");
  const failuresPath = rootOrFirst("failures.json");
  const metrics = metricsPath ? readJson(metricsPath) ?? {} : {};
  const explicitFailures = failuresPath ? readJson(failuresPath) ?? [] : [];
  const sessions = new Map();
  const ensureSession = (sessionId, defaults = {}) => {
    const key = text(sessionId || defaults.sessionId || `session-${sessions.size + 1}`);
    if (!sessions.has(key)) {
      sessions.set(key, {
        sessionId: key,
        index: defaults.index ?? sessions.size + 1,
        level: normalizeLevel(defaults.level, levelFromPath(defaults.sourcePath || "")),
        seed: defaults.seed ?? summary.seed ?? null,
        scenario: defaults.scenario ?? null,
        status: defaults.status ?? null,
        error: defaults.error ?? null,
        path: defaults.path ?? null,
        testedBehaviors: [...array(defaults.testedBehaviors)],
        liveApiUsed: Boolean(defaults.liveApiUsed),
        transcriptSource: defaults.transcriptSource ?? null,
        engine: defaults.engine ?? defaults.provider ?? null,
        provider: defaults.provider ?? defaults.engine ?? null,
        evidence: null,
        turns: [],
      });
    } else {
      const current = sessions.get(key);
      Object.assign(current, {
        index: current.index ?? defaults.index,
        level: current.level || normalizeLevel(defaults.level, 2),
        seed: current.seed ?? defaults.seed ?? summary.seed ?? null,
        scenario: current.scenario ?? defaults.scenario ?? null,
        status: current.status ?? defaults.status ?? null,
        error: current.error ?? defaults.error ?? null,
        path: current.path ?? defaults.path ?? null,
        liveApiUsed: current.liveApiUsed || Boolean(defaults.liveApiUsed),
        transcriptSource: current.transcriptSource ?? defaults.transcriptSource ?? null,
        engine: current.engine ?? defaults.engine ?? defaults.provider ?? null,
        provider: current.provider ?? defaults.provider ?? defaults.engine ?? null,
      });
    }
    return sessions.get(key);
  };

  const addSessionRecord = (record, defaults = {}) => {
    const source = isObject(record) ? record : {};
    const session = ensureSession(
      source.sessionId ?? source.id ?? defaults.sessionId,
      { ...defaults, ...source },
    );
    session.testedBehaviors = unique([
      ...session.testedBehaviors,
      ...array(source.testedBehaviors).map(normalizeBehavior),
    ]);
    for (const turn of array(source.turns)) mergeSessionTurn(session, turn, {
      ...defaults,
      ...source,
    });
    return session;
  };

  for (const session of array(summary.sessions)) addSessionRecord(session, {
    level: summary.level,
    seed: summary.seed,
    sourcePath: summaryPath,
  });

  const level3Summaries = byName("level3-summary.json");
  for (const filePath of level3Summaries) {
    const level3 = readJson(filePath) ?? {};
    const records = [...array(level3.results), ...array(level3.failures)];
    for (const record of records) {
      const session = addSessionRecord({
        sessionId: record.sessionId ?? `level3-${record.sessionIndex ?? sessions.size + 1}`,
        index: record.sessionIndex,
        status: record.status,
        level: 3,
        seed: record.seed ?? level3.seed,
        scenario: record.behavior,
        liveApiUsed: true,
        turns: [{
          turn: record.turn ?? record.level2Turn ?? 1,
          behavior: record.behavior,
          status: record.status,
          transcript: record.transcript,
          assistantResponse: record.assistantResponse,
          state: record.state,
          timing: record.timing,
          hardGate: record.hardGate,
          anomalies: record.hardGate?.codes ?? [],
        }],
      }, { level: 3, seed: level3.seed, sourcePath: filePath });
      session.level = 3;
    }
  }

  const evidenceMap = findEvidence(root, files);
  const snapshots = byName("telemetry.json").filter((filePath) => (
    !filePath.includes(`${path.sep}evidence${path.sep}`)
  ));
  for (const filePath of snapshots) {
    const snapshot = readJson(filePath);
    if (!isObject(snapshot)) continue;
    const snapshotDefaults = {
      level: snapshot.level ?? levelFromPath(filePath),
      seed: snapshot.seed,
      scenario: snapshot.scenario,
      liveApiUsed: snapshot.liveApiUsed,
      transcriptSource: snapshot.transcriptSource,
      engine: snapshot.engine ?? snapshot.provider,
      provider: snapshot.provider ?? snapshot.engine,
      sourcePath: filePath,
    };
    const snapshotEvidence = evidenceMap.get(snapshot.sessionId) ?? null;
    const snapshotTurns = array(snapshot.turns);
    if (!snapshotTurns.length) {
      const session = ensureSession(snapshot.sessionId, snapshotDefaults);
      session.evidence = snapshotEvidence ?? session.evidence;
    }
    for (const turn of snapshotTurns) {
      // A single VoiceTelemetry instance can cover several bounded sessions.
      // Prefer the per-turn sessionId so the report does not collapse those
      // sessions into the telemetry container's sessionId. When the telemetry
      // writer uses a container sessionId, use sessionIndex to join the
      // session records written by the supervisor.
      const indexedSession = turn.sessionIndex === null || turn.sessionIndex === undefined
        ? null
        : [...sessions.values()].find((candidate) => (
          Number(candidate.index) === Number(turn.sessionIndex)
        ));
      // A report can aggregate several independent Playwright runs. Their
      // sessionIndex values commonly restart at 1, so a per-turn sessionId
      // must win over the index when the snapshot provides one.
      const targetSessionId = turn.sessionId ?? indexedSession?.sessionId ?? snapshot.sessionId;
      const session = ensureSession(targetSessionId, {
        ...snapshotDefaults,
        sessionId: targetSessionId,
        index: turn.sessionIndex ?? indexedSession?.index,
        seed: turn.seed ?? snapshot.seed,
        scenario: turn.scenario ?? snapshot.scenario,
        engine: turn.engine ?? snapshot.engine ?? snapshot.provider,
        provider: turn.provider ?? snapshot.provider ?? snapshot.engine,
      });
      session.evidence = snapshotEvidence ?? evidenceMap.get(session.sessionId) ?? session.evidence;
      mergeSessionTurn(session, turn, {
        level: turn.level ?? snapshot.level ?? levelFromPath(filePath),
        seed: turn.seed ?? snapshot.seed,
        behavior: turn.behavior ?? snapshot.scenario ?? null,
        sourcePath: filePath,
      });
    }
    if (array(snapshot.errors).length) {
      const errorMessage = text(snapshot.errors[0]?.message ?? snapshot.errors[0]);
      const targetSessions = snapshotTurns.map((turn) => {
        const indexedSession = turn.sessionIndex === null || turn.sessionIndex === undefined
          ? null
          : [...sessions.values()].find((candidate) => (
            Number(candidate.index) === Number(turn.sessionIndex)
          ));
        return ensureSession(
          turn.sessionId ?? indexedSession?.sessionId ?? snapshot.sessionId,
          snapshotDefaults,
        );
      });
      const targets = targetSessions.length
        ? targetSessions
        : [ensureSession(snapshot.sessionId, snapshotDefaults)];
      for (const session of targets) {
        if (!session.error) session.error = errorMessage;
      }
    }
  }

  const lineFiles = [...byName("sessions.jsonl"), ...byName("telemetry.jsonl")];
  for (const filePath of lineFiles) {
    for (const record of readJsonLines(filePath)) {
      if (isObject(record) && record.turn !== undefined && (
        record.interview || record.transcription || record.behavior || record.hardGate
      )) {
        const indexedSession = record.sessionIndex === null || record.sessionIndex === undefined
          ? null
          : [...sessions.values()].find((candidate) => (
            Number(candidate.index) === Number(record.sessionIndex)
          ));
        // A snapshot has already established the concrete per-turn session
        // when available. Keep the old index-based fallback for supervisor
        // streams whose sessionId is only a shared telemetry container.
        const targetSessionId = sessions.has(record.sessionId)
          ? record.sessionId
          : indexedSession?.sessionId ?? record.sessionId;
        const session = ensureSession(targetSessionId, {
          level: record.level ?? levelFromPath(filePath),
          seed: record.seed,
          scenario: record.scenario,
          index: record.sessionIndex ?? indexedSession?.index,
          sourcePath: filePath,
          engine: record.engine ?? record.provider,
          provider: record.provider ?? record.engine,
        });
        mergeSessionTurn(session, record, {
          level: record.level ?? levelFromPath(filePath),
          seed: record.seed,
          behavior: record.behavior ?? record.scenario ?? null,
          engine: record.engine ?? record.provider,
          provider: record.provider ?? record.engine,
          sourcePath: filePath,
        });
      } else if (record.event === "session_summary" || record.event === "session_started") {
        addSessionRecord(record, { sourcePath: filePath });
      } else if (isObject(record) && Array.isArray(record.turns)) {
        addSessionRecord(record, { sourcePath: filePath });
      }
    }
  }

  const rawFailureList = Array.isArray(explicitFailures)
    ? explicitFailures
    : array(explicitFailures.failures);
  const failures = rawFailureList.map((failure) => ({ ...failure }));
  for (const session of sessions.values()) {
    if (!session.evidence) session.evidence = evidenceMap.get(session.sessionId) ?? null;
    session.turns.sort((left, right) => (left.turn ?? 0) - (right.turn ?? 0));
  }

  return {
    runDir: root,
    runId: runId ?? summary.runId ?? path.basename(root),
    summary,
    metrics,
    explicitFailures: failures,
    sessions: [...sessions.values()],
    files,
    generatedAt: new Date().toISOString(),
  };
}

function percentile(values, ratio) {
  const sorted = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (!sorted.length) return null;
  const index = (sorted.length - 1) * ratio;
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  if (lower === upper) return Math.round(sorted[lower]);
  return Math.round(sorted[lower] + (sorted[upper] - sorted[lower]) * (index - lower));
}

function metricValue(turn, name) {
  const candidates = [
    turn?.response?.[name],
    turn?.realTiming?.[name],
    turn?.timing?.[name],
    turn?.latency?.[name],
  ];
  if (name === "fullTurnLatencyMs") {
    candidates.push(
      turn?.response?.firstAudioLatencyMs,
      turn?.response?.simulatedTurnLatencyMs,
      turn?.realTiming?.speechEndToAssistantStartMs,
    );
  }
  return candidates.map((value) => number(value, null)).find((value) => value !== null) ?? null;
}

function rawMetricValues(metrics, level, name) {
  const levelKeys = level === 3 ? ["level3", "3", "LEVEL3"] : ["level2", "2", "LEVEL2"];
  const locations = [
    metrics?.latency,
    metrics?.latencies,
    metrics,
  ];
  for (const location of locations) {
    if (!isObject(location)) continue;
    for (const key of levelKeys) {
      const value = location[key]?.[name];
      if (Array.isArray(value)) return value.map((item) => number(item, null)).filter((item) => item !== null);
      if (number(value, null) !== null) return [number(value)];
    }
  }
  return [];
}

function rawMetricSummary(metrics, level, name) {
  const levelKeys = level === 3 ? ["level3", "3", "LEVEL3"] : ["level2", "2", "LEVEL2"];
  const locations = [metrics?.latency, metrics?.latencies, metrics];
  for (const location of locations) {
    if (!isObject(location)) continue;
    for (const key of levelKeys) {
      const value = location[key]?.[name];
      if (!isObject(value)) continue;
      const count = number(value.count, null);
      const p50 = number(value.p50, null);
      const p95 = number(value.p95, null);
      const p99 = number(value.p99, null);
      if (count !== null && (p50 !== null || p95 !== null || p99 !== null)) {
        return { count, p50, p95, p99 };
      }
    }
  }
  return null;
}

function qualityValue(turn, key) {
  const source = turn?.conversationQuality
    ?? turn?.llmJudge
    ?? turn?.judge
    ?? turn?.quality
    ?? turn?.semanticJudge;
  if (!isObject(source) || source[key] === undefined) return null;
  return boolean(source[key], null);
}

function evidenceLinks(evidence, reportDir) {
  if (!evidence) return null;
  const links = {};
  for (const key of ["screenshot", "telemetry", "trace"]) {
    if (evidence[key] && fs.existsSync(evidence[key])) {
      links[key] = path.relative(reportDir, evidence[key]).split(path.sep).join("/");
    }
  }
  return Object.keys(links).length ? links : null;
}

function failureTypeForIssue(issue) {
  const normalized = issueCode(issue);
  if (!normalized) return "ERROR";
  if (REPORT_FAILURE_TYPES.includes(normalized)) return normalized;
  if (/answer.*persist|persist.*answer/i.test(normalized)) return "ANSWER_NOT_PERSISTED";
  if (/question.*advance|advance.*question/i.test(normalized)) return "QUESTION_NOT_ADVANCED";
  if (/repeat/i.test(normalized)) return "REPEATED_QUESTION";
  if (/context/i.test(normalized)) return "CONTEXT_LOSS";
  if (/self.?correct/i.test(normalized)) return "SELF_CORRECTION_FAILED";
  if (/multi.?answer|information.*lost/i.test(normalized)) return "MULTI_ANSWER_INFORMATION_LOST";
  if (/5\d\d|api/i.test(normalized)) return "API_5XX";
  if (/websocket|data.?channel/i.test(normalized)) return "WebSocket/DataChannel error";
  if (/clarification/i.test(normalized)) return "inappropriate clarification";
  return normalized;
}

function buildFailureRecords(data, sessions) {
  const result = [];
  const seen = new Set();
  const add = (raw, session, turn, issue) => {
    const issueType = failureTypeForIssue(issue ?? raw?.issueType ?? raw?.type ?? raw?.error);
    const key = [session?.sessionId, turn?.turn ?? raw?.turn ?? null, issueType].join("|");
    if (seen.has(key)) return;
    seen.add(key);
    result.push({
      failureId: text(raw?.failureId || `${data.runId}-F${String(result.length + 1).padStart(4, "0")}`),
      sessionId: session?.sessionId ?? raw?.sessionId ?? null,
      engine: normalizeEngine(raw?.engine ?? raw?.provider ?? turn?.engine ?? turn?.provider ?? session?.engine ?? session?.provider),
      provider: raw?.provider ?? turn?.provider ?? session?.provider ?? null,
      seed: raw?.seed ?? turn?.seed ?? session?.seed ?? data.summary?.seed ?? null,
      behavior: normalizeBehavior(raw?.behavior ?? turn?.behavior ?? session?.scenario) ?? null,
      turn: number(raw?.turn ?? turn?.turn, null),
      issueType,
      level: normalizeLevel(raw?.level ?? turn?.level ?? session?.level, 2),
      status: "FAIL",
      aiQuestion: firstText(raw?.aiQuestion, turn?.aiQuestion),
      userTranscript: firstText(raw?.userTranscript, turn?.userTranscript, "") ?? "",
      aiResponse: firstText(raw?.aiResponse, turn?.aiResponse),
      expected: raw?.expected ?? turn?.expected ?? turn?.expectations ?? null,
      actual: raw?.actual ?? turn?.actual ?? turn?.observed ?? null,
      interviewState: raw?.interviewState ?? turn?.interviewState ?? null,
      conversation: raw?.conversation ?? turn?.conversation ?? null,
      judgeReason: firstText(
        raw?.judgeReason,
        raw?.llmJudge?.reason,
        raw?.semanticJudge?.reason,
        turn?.judgeReason,
        turn?.llmJudge?.reason,
        turn?.semanticJudge?.reason,
      ),
      timeline: raw?.timeline ?? turn?.timeline ?? [],
      evidence: raw?.evidence ?? session?.evidence ?? null,
      raw,
    });
  };

  for (const raw of data.explicitFailures) {
    const session = sessions.find((item) => item.sessionId === raw?.sessionId);
    const turn = session?.turns.find((item) => item.turn === number(raw?.turn, null));
    add(raw, session, turn, raw?.issueType ?? raw?.type ?? raw?.error);
  }
  for (const session of sessions) {
    for (const turn of session.turns) {
      for (const issue of turn.issues) add({}, session, turn, issue);
    }
    if (sessionStatus(session) === "FAIL" && !session.turns.some((turn) => turn.status === "FAIL")) {
      add({ error: session.error }, session, null, session.error || "ERROR");
    }
  }
  return result;
}

function buildBehaviorCoverage(sessions) {
  const coverage = Object.fromEntries(REPORT_BEHAVIORS.map((behavior) => [behavior, {
    behavior,
    runs: 0,
    pass: 0,
    fail: 0,
    passRate: null,
  }]));
  for (const session of sessions) {
    for (const turn of session.turns) {
      if (!turn.behavior || !coverage[turn.behavior]) continue;
      const item = coverage[turn.behavior];
      item.runs += 1;
      if (turn.status === "FAIL") item.fail += 1;
      else item.pass += 1;
    }
  }
  for (const item of Object.values(coverage)) {
    item.passRate = item.runs ? Math.round((item.pass / item.runs) * 1000) / 10 : null;
  }
  return coverage;
}

const REPORT_ENGINE_INFO = Object.freeze({
  gpt_live: { label: "GPT-Live", description: "GPT-Live実音声経路" },
  transcribe_polly: { label: "Transcribe + Polly", description: "Amazon Transcribe + Polly実音声経路" },
});

function normalizeEngine(value) {
  const normalized = text(value).trim().toLowerCase();
  if (normalized === "gpt-live" || normalized === "gptlive" || normalized === "gpt_live") return "gpt_live";
  if (normalized === "transcribe+polly" || normalized === "transcribe-polly" || normalized === "transcribe_polly") return "transcribe_polly";
  return normalized || null;
}

function engineOf(value) {
  if (isObject(value)) return normalizeEngine(value.engine ?? value.provider ?? value.transcriptSource);
  return normalizeEngine(value);
}

function buildEngineComparison(sessions) {
  const result = {};
  for (const engine of Object.keys(REPORT_ENGINE_INFO)) {
    const matching = sessions.filter((session) => engineOf(session) === engine);
    const turns = matching.flatMap((session) => session.turns);
    const pass = matching.filter((session) => sessionStatus(session) === "PASS").length;
    result[engine] = {
      engine,
      label: REPORT_ENGINE_INFO[engine].label,
      description: REPORT_ENGINE_INFO[engine].description,
      sessions: matching.length,
      turns: turns.length,
      pass,
      fail: matching.length - pass,
      successRate: matching.length ? Math.round((pass / matching.length) * 1000) / 10 : null,
      behaviorCoverage: buildBehaviorCoverage(matching),
      latency: buildLatency(matching, {}),
    };
  }
  return result;
}

function buildLatencyByEngine(sessions) {
  const result = {};
  for (const engine of Object.keys(REPORT_ENGINE_INFO)) {
    const matching = sessions.filter((session) => engineOf(session) === engine);
    const values = {};
    for (const name of LATENCY_METRICS) {
      const items = matching.flatMap((session) => session.turns)
        .map((turn) => metricValue(turn, name))
        .filter((value) => value !== null);
      values[name] = items.length
        ? { count: items.length, p50: percentile(items, 0.5), p95: percentile(items, 0.95), p99: percentile(items, 0.99) }
        : { count: 0, p50: null, p95: null, p99: null };
    }
    result[engine] = values;
  }
  return result;
}

function buildLatency(sessions, metrics) {
  const result = { level2: {}, level3: {} };
  for (const level of [2, 3]) {
    for (const name of LATENCY_METRICS) {
      const values = [];
      for (const session of sessions.filter((item) => item.level === level)) {
        for (const turn of session.turns) {
          const value = metricValue(turn, name);
          if (value !== null) values.push(value);
        }
      }
      values.push(...rawMetricValues(metrics, level, name));
      const aggregate = rawMetricSummary(metrics, level, name);
      result[`level${level}`][name] = values.length
        ? {
            count: values.length,
            p50: percentile(values, 0.5),
            p95: percentile(values, 0.95),
            p99: percentile(values, 0.99),
          }
        : aggregate ?? {
            count: 0,
            p50: null,
            p95: null,
            p99: null,
          };
    }
  }
  return result;
}

function buildQuality(sessions, metrics) {
  const result = Object.fromEntries(QUALITY_METRICS.map((key) => [key, {
    key,
    count: 0,
    pass: 0,
    fail: 0,
    passRate: null,
  }]));
  for (const session of sessions) {
    for (const turn of session.turns) {
      for (const key of QUALITY_METRICS) {
        const value = qualityValue(turn, key);
        if (value === null) continue;
        result[key].count += 1;
        if (value) result[key].pass += 1;
        else result[key].fail += 1;
      }
    }
  }
  const rawQuality = metrics?.conversationQuality ?? metrics?.quality;
  if (isObject(rawQuality)) {
    for (const key of QUALITY_METRICS) {
      const value = rawQuality[key];
      if (Array.isArray(value)) {
        for (const item of value) {
          const parsed = boolean(item, null);
          if (parsed === null) continue;
          result[key].count += 1;
          if (parsed) result[key].pass += 1;
          else result[key].fail += 1;
        }
      } else if (isObject(value) && number(value.count, null) !== null) {
        result[key].count += number(value.count, 0);
        result[key].pass += number(value.pass, 0);
        result[key].fail += number(value.fail, 0);
      }
    }
  }
  for (const item of Object.values(result)) {
    item.passRate = item.count ? Math.round((item.pass / item.count) * 1000) / 10 : null;
  }
  return result;
}

function buildUsage(data, sessions) {
  const totals = isObject(data.summary?.totals) ? data.summary.totals : {};
  const costGuard = data.summary?.costGuard ?? {};
  const counters = costGuard?.counters ?? costGuard ?? {};
  const jevCalls = number(
    totals.jevApiCalls ?? totals.jevCalls ?? counters.jevCalls,
    sessions.flatMap((session) => session.turns)
      .filter((turn) => turn.jev?.called && !turn.jev?.cacheHit).length,
  );
  const cacheHits = number(
    totals.cacheHits ?? counters.jevCacheHits,
    sessions.flatMap((session) => session.turns).filter((turn) => turn.jev?.cacheHit).length,
  );
  const cacheMisses = number(
    totals.cacheMisses ?? counters.jevCacheMisses,
    Math.max(0, jevCalls - cacheHits),
  );
  const judgeCalls = number(
    totals.llmJudgeCalls ?? counters.llmJudgeCalls,
    sessions.flatMap((session) => session.turns)
      .filter((turn) => turn.llmJudge?.called || turn.semanticJudge?.called).length,
  );
  const gptLiveSessions = sessions.filter((session) => session.level === 3 || session.liveApiUsed).length;
  const transcribeUsage = totals.transcribeUsage
    ?? data.summary?.usage?.transcribe
    ?? data.metrics?.usage?.transcribe
    ?? null;
  const pollyUsage = totals.pollyUsage
    ?? data.summary?.usage?.polly
    ?? data.metrics?.usage?.polly
    ?? null;
  return {
    jevCalls,
    jevCacheHits: cacheHits,
    jevCacheMisses: cacheMisses,
    llmJudgeCalls: judgeCalls,
    gptLiveSessions,
    transcribeUsage,
    pollyUsage,
    estimatedCost: null,
  };
}

function buildSummary(
  data,
  sessions,
  failures,
  behaviorCoverage,
  latency,
  quality,
  engineComparison,
  latencyByEngine,
) {
  const passSessions = sessions.filter((session) => sessionStatus(session) === "PASS").length;
  const failSessions = sessions.length - passSessions;
  const turns = sessions.flatMap((session) => session.turns);
  const passTurns = turns.filter((turn) => turn.status !== "FAIL").length;
  const failTurns = turns.length - passTurns;
  const usage = buildUsage(data, sessions);
  return {
    runId: data.runId,
    generatedAt: data.generatedAt,
    runDate: data.summary?.runDate ?? data.summary?.startedAt ?? data.generatedAt,
    sessions: sessions.length,
    turns: turns.length,
    pass: passSessions,
    fail: failSessions,
    passTurns,
    failTurns,
    successRate: sessions.length ? Math.round((passSessions / sessions.length) * 1000) / 10 : null,
    level2Sessions: sessions.filter((session) => session.level === 2).length,
    level3Sessions: sessions.filter((session) => session.level === 3).length,
    behaviorCoverage,
    failures,
    failureSummary: Object.fromEntries(REPORT_FAILURE_TYPES.map((type) => [
      type,
      failures.filter((failure) => failure.issueType === type).length,
    ])),
    latency,
    latencyByEngine,
    engineComparison,
    gptLivePassRate: engineComparison.gpt_live?.successRate ?? null,
    transcribePollyPassRate: engineComparison.transcribe_polly?.successRate ?? null,
    quality,
    usage,
  };
}

function previousRunDirectory(runDir) {
  const parent = path.dirname(runDir);
  if (!fs.existsSync(parent)) return null;
  const currentName = path.basename(runDir);
  const candidates = fs.readdirSync(parent, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && entry.name !== currentName && entry.name !== "latest")
    .map((entry) => path.join(parent, entry.name))
    .filter((directory) => walkFiles(directory).some((filePath) => (
      ["summary.json", "exploration-summary.json", "report.html"].includes(path.basename(filePath))
    )))
    .map((directory) => {
      try {
        return { directory, mtimeMs: fs.statSync(directory).mtimeMs };
      } catch {
        return { directory, mtimeMs: 0 };
      }
    })
    .sort((left, right) => right.mtimeMs - left.mtimeMs);
  return candidates[0]?.directory ?? null;
}

export function normalizeVoiceRunData(data, { reportDir = data.runDir } = {}) {
  const sessions = data.sessions.map((session) => ({
    ...session,
    level: normalizeLevel(session.level, 2),
    status: sessionStatus(session),
    evidence: session.evidence,
    turns: session.turns.map((turn) => normalizeTurn(turn, {
      sessionId: session.sessionId,
      level: session.level,
      defaultSeed: session.seed,
      defaultBehavior: session.scenario,
      defaultEngine: session.engine ?? session.provider,
    })),
  }));
  const failures = buildFailureRecords(data, sessions);
  const behaviorCoverage = buildBehaviorCoverage(sessions);
  const latency = buildLatency(sessions, data.metrics);
  const engineComparison = buildEngineComparison(sessions);
  const latencyByEngine = buildLatencyByEngine(sessions);
  const quality = buildQuality(sessions, data.metrics);
  const summary = buildSummary(
    data,
    sessions,
    failures,
    behaviorCoverage,
    latency,
    quality,
    engineComparison,
    latencyByEngine,
  );
  return {
    ...data,
    sessions,
    turns: sessions.flatMap((session) => session.turns),
    failures,
    summary,
    usage: summary.usage,
  };
}

function readPreviousReportData(runDir) {
  const previous = previousRunDirectory(runDir);
  if (!previous) return null;
  try {
    return normalizeVoiceRunData(loadVoiceRunData({ runDir: previous }), { reportDir: previous });
  } catch {
    return null;
  }
}

function safeJson(value) {
  return JSON.stringify(value)
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e")
    .replace(/&/g, "\\u0026")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");
}

export function renderVoiceReportHtml(data, { reportDir = data.runDir } = {}) {
  const linkedFailures = data.failures.map((failure) => ({
    ...failure,
    evidence: evidenceLinks(
      failure.evidence
        ?? data.sessions.find((session) => session.sessionId === failure.sessionId)?.evidence,
      reportDir,
    ),
  }));
  const linkData = {
    ...data,
    reportLabels: {
      behaviors: REPORT_BEHAVIOR_INFO,
      failures: REPORT_FAILURE_INFO,
      latency: REPORT_LATENCY_INFO,
      quality: REPORT_QUALITY_INFO,
      engines: REPORT_ENGINE_INFO,
    },
    summary: {
      ...data.summary,
      failures: linkedFailures,
    },
    sessions: data.sessions.map((session) => ({
      ...session,
      evidence: evidenceLinks(session.evidence, reportDir),
    })),
    failures: linkedFailures,
  };
  const embedded = safeJson(linkData);
  return `<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KIKIORI 音声インタビューテストレポート</title>
<style>
:root { color-scheme: light; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; color: #172033; background: #f5f7fb; }
* { box-sizing: border-box; }
body { margin: 0; }
main { max-width: 1440px; margin: 0 auto; padding: 28px; }
h1, h2 { margin: 0 0 14px; }
h1 { font-size: 28px; }
h2 { font-size: 20px; margin-top: 30px; }
.muted { color: #64748b; }
.meta { display: flex; flex-wrap: wrap; gap: 8px 20px; margin: 0 0 24px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }
.card, .panel { background: white; border: 1px solid #dbe3ef; border-radius: 12px; box-shadow: 0 2px 7px #1e293b0d; }
.card { padding: 14px; }
.card .label { color: #64748b; font-size: 12px; }
.card .value { display: block; font-size: 24px; font-weight: 700; margin-top: 4px; }
.panel { padding: 16px; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; min-width: 620px; }
th, td { padding: 9px 10px; border-bottom: 1px solid #e5eaf2; text-align: left; vertical-align: top; }
th { color: #475569; font-size: 12px; background: #f8fafc; }
td.num, th.num { text-align: right; }
.bar { height: 9px; min-width: 110px; background: #e7edf5; border-radius: 99px; overflow: hidden; }
.bar > span { display: block; height: 100%; background: #2563eb; border-radius: inherit; }
.pass { color: #15803d; font-weight: 650; }
.fail { color: #b91c1c; font-weight: 650; }
.level2 { color: #475569; }
.level3 { color: #7c3aed; font-weight: 650; }
.empty { padding: 18px; color: #64748b; }
.filters { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
select { border: 1px solid #cbd5e1; border-radius: 7px; padding: 7px 9px; background: white; color: inherit; }
details.failure { border: 1px solid #dbe3ef; border-radius: 9px; background: white; margin: 8px 0; }
details.failure summary { cursor: pointer; padding: 11px 13px; list-style: none; }
details.failure summary::-webkit-details-marker { display: none; }
details.failure summary::before { content: "▸"; margin-right: 8px; color: #64748b; }
details.failure[open] summary::before { content: "▾"; }
.failure-body { padding: 0 13px 14px; border-top: 1px solid #e5eaf2; }
.failure-explanation { margin: 12px 0 0; color: #334155; }
.detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 12px; margin-top: 12px; }
.detail-box { background: #f8fafc; border-radius: 8px; padding: 10px; }
.detail-box h3 { font-size: 13px; margin: 0 0 6px; color: #475569; }
pre { white-space: pre-wrap; word-break: break-word; margin: 0; font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
.evidence a { margin-right: 12px; }
.small { font-size: 12px; }
svg.spark { width: 100%; height: 46px; }
@media (max-width: 700px) { main { padding: 16px; } h1 { font-size: 23px; } }
</style>
</head>
<body>
<main>
  <header>
    <h1>KIKIORI 音声インタビューテストレポート</h1>
    <p id="run-meta" class="meta muted"></p>
  </header>
  <section><h2>実行概要</h2><div id="dashboard" class="cards"></div></section>
  <section><h2>実行方式別比較</h2><div class="panel"><div id="engine-comparison"></div></div></section>
  <section><h2>回答パターンの網羅状況</h2><div class="panel"><div id="behavior-coverage"></div></div></section>
  <section><h2>失敗の概要</h2><div class="panel"><div id="failure-summary"></div></div></section>
  <section><h2>会話品質（LLM Judge）</h2><div class="panel"><div id="quality"></div></div></section>
  <section><h2>応答時間</h2><div class="panel"><div id="latency"></div></div></section>
  <section><h2>API利用状況・コスト</h2><div class="panel"><div id="usage"></div></div></section>
  <section><h2>前回実行との比較</h2><div class="panel"><div id="comparison"></div></div></section>
  <section><h2>失敗の詳細</h2><div class="panel">
    <div class="filters">
      <select id="filter-behavior"><option value="">回答パターン：すべて</option></select>
      <select id="filter-status"><option value="">結果：すべて</option><option value="PASS">成功</option><option value="FAIL">失敗</option></select>
      <select id="filter-level"><option value="">実行レベル：すべて</option><option value="2">Level 2（ローカル）</option><option value="3">Level 3（実音声API）</option></select>
      <select id="filter-engine"><option value="">実行方式：すべて</option></select>
      <select id="filter-issue"><option value="">失敗種別：すべて</option></select>
    </div>
    <div id="failure-list"></div>
  </div></section>
</main>
<script id="voice-report-data" type="application/json">${embedded}</script>
<script>
(() => {
  const data = JSON.parse(document.getElementById("voice-report-data").textContent);
  const summary = data.summary || {};
  const labels = data.reportLabels || {};
  const info = (group, key, fallback = key) => group?.[key] || { label: fallback, description: "" };
  const behaviorInfo = (key) => info(labels.behaviors, key);
  const failureInfo = (key) => info(labels.failures, key, "未分類の失敗");
  const latencyLabel = (key) => labels.latency?.[key] || key;
  const qualityLabel = (key) => labels.quality?.[key] || key;
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
  const json = (value) => esc(JSON.stringify(value ?? null, null, 2));
  const fmt = (value, suffix = "") => value === null || value === undefined ? "—" : esc(value) + suffix;
  const rate = (value) => value === null || value === undefined ? "—" : esc(value.toFixed ? value.toFixed(1) : value) + "%";
  const date = (value) => {
    if (!value) return "—";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? esc(value) : esc(parsed.toLocaleString("ja-JP"));
  };
  const statusLabel = (value) => value === "FAIL" ? "失敗" : value === "PASS" ? "成功" : value || "—";
  const level = (value) => value === 3
    ? '<span class="level3">Level 3（実音声API）</span>'
    : '<span class="level2">Level 2（ローカル・無料）</span>';
  const code = (value) => '<span class="muted small">（コード: ' + esc(value) + '）</span>';
  const card = (label, value) => '<div class="card"><span class="label">' + esc(label) + '</span><span class="value">' + value + '</span></div>';
  document.getElementById("run-meta").innerHTML = [
    "実行ID: <strong>" + esc(summary.runId) + "</strong>",
    "実行日時: " + date(summary.runDate),
  ].join("<span>•</span>");
  document.getElementById("dashboard").innerHTML = [
    card("セッション数", fmt(summary.sessions)), card("ターン数", fmt(summary.turns)),
    card("成功", '<span class="pass">' + fmt(summary.pass) + '</span>'),
    card("失敗", '<span class="fail">' + fmt(summary.fail) + '</span>'),
    card("成功率", rate(summary.successRate)),
    card("Level 2（ローカル）", fmt(summary.level2Sessions)),
    card("Level 3（実音声API）", fmt(summary.level3Sessions)),
    card("GPT-Live 成功率", rate(summary.gptLivePassRate)),
    card("Transcribe + Polly 成功率", rate(summary.transcribePollyPassRate)),
  ].join("");
  const engineComparison = summary.engineComparison || {};
  const engineComparisonRows = Object.values(engineComparison).map((engine) => {
    const behaviorRuns = Object.values(engine.behaviorCoverage || {})
      .filter((item) => item.runs > 0).length;
    return '<tr><td><strong>' + esc(engine.label) + '</strong><div class="muted small">' + esc(engine.description) + '</div><span class="muted small">（コード: ' + esc(engine.engine) + '）</span></td>'
      + '<td class="num">' + fmt(engine.sessions) + '</td><td class="num">' + fmt(engine.turns) + '</td>'
      + '<td class="num pass">' + fmt(engine.pass) + '</td><td class="num fail">' + fmt(engine.fail) + '</td>'
      + '<td class="num">' + rate(engine.successRate) + '</td><td class="num">' + fmt(behaviorRuns) + '</td></tr>';
  }).join("");
  document.getElementById("engine-comparison").innerHTML = engineComparisonRows
    ? '<table><thead><tr><th>実行方式</th><th class="num">セッション</th><th class="num">ターン</th><th class="num">成功</th><th class="num">失敗</th><th class="num">成功率</th><th class="num">実行済みパターン数</th></tr></thead><tbody>' + engineComparisonRows + '</tbody></table>'
    : '<div class="empty">方式別の実音声結果はありません。</div>';
  const engineBehaviorRows = Object.values(engineComparison).flatMap((engine) =>
    Object.values(engine.behaviorCoverage || {}).filter((item) => item.runs > 0).map((item) => ({ engine, item }))
  ).map(({ engine, item }) => '<tr><td>' + esc(engine.label) + '</td><td><strong>' + esc(behaviorInfo(item.behavior).label) + '</strong><div class="muted small">' + esc(item.behavior) + '</div></td><td class="num">' + fmt(item.runs) + '</td><td class="num pass">' + fmt(item.pass) + '</td><td class="num fail">' + fmt(item.fail) + '</td><td class="num">' + rate(item.passRate) + '</td></tr>').join("");
  if (engineBehaviorRows) {
    document.getElementById("behavior-coverage").insertAdjacentHTML("afterbegin", '<h3>実行方式別</h3><table><thead><tr><th>実行方式</th><th>回答パターン</th><th class="num">実行回数</th><th class="num">成功</th><th class="num">失敗</th><th class="num">成功率</th></tr></thead><tbody>' + engineBehaviorRows + '</tbody></table><h3>全実行</h3>');
  }
  const behaviorRows = Object.values(summary.behaviorCoverage || {}).map((item) => {
    const width = item.passRate === null ? 0 : Math.max(0, Math.min(100, item.passRate));
    const itemInfo = behaviorInfo(item.behavior);
    return '<tr><td><strong>' + esc(itemInfo.label) + '</strong><div class="muted small">' + esc(itemInfo.description) + '</div>' + code(item.behavior) + '</td><td class="num">' + fmt(item.runs) + '</td>'
      + '<td class="num pass">' + fmt(item.pass) + '</td><td class="num fail">' + fmt(item.fail) + '</td>'
      + '<td class="num">' + rate(item.passRate) + '</td><td><div class="bar"><span style="width:' + width + '%"></span></div></td></tr>';
  }).join("");
  document.getElementById("behavior-coverage").innerHTML = '<table><thead><tr><th>回答パターン</th><th class="num">実行回数</th><th class="num">成功</th><th class="num">失敗</th><th class="num">成功率</th><th>実行状況</th></tr></thead><tbody>' + behaviorRows + '</tbody></table>';
  const failureRows = Object.entries(summary.failureSummary || {}).map(([key, count]) => {
    const itemInfo = failureInfo(key);
    return '<tr><td><strong>' + esc(itemInfo.label) + '</strong><div>' + esc(itemInfo.description) + '</div>' + code(key) + '</td><td class="num ' + (count ? 'fail' : '') + '">' + fmt(count) + '</td></tr>';
  }).join("");
  document.getElementById("failure-summary").innerHTML = '<table><thead><tr><th>何が起きたか</th><th class="num">件数</th></tr></thead><tbody>' + failureRows + '</tbody></table>';
  const qualityRows = Object.values(summary.quality || {}).filter((item) => item.count).map((item) => '<tr><td>' + esc(qualityLabel(item.key)) + code(item.key) + '</td><td class="num">' + fmt(item.count) + '</td><td class="num pass">' + fmt(item.pass) + '</td><td class="num fail">' + fmt(item.fail) + '</td><td class="num">' + rate(item.passRate) + '</td></tr>').join("");
  document.getElementById("quality").innerHTML = qualityRows ? '<table><thead><tr><th>判定項目</th><th class="num">件数</th><th class="num">成功</th><th class="num">失敗</th><th class="num">成功率</th></tr></thead><tbody>' + qualityRows + '</tbody></table>' : '<div class="empty">LLM Judgeの結果はありません。</div>';
  const latencyRows = LATENCY_METRICS_PLACEHOLDER(data).map((name) => {
    const l2 = summary.latency?.level2?.[name] || {}; const l3 = summary.latency?.level3?.[name] || {};
    return '<tr><td>' + esc(latencyLabel(name)) + code(name) + '</td><td>' + latencyCell(l2) + '</td><td>' + latencyCell(l3) + '</td></tr>';
  }).join("");
  const engineLatencyRows = Object.entries(summary.latencyByEngine || {}).flatMap(([engine, values]) =>
    LATENCY_METRICS_PLACEHOLDER(data).map((name) => '<tr><td>' + esc(labels.engines?.[engine]?.label || engine) + '</td><td>' + esc(latencyLabel(name)) + code(name) + '</td><td>' + latencyCell(values?.[name] || {}) + '</td></tr>')
  ).join("");
  document.getElementById("latency").innerHTML = '<h3>実行レベル別</h3><table><thead><tr><th>計測項目</th><th>Level 2（p50 / p95 / p99）</th><th>Level 3（p50 / p95 / p99）</th></tr></thead><tbody>' + latencyRows + '</tbody></table>'
    + '<h3>実行方式別（実音声）</h3><table><thead><tr><th>実行方式</th><th>計測項目</th><th>p50 / p95 / p99</th></tr></thead><tbody>' + (engineLatencyRows || '<tr><td colspan="3">実音声の計測結果はありません。</td></tr>') + '</tbody></table>';
  function LATENCY_METRICS_PLACEHOLDER() { return ["speechEndToTranscriptFinalMs", "transcriptFinalToAssistantStartMs", "speechEndToAssistantStartMs", "fullTurnLatencyMs", "segmentFinalToTurnCommitMs", "turnCommitToAckMs", "ackToMainResponseMs"]; }
  function latencyCell(item) { return item && item.count ? fmt(item.p50, "ms") + " / " + fmt(item.p95, "ms") + " / " + fmt(item.p99, "ms") + ' <span class="muted small">（' + fmt(item.count) + '件）</span>' : "—"; }
  const usage = summary.usage || {};
  const usageRows = [
    ["Jev呼び出し回数", usage.jevCalls], ["Jevキャッシュヒット", usage.jevCacheHits], ["Jevキャッシュミス", usage.jevCacheMisses],
    ["LLM Judge呼び出し回数", usage.llmJudgeCalls], ["GPT Liveセッション数", usage.gptLiveSessions],
    ["Transcribe利用量", usage.transcribeUsage], ["Polly利用量", usage.pollyUsage], ["推測費用", null],
  ].map(([label, value]) => '<tr><td>' + esc(label) + '</td><td class="num">' + (value === null || value === undefined ? '<span class="muted">記録なし</span>' : fmt(value)) + '</td></tr>').join("");
  document.getElementById("usage").innerHTML = '<table><thead><tr><th>項目</th><th class="num">記録値</th></tr></thead><tbody>' + usageRows + '</tbody></table><p class="muted small">金額を正確に算出できないため、推測費用は表示していません。</p>';
  const previous = data.previous?.summary?.behaviorCoverage;
  const comparisonRows = previous ? Object.values(summary.behaviorCoverage || {}).map((item) => {
    const old = previous[item.behavior]; if (!old || old.passRate === null || item.passRate === null) return "";
    const delta = Math.round((item.passRate - old.passRate) * 10) / 10;
    return '<tr><td>' + esc(behaviorInfo(item.behavior).label) + code(item.behavior) + '</td><td class="num">' + rate(old.passRate) + '</td><td class="num">' + rate(item.passRate) + '</td><td class="num ' + (delta >= 0 ? 'pass' : 'fail') + '">' + (delta >= 0 ? '↑ ' : '↓ ') + rate(Math.abs(delta)) + '</td></tr>';
  }).filter(Boolean).join("") : "";
  document.getElementById("comparison").innerHTML = comparisonRows ? '<table><thead><tr><th>回答パターン</th><th class="num">前回</th><th class="num">今回</th><th class="num">差分</th></tr></thead><tbody>' + comparisonRows + '</tbody></table>' : '<div class="empty">比較可能な前回実行はありません。</div>';
  const failures = arrayValue(data.failures);
  const behaviorFilter = document.getElementById("filter-behavior");
  const issueFilter = document.getElementById("filter-issue");
  const engineFilter = document.getElementById("filter-engine");
  [...new Set(failures.map((failure) => failure.behavior).filter(Boolean))].sort().forEach((value) => {
    const itemInfo = behaviorInfo(value);
    behaviorFilter.insertAdjacentHTML("beforeend", '<option value="' + esc(value) + '">' + esc(itemInfo.label) + '（' + esc(value) + '）</option>');
  });
  [...new Set(failures.map((failure) => failure.issueType).filter(Boolean))].sort().forEach((value) => {
    const itemInfo = failureInfo(value);
    issueFilter.insertAdjacentHTML("beforeend", '<option value="' + esc(value) + '">' + esc(itemInfo.label) + '（' + esc(value) + '）</option>');
  });
  [...new Set(failures.map((failure) => failure.engine).filter(Boolean))].sort().forEach((value) => {
    engineFilter.insertAdjacentHTML("beforeend", '<option value="' + esc(value) + '">' + esc(labels.engines?.[value]?.label || value) + '</option>');
  });
  [behaviorFilter, issueFilter, engineFilter, document.getElementById("filter-status"), document.getElementById("filter-level")].forEach((element) => element.addEventListener("change", renderFailures));
  function arrayValue(value) { return Array.isArray(value) ? value : []; }
  function renderFailures() {
    const behavior = behaviorFilter.value; const status = document.getElementById("filter-status").value; const levelValue = document.getElementById("filter-level").value; const issue = issueFilter.value; const engine = engineFilter.value;
    const filtered = failures.filter((failure) => (!behavior || failure.behavior === behavior) && (!status || failure.status === status) && (!levelValue || String(failure.level) === levelValue) && (!issue || failure.issueType === issue) && (!engine || failure.engine === engine));
    document.getElementById("failure-list").innerHTML = filtered.length ? filtered.map((failure) => {
      const behavior = behaviorInfo(failure.behavior || "");
      const failureType = failureInfo(failure.issueType);
      const evidenceLabels = { screenshot: "スクリーンショット", telemetry: "Telemetry", trace: "Playwright trace" };
      const links = failure.evidence ? Object.entries(failure.evidence).map(([key, value]) => '<a href="' + esc(value) + '">' + esc(evidenceLabels[key] || key) + '</a>').join("") : '<span class="muted">証拠ファイルなし</span>';
      const engineLabel = labels.engines?.[failure.engine]?.label || failure.engine || "実行方式不明";
      return '<details class="failure"><summary><strong>' + esc(failure.failureId) + '</strong> · ' + esc(failureType.label) + ' · ' + esc(behavior.label || "回答パターン不明") + ' · ターン ' + fmt(failure.turn) + ' · ' + esc(engineLabel) + ' · ' + level(failure.level) + '</summary><div class="failure-body"><p class="failure-explanation"><strong>' + esc(failureType.description) + '</strong><br>' + code(failure.issueType) + '　' + statusLabel(failure.status) + '</p><div class="detail-grid">' + detailBox("実行方式", engineLabel) + detailBox("seed", failure.seed) + detailBox("会話", failure.conversation) + detailBox("AIの質問", failure.aiQuestion) + detailBox("ユーザーの文字起こし", failure.userTranscript) + detailBox("AIの返答", failure.aiResponse) + detailBox("期待結果", failure.expected) + detailBox("実際の結果", failure.actual) + detailBox("インタビュー状態", failure.interviewState) + detailBox("Judge理由", failure.judgeReason) + '</div><p><strong>関連タイムライン</strong></p><pre>' + json(failure.timeline) + '</pre><p class="evidence"><strong>証拠ファイル：</strong> ' + links + '</p></div></details>';
    }).join("") : '<div class="empty">条件に一致する失敗はありません。</div>';
  }
  function detailBox(label, value) { return '<div class="detail-box"><h3>' + esc(label) + '</h3><pre>' + (typeof value === "object" ? json(value) : esc(value ?? "—")) + '</pre></div>'; }
  renderFailures();
})();
</script>
</body>
</html>
`;
}

function writeIfMissing(filePath, value) {
  if (fs.existsSync(filePath)) return;
  fs.writeFileSync(filePath, value, "utf8");
}

function writeCanonicalInputs(root, data) {
  const normalized = normalizeVoiceRunData(data, { reportDir: root });
  writeIfMissing(path.join(root, "summary.json"), JSON.stringify(normalized.summary, null, 2));
  writeIfMissing(
    path.join(root, "sessions.jsonl"),
    normalized.sessions.map((session) => JSON.stringify(session)).join("\n") + (normalized.sessions.length ? "\n" : ""),
  );
  writeIfMissing(path.join(root, "metrics.json"), JSON.stringify({
    latency: normalized.summary.latency,
    conversationQuality: normalized.summary.quality,
    usage: normalized.summary.usage,
  }, null, 2));
  writeIfMissing(path.join(root, "failures.json"), JSON.stringify(normalized.failures, null, 2));
  return normalized;
}

export function generateVoiceReportForRun({ runDir, runId = null, testStatus = null } = {}) {
  const root = path.resolve(runDir || process.cwd());
  fs.mkdirSync(root, { recursive: true });
  const loaded = loadVoiceRunData({ runDir: root, runId });
  loaded.summary = { ...loaded.summary, testStatus };
  const normalized = writeCanonicalInputs(root, loaded);
  const previous = readPreviousReportData(root);
  normalized.previous = previous;
  const reportPath = path.join(root, "report.html");
  fs.writeFileSync(reportPath, renderVoiceReportHtml(normalized, { reportDir: root }), "utf8");

  const runsRoot = path.basename(path.dirname(root)) === "voice-runs"
    ? path.dirname(root)
    : null;
  if (runsRoot && path.basename(root) !== "latest") {
    const latestDir = path.join(runsRoot, "latest");
    fs.mkdirSync(latestDir, { recursive: true });
    fs.writeFileSync(
      path.join(latestDir, "report.html"),
      renderVoiceReportHtml(normalized, { reportDir: latestDir }),
      "utf8",
    );
  }
  return reportPath;
}
