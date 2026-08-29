import { useEffect, useMemo, useRef, useState } from "react";
import {
  getInterviewAnswerStatusLabel,
  getInterviewDisplayAnswer,
} from "../features/interviews/answerVisibility.js";
import { VoiceConversationButton } from "../features/realtime-voice/components/VoiceConversationButton";
import { VoiceConversationStatus } from "../features/realtime-voice/components/VoiceConversationStatus";
import { useRealtimeVoiceInterview } from "../features/realtime-voice/hooks/useRealtimeVoiceInterview";
import { ProcessModelPanel } from "../features/interviews/components/ProcessModelPanel";
import { SystemRequirementProgressPanel } from "../features/interviews/components/SystemRequirementProgressPanel";
import {
  getInterviewModelLabel,
  isInterviewConfigurationComplete,
} from "../features/interviews/interviewConfiguration";
import { resetDevSystemRequirementDemo, resetDevVoiceDemo } from "../lib/api";
import type { KnowledgeLayoutProps } from "../types/pageProps";

const DEV_VOICE_DEMO_RECORD_ID = "dev-voice-demo-record";
const DEV_SYSTEM_REQUIREMENT_DEMO_RECORD_ID = "dev-system-requirement-demo-record";

const recordStatusLabels: Record<NonNullable<KnowledgeLayoutProps["selectedRecord"]>["status"], string> = {
  draft: "準備中",
  in_progress: "回答中",
  submitted: "確認待ち",
  returned: "修正依頼",
  approved: "承認済み",
};

type InterviewSidebarItem = {
  id: string;
  fieldId: string | null;
  questionId: string | null;
  label: string;
  question: string;
  answer?: string;
  status: "answered" | "active" | "pending";
};

function toSpokenQuestionFromLabel(label: string) {
  const text = label.trim();
  if (!text) {
    return "この項目について教えてください。";
  }
  if (/[?？]$/.test(text) || /(ですか|ますか|でしょうか|教えてください)$/.test(text)) {
    return text;
  }
  return `${text}について教えてください。`;
}

function buildFieldQuestion(field: KnowledgeLayoutProps["sortedFields"][number]) {
  return field.aiQuestionExamples?.find((example) => example.trim()) ?? toSpokenQuestionFromLabel(field.name);
}

