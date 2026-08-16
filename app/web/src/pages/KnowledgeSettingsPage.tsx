import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import {
  suggestKnowledgeFields,
  type FieldSuggestionChatMessage,
  type KnowledgeField
} from "../lib/api";
import type { KnowledgeLayoutProps } from "../types/pageProps";

const modelOptions = [
  { value: "apac.amazon.nova-pro-v1:0", label: "Amazon Nova Pro (APAC)" },
] as const;

const noPromptProfileValue = "__none__";

type SettingsTab = "basic" | "fields" | "assist";

type AssistMessage = {
  role: "user" | "ai";
  text: string;
};

type PendingSuggestedField = KnowledgeField & {
  proposalId: string;
  status: "pending" | "approved" | "rejected";
};

const settingsTabStorageKey = "knowledge-settings-active-tab";

function uniqueFields(fields: KnowledgeField[]) {
  const seen = new Set<string>();
  return fields.filter((field) => {
    if (seen.has(field.name)) return false;
    seen.add(field.name);
    return true;
  });
}

function toRecentAssistMessages(messages: AssistMessage[]): FieldSuggestionChatMessage[] {
  return messages
    .map((message) => ({ role: message.role, content: message.text.trim() }))
    .filter((message) => message.content.length > 0)
    .slice(-20);
}

function isStatusCodeError(error: unknown): error is { status?: number; detail?: string } {
  return typeof error === "object" && error !== null && "status" in error;
}

function toFieldSuggestionErrorMessage(error: unknown) {
  if (isStatusCodeError(error)) {
    if (!error.status || [408, 503, 504].includes(error.status)) {
      return "通信エラーが発生しました。接続を確認して、もう一度お試しください。";
    }
    if (error.status === 429) {
      return "AIサービスが混雑しています。少し待ってから、もう一度お試しください。";
    }
    if (error.detail === "question_design_output_invalid" || error.detail === "question_design_empty_suggestions") {
      return "AIの質問項目出力を正しく読み取れませんでした。もう一度お試しください。";
    }
    if (error.detail === "question_design_validation_output_invalid" || error.detail === "question_design_validation_failed") {
      return "AIが作成した質問項目の検証に失敗しました。内容を少し具体化して、もう一度お試しください。";
    }
    if (error.status === 502) {
      return "AIサービスから有効な回答を取得できませんでした。もう一度お試しください。";
    }
    if (error.status === 500) {
      return "質問項目の生成中に内部エラーが発生しました。もう一度お試しください。";
    }
    return `エラー（コード:${error.status}） 管理者にお問い合わせください。`;
  }
  return "通信エラーが発生しました。接続を確認して、もう一度お試しください。";
}

