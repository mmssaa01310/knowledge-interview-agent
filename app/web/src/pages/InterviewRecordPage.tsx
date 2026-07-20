import { useEffect, useMemo, useRef, useState } from "react";
import {
  getInterviewAnswerStatusLabel,
  getInterviewDisplayAnswer,
} from "../features/interviews/answerVisibility.js";
import { VoiceConversationButton } from "../features/realtime-voice/components/VoiceConversationButton";
import { VoiceConversationStatus } from "../features/realtime-voice/components/VoiceConversationStatus";
import { useRealtimeVoiceInterview } from "../features/realtime-voice/hooks/useRealtimeVoiceInterview";
import { resetDevVoiceDemo } from "../lib/api";
import type { KnowledgeLayoutProps } from "../types/pageProps";

const DEV_VOICE_DEMO_RECORD_ID = "dev-voice-demo-record";

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

  async function handleResetVoiceDemo() {
    if (isResettingDemo || realtimeVoice.isActive) return;
    setIsResettingDemo(true);
    try {
      await resetDevVoiceDemo();
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
    () => assistantMessages.filter((message) => message.questionType === "configured_field" && message.fieldId),
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
  const hasVoiceQuestions = props.sortedFields.some((field) => field.askByAi);

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
        }
      : null;
  }

  function handleSendInterviewMessage() {
    if (
      !props.chatInput.trim()
      || props.isInterviewStreaming
      || props.interviewState?.status === "completed"
      || realtimeVoice.isActive
    ) {
      return;
    }
    props.onSendInterviewMessage(resolveInterviewAnswerTarget());
  }

  const canStartInterview = Boolean(props.selectedRecord)
    && !props.isInterviewStreaming
    && !realtimeVoice.isActive
    && props.interviewMessages.length === 0
    && props.interviewState?.status !== "completed";
  const isCompleted = props.interviewState?.status === "completed";
  const isTextInputDisabled = isCompleted || realtimeVoice.isActive;

  return (
    <section className="panel interview-page">
      <div className="panel-header interview-page-header">
        <div>
          <h2>AIインタビュー</h2>
          <p className="lede">{props.selectedRecord?.title ?? "記録"}</p>
        </div>
        {props.selectedRecord?.id === DEV_VOICE_DEMO_RECORD_ID ? (
          <button
            type="button"
            className="ghost compact"
            onClick={handleResetVoiceDemo}
            disabled={isResettingDemo || realtimeVoice.isActive}
          >
            {isResettingDemo ? "リセット中" : "テスト状態をリセット"}
          </button>
        ) : null}
      </div>

      <div className="interview-shell">
        <aside className="interview-sidebar">
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
                          {fieldState?.answerState === "CONFIRMED" ? (
                            <button
                              className="ghost compact"
                              type="button"
                              onClick={() => void handleConfiguredAnswerSave(item)}
                              disabled={!item.answer?.trim() || savingFieldId !== null}
                            >
                              {savingFieldId === item.fieldId ? "保存中" : "保存"}
                            </button>
                          ) : null}
                          <button className="ghost compact" type="button" onClick={() => handleConfiguredAnswerDelete(item.label)}>
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
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="empty">質問がありません</p>
          )}
        </aside>

        <div className="interview-chat-panel">
          <div className="interview-chat-header">
            <div>
              <strong>チャット</strong>
            </div>
            <button className="ghost compact" type="button" onClick={props.onStartInterview} disabled={!canStartInterview}>
              インタビュー開始
            </button>
          </div>
          <div ref={chatLogRef} className="chat-log">
            {props.interviewMessages.map((message, index) => (
              <div key={message.id ?? `${message.role}-${index}`} className={`bubble ${message.role === "assistant" || message.role === "ai" ? "ai" : "user"}`}>
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
              <p>以上で、設定されているすべての質問項目へのインタビューが完了しました。ご協力ありがとうございました。</p>
            </div>
          ) : null}
          <div className="answer-composer">
            <VoiceConversationStatus
              status={realtimeVoice.status}
              message={realtimeVoice.message}
              partialTranscript={realtimeVoice.partialTranscript}
            />
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
            <audio ref={remoteAudioRef} className="voice-remote-audio" autoPlay playsInline />
            {realtimeVoice.requiresManualPlayback ? (
              <button className="secondary" type="button" onClick={() => void realtimeVoice.playRemoteAudio()}>
                音声を再生
              </button>
            ) : null}
            <div className="answer-composer-actions">
              <button className="primary" onClick={handleSendInterviewMessage} disabled={props.isInterviewStreaming || isTextInputDisabled}>
                {props.isInterviewStreaming ? "受信中..." : isCompleted ? "完了済み" : "送信"}
              </button>
              <div className="voice-controls">
                <VoiceConversationButton
                  status={realtimeVoice.status}
                  disabled={!props.selectedRecord || isCompleted || realtimeVoice.status === "completed"}
                  onStart={() => void realtimeVoice.start()}
                  onStop={() => void realtimeVoice.stop()}
                />
              </div>
            </div>
            {props.recordNotice ? <p className="notice interview-inline-notice">{props.recordNotice}</p> : null}
          </div>
        </div>
      </div>
    </section>
  );
}
