import { useEffect, useRef, useState } from "react";
import type { KnowledgeLayoutProps } from "../types/pageProps";

type InterviewSidebarTab = "configured" | "extra";

type ConversationQuestionAnswer = {
  question: string;
  answer?: string;
};

type InterviewSidebarItem = {
  id: string;
  answerKey: string;
  label: string;
  question: string;
  answer?: string;
  status: "answered" | "active" | "pending";
  source: "field" | "dynamic";
};

function normalizeQuestionText(value: string) {
  return value.replace(/\s+/g, "").toLowerCase();
}

function buildFieldQuestion(field: KnowledgeLayoutProps["sortedFields"][number]) {
  return field.aiQuestionExamples?.find((example) => example.trim())
    ?? field.description?.trim()
    ?? field.name;
}

function isLikelyQuestionSentence(value: string) {
  const text = value.trim();
  if (!text) {
    return false;
  }

  if (/[?？]$/.test(text)) {
    return true;
  }

  return /(ですか。?|ますか。?|でしょうか。?|でしたか。?|ありませんか。?|教えてください。?|どこ|いつ|なに|何|どの|どう|なぜ|誰)/.test(text);
}

function extractQuestionFromAiMessage(value: string) {
  const segments = value
    .split(/\r?\n|(?<=[。!?？])/)
    .map((segment) => segment.trim())
    .filter(Boolean);

  for (let index = segments.length - 1; index >= 0; index -= 1) {
    const segment = segments[index];
    if (isLikelyQuestionSentence(segment)) {
      return segment;
    }
  }

  return "";
}

function buildConversationQuestionAnswers(messages: KnowledgeLayoutProps["interviewMessages"]) {
  const pairs: ConversationQuestionAnswer[] = [];
  let currentQuestion = "";
  let answerParts: string[] = [];

  function flushCurrent() {
    const question = currentQuestion.trim();
    if (!question) return;
    const answer = answerParts
      .map((part) => part.trim())
      .filter(Boolean)
      .join("\n");
    pairs.push({
      question,
      answer: answer || undefined,
    });
  }

  for (const message of messages) {
    if (message.role === "ai") {
      flushCurrent();
      currentQuestion = extractQuestionFromAiMessage(message.text);
      answerParts = [];
      continue;
    }

    if (currentQuestion.trim()) {
      answerParts.push(message.text);
    }
  }

  flushCurrent();
  return pairs;
}

function findConversationMatchIndex(
  field: KnowledgeLayoutProps["sortedFields"][number],
  conversationItems: ConversationQuestionAnswer[],
  consumedIndexes: Set<number>
) {
  const fieldSignals = [
    field.name,
    field.description ?? "",
    ...field.aiQuestionExamples ?? [],
  ]
    .map((value) => normalizeQuestionText(value))
    .filter(Boolean);

  const matchedIndex = conversationItems.findIndex((item, index) => {
    if (consumedIndexes.has(index)) return false;
    const question = normalizeQuestionText(item.question);
    return fieldSignals.some((signal) => signal.includes(question) || question.includes(signal));
  });

  if (matchedIndex >= 0) {
    return matchedIndex;
  }

  return conversationItems.findIndex((item, index) => !consumedIndexes.has(index) && Boolean(item.answer));
}