export function InterviewRecordPage(props: KnowledgeLayoutProps) {
  const chatLogRef = useRef<HTMLDivElement | null>(null);
  const remoteAudioRef = useRef<HTMLAudioElement | null>(null);
  const [isResettingDemo, setIsResettingDemo] = useState(false);
  const [savingFieldId, setSavingFieldId] = useState<string | null>(null);
  const [summaryView, setSummaryView] = useState<"requirements" | "process">("requirements");
  const isManagementUser = props.user?.role === "admin" || props.user?.role === "knowledge_manager";
  const isInterviewConfigured = isInterviewConfigurationComplete(props.selectedKnowledge);
  const canAnswerRecord = Boolean(
    isInterviewConfigured
      && (isManagementUser
        || (props.user?.role === "interviewer" && ["in_progress", "returned"].includes(props.selectedRecord?.status ?? ""))),
  );

  async function handleResetDemo() {
    if (!isManagementUser || isResettingDemo || realtimeVoice.isActive) return;
    setIsResettingDemo(true);
    try {
      if (props.selectedRecord?.id === DEV_VOICE_DEMO_RECORD_ID) {
        await resetDevVoiceDemo();
      } else if (props.selectedRecord?.id === DEV_SYSTEM_REQUIREMENT_DEMO_RECORD_ID) {
        await resetDevSystemRequirementDemo();
      } else {
        return;
      }
      window.location.reload();
    } finally {
      setIsResettingDemo(false);
    }
  }

  const assistantMessages = useMemo(
    () => props.interviewMessages.filter((message) => message.role === "assistant" || message.role === "ai"),
    [props.interviewMessages],
  );
  const configuredQuestionMessages = useMemo(
    () => assistantMessages.filter(
      (message) => (message.questionType === "configured_field" || message.questionType === "structured") && message.fieldId,
    ),
    [assistantMessages],
  );

  const configuredQuestionItems: InterviewSidebarItem[] = useMemo(
    () => props.sortedFields.map((field) => {
      const latestConfiguredQuestion = [...configuredQuestionMessages]
        .reverse()
        .find((message) => message.fieldId === field.id);
      const fieldState = field.id ? props.interviewState?.fieldStates?.[field.id] : undefined;
      const answer = getInterviewDisplayAnswer(
        fieldState,
        props.structuredDraft[field.name],
      ) || undefined;
      const status: InterviewSidebarItem["status"] = fieldState?.answerState === "CONFIRMED"
        ? "answered"
        : fieldState?.answerState === "AWAITING_CONFIRMATION" || fieldState?.answerState === "CANDIDATE_PENDING"
          ? "active"
          : "pending";
      return {
        id: field.id ?? `field-${field.displayOrder}-${field.name}`,
        fieldId: field.id ?? null,
        questionId: latestConfiguredQuestion?.questionId ?? null,
        label: field.name,
        question: latestConfiguredQuestion?.text ?? buildFieldQuestion(field),
        answer,
        status,
      };
    }),
    [configuredQuestionMessages, props.interviewState, props.sortedFields, props.structuredDraft],
  );
  const interviewProfile = props.interviewState?.interviewProfile
    ?? props.selectedKnowledge?.interviewPlan?.profile;
  const isChatOnlyInterview = interviewProfile === "system_requirement";
  const hasVoiceQuestions = !isChatOnlyInterview && (
    props.sortedFields.some((field) => field.name.trim())
      || interviewProfile === "business_process"
  );

  const realtimeVoice = useRealtimeVoiceInterview({
    recordId: props.selectedRecord?.id,
    hasQuestions: hasVoiceQuestions,
    remoteAudioRef,
    onMessage: props.onAppendInterviewMessage,
    onInterviewStateChanged: props.onRefreshInterviewSnapshot,
    onCompleted: props.onRefreshInterviewSnapshot,
  });

  useEffect(() => {
    const container = chatLogRef.current;
    if (!container) {
      return;
    }
    container.scrollTop = container.scrollHeight;
  }, [props.interviewMessages.length, props.streamingInterviewReply]);

  function handleChatInputKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) {
      return;
    }
    event.preventDefault();
    handleSendInterviewMessage();
  }

  function handleConfiguredAnswerChange(answerKey: string, value: string) {
    props.setStructuredDraft({
      ...props.structuredDraft,
      [answerKey]: value,
    });
  }

  function handleConfiguredAnswerDelete(answerKey: string) {
    if (!window.confirm("この項目メモを削除しますか？")) {
      return;
    }
    const nextDraft = { ...props.structuredDraft };
    delete nextDraft[answerKey];
    props.setStructuredDraft(nextDraft);
  }

  async function handleConfiguredAnswerSave(item: InterviewSidebarItem) {
    if (!item.fieldId || !item.answer?.trim() || savingFieldId) return;
    setSavingFieldId(item.fieldId);
    try {
      await props.onSaveInterviewAnswer(item.fieldId, item.answer.trim());
    } finally {
      setSavingFieldId(null);
    }
  }

  function resolveInterviewAnswerTarget() {
    const currentQuestionId = props.interviewState?.currentQuestionId;
    if (!currentQuestionId) {
      return null;
    }
    const currentQuestion = props.interviewState?.askedQuestions.find(
      (question) => question.questionId === currentQuestionId,
    );
    return currentQuestion
      ? {
          questionId: currentQuestion.questionId,
          questionType: currentQuestion.questionType,
          fieldId: currentQuestion.fieldId,
          targetType: currentQuestion.targetType,
          targetId: currentQuestion.targetId,
        }
      : null;
  }

  function handleSendInterviewMessage() {
    if (
      !props.chatInput.trim()
      || props.isInterviewStreaming
      || props.interviewState?.status === "completed"
      || !canAnswerRecord
      || realtimeVoice.isActive
    ) {
      return;
    }
    props.onSendInterviewMessage(resolveInterviewAnswerTarget());
  }

  const canStartInterview = Boolean(props.selectedRecord)
    && canAnswerRecord
    && !props.isInterviewStreaming
    && !realtimeVoice.isActive
    && props.interviewMessages.length === 0
    && props.interviewState?.status !== "completed";
  const isCompleted = props.interviewState?.status === "completed";
  const isTextInputDisabled = !canAnswerRecord || isCompleted || (!isChatOnlyInterview && realtimeVoice.isActive);
  const interviewLaunchPath = props.selectedKnowledgeDb && props.selectedKnowledge
    ? `/knowledge-dbs/${props.selectedKnowledgeDb.id}/knowledges/${props.selectedKnowledge.id}/interview`
    : null;
  const currentTargetLabel = props.interviewState?.nextQuestionTarget?.label;
  const currentTargetMessage = currentTargetLabel
    ? `いま確認していること：${currentTargetLabel}`
    : props.interviewMessages.length > 0
      ? "回答内容を整理しています。"
      : "開始すると質問が表示されます。";

  return (
    <section className="panel interview-page">
      <div className="panel-header interview-page-header">
        <div className="interview-page-title">
          {interviewLaunchPath ? (
            <button
              type="button"
              className="ghost compact interview-back-button"
              onClick={() => props.navigate(interviewLaunchPath)}
            >
              ← インタビュー開始画面に戻る
            </button>
          ) : null}
          <h2>AIインタビュー</h2>
          <span className="interview-model-badge">実行モデル：{getInterviewModelLabel(props.selectedKnowledge)}</span>
          {props.selectedRecord ? (
            <span className={props.selectedRecord.status === "approved" ? "status-pill" : "status-pill muted"}>
              {recordStatusLabels[props.selectedRecord.status]}
            </span>
          ) : null}
          {!isInterviewConfigured ? (
            <p className="notice">用途と実行モデルを設定してください。</p>
          ) : null}
        </div>
        {isManagementUser && (props.selectedRecord?.id === DEV_VOICE_DEMO_RECORD_ID
        || props.selectedRecord?.id === DEV_SYSTEM_REQUIREMENT_DEMO_RECORD_ID) ? (
          <button
            type="button"
            className="ghost compact"
            onClick={handleResetDemo}
            disabled={isResettingDemo || realtimeVoice.isActive}
          >
            {isResettingDemo ? "リセット中" : "テスト状態をリセット"}
          </button>
        ) : null}
        {isManagementUser && props.selectedRecord?.status === "submitted" ? (
          <>
            <button
              type="button"
              className="ghost compact"
              onClick={() => {
                const note = window.prompt("修正してほしい内容を入力してください。", "");
                if (note?.trim()) void props.onChangeRecordStatus("returned", note.trim());
              }}
            >
              修正依頼
            </button>
            <button
              type="button"
              className="primary compact"
              onClick={() => void props.onChangeRecordStatus("approved")}
            >
              記録を承認
            </button>
          </>
        ) : null}
      </div>

      <div className="interview-shell">
        <aside className="interview-sidebar">
          {interviewProfile === "system_requirement" ? (
            <>
              <div className="interview-summary-header">
                <strong>整理結果</strong>
              </div>
              <div className="interview-summary-tabs" role="tablist" aria-label="整理結果の表示切替">
                <button
                  type="button"
                  role="tab"
                  aria-selected={summaryView === "requirements"}
                  className={summaryView === "requirements" ? "active" : ""}
                  onClick={() => setSummaryView("requirements")}
                >
                  要件整理
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={summaryView === "process"}
                  className={summaryView === "process" ? "active" : ""}
                  onClick={() => setSummaryView("process")}
                >
                  処理の流れ
                </button>
              </div>
              {summaryView === "requirements" ? (
                <SystemRequirementProgressPanel interviewState={props.interviewState} />
              ) : (
                <ProcessModelPanel interviewState={props.interviewState} />
              )}
            </>
          ) : interviewProfile === "business_process" ? (
            <ProcessModelPanel interviewState={props.interviewState} />
          ) : <>
            <div className="interview-sidebar-header">
              <strong>質問リスト</strong>
              <span>{configuredQuestionItems.length}</span>
            </div>
            {configuredQuestionItems.length ? (
              <div className="interview-question-list">
                {configuredQuestionItems.map((item) => {
                  const fieldState = item.fieldId ? props.interviewState?.fieldStates?.[item.fieldId] : undefined;
                  const statusLabel = getInterviewAnswerStatusLabel(fieldState);

                  return (
                    <div key={item.id} className={`interview-question-item ${item.status}`}>
                      <div className="interview-question-head">
                        <strong>{item.label}</strong>
                        <div className="interview-question-actions">
                          <span className={`question-status ${item.status}`}>{statusLabel}</span>
                          {fieldState?.answerState === "CONFIRMED" && canAnswerRecord ? (
                            <button
                              className="ghost compact"
                              type="button"
                              onClick={() => void handleConfiguredAnswerSave(item)}
                              disabled={!item.answer?.trim() || savingFieldId !== null}
                            >
                              {savingFieldId === item.fieldId ? "保存中" : "保存"}
                            </button>
                          ) : null}
                          <button className="ghost compact" type="button" onClick={() => handleConfiguredAnswerDelete(item.label)} disabled={!canAnswerRecord}>
                            削除
                          </button>
                        </div>
                      </div>
                      <p className="interview-question-text">{item.question}</p>
                      <div className="interview-answer-block">
                        <span className="interview-answer-label">回答</span>
                        <textarea
                          className="interview-answer-input"
                          value={item.answer ?? ""}
                          onChange={(event) => handleConfiguredAnswerChange(item.label, event.target.value)}
                          placeholder="回答を入力"
                          disabled={!canAnswerRecord}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
              ) : (
                <p className="empty">質問がありません</p>
              )}
          </>}
        </aside>

        <div className="interview-main-column">
          <div className="interview-chat-panel">
            <div className="interview-chat-header">
              <div>
                <strong>会話</strong>
                <p className="interview-current-target">{currentTargetMessage}</p>
              </div>
              <button className="ghost compact" type="button" onClick={props.onStartInterview} disabled={!canStartInterview}>
                インタビュー開始
              </button>
            </div>
            <div ref={chatLogRef} className="chat-log">
              {props.interviewMessages.map((message, index) => (
                <div key={message.id ?? `${message.role}-${index}`} className={`bubble ${message.role === "assistant" || message.role === "ai" ? "ai" : "user"}`}>
                  {message.candidateSource === "assistant_proposal" ? <span className="proposal-message-label">AIの案</span> : null}
                  <p>{message.text}</p>
                </div>
              ))}
              {props.streamingInterviewReply ? (
                <div className="bubble ai">
                  <p>{props.streamingInterviewReply}</p>
                </div>
              ) : null}
            </div>
            {isCompleted ? (
              <div className="interview-completed-banner">
                <p>{props.selectedRecord?.status === "submitted" ? "回答を提出しました。管理者の確認を待っています。" : "インタビューが完了しました。回答内容を確認してください。"}</p>
              </div>
            ) : null}
            {props.selectedRecord?.status === "returned" && props.selectedRecord.reviewNote ? (
              <div className="interview-completed-banner">
                <p>管理者からの修正依頼：{props.selectedRecord.reviewNote}</p>
              </div>
            ) : null}
            <div className="answer-composer">
              {!isChatOnlyInterview ? (
                <VoiceConversationStatus
                  status={realtimeVoice.status}
                  message={realtimeVoice.message}
                  partialTranscript={realtimeVoice.partialTranscript}
                />
              ) : null}
              <textarea
                value={props.chatInput}
                onChange={(event) => props.setChatInput(event.target.value)}
                onKeyDown={handleChatInputKeyDown}
                placeholder={
                  isCompleted
                    ? "インタビューは完了しています"
                    : realtimeVoice.isActive
                      ? "音声会話中はテキスト入力を利用できません"
                      : "回答を入力"
                }
                disabled={isTextInputDisabled}
              />
              {!isChatOnlyInterview ? (
                <audio ref={remoteAudioRef} className="voice-remote-audio" autoPlay playsInline />
              ) : null}
              {!isChatOnlyInterview && realtimeVoice.requiresManualPlayback ? (
                <button className="secondary" type="button" onClick={() => void realtimeVoice.playRemoteAudio()}>
                  音声を再生
                </button>
              ) : null}
              <div className="answer-composer-actions">
                <button className="primary" onClick={handleSendInterviewMessage} disabled={props.isInterviewStreaming || isTextInputDisabled}>
                  {props.isInterviewStreaming ? "受信中..." : isCompleted ? "完了済み" : "送信"}
                </button>
                {!isChatOnlyInterview ? (
                  <div className="voice-controls">
                    <VoiceConversationButton
                      status={realtimeVoice.status}
                      disabled={!props.selectedRecord || !canAnswerRecord || isCompleted || realtimeVoice.status === "completed"}
                      onStart={() => void realtimeVoice.start()}
                      onStop={() => void realtimeVoice.stop()}
                    />
                  </div>
                ) : null}
              </div>
              {props.recordNotice ? <p className="notice interview-inline-notice">{props.recordNotice}</p> : null}
            </div>
          </div>

        </div>
      </div>
    </section>
  );
}
