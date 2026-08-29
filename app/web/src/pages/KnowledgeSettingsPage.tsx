import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import {
  suggestKnowledgeFields,
  type FieldSuggestionChatMessage,
  type KnowledgeField
} from "../lib/api";
import {
  DEFAULT_INTERVIEW_MODEL_ID,
  isInterviewConfigurationComplete,
} from "../features/interviews/interviewConfiguration";
import { KnowledgeDocumentsContent } from "./KnowledgeDocumentsPage";
import type { KnowledgeLayoutProps } from "../types/pageProps";

const modelOptions = [
  { value: "global.openai.gpt-5.6-luna", label: "GPT-5.6 Luna（標準）" },
  { value: "global.openai.gpt-5.6-terra", label: "GPT-5.6 Terra（高精度）" },
] as const;

const structuredInterviewModelOptions = [
  { value: "global.openai.gpt-5.6-luna", label: "GPT-5.6 Luna（標準）", description: "標準" },
  { value: "global.openai.gpt-5.6-terra", label: "GPT-5.6 Terra（高精度）", description: "高精度優先" },
] as const;

const interviewProfileOptions = [
  { value: "fixed_form", label: "定型情報を聞き取る", description: "設定した質問項目を順番に確認します。" },
  { value: "business_process", label: "業務フローを整理する", description: "開始から終了までの業務フローと例外を整理します。" },
  { value: "system_requirement", label: "システム要件を整理する", description: "目的・課題、要求内容、必要な処理の流れを整理します。" },
] as const;

const noPromptProfileValue = "__none__";

type SettingsTab = "fields" | "execution" | "knowledge";

type AssistMessage = {
  role: "user" | "ai";
  text: string;
};

type PendingSuggestedField = KnowledgeField & {
  proposalId: string;
  status: "pending" | "approved" | "rejected";
};

const settingsTabStorageKey = "knowledge-settings-active-tab";

function getStoredSettingsTab(): SettingsTab {
  const savedTab = window.localStorage.getItem(settingsTabStorageKey);
  if (savedTab === "execution" || savedTab === "knowledge") return savedTab;
  if (savedTab === "assist") return "execution";
  return "fields";
}

function getSettingsSaveTab(tab: SettingsTab): "fields" | "execution" {
  return tab === "fields" ? "fields" : "execution";
}

function uniqueFields(fields: KnowledgeField[]) {
  const seen = new Set<string>();
  return fields.filter((field) => {
    if (seen.has(field.name)) return false;
    seen.add(field.name);
    return true;
  });
}