export function InterviewRecordPage(props: KnowledgeLayoutProps) {
  const chatLogRef = useRef<HTMLDivElement | null>(null);
  const [activeSidebarTab, setActiveSidebarTab] = useState<InterviewSidebarTab>("configured");
  const conversationItems = buildConversationQuestionAnswers(props.interviewMessages);
  const consumedConversationIndexes = new Set<number>();

  const configuredItems = props.sortedFields.map((field) => {
    const matchIndex = findConversationMatchIndex(field, conversationItems, consumedConversationIndexes);
    if (matchIndex >= 0) {
      consumedConversationIndexes.add(matchIndex);
    }

    const metadataAnswer = props.structuredDraft[field.name]?.trim();

    return {
      id: field.id ?? `field-${field.displayOrder}-${field.name}`,
      answerKey: field.name,
      label: field.name,
      question: buildFieldQuestion(field),
      answer: metadataAnswer,
      source: "field" as const,
    };
  });

  const firstPendingConfiguredIndex = configuredItems.findIndex((item) => !item.answer);

  const configuredQuestionItems: InterviewSidebarItem[] = configuredItems.map((item, index) => ({
    ...item,
    status: item.answer
      ? "answered"
      : firstPendingConfiguredIndex === index
        ? "active"
        : "pending",
  }));

  const extraConversationItems: InterviewSidebarItem[] = conversationItems
    .map((item, index) => ({ item, index }))
    .filter(({ index, item }) => !consumedConversationIndexes.has(index) && !props.deletedExtraQuestionIds.includes(`conversation-${index}`) && (item.question.trim() || item.answer?.trim()))
    .map(({ item, index }) => ({
      id: `conversation-${index}`,
      answerKey: `conversation-${index}`,
      label: "",
      question: item.question,
      answer: props.interviewAnswerOverrides[`conversation-${index}`],
      status: props.interviewAnswerOverrides[`conversation-${index}`] ? "answered" : "active",
      source: "dynamic" as const,
    }));

  useEffect(() => {
    const container = chatLogRef.current;
    if (!container) {
      return;
    }

    container.scrollTop = container.scrollHeight;
  }, [props.interviewMessages.length]);

  useEffect(() => {
    if (activeSidebarTab === "extra" && extraConversationItems.length === 0) {
      setActiveSidebarTab("configured");
    }
  }, [activeSidebarTab, extraConversationItems.length]);

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

  function handleExtraAnswerChange(answerKey: string, value: string) {
    props.setInterviewAnswerOverrides({
      ...props.interviewAnswerOverrides,
      [answerKey]: value,
    });
  }

  function handleConfiguredAnswerDelete(answerKey: string) {
    if (!window.confirm("この質問の回答を削除しますか？")) {
      return;
    }

    const nextDraft = { ...props.structuredDraft };
    delete nextDraft[answerKey];
    props.setStructuredDraft(nextDraft);
  }

  function handleExtraQuestionDelete(answerKey: string) {
    if (!window.confirm("この追加質問カードを削除しますか？")) {
      return;
    }

    const nextOverrides = { ...props.interviewAnswerOverrides };
    delete nextOverrides[answerKey];
    props.setInterviewAnswerOverrides(nextOverrides);
    props.setDeletedExtraQuestionIds([...props.deletedExtraQuestionIds, answerKey]);
  }

  function resolveInterviewAnswerTarget() {
    if (activeSidebarTab === "extra" && extraConversationItems.length > 0) {
      const extraTarget = extraConversationItems.find((item) => item.status === "active")
        ?? extraConversationItems[extraConversationItems.length - 1];
      return extraTarget ? { scope: "extra" as const, answerKey: extraTarget.answerKey } : null;
    }

    const configuredTarget = configuredQuestionItems.find((item) => item.status === "active")
      ?? configuredQuestionItems.find((item) => item.status === "pending")
      ?? configuredQuestionItems[configuredQuestionItems.length - 1];
    return configuredTarget ? { scope: "configured" as const, answerKey: configuredTarget.answerKey } : null;
  }

  function handleSendInterviewMessage() {
    if (!props.chatInput.trim() || props.isInterviewStreaming) {
      return;
    }

    props.onSendInterviewMessage(resolveInterviewAnswerTarget());
  }

  return (
    <section className="panel interview-page">
      <div className="panel-header interview-page-header">
        <div>
          <h2>AIインタビュー</h2>
          <p className="lede">{props.selectedRecord?.title ?? "記録"}</p>
        </div>
      </div>

      <div className="interview-shell">
        <aside className="interview-sidebar">
          <div className="interview-sidebar-header">
            <strong>質問リスト</strong>
          </div>
          <div className="interview-sidebar-tabs" role="tablist" aria-label="質問一覧の切り替え">
            <button
              type="button"
              className={`interview-sidebar-tab ${activeSidebarTab === "configured" ? "active" : ""}`}
              onClick={() => setActiveSidebarTab("configured")}
            >
              項目の質問
              <span>{configuredQuestionItems.length}</span>
            </button>
            <button
              type="button"
              className={`interview-sidebar-tab ${activeSidebarTab === "extra" ? "active" : ""}`}
              onClick={() => setActiveSidebarTab("extra")}
              disabled={extraConversationItems.length === 0}
            >
              追加質問
              <span>{extraConversationItems.length}</span>
            </button>
          </div>

          {activeSidebarTab === "configured" ? (
            configuredQuestionItems.length ? (
              <div className="interview-question-list">
                {configuredQuestionItems.map((item) => {
                  const statusLabel = item.status === "answered"
                    ? "回答済み"
                    : item.status === "active"
                      ? "確認中"
                      : "未回答";

                  return (
                    <div key={item.id} className={`interview-question-item ${item.status}`}>
                      <div className="interview-question-head">
                        <strong>{item.label}</strong>
                        <div className="interview-question-actions">
                          <span className={`question-status ${item.status}`}>{statusLabel}</span>
                          <button className="ghost compact" type="button" onClick={() => handleConfiguredAnswerDelete(item.answerKey)}>
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
                          onChange={(event) => handleConfiguredAnswerChange(item.answerKey, event.target.value)}
                          placeholder="回答を入力"
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="empty">質問がありません</p>
            )
          ) : extraConversationItems.length ? (
            <div className="interview-question-list extra-question-list">
              {extraConversationItems.map((item) => (
                <div key={item.id} className={`interview-question-item ${item.status} ${item.source}`}>
                  <div className="interview-question-head">
                    <div className="interview-question-actions">
                      <span className={`question-status ${item.status}`}>{item.answer ? "回答済み" : "確認中"}</span>
                      <button className="ghost compact" type="button" onClick={() => handleExtraQuestionDelete(item.answerKey)}>
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
                      onChange={(event) => handleExtraAnswerChange(item.answerKey, event.target.value)}
                      placeholder="回答を入力"
                    />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="empty">追加質問はまだありません</p>
          )}

          {props.interviewStreamMetadata?.answer_status !== "not_answered" && props.interviewStreamMetadata?.next_questions?.length ? (
            <div className="next-question-panel">
              <strong>次に聞く候補</strong>
              <div className="next-question-list">
                {props.interviewStreamMetadata.next_questions.map((question, index) => (
                  <button
                    key={`${question}-${index}`}
                    type="button"
                    className="next-question-chip"
                    onClick={() => props.setChatInput(question)}
                  >
                    {question}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
        </aside>

        <div className="interview-chat-panel">
          <div className="interview-chat-header">
            <div>
              <strong>チャット</strong>
            </div>
          </div>
          <div ref={chatLogRef} className="chat-log">
            {props.interviewMessages.map((message, index) => (
              <div key={index} className={`bubble ${message.role === "ai" ? "ai" : "user"}`}>
                <p>{message.text}</p>
              </div>
            ))}
          </div>
          <div className="answer-composer">
            <textarea
              value={props.chatInput}
              onChange={(event) => props.setChatInput(event.target.value)}
              onKeyDown={handleChatInputKeyDown}
              placeholder="回答を入力"
            />
            <div className="answer-composer-actions">
              <button className="primary" onClick={handleSendInterviewMessage} disabled={props.isInterviewStreaming}>
                {props.isInterviewStreaming ? "受信中..." : "送信"}
              </button>
              <div className="voice-controls">
                <button className="ghost" type="button" disabled>音声開始（準備中）</button>
                <button className="ghost" type="button" disabled>停止</button>
              </div>
            </div>
            {props.recordNotice ? <p className="notice interview-inline-notice">{props.recordNotice}</p> : null}
          </div>
        </div>
      </div>
    </section>
  );
}
