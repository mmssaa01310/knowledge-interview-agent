import { useEffect, useMemo, useRef, useState } from "react";
import {
  getInterviewAnswerStatusLabel,
  getInterviewDisplayAnswer,
} from "../features/interviews/answerVisibility.js";
import { VoiceConversationButton } from "../features/realtime-voice/components/VoiceConversationButton";
import { VoiceConversationStatus } from "../features/realtime-voice/components/VoiceConversationStatus";
import { useRealtimeVoiceInterview } from "../features/realtime-voice/hooks/useRealtimeVoiceInterview";
import { KikoAvatar, type KikoAvatarState } from "../features/interview-chat/components/KikoAvatar";
import { ProcessModelPanel } from "../features/interviews/components/ProcessModelPanel";
import { SystemRequirementProgressPanel } from "../features/interviews/components/SystemRequirementProgressPanel";
import {
  isInterviewConfigurationComplete,
} from "../features/interviews/interviewConfiguration";
import { resetDevSystemRequirementDemo, resetDevVoiceDemo } from "../lib/api";
import { useI18n, type Translate } from "../i18n";
import { formatNumber } from "../lib/date";
import type { KnowledgeLayoutProps } from "../types/pageProps";

const DEV_VOICE_DEMO_RECORD_ID = "dev-voice-demo-record";
const DEV_SYSTEM_REQUIREMENT_DEMO_RECORD_ID = "dev-system-requirement-demo-record";

type InterviewSidebarItem = {
  id: string;
  fieldId: string | null;
  questionId: string | null;
  label: string;
  question: string;
  answer?: string;
  status: "answered" | "active" | "pending";
};

function toSpokenQuestionFromLabel(label: string, t: Translate) {
  const text = label.trim();
  if (!text) {
    return t("interview.questionGeneral");
  }
  if (/[?？]$/.test(text) || /(ですか|ますか|でしょうか|教えてください)$/.test(text)) {
    return text;
  }
  return t("interview.questionFromLabel", { label: text });
}

function buildFieldQuestion(field: KnowledgeLayoutProps["sortedFields"][number], t: Translate) {
  return field.aiQuestionExamples?.find((example) => example.trim()) ?? toSpokenQuestionFromLabel(field.name, t);
}