function summarizeFieldDetail(field: KnowledgeField) {
  const detail = (field.description ?? "").trim().replace(/\s+/g, " ");
  if (!detail) return "詳細項目未設定";
  return detail.length > 72 ? `${detail.slice(0, 72)}…` : detail;
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
    if (!isInterviewConfigurationComplete(props.selectedKnowledge)) return "execution";
    return getStoredSettingsTab();
  });
  const [assistInput, setAssistInput] = useState("");
  const [assistMessages, setAssistMessages] = useState<AssistMessage[]>([
    { role: "ai", text: "質問項目を作りたい業務や場面を入力してください。" }
  ]);
  const [pendingSuggestions, setPendingSuggestions] = useState<PendingSuggestedField[]>([]);
  const [isGenerating, setIsGenerating] = useState(false);
  const [selectedPromptProfileId, setSelectedPromptProfileId] = useState(noPromptProfileValue);
  const [promptProfileNotice, setPromptProfileNotice] = useState("");
  const [isSavePromptDialogOpen, setIsSavePromptDialogOpen] = useState(false);
  const [savePromptTemplateName, setSavePromptTemplateName] = useState("");
  const [expandedFieldIndex, setExpandedFieldIndex] = useState<number | null>(null);
  const chatLogRef = useRef<HTMLDivElement | null>(null);
  const assistInputRef = useRef<HTMLTextAreaElement | null>(null);
  const initializedKnowledgeIdRef = useRef(props.selectedKnowledge?.id ?? null);
  const promptProfiles = props.promptProfiles ?? [];
  const requiresInterviewConfiguration = !isInterviewConfigurationComplete(props.selectedKnowledge);

  const selectedModelOption = modelOptions.some((option) => option.value === props.settingsDefaultModelId)
    ? props.settingsDefaultModelId
    : modelOptions[0].value;
  const selectedPromptProfile = promptProfiles.find((profile) => profile.id === selectedPromptProfileId) ?? null;
  const selectedInterviewProfile = props.settingsInterviewPlan?.profile ?? "fixed_form";
  const configuredStructuredInterviewModel = props.settingsInterviewPlan?.modelId;
  const selectedStructuredInterviewModel = configuredStructuredInterviewModel
    && structuredInterviewModelOptions.some((option) => option.value === configuredStructuredInterviewModel)
    ? configuredStructuredInterviewModel
    : DEFAULT_INTERVIEW_MODEL_ID;
  const canSendAssistMessage = assistInput.trim().length > 0 && !isGenerating;
  const canSavePromptProfile = Boolean(props.onCreatePromptProfile)
    && props.settingsSystemPrompt.trim().length > 0
    && savePromptTemplateName.trim().length > 0;
  function clearSettingsNotice() {
    if (!props.settingsNotice && props.settingsSaveState === "idle") return;
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

  useEffect(() => {
    const knowledgeId = props.selectedKnowledge?.id ?? null;
    if (initializedKnowledgeIdRef.current === knowledgeId) return;

    initializedKnowledgeIdRef.current = knowledgeId;
    const nextTab = requiresInterviewConfiguration
      ? "execution"
      : getStoredSettingsTab();
    setActiveTab(nextTab);
    setExpandedFieldIndex(null);
  }, [props.selectedKnowledge?.id, requiresInterviewConfiguration]);

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

  function addField() {
    clearSettingsNotice();
    const nextIndex = props.draftFields.length;
    props.setDraftFields([
      ...props.draftFields,
      {
        name: "新規項目",
        inputType: "short_text",
        required: false,
        askByAi: true,
        aiQuestionExamples: [],
        displayOrder: nextIndex + 1
      }
    ]);
    setExpandedFieldIndex(nextIndex);
  }

  function deleteField(index: number) {
    clearSettingsNotice();
    props.setDraftFields(props.draftFields.filter((_, currentIndex) => currentIndex !== index));
    setExpandedFieldIndex((currentIndex) => {
      if (currentIndex === null || currentIndex === index) return null;
      return currentIndex > index ? currentIndex - 1 : currentIndex;
    });
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
        askByAi: true,
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
        askByAi: true,
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
        props.setSettingsInterviewPlan({
          ...result.interviewPlan,
          profile: props.settingsInterviewPlan?.profile ?? result.interviewPlan.profile ?? "fixed_form",
          modelId: props.settingsInterviewPlan?.modelId ?? DEFAULT_INTERVIEW_MODEL_ID,
        });
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
          <h2>インタビュー設定</h2>
        </div>
        <div className="actions">
          <button
            className="ghost"
            type="button"
            onClick={() => props.navigate(`/knowledge-dbs/${props.selectedKnowledgeDb?.id}/knowledges/${props.selectedKnowledge?.id}/interview`)}
          >
            インタビューに戻る
          </button>
          <button
            className="primary"
            type="button"
            onClick={() => props.onSaveSettings(getSettingsSaveTab(activeTab))}
            disabled={props.settingsSaveState === "saving"}
          >
            {props.settingsSaveState === "saving" ? "保存中…" : "設定を保存"}
          </button>
        </div>
      </div>

      {requiresInterviewConfiguration ? (
        <div className="settings-setup-notice">
          <strong>インタビューを開始するには、用途と実行モデルの保存が必要です。</strong>
        </div>
      ) : null}

      <section className="settings-section knowledge-info-section" aria-label="ナレッジ情報">
        <div className="knowledge-info-fields">
          <label>ナレッジ名<input value={props.settingsName} onChange={(event) => { clearSettingsNotice(); props.setSettingsName(event.target.value); }} /></label>
          <label>説明<textarea value={props.settingsDescription} onChange={(event) => { clearSettingsNotice(); props.setSettingsDescription(event.target.value); }} /></label>
        </div>
      </section>

      <div className="settings-tabs-row">
        <div className="settings-tabs" role="tablist" aria-label="インタビュー設定メニュー">
          <button id="settings-tab-fields" type="button" role="tab" aria-controls="settings-panel-fields" aria-selected={activeTab === "fields"} className={activeTab === "fields" ? "settings-tab active" : "settings-tab"} onClick={() => selectTab("fields")}>質問項目</button>
          <button id="settings-tab-execution" type="button" role="tab" aria-controls="settings-panel-execution" aria-selected={activeTab === "execution"} className={activeTab === "execution" ? "settings-tab active" : "settings-tab"} onClick={() => selectTab("execution")}>実行設定</button>
          <button id="settings-tab-knowledge" type="button" role="tab" aria-controls="settings-panel-knowledge" aria-selected={activeTab === "knowledge"} className={activeTab === "knowledge" ? "settings-tab active" : "settings-tab"} onClick={() => selectTab("knowledge")}>事前知識</button>
        </div>
      </div>

      <div className="settings-workspace">
        <div className="settings-main">
          {activeTab === "execution" ? (
            <section id="settings-panel-execution" className="settings-section" role="tabpanel" aria-labelledby="settings-tab-execution">
              <div className="section-title-row">
                <div>
                  <h3>実行設定</h3>
                </div>
              </div>
              <div className="interview-settings">
                <div className="interview-setting-grid">
                  <section className="interview-setting-card">
                    <div className="interview-setting-card-header">
                      <div>
                        <h4>用途</h4>
                      </div>
                    </div>
                    <label className="sr-only" htmlFor="interview-profile">インタビュー用途</label>
                    <select
                      id="interview-profile"
                      value={selectedInterviewProfile}
                      onChange={(event) => {
                        clearSettingsNotice();
                        props.setSettingsInterviewPlan({
                          ...(props.settingsInterviewPlan ?? {}),
                          version: props.settingsInterviewPlan?.version ?? 1,
                          profile: event.target.value as typeof selectedInterviewProfile,
                        });
                      }}
                    >
                      {interviewProfileOptions.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                    <p className="form-help">
                      {interviewProfileOptions.find((option) => option.value === selectedInterviewProfile)?.description}
                    </p>
                  </section>
                  <section className="interview-setting-card featured">
                    <div className="interview-setting-card-header">
                      <div>
                        <h4>インタビュー実行モデル</h4>
                      </div>
                    </div>
                    <label className="sr-only" htmlFor="structured-interview-model">インタビュー実行モデル</label>
                    <select
                      id="structured-interview-model"
                      value={selectedStructuredInterviewModel}
                      onChange={(event) => {
                        clearSettingsNotice();
                        props.setSettingsInterviewPlan({
                          ...(props.settingsInterviewPlan ?? {}),
                          version: props.settingsInterviewPlan?.version ?? 1,
                          modelId: event.target.value as NonNullable<typeof selectedStructuredInterviewModel>,
                        });
                      }}
                    >
                      {structuredInterviewModelOptions.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                    <p className="form-help">
                      {structuredInterviewModelOptions.find((option) => option.value === selectedStructuredInterviewModel)?.description}
                    </p>
                  </section>
                </div>
                <section className="interview-setting-group">
                  <div className="interview-setting-group-header">
                    <div>
                      <h4>補助設定</h4>
                    </div>
                  </div>
                  <div className="interview-setting-grid">
                    <section className="interview-setting-card compact">
                      <h5>質問項目の設計モデル</h5>
                      <label className="sr-only" htmlFor="question-design-model">質問項目の設計モデル</label>
                      <select id="question-design-model" value={selectedModelOption} onChange={(event) => { clearSettingsNotice(); props.setSettingsDefaultModelId(event.target.value); }}>
                        {modelOptions.map((option) => (<option key={option.value} value={option.value}>{option.label}</option>))}
                      </select>
                    </section>
                    <section className="interview-setting-card compact">
                      <h5>保存済みテンプレート</h5>
                      <div className="inline-form">
                        <select aria-label="保存済みテンプレート" value={selectedPromptProfileId} onChange={(event) => setSelectedPromptProfileId(event.target.value)}>
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
                    </section>
                  </div>
                </section>
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
              {promptProfileNotice ? <span className="notice">{promptProfileNotice}</span> : null}
            </section>
          ) : null}

          {activeTab === "fields" ? (
            <div id="settings-panel-fields" className="settings-split" role="tabpanel" aria-labelledby="settings-tab-fields">
              <section className="settings-section">
                <div className="section-title-row compact-row">
                  <div className="question-list-title">
                    <h3>質問項目</h3>
                    <span className="counter">{props.draftFields.length}件</span>
                  </div>
                  <button className="ghost compact" type="button" onClick={addField}>項目追加</button>
                </div>
                <div className="field-list">
                  {props.draftFields.length === 0 ? <p className="empty">質問項目はありません。</p> : props.draftFields.map((field, index) => {
                    const isExpanded = expandedFieldIndex === index;
                    const editorId = `knowledge-field-editor-${index}`;
                    return (
                      <article className={isExpanded ? "field-card expanded" : "field-card"} key={`${field.id ?? "new"}-${index}`}>
                        <button
                          type="button"
                          className="field-card-summary"
                          aria-expanded={isExpanded}
                          aria-controls={isExpanded ? editorId : undefined}
                          onClick={() => setExpandedFieldIndex(isExpanded ? null : index)}
                        >
                          <span className="field-index">{String(index + 1).padStart(2, "0")}</span>
                          <span className="field-summary-content">
                            <strong>{field.name.trim() || "未入力の質問項目"}</strong>
                            <span>{summarizeFieldDetail(field)}</span>
                          </span>
                          {field.required ? <span className="field-required-badge">必須</span> : null}
                          <span className={isExpanded ? "field-chevron open" : "field-chevron"} aria-hidden="true" />
                        </button>
                        {isExpanded ? (
                          <div id={editorId} className="field-card-editor">
                            <label className="sr-only" htmlFor={`knowledge-field-name-${index}`}>質問項目名</label>
                            <input
                              id={`knowledge-field-name-${index}`}
                              value={field.name}
                              onChange={(event) => updateField(index, { name: event.target.value })}
                              placeholder="質問項目名"
                            />
                            <label className="field-detail-field">詳細項目
                              <textarea value={field.description ?? ""} onChange={(event) => updateField(index, { description: event.target.value })} placeholder="例: 名前、年齢、性別、出身地" />
                            </label>
                            <div className="toolbar field-card-editor-actions">
                              <label className="check-row"><input type="checkbox" checked={field.required} onChange={(event) => updateField(index, { required: event.target.checked })} />必須項目</label>
                              <button className="danger compact" type="button" onClick={() => deleteField(index)}>削除</button>
                            </div>
                          </div>
                        ) : null}
                      </article>
                    );
                  })}
                </div>
              </section>

              <aside className="settings-side">
                <div className="settings-ai-chat" aria-label="AI質問項目提案チャット">
                  <div className="ai-chat-header">
                    <div>
                      <strong>AI質問項目設計チャット</strong>
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
                          <div className="proposal-field"><strong>詳細項目</strong><p>{field.description || "詳細項目はまだありません。"}</p></div>
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

          {activeTab === "knowledge" ? (
            <section id="settings-panel-knowledge" className="settings-section knowledge-documents-settings" role="tabpanel" aria-labelledby="settings-tab-knowledge">
              <div className="section-title-row">
                <div>
                  <h3>事前知識</h3>
                  <p className="form-help">登録した文書は、質問項目の設計やインタビュー内容の整理で参照されます。</p>
                </div>
                <span className="status-pill muted">{props.documents.length}件</span>
              </div>
              <KnowledgeDocumentsContent {...props} />
            </section>
          ) : null}

          <div className="actions sticky-actions">
            <button
              type="button"
              className="primary"
              onClick={() => props.onSaveSettings(getSettingsSaveTab(activeTab))}
              disabled={props.settingsSaveState === "saving"}
              aria-busy={props.settingsSaveState === "saving"}
            >
              {props.settingsSaveState === "saving" ? "保存中…" : "設定を保存"}
            </button>
            {props.settingsNotice ? (
              <span
                className={`notice settings-save-status ${props.settingsSaveState}`}
                role="status"
                aria-live="polite"
              >
                {props.settingsSaveState === "saving" ? <span className="save-status-spinner" aria-hidden="true" /> : null}
                {props.settingsNotice}
              </span>
            ) : null}
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