export function KnowledgeSettingsPage(props: KnowledgeLayoutProps) {
  const [activeTab, setActiveTab] = useState<SettingsTab>(() => {
    const savedTab = window.localStorage.getItem(settingsTabStorageKey);
    if (savedTab === "basic" || savedTab === "fields" || savedTab === "assist") {
      return savedTab;
    }
    return savedTab === "chat" ? "fields" : "assist";
  });
  const [assistInput, setAssistInput] = useState("");
  const [assistMessages, setAssistMessages] = useState<AssistMessage[]>([
    { role: "ai", text: "こんにちは。まず、どの業務や場面について質問項目を作りたいか教えてください。必要なら確認しながら整理します。" }
  ]);
  const [pendingSuggestions, setPendingSuggestions] = useState<PendingSuggestedField[]>([]);
  const [isGenerating, setIsGenerating] = useState(false);
  const [selectedPromptProfileId, setSelectedPromptProfileId] = useState(noPromptProfileValue);
  const [promptProfileNotice, setPromptProfileNotice] = useState("");
  const [isSavePromptDialogOpen, setIsSavePromptDialogOpen] = useState(false);
  const [savePromptTemplateName, setSavePromptTemplateName] = useState("");
  const chatLogRef = useRef<HTMLDivElement | null>(null);
  const assistInputRef = useRef<HTMLTextAreaElement | null>(null);
  const promptProfiles = props.promptProfiles ?? [];

  const selectedModelOption = modelOptions.some((option) => option.value === props.settingsDefaultModelId)
    ? props.settingsDefaultModelId
    : modelOptions[0].value;
  const selectedPromptProfile = promptProfiles.find((profile) => profile.id === selectedPromptProfileId) ?? null;
  const canSendAssistMessage = assistInput.trim().length > 0 && !isGenerating;
  const canSavePromptProfile = Boolean(props.onCreatePromptProfile)
    && props.settingsSystemPrompt.trim().length > 0
    && savePromptTemplateName.trim().length > 0;
  const activeTabLabel = activeTab === "assist"
    ? "AIインタビュー設定"
    : activeTab === "fields"
      ? "ヒアリング項目"
      : "基本設定";

  function clearSettingsNotice() {
    if (!props.settingsNotice) return;
    props.onClearSettingsNotice();
  }

  useEffect(() => {
    if (activeTab !== "fields") return;
    window.requestAnimationFrame(() => {
      chatLogRef.current?.scrollTo({ top: chatLogRef.current.scrollHeight, behavior: "smooth" });
    });
  }, [activeTab, assistMessages, pendingSuggestions, isGenerating]);

  useEffect(() => {
    if (!promptProfileNotice) return;
    const timeoutId = window.setTimeout(() => setPromptProfileNotice(""), 3000);
    return () => window.clearTimeout(timeoutId);
  }, [promptProfileNotice]);

  useEffect(() => {
    if (modelOptions.some((option) => option.value === props.settingsDefaultModelId)) return;
    props.setSettingsDefaultModelId(modelOptions[0].value);
  }, [props.settingsDefaultModelId, props.setSettingsDefaultModelId]);

  function selectTab(tab: SettingsTab) {
    if (tab !== activeTab) {
      clearSettingsNotice();
    }
    setActiveTab(tab);
    window.localStorage.setItem(settingsTabStorageKey, tab);
  }

  function updateField(index: number, patch: Partial<KnowledgeField>) {
    clearSettingsNotice();
    const next = [...props.draftFields];
    next[index] = { ...props.draftFields[index], ...patch };
    props.setDraftFields(next);
  }

  function toPendingSuggestions(fields: KnowledgeField[]) {
    return uniqueFields(fields).map((field, index) => ({
      ...field,
      id: undefined,
      proposalId: `${field.name}-${Date.now()}-${index}`,
      status: "pending" as const
    }));
  }

  function approveSuggestion(proposalId: string) {
    const suggestion = pendingSuggestions.find((item) => item.proposalId === proposalId && item.status === "pending");
    if (!suggestion) return;

    clearSettingsNotice();
    props.setDraftFields([
      ...props.draftFields,
      {
        name: suggestion.name,
        description: suggestion.description,
        inputType: suggestion.inputType,
        required: suggestion.required,
        askByAi: suggestion.askByAi,
        aiQuestionExamples: suggestion.aiQuestionExamples,
        questionPlan: suggestion.questionPlan,
        displayOrder: props.draftFields.length + 1
      }
    ]);
    setPendingSuggestions((items) => items.map((item) => (
      item.proposalId === proposalId ? { ...item, status: "approved" } : item
    )));
  }

  function rejectSuggestion(proposalId: string) {
    setPendingSuggestions((items) => items.map((item) => (
      item.proposalId === proposalId ? { ...item, status: "rejected" } : item
    )));
  }

  function approveAllSuggestions() {
    const pendingItems = pendingSuggestions.filter((item) => item.status === "pending");
    if (pendingItems.length === 0) return;

    clearSettingsNotice();
    props.setDraftFields([
      ...props.draftFields,
      ...pendingItems.map((item, index) => ({
        name: item.name,
        description: item.description,
        inputType: item.inputType,
        required: item.required,
        askByAi: item.askByAi,
        aiQuestionExamples: item.aiQuestionExamples,
        questionPlan: item.questionPlan,
        displayOrder: props.draftFields.length + index + 1
      }))
    ]);
    setPendingSuggestions((items) => items.map((item) => (
      item.status === "pending" ? { ...item, status: "approved" } : item
    )));
  }

  async function handleCreatePromptProfile() {
    if (!canSavePromptProfile || !props.onCreatePromptProfile) return;
    const created = await props.onCreatePromptProfile({
      name: savePromptTemplateName.trim(),
      prompt: props.settingsSystemPrompt.trim()
    });
    setSelectedPromptProfileId(created.id);
    setIsSavePromptDialogOpen(false);
    setSavePromptTemplateName("");
    setPromptProfileNotice("追加カスタマイズを登録しました");
  }

  async function handleSendAssistMessage() {
    if (!canSendAssistMessage) return;
    if (!props.selectedKnowledge) {
      setAssistMessages((messages) => [...messages, { role: "ai", text: "先にナレッジを選択してください。" }]);
      return;
    }

    const userText = assistInput.trim();
    const nextMessages = [...assistMessages, { role: "user" as const, text: userText }];
    setAssistInput("");
    setIsGenerating(true);
    setAssistMessages(nextMessages);

    try {
      const result = await suggestKnowledgeFields(props.selectedKnowledge.id, {
        content: userText,
        context: {
          name: props.settingsName,
          description: props.settingsDescription,
          category: props.settingsCategory,
          targetBusiness: props.settingsTargetBusiness,
          targetEquipment: props.settingsTargetEquipment,
          language: props.settingsLanguage,
          defaultModelId: props.settingsDefaultModelId
        },
        existingFields: props.draftFields,
        recentMessages: toRecentAssistMessages(nextMessages),
        maxFields: 5
      });

      const existingNames = new Set(props.draftFields.map((field) => field.name.trim()).filter(Boolean));
      const unrejectedPendingNames = new Set(
        pendingSuggestions
          .filter((field) => field.status !== "rejected")
          .map((field) => field.name.trim())
          .filter(Boolean)
      );
      const proposedBeforeDedup = toPendingSuggestions(result.fields);
      const newlyProposed = proposedBeforeDedup.filter((field) => (
        !existingNames.has(field.name.trim()) && !unrejectedPendingNames.has(field.name.trim())
      ));

      if (import.meta.env.DEV) {
        console.debug("fieldSuggestions.frontendResult", {
          reply: result.reply,
          fieldsLength: result.fields.length,
          proposedBeforeDedupLength: proposedBeforeDedup.length,
          newlyProposedLength: newlyProposed.length,
          existingNamesCount: existingNames.size,
          unrejectedPendingNamesCount: unrejectedPendingNames.size,
          pendingSuggestionsAddedCount: newlyProposed.length
        });
      }

      if (newlyProposed.length > 0) {
        setPendingSuggestions((items) => [...items, ...newlyProposed]);
      }
      if (result.interviewPlan) {
        props.setSettingsInterviewPlan(result.interviewPlan);
      }

      setAssistMessages((messages) => [...messages, { role: "ai", text: result.reply }]);
    } catch (error) {
      setAssistMessages((messages) => [...messages, { role: "ai", text: toFieldSuggestionErrorMessage(error) }]);
    } finally {
      setIsGenerating(false);
      window.requestAnimationFrame(() => {
        assistInputRef.current?.focus();
      });
    }
  }

  function handleAssistKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    handleSendAssistMessage();
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>ナレッジDB設定</h2>
          <p className="lede">ナレッジ名、説明、AI設定、ヒアリング項目を管理します。</p>
        </div>
        <button className="ghost" onClick={() => props.navigate(`/knowledge/${props.selectedKnowledgeDb?.id}`)}>戻る</button>
      </div>

      <div className="settings-tabs" role="tablist" aria-label="ナレッジDB設定メニュー">
        <button type="button" role="tab" aria-selected={activeTab === "basic"} className={activeTab === "basic" ? "settings-tab active" : "settings-tab"} onClick={() => selectTab("basic")}>基本設定</button>
        <button type="button" role="tab" aria-selected={activeTab === "fields"} className={activeTab === "fields" ? "settings-tab active" : "settings-tab"} onClick={() => selectTab("fields")}>ヒアリング項目</button>
        <button type="button" role="tab" aria-selected={activeTab === "assist"} className={activeTab === "assist" ? "settings-tab active" : "settings-tab"} onClick={() => selectTab("assist")}>AI設定</button>
      </div>

      <div className="settings-workspace">
        <div className="settings-main">
          {activeTab === "basic" ? (
            <section className="settings-section" role="tabpanel">
              <div className="section-title-row">
                <div>
                  <h3>基本設定</h3>
                  <p>ナレッジ名と説明を設定します。</p>
                </div>
              </div>
              <div className="form-grid">
                <label>ナレッジ名<input value={props.settingsName} onChange={(event) => { clearSettingsNotice(); props.setSettingsName(event.target.value); }} /></label>
              </div>
              <label>説明<textarea value={props.settingsDescription} onChange={(event) => { clearSettingsNotice(); props.setSettingsDescription(event.target.value); }} /></label>
            </section>
          ) : null}

          {activeTab === "assist" ? (
            <section className="settings-section" role="tabpanel">
              <div className="section-title-row">
                <div>
                  <h3>AIインタビュー設定</h3>
                  <p>実際のインタビューで使うモデルと、追加カスタマイズを設定します。</p>
                </div>
              </div>
              <div className="form-grid two-column">
                <label>生成AIモデル
                  <select value={selectedModelOption} onChange={(event) => { clearSettingsNotice(); props.setSettingsDefaultModelId(event.target.value); }}>
                    {modelOptions.map((option) => (<option key={option.value} value={option.value}>{option.label}</option>))}
                  </select>
                </label>
                <div className="template-picker-field">
                  <span>保存済みテンプレート</span>
                  <div className="inline-form">
                    <select value={selectedPromptProfileId} onChange={(event) => setSelectedPromptProfileId(event.target.value)}>
                      <option value={noPromptProfileValue}>未選択</option>
                      {promptProfiles.map((profile) => (<option key={profile.id} value={profile.id}>{profile.name}</option>))}
                    </select>
                    <button
                      className="ghost compact"
                      type="button"
                      onClick={() => {
                        if (!selectedPromptProfile) return;
                        clearSettingsNotice();
                        props.setSettingsSystemPrompt(selectedPromptProfile.prompt);
                        setPromptProfileNotice("保存済みテンプレートを読み込みました");
                      }}
                      disabled={!selectedPromptProfile}
                    >
                      読み込む
                    </button>
                  </div>
                </div>
              </div>
              <div className="section-title-row compact-row">
                <div>
                  <h3>追加カスタマイズプロンプト</h3>
                </div>
                <button
                  className="ghost compact"
                  type="button"
                  onClick={() => {
                    if (!props.settingsSystemPrompt.trim() || !props.onCreatePromptProfile) return;
                    setSavePromptTemplateName("");
                    setIsSavePromptDialogOpen(true);
                  }}
                  disabled={!props.settingsSystemPrompt.trim() || !props.onCreatePromptProfile}
                >
                  テンプレートとして保存
                </button>
              </div>
              <label className="system-prompt-field">
                <textarea
                  value={props.settingsSystemPrompt}
                  onChange={(event) => { clearSettingsNotice(); props.setSettingsSystemPrompt(event.target.value); }}
                  placeholder="必要なら、このナレッジ専用の深掘り観点や聞き方だけを追加してください。"
                />
              </label>
              <p className="empty">保存したテンプレートは、他のナレッジでも読み込めます。</p>
              {promptProfileNotice ? <span className="notice">{promptProfileNotice}</span> : null}
            </section>
          ) : null}

          {activeTab === "fields" ? (
            <div className="settings-split" role="tabpanel">
              <section className="settings-section">
                <div className="section-title-row">
                  <div>
                    <h3>ヒアリング項目</h3>
                    <p>AIが聞き取る項目と、構造化ナレッジとして保存する項目を編集します。</p>
                  </div>
                  <button className="ghost compact" onClick={() => {
                    clearSettingsNotice();
                    props.setDraftFields([...props.draftFields, { name: "新規項目", inputType: "short_text", required: false, askByAi: true, aiQuestionExamples: [], displayOrder: props.draftFields.length + 1 }]);
                  }}>項目追加</button>
                </div>
                <div className="field-list">
                  {props.draftFields.length === 0 ? <p className="empty">ヒアリング項目はまだありません。右側の AIチャット からも作成できます。</p> : props.draftFields.map((field, index) => (
                    <div className="field-card readable-field-card" key={`${field.id ?? "new"}-${index}`}>
                      <div className="field-card-header">
                        <div>
                          <span className="field-order">項目 {index + 1}</span>
                          <strong>{field.name || "未命名項目"}</strong>
                        </div>
                        <button className="danger compact" onClick={() => { clearSettingsNotice(); props.setDraftFields(props.draftFields.filter((_, i) => i !== index)); }}>削除</button>
                      </div>
                      <div className="form-grid field-form-grid">
                        <label>項目名<input value={field.name} onChange={(event) => updateField(index, { name: event.target.value })} /></label>
                        <label className="wide-field">AI質問例
                          <textarea value={(field.aiQuestionExamples ?? []).join("\n")} onChange={(event) => updateField(index, { aiQuestionExamples: event.target.value.split("\n").map((item) => item.trim()).filter(Boolean) })} placeholder="例: いつ、どこで、どの条件で発生しましたか" />
                        </label>
                      </div>
                      <div className="toolbar">
                        <label className="check-row"><input type="checkbox" checked={field.required} onChange={(event) => updateField(index, { required: event.target.checked })} />必須項目</label>
                        <label className="check-row"><input type="checkbox" checked={field.askByAi} onChange={(event) => updateField(index, { askByAi: event.target.checked })} />AIが質問する</label>
                      </div>
                    </div>
                  ))}
                </div>
              </section>

              <aside className="settings-side">
                <div className="settings-ai-chat" aria-label="AIヒアリング項目提案チャット">
                  <div className="ai-chat-header">
                    <div>
                      <strong>AIヒアリング設計チャット</strong>
                    </div>
                  </div>
                  <div className="settings-chat-log" ref={chatLogRef}>
                    {assistMessages.map((message, index) => (
                      <div key={`${message.role}-${index}`} className={`bubble ${message.role === "ai" ? "ai" : "user"}`}>
                        <p>{message.text}</p>
                      </div>
                    ))}
                    {pendingSuggestions.filter((item) => item.status === "pending").map((field) => (
                      <article key={field.proposalId} className="proposal-card settings-suggestion-card">
                        <div className="proposal-fields">
                          <div className="proposal-field"><strong>項目</strong><p>{field.name}</p></div>
                          <div className="proposal-field"><strong>質問例</strong><p>{field.aiQuestionExamples?.join(" / ") || "質問例はまだありません。"}</p></div>
                        </div>
                        <div className="actions">
                          <button className="primary compact" onClick={() => approveSuggestion(field.proposalId)}>承認</button>
                          <button className="ghost compact" onClick={() => rejectSuggestion(field.proposalId)}>拒否</button>
                        </div>
                      </article>
                    ))}
                    {isGenerating ? <div className="bubble ai typing-bubble"><p>thinking...</p></div> : null}
                  </div>
                  <div className="settings-chat-input">
                    <textarea ref={assistInputRef} value={assistInput} onChange={(event) => setAssistInput(event.target.value)} onKeyDown={handleAssistKeyDown} disabled={isGenerating} placeholder="例: 故障原因の切り分けを聞き出す質問を設計したい" />
                    <div className="toolbar">
                      <button className="ghost compact" onClick={approveAllSuggestions} disabled={pendingSuggestions.every((item) => item.status !== "pending")}>一括承認</button>
                      <button className="primary compact" onClick={handleSendAssistMessage} disabled={!canSendAssistMessage}>{isGenerating ? "生成中" : "送信"}</button>
                    </div>
                  </div>
                </div>
              </aside>
            </div>
          ) : null}

          <div className="actions sticky-actions">
            <button className="primary" onClick={() => props.onSaveSettings(activeTab)}>{activeTabLabel}を保存</button>
            {props.settingsNotice ? <span className="notice">{props.settingsNotice}</span> : null}
          </div>
        </div>
      </div>

      {isSavePromptDialogOpen ? (
        <div className="dialog-backdrop" role="presentation">
          <form
            className="dialog-panel"
            role="dialog"
            aria-modal="true"
            aria-label="テンプレート保存確認"
            onSubmit={(event) => {
              event.preventDefault();
              handleCreatePromptProfile().catch(() => undefined);
            }}
          >
            <div className="dialog-header">
              <div>
                <h2>テンプレート保存</h2>
                <p>現在の追加カスタマイズプロンプトを保存しますか。</p>
              </div>
            </div>
            <label className="field-group">
              <span>テンプレート名</span>
              <input
                autoFocus
                value={savePromptTemplateName}
                onChange={(event) => setSavePromptTemplateName(event.target.value)}
                placeholder="例: 深掘り重視"
              />
            </label>
            <div className="dialog-actions">
              <button type="button" className="ghost" onClick={() => setIsSavePromptDialogOpen(false)}>キャンセル</button>
              <button type="submit" className="primary" disabled={!canSavePromptProfile}>保存する</button>
            </div>
          </form>
        </div>
      ) : null}
    </section>
  );
}