export function InterviewRecordPage(props: KnowledgeLayoutProps) {
  const { t, locale } = useI18n();
  const chatLogRef = useRef<HTMLDivElement | null>(null);
  const remoteAudioRef = useRef<HTMLAudioElement | null>(null);
  const [isResettingDemo, setIsResettingDemo] = useState(false);
  const [savingFieldId, setSavingFieldId] = useState<string | null>(null);
  const [summaryView, setSummaryView] = useState<"requirements" | "process">("requirements");
  const [isInterviewContextOpen, setIsInterviewContextOpen] = useState(false);
  const [isMoreActionsOpen, setIsMoreActionsOpen] = useState(false);
  const isManagementUser = props.user?.role === "admin" || props.user?.role === "knowledge_manager";
  const isInterviewConfigured = isInterviewConfigurationComplete(props.selectedKnowledge);
  const canAnswerRecord = Boolean(
    isInterviewConfigured
      && (isManagementUser
        || (props.user?.role === "interviewer" && ["in_progress", "returned"].includes(props.selectedRecord?.status ?? ""))),
  );

  async function handleResetDemo() {
    if (!import.meta.env.DEV || !isManagementUser || isResettingDemo || realtimeVoice.isActive) return;
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
        question: latestConfiguredQuestion?.text ?? buildFieldQuestion(field, t),
        answer,
        status,
      };
    }),
    [configuredQuestionMessages, props.interviewState, props.sortedFields, props.structuredDraft, t],
  );
  const interviewProfile = props.interviewState?.interviewProfile
    ?? props.selectedKnowledge?.interviewPlan?.profile;
  const usesProcessModel = interviewProfile === "business_process" || interviewProfile === "system_requirement";
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

  const isKikoError = props.interviewError || realtimeVoice.status === "error";
  const isKikoThinking = props.isInterviewStreaming || [
    "preparing_initial_reply",
    "processing_interview",
    "preparing_audio",
    "processing",
    "speaking",
  ].includes(realtimeVoice.status);
  const currentKikoState: KikoAvatarState = isKikoError
    ? "error"
    : isKikoThinking
      ? "thinking"
      : "waiting";
  const hasInterviewErrorMessage = props.interviewMessages.some(
    (message) => message.id?.startsWith("interview-error-"),
  );

  useEffect(() => {
    const container = chatLogRef.current;
    if (!container) {
      return;
    }
    container.scrollTop = container.scrollHeight;
  }, [props.interviewMessages.length, props.streamingInterviewReply]);

  useEffect(() => {
    document.body.classList.toggle("interview-context-open", isInterviewContextOpen);
    return () => document.body.classList.remove("interview-context-open");
  }, [isInterviewContextOpen]);

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
    if (!window.confirm(t("interview.deleteQuestionMemoPrompt"))) {
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

  function proposalTarget(message: KnowledgeLayoutProps["interviewMessages"][number]) {
    if (!message.questionId) return null;
    return {
      questionId: message.questionId,
      questionType: message.questionType ?? "structured",
      fieldId: message.fieldId ?? null,
      targetType: message.targetType,
      targetId: message.targetId,
    };
  }

  function isCurrentProposal(message: KnowledgeLayoutProps["interviewMessages"][number]) {
    return Boolean(
      message.candidateSource === "assistant_proposal"
      && message.questionId
      && message.questionId === props.interviewState?.currentQuestionId
      && props.interviewState?.nextQuestionTarget?.candidateSource === "assistant_proposal"
      && message.targetType === props.interviewState?.nextQuestionTarget?.targetType
      && message.targetId === props.interviewState?.nextQuestionTarget?.targetId,
    );
  }

  function handleConfirmProposal(message: KnowledgeLayoutProps["interviewMessages"][number]) {
    if (
      !isCurrentProposal(message)
      || !canAnswerRecord
      || isCompleted
      || props.isInterviewStreaming
      || realtimeVoice.isActive
    ) {
      return;
    }
    const target = proposalTarget(message);
    // Backendの現在の確認フローは、日本語の肯定応答を入力として扱う契約を維持する。
    if (target) props.onSendInterviewMessage(target, "はい");
  }

  const canStartInterview = Boolean(props.selectedRecord)
    && canAnswerRecord
    && !props.isInterviewStreaming
    && !realtimeVoice.isActive
    && props.interviewMessages.length === 0
    && props.interviewState?.status !== "completed";
  const isCompleted = props.interviewState?.status === "completed";
  const hasDemoResetAction = import.meta.env.DEV && isManagementUser && (
    props.selectedRecord?.id === DEV_VOICE_DEMO_RECORD_ID
      || props.selectedRecord?.id === DEV_SYSTEM_REQUIREMENT_DEMO_RECORD_ID
  );
  const hasReviewActions = isManagementUser && props.selectedRecord?.status === "submitted";
  const isTextInputDisabled = !canAnswerRecord || isCompleted || (!isChatOnlyInterview && realtimeVoice.isActive);
  const interviewLaunchPath = props.selectedKnowledgeDb && props.selectedKnowledge
    ? `/knowledge-dbs/${props.selectedKnowledgeDb.id}/knowledges/${props.selectedKnowledge.id}/interview`
    : null;
  const hasInterviewActions = hasDemoResetAction || hasReviewActions;
  const currentTargetLabel = props.interviewState?.nextQuestionTarget?.label;
  const currentTargetMessage = currentTargetLabel
    ? t("interview.targetNow", { target: currentTargetLabel })
    : props.interviewMessages.length > 0
      ? t("interview.organizing")
      : t("interview.startPrompt");
  const publishedLearningGuidance = props.user?.role === "interviewer"
    ? props.publishedGuidance
    : [];

  return (
    <section className="panel interview-page" data-guide="interview-pane">
      <div className="panel-header interview-page-header">
        <div className="interview-page-title">
          <div className="interview-title-status">
            <h2>{t("interview.title")}</h2>
            <span className="interview-model-badge">{t("interview.modelLabel", { model: props.selectedKnowledge?.interviewPlan?.modelId === "global.openai.gpt-5.6-terra" ? t("interview.model.terra") : props.selectedKnowledge?.interviewPlan?.modelId === "global.openai.gpt-5.6-luna" ? t("interview.model.luna") : t("interview.profile.notSet") })}</span>
            {props.selectedRecord ? (
              <span className={props.selectedRecord.status === "approved" ? "status-pill" : "status-pill muted"}>
                {t(`interview.status.${props.selectedRecord.status}`)}
              </span>
            ) : null}
            {interviewLaunchPath ? (
              <button
                type="button"
                className="ghost compact interview-back-button"
                onClick={() => props.navigate(interviewLaunchPath)}
                aria-label={t("interview.returnToStart")}
                title={t("interview.returnToStart")}
              >
                <svg
                  className="interview-back-icon"
                  viewBox="0 0 20 20"
                  fill="currentColor"
                  aria-hidden="true"
                >
                  <path fillRule="evenodd" clipRule="evenodd" d="M7.79252 2.23214C8.07852 2.53177 8.06748 3.00651 7.76786 3.29252L3.62192 7.25H13.625C16.5935 7.25 19 9.65647 19 12.625C19 15.5935 16.5935 18 13.625 18H10.75C10.3358 18 10 17.6642 10 17.25C10 16.8358 10.3358 16.5 10.75 16.5H13.625C15.7651 16.5 17.5 14.7651 17.5 12.625C17.5 10.4849 15.7651 8.75 13.625 8.75H3.62192L7.76786 12.7075C8.06748 12.9935 8.07852 13.4682 7.79252 13.7679C7.50651 14.0675 7.03177 14.0785 6.73214 13.7925L1.23214 8.54252C1.08388 8.401 1 8.20496 1 8C1 7.79504 1.08388 7.59901 1.23214 7.45748L6.73214 2.20748C7.03177 1.92148 7.50651 1.93252 7.79252 2.23214Z" />
                </svg>
              </button>
            ) : null}
          </div>
          {!isInterviewConfigured ? (
            <p className="notice">{t("interview.configureNotice")}</p>
          ) : null}
        </div>
        {hasInterviewActions ? (
          <div className="interview-more-actions">
            <button
              type="button"
              className="interview-more-actions-trigger"
              aria-label={t("common.operation")}
              aria-expanded={isMoreActionsOpen}
              onClick={() => setIsMoreActionsOpen((value) => !value)}
            >
              ⋯
            </button>
            <div className={isMoreActionsOpen ? "interview-more-actions-menu open" : "interview-more-actions-menu"}>
              {hasDemoResetAction ? (
                <button
                  type="button"
                  className="ghost compact"
                  onClick={handleResetDemo}
                  disabled={isResettingDemo || realtimeVoice.isActive}
                >
                  {isResettingDemo ? t("interview.resettingDemo") : t("interview.resetDemo")}
                </button>
              ) : null}
              {hasReviewActions ? (
                <>
                  <button
                    type="button"
                    className="ghost compact"
                    onClick={() => {
                      const note = window.prompt(t("knowledge.records.reviewPrompt"), "");
                      if (note?.trim()) void props.onChangeRecordStatus("returned", note.trim());
                    }}
                  >
                    {t("interview.reviewRequest")}
                  </button>
                  <button
                    type="button"
                    className="primary compact"
                    data-guide="knowledge-confirm"
                    onClick={() => void props.onChangeRecordStatus("approved")}
                  >
                    {t("interview.approveRecord")}
                  </button>
                </>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>

      <div className={usesProcessModel ? "interview-shell process-interview-shell" : "interview-shell"}>
        <button
          type="button"
          className={isInterviewContextOpen ? "interview-context-backdrop visible" : "interview-context-backdrop"}
          onClick={() => setIsInterviewContextOpen(false)}
          aria-label={t("interview.closeContext")}
        />
        <aside id="interview-context-panel" data-guide="knowledge-pane" data-guide-record-review="true" className={isInterviewContextOpen ? "interview-sidebar knowledge-panel-open" : "interview-sidebar"} aria-label={t("interview.contextPanel")}>
          <div className="interview-context-drawer-header">
            <strong>{t("interview.contextPanel")}</strong>
            <button type="button" className="ghost compact" onClick={() => setIsInterviewContextOpen(false)}>
              {t("common.close")}
            </button>
          </div>
          {interviewProfile === "system_requirement" ? (
            <>
              <div className="interview-summary-header">
                <strong>{t("interview.result")}</strong>
              </div>
              <div className="interview-summary-tabs" role="tablist" aria-label={t("interview.result")}>
                <button
                  type="button"
                  role="tab"
                  aria-selected={summaryView === "requirements"}
                  className={summaryView === "requirements" ? "active" : ""}
                  onClick={() => setSummaryView("requirements")}
                >
                  {t("interview.resultTabs.requirements")}
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={summaryView === "process"}
                  className={summaryView === "process" ? "active" : ""}
                  onClick={() => setSummaryView("process")}
                >
                  {t("interview.resultTabs.process")}
                </button>
              </div>
              {summaryView === "requirements" ? (
                <SystemRequirementProgressPanel interviewState={props.interviewState} />
              ) : (
                <ProcessModelPanel
                  interviewState={props.interviewState}
                  canEdit={isManagementUser}
                  onSaveProcessModel={props.onSaveProcessModel}
                  onEditProcessModel={props.onEditProcessModel}
                />
              )}
            </>
          ) : interviewProfile === "business_process" ? (
            <ProcessModelPanel
              interviewState={props.interviewState}
              canEdit={isManagementUser}
              onSaveProcessModel={props.onSaveProcessModel}
              onEditProcessModel={props.onEditProcessModel}
            />
          ) : <>
            <div className="interview-sidebar-header">
              <div className="interview-sidebar-title">
                <strong>{t("interview.questionList")}</strong>
                <span className="counter">
                  {t("common.itemCount", { count: formatNumber(configuredQuestionItems.length, locale) })}
                </span>
              </div>
            </div>
            {configuredQuestionItems.length ? (
              <div className="interview-question-list">
                {configuredQuestionItems.map((item, index) => {
                  const fieldState = item.fieldId ? props.interviewState?.fieldStates?.[item.fieldId] : undefined;
                  const statusLabel = getInterviewAnswerStatusLabel(fieldState, t);

                  return (
                    <div
                      key={item.id}
                      className={`interview-question-item ${item.status}`}
                      data-guide={item.status !== "answered" ? "knowledge-review" : undefined}
                      data-guide-index={item.status !== "answered" ? index : undefined}
                    >
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
                              {savingFieldId === item.fieldId ? t("interview.savingAnswer") : t("interview.saveAnswer")}
                            </button>
                          ) : null}
                          <button className="ghost compact" type="button" onClick={() => handleConfiguredAnswerDelete(item.label)} disabled={!canAnswerRecord}>
                            {t("common.delete")}
                          </button>
                        </div>
                      </div>
                      <p className="interview-question-text">{item.question}</p>
                      <div className="interview-answer-block">
                        <span className="interview-answer-label">{t("interview.answer")}</span>
                        <textarea
                          className="interview-answer-input"
                          value={item.answer ?? ""}
                          onChange={(event) => handleConfiguredAnswerChange(item.label, event.target.value)}
                          placeholder={t("interview.answerPlaceholder")}
                          disabled={!canAnswerRecord}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
              ) : (
                <p className="empty">{t("interview.noQuestions")}</p>
              )}
          </>}
        </aside>

        <div className="interview-main-column">
          <div className="interview-chat-panel">
            <div className="interview-chat-header">
              <div>
                <strong>{t("interview.conversation")}</strong>
                <p className="interview-current-target">{currentTargetMessage}</p>
              </div>
              <div className="interview-chat-header-actions">
                <button
                  className="ghost compact interview-context-toggle"
                  type="button"
                  data-guide="knowledge-toggle"
                  onClick={() => setIsInterviewContextOpen((value) => !value)}
                  aria-controls="interview-context-panel"
                  aria-expanded={isInterviewContextOpen}
                >
                  {isInterviewContextOpen ? t("interview.closeContext") : t("interview.openContext")}
                </button>
                <button className="ghost compact" type="button" onClick={props.onStartInterview} disabled={!canStartInterview}>
                  {t("interview.start")}
                </button>
              </div>
            </div>
            <div ref={chatLogRef} className="chat-log">
              {props.interviewMessages.map((message, index) => (
                <div key={message.id ?? `${message.role}-${index}`} className={`bubble ${message.role === "assistant" || message.role === "ai" ? "ai" : "user"}`}>
                  {message.role === "assistant" || message.role === "ai" ? (
                    <div className="message-meta">
                      <KikoAvatar
                        state={message.id?.startsWith("interview-error-") ? "error" : "waiting"}
                        label={t("interview.kikoName")}
                      />
                    </div>
                  ) : null}
                  {message.candidateSource === "assistant_proposal" ? <span className="proposal-message-label">{t("interview.proposalLabel")}</span> : null}
                  <p>{message.text}</p>
                  {isCurrentProposal(message) ? (
                    <button
                      type="button"
                      className="proposal-confirm-button"
                      data-guide="knowledge-confirm"
                      onClick={() => handleConfirmProposal(message)}
                      disabled={!canAnswerRecord || isCompleted || props.isInterviewStreaming || realtimeVoice.isActive}
                    >
                      {t("interview.ok")}
                    </button>
                  ) : null}
                </div>
              ))}
              {props.streamingInterviewReply ? (
                <div className="bubble ai">
                  <div className="message-meta">
                    <KikoAvatar state={currentKikoState} label={t("interview.kikoName")} />
                  </div>
                  <p>{props.streamingInterviewReply}</p>
                </div>
              ) : null}
              {isKikoThinking && !props.streamingInterviewReply ? (
                <div className="kiko-chat-status thinking" role="status">
                  <KikoAvatar state="thinking" label={t("interview.kikoName")} />
                  <span>{t("interview.receiving")}</span>
                </div>
              ) : null}
              {isKikoError && !hasInterviewErrorMessage ? (
                <div className="kiko-chat-status error" role="alert">
                  <KikoAvatar state="error" label={t("interview.kikoName")} />
                  <span>{props.recordNotice || t("errors.interviewResponseReceiveFailed")}</span>
                </div>
              ) : null}
            </div>
            {isCompleted ? (
              <div className="interview-completed-banner">
                <p>{props.selectedRecord?.status === "submitted" ? t("interview.submittedMessage") : t("interview.completedMessage")}</p>
              </div>
            ) : null}
            {props.selectedRecord?.status === "returned" && props.selectedRecord.reviewNote ? (
              <div className="interview-completed-banner">
                <p>{t("interview.reviewNote", { note: props.selectedRecord.reviewNote })}</p>
              </div>
            ) : null}
            <div className="answer-composer" data-guide="message-composer">
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
                    ? t("interview.completedInput")
                    : realtimeVoice.isActive
                      ? t("interview.voiceInputDisabled")
                      : t("interview.answerPlaceholder")
                }
                disabled={isTextInputDisabled}
              />
              {!isChatOnlyInterview ? (
                <audio ref={remoteAudioRef} className="voice-remote-audio" autoPlay playsInline />
              ) : null}
              {!isChatOnlyInterview && realtimeVoice.requiresManualPlayback ? (
                <button className="secondary" type="button" onClick={() => void realtimeVoice.playRemoteAudio()}>
                  {t("interview.playAudio")}
                </button>
              ) : null}
              <div className="answer-composer-actions">
                <button className="primary" onClick={handleSendInterviewMessage} disabled={props.isInterviewStreaming || isTextInputDisabled}>
                  {props.isInterviewStreaming ? t("interview.receiving") : isCompleted ? t("interview.done") : t("interview.send")}
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

          {publishedLearningGuidance.length ? (
            <section className="published-learning-guidance" aria-labelledby="published-learning-guidance-title">
              <div className="published-learning-guidance-header">
                <div>
                  <p className="eyebrow">KIKIORI</p>
                  <h2 id="published-learning-guidance-title">{t("interview.learningGuidance.title")}</h2>
                  <p>{t("interview.learningGuidance.description")}</p>
                </div>
              </div>
              <div className="published-learning-guidance-list">
                {publishedLearningGuidance.map((guidance) => (
                  <article className="published-learning-guidance-item" key={guidance.id}>
                    <div className="published-learning-guidance-summary">
                      <strong>{t("interview.learningGuidance.summary")}</strong>
                      <p>{guidance.summary}</p>
                    </div>
                    <div className="published-learning-guidance-next">
                      <strong>{t("interview.learningGuidance.next")}</strong>
                      <p>{guidance.learnerGuidance}</p>
                    </div>
                    {guidance.assessments.length ? (
                      <div className="published-learning-guidance-assessments">
                        {guidance.assessments.map((assessment) => {
                          const statusKey = assessment.status === "partially_confirmed"
                            ? "partiallyConfirmed"
                            : assessment.status === "not_evidenced"
                              ? "notEvidenced"
                              : assessment.status === "needs_follow_up"
                                ? "needsFollowUp"
                                : assessment.status === "not_applicable"
                                  ? "notApplicable"
                                  : "confirmed";
                          return (
                            <div className="published-learning-guidance-assessment" key={assessment.objectiveId}>
                              <div className="published-learning-guidance-assessment-heading">
                                <strong>{assessment.label}</strong>
                                <span className={`dashboard-assessment-status ${assessment.status}`}>
                                  {t(`dashboard.learning.status.${statusKey}`)}
                                </span>
                              </div>
                              <small>{t("interview.learningGuidance.evidence", { count: formatNumber(assessment.evidenceIds.length, locale) })}</small>
                              {assessment.followUpQuestion ? (
                                <p><strong>{t("interview.learningGuidance.followUp")}：</strong>{assessment.followUpQuestion}</p>
                              ) : null}
                            </div>
                          );
                        })}
                      </div>
                    ) : null}
                  </article>
                ))}
              </div>
            </section>
          ) : null}

        </div>
      </div>
    </section>
  );
}
