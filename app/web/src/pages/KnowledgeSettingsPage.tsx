import { useEffect, useMemo, useRef, useState } from "react";
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
import { useI18n, type Translate } from "../i18n";
import { formatNumber } from "../lib/date";
import type { KnowledgeLayoutProps } from "../types/pageProps";
import { OptionPicker } from "../components/ui/OptionPicker";
import { TagEditor } from "../components/ui/TagEditor";
import { useGuide } from "../features/guides/GuideProvider";

const modelOptions = [
  { value: "global.openai.gpt-5.6-luna", labelKey: "interview.model.lunaStandard" },
  { value: "global.openai.gpt-5.6-terra", labelKey: "interview.model.terraAccurate" },
] as const;

const structuredInterviewModelOptions = [
  { value: "global.openai.gpt-5.6-luna", labelKey: "settings.models.luna", descriptionKey: "settings.models.standard" },
  { value: "global.openai.gpt-5.6-terra", labelKey: "settings.models.terra", descriptionKey: "settings.models.accuracyPriority" },
] as const;

const interviewProfileOptions = [
  { value: "fixed_form", labelKey: "settings.profiles.fixed_form", descriptionKey: "settings.profileDescriptions.fixed_form" },
  { value: "business_process", labelKey: "settings.profiles.business_process", descriptionKey: "settings.profileDescriptions.business_process" },
  { value: "system_requirement", labelKey: "settings.profiles.system_requirement", descriptionKey: "settings.profileDescriptions.system_requirement" },
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

function summarizeFieldDetail(field: KnowledgeField, t: Translate) {
  const detail = (field.description ?? "").trim().replace(/\s+/g, " ");
  if (!detail) return t("settings.fields.noDetail");
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

function toFieldSuggestionErrorMessage(error: unknown, t: Translate) {
  if (isStatusCodeError(error)) {
    if (!error.status || [408, 503, 504].includes(error.status)) {
      return t("errors.network");
    }
    if (error.status === 429) {
      return t("errors.aiBusy");
    }
    if (error.detail === "question_design_output_invalid" || error.detail === "question_design_empty_suggestions") {
      return t("errors.invalidAiOutput");
    }
    if (error.detail === "question_design_validation_output_invalid" || error.detail === "question_design_validation_failed") {
      return t("errors.validationFailed");
    }
    if (error.status === 502) {
      return t("errors.invalidAiResponse");
    }
    if (error.status === 500) {
      return t("errors.generationInternal");
    }
    return t("errors.statusWithCode", { code: error.status });
  }
  return t("errors.network");
}

export function KnowledgeSettingsPage(props: KnowledgeLayoutProps) {
  const { t, locale } = useI18n();
  const guide = useGuide();
  const [activeTab, setActiveTab] = useState<SettingsTab>(() => {
    if (!isInterviewConfigurationComplete(props.selectedKnowledge)) return "execution";
    return getStoredSettingsTab();
  });
  const [assistInput, setAssistInput] = useState("");
  const [assistMessages, setAssistMessages] = useState<AssistMessage[]>([
    { role: "ai", text: t("settings.assistant.initialMessage") }
  ]);
  const [pendingSuggestions, setPendingSuggestions] = useState<PendingSuggestedField[]>([]);
  const [isGenerating, setIsGenerating] = useState(false);
  const [selectedPromptProfileId, setSelectedPromptProfileId] = useState(noPromptProfileValue);
  const [promptProfileNotice, setPromptProfileNotice] = useState("");
  const [isSavePromptDialogOpen, setIsSavePromptDialogOpen] = useState(false);
  const [savePromptTemplateName, setSavePromptTemplateName] = useState("");
  const [dontShowCreationGuideAgain, setDontShowCreationGuideAgain] = useState(false);
  const [expandedFieldIndex, setExpandedFieldIndex] = useState<number | null>(null);
  const chatLogRef = useRef<HTMLDivElement | null>(null);
  const assistInputRef = useRef<HTMLTextAreaElement | null>(null);
  const initializedKnowledgeIdRef = useRef(props.selectedKnowledge?.id ?? null);
  const promptProfiles = props.promptProfiles ?? [];
  const requiresInterviewConfiguration = !isInterviewConfigurationComplete(props.selectedKnowledge);
  const existingTags = useMemo(
    () => props.knowledges.flatMap((knowledge) => knowledge.tags ?? []),
    [props.knowledges],
  );

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
    if (assistMessages.length === 1 && assistMessages[0]?.role === "ai") {
      setAssistMessages([{ role: "ai", text: t("settings.assistant.initialMessage") }]);
    }
  }, [locale]);

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
        name: t("settings.fields.unnamed"),
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
    setPromptProfileNotice(t("settings.messages.templateSaved"));
  }

  async function handleSendAssistMessage() {
    if (!canSendAssistMessage) return;
    if (!props.selectedKnowledge) {
      setAssistMessages((messages) => [...messages, { role: "ai", text: t("settings.messages.selectKnowledge") }]);
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
      setAssistMessages((messages) => [...messages, { role: "ai", text: toFieldSuggestionErrorMessage(error, t) }]);
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

  const showKnowledgeCreationGuideNotice = props.knowledgeCreationNotice
    && !guide.isKnowledgeCreationGuideAutoPromptDisabled();

  function dismissKnowledgeCreationGuideNotice() {
    if (dontShowCreationGuideAgain) {
      guide.setKnowledgeCreationGuideAutoPromptDisabled(true);
    }
    props.onDismissKnowledgeCreationNotice();
  }

  return (
    <section className="panel" data-guide="knowledge-settings">
      <div className="panel-header">
        <div>
          <h2>{t("settings.title")}</h2>
        </div>
        <div className="actions">
          <button
            className="ghost"
            type="button"
            onClick={() => props.navigate(`/knowledge-dbs/${props.selectedKnowledgeDb?.id}/knowledges/${props.selectedKnowledge?.id}/interview`)}
          >
            {t("settings.returnToInterview")}
          </button>
          <button
            className="primary"
            type="button"
            onClick={() => props.onSaveSettings(getSettingsSaveTab(activeTab))}
            disabled={props.settingsSaveState === "saving"}
          >
            {props.settingsSaveState === "saving" ? t("settings.saving") : t("settings.save")}
          </button>
        </div>
      </div>

      {showKnowledgeCreationGuideNotice ? (
        <div className="knowledge-created-guide-notice" data-guide="knowledge-created-notice" role="status">
          <div className="knowledge-created-guide-copy">
            <strong>{t("guide.knowledgeCreated.title")}</strong>
            <p>{t("guide.knowledgeCreated.description")}</p>
          </div>
          <div className="knowledge-created-guide-actions">
            <button
              type="button"
              className="primary compact"
              onClick={() => {
                dismissKnowledgeCreationGuideNotice();
                guide.startGuide("knowledge-settings");
              }}
            >
              {t("guide.knowledgeCreated.start")}
            </button>
            <button type="button" className="ghost compact" onClick={dismissKnowledgeCreationGuideNotice}>
              {t("guide.knowledgeCreated.later")}
            </button>
          </div>
          <label className="check-row knowledge-created-guide-preference">
            <input
              type="checkbox"
              checked={dontShowCreationGuideAgain}
              onChange={(event) => setDontShowCreationGuideAgain(event.target.checked)}
            />
            {t("guide.knowledgeCreated.dontShowAgain")}
          </label>
        </div>
      ) : null}

      {requiresInterviewConfiguration ? (
        <div className="settings-setup-notice">
          <strong>{t("settings.setupNotice")}</strong>
        </div>
      ) : null}

      <details className="settings-section knowledge-info-section" data-guide="knowledge-details" open>
        <summary className="knowledge-info-summary">
          <span className="knowledge-info-summary-copy">
            <span className="knowledge-info-summary-label">{t("settings.knowledgeInfo")}</span>
            <strong>{props.settingsName.trim() || t("common.notSet")}</strong>
          </span>
          <span className="knowledge-info-summary-meta">
            {t("settings.knowledgeInfoDetails")}
            <span className="knowledge-info-summary-chevron" aria-hidden="true" />
          </span>
        </summary>
        <div className="knowledge-info-content">
          <div className="knowledge-info-primary">
            <label>{t("common.name")}<input value={props.settingsName} onChange={(event) => { clearSettingsNotice(); props.setSettingsName(event.target.value); }} /></label>
            <div className="knowledge-tags-field">
              <strong className="knowledge-info-field-label">{t("settings.tags.title")}</strong>
              <TagEditor
                tags={props.settingsTags}
                suggestions={existingTags}
                onChange={(tags) => { clearSettingsNotice(); props.setSettingsTags(tags); }}
                ariaLabel={t("settings.tags.inputAria")}
                placeholder={t("settings.tags.placeholder")}
                addLabel={t("settings.tags.add")}
                removeLabel={(tag) => t("settings.tags.remove", { tag })}
                suggestionsLabel={t("settings.tags.existing")}
                selectSuggestionLabel={(tag) => t("settings.tags.select", { tag })}
              />
            </div>
          </div>
          <label className="knowledge-info-description">{t("common.description")}<textarea value={props.settingsDescription} onChange={(event) => { clearSettingsNotice(); props.setSettingsDescription(event.target.value); }} /></label>
        </div>
      </details>

      <div className="settings-tabs-row" data-guide="settings-tabs">
        <div className="settings-tabs" role="tablist" aria-label={t("settings.menuAria")}>
          <button id="settings-tab-fields" type="button" role="tab" data-guide="settings-tab-fields" aria-controls="settings-panel-fields" aria-selected={activeTab === "fields"} className={activeTab === "fields" ? "settings-tab active" : "settings-tab"} onClick={() => selectTab("fields")}>{t("settings.tabs.fields")}</button>
          <button id="settings-tab-execution" type="button" role="tab" data-guide="settings-tab-execution" aria-controls="settings-panel-execution" aria-selected={activeTab === "execution"} className={activeTab === "execution" ? "settings-tab active" : "settings-tab"} onClick={() => selectTab("execution")}>{t("settings.tabs.execution")}</button>
          <button id="settings-tab-knowledge" type="button" role="tab" data-guide="settings-tab-knowledge" aria-controls="settings-panel-knowledge" aria-selected={activeTab === "knowledge"} className={activeTab === "knowledge" ? "settings-tab active" : "settings-tab"} onClick={() => selectTab("knowledge")}>{t("settings.tabs.knowledge")}</button>
        </div>
      </div>

      <div className="settings-workspace">
        <div className="settings-main">
          {activeTab === "execution" ? (
            <section id="settings-panel-execution" className="settings-section" data-guide="interview-settings" role="tabpanel" aria-labelledby="settings-tab-execution">
              <div className="section-title-row">
                <div>
                  <h3>{t("settings.execution.title")}</h3>
                </div>
              </div>
              <div className="interview-settings">
                <div className="interview-setting-grid">
                  <section className="interview-setting-card">
                    <div className="interview-setting-card-header">
                      <div>
                        <h4>{t("settings.execution.purpose")}</h4>
                      </div>
                    </div>
                    <span className="sr-only">{t("settings.execution.purposeAria")}</span>
                    <OptionPicker
                      value={selectedInterviewProfile}
                      options={interviewProfileOptions.map((option) => ({
                        value: option.value,
                        label: t(option.labelKey),
                        description: t(option.descriptionKey),
                      }))}
                      onChange={(value) => {
                        clearSettingsNotice();
                        props.setSettingsInterviewPlan({
                          ...(props.settingsInterviewPlan ?? {}),
                          version: props.settingsInterviewPlan?.version ?? 1,
                          profile: value as typeof selectedInterviewProfile,
                        });
                      }}
                      ariaLabel={t("settings.execution.purposeAria")}
                    />
                    <p className="form-help">
                      {(() => { const option = interviewProfileOptions.find((item) => item.value === selectedInterviewProfile); return option ? t(option.descriptionKey) : ""; })()}
                    </p>
                  </section>
                  <section className="interview-setting-card featured">
                    <div className="interview-setting-card-header">
                      <div>
                        <h4>{t("settings.execution.model")}</h4>
                      </div>
                    </div>
                    <span className="sr-only">{t("settings.execution.modelAria")}</span>
                    <OptionPicker
                      value={selectedStructuredInterviewModel}
                      options={structuredInterviewModelOptions.map((option) => ({
                        value: option.value,
                        label: t(option.labelKey),
                        description: t(option.descriptionKey),
                      }))}
                      onChange={(value) => {
                        clearSettingsNotice();
                        props.setSettingsInterviewPlan({
                          ...(props.settingsInterviewPlan ?? {}),
                          version: props.settingsInterviewPlan?.version ?? 1,
                          modelId: value as NonNullable<typeof selectedStructuredInterviewModel>,
                        });
                      }}
                      ariaLabel={t("settings.execution.modelAria")}
                    />
                    <p className="form-help">
                      {(() => { const option = structuredInterviewModelOptions.find((item) => item.value === selectedStructuredInterviewModel); return option ? t(option.descriptionKey) : ""; })()}
                    </p>
                  </section>
                </div>
                <section className="interview-setting-group">
                  <div className="interview-setting-group-header">
                    <div>
                      <h4>{t("settings.execution.assistant")}</h4>
                    </div>
                  </div>
                  <div className="interview-setting-grid">
                    <section className="interview-setting-card compact">
                      <h5>{t("settings.execution.questionDesignModel")}</h5>
                      <span className="sr-only">{t("settings.execution.questionDesignModelAria")}</span>
                      <OptionPicker
                        value={selectedModelOption}
                        options={modelOptions.map((option) => ({ value: option.value, label: t(option.labelKey) }))}
                        onChange={(value) => { clearSettingsNotice(); props.setSettingsDefaultModelId(value); }}
                        ariaLabel={t("settings.execution.questionDesignModelAria")}
                      />
                    </section>
                    <section className="interview-setting-card compact">
                      <h5>{t("settings.execution.template")}</h5>
                      <div className="inline-form">
                        <OptionPicker
                          value={selectedPromptProfileId}
                          options={[
                            { value: noPromptProfileValue, label: t("settings.execution.unselected") },
                            ...promptProfiles.map((profile) => ({ value: profile.id, label: profile.name })),
                          ]}
                          onChange={setSelectedPromptProfileId}
                          ariaLabel={t("settings.execution.templateAria")}
                          searchable={promptProfiles.length > 6}
                          searchPlaceholder={t("settings.execution.templateAria")}
                          emptyLabel={t("settings.execution.unselected")}
                        />
                        <button
                          className="ghost compact"
                          type="button"
                          onClick={() => {
                            if (!selectedPromptProfile) return;
                            clearSettingsNotice();
                            props.setSettingsSystemPrompt(selectedPromptProfile.prompt);
                            setPromptProfileNotice(t("settings.messages.templateLoaded"));
                          }}
                          disabled={!selectedPromptProfile}
                        >
                          {t("settings.execution.load")}
                        </button>
                      </div>
                    </section>
                  </div>
                </section>
              </div>
              <div className="section-title-row compact-row">
                <div>
                  <h3>{t("settings.execution.additionalPrompt")}</h3>
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
                  {t("settings.execution.saveAsTemplate")}
                </button>
              </div>
              <label className="system-prompt-field">
                <textarea
                  value={props.settingsSystemPrompt}
                  onChange={(event) => { clearSettingsNotice(); props.setSettingsSystemPrompt(event.target.value); }}
                  placeholder={t("settings.execution.promptPlaceholder")}
                />
              </label>
              {promptProfileNotice ? <span className="notice">{promptProfileNotice}</span> : null}
            </section>
          ) : null}

          {activeTab === "fields" ? (
            <div id="settings-panel-fields" className="settings-split" data-guide="question-settings" role="tabpanel" aria-labelledby="settings-tab-fields">
              <section className="settings-section">
                <div className="section-title-row compact-row">
                  <div className="question-list-title">
                    <h3>{t("settings.fields.title")}</h3>
                    <span className="counter">{t("settings.fields.count", { count: formatNumber(props.draftFields.length, locale) })}</span>
                  </div>
                  <button className="ghost compact" type="button" data-guide="question-add" onClick={addField}>{t("settings.fields.add")}</button>
                </div>
                <div className="field-list" data-guide="knowledge-edit">
                  {props.draftFields.length === 0 ? <p className="empty">{t("settings.fields.empty")}</p> : props.draftFields.map((field, index) => {
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
                            <strong>{field.name.trim() || t("settings.fields.unnamed")}</strong>
                            <span>{summarizeFieldDetail(field, t)}</span>
                          </span>
                          {field.required ? <span className="field-required-badge">{t("common.required")}</span> : null}
                          <span className={isExpanded ? "field-chevron open" : "field-chevron"} aria-hidden="true" />
                        </button>
                        {isExpanded ? (
                          <div id={editorId} className="field-card-editor">
                            <label className="sr-only" htmlFor={`knowledge-field-name-${index}`}>{t("settings.fields.nameAria")}</label>
                            <input
                              id={`knowledge-field-name-${index}`}
                              value={field.name}
                              onChange={(event) => updateField(index, { name: event.target.value })}
                              placeholder={t("settings.fields.namePlaceholder")}
                            />
                            <label className="field-detail-field">{t("settings.fields.detail")}
                              <textarea value={field.description ?? ""} onChange={(event) => updateField(index, { description: event.target.value })} placeholder={t("settings.fields.detailPlaceholder")} />
                            </label>
                            <div className="toolbar field-card-editor-actions">
                              <label className="check-row"><input type="checkbox" checked={field.required} onChange={(event) => updateField(index, { required: event.target.checked })} />{t("settings.fields.required")}</label>
                              <button className="danger compact" type="button" onClick={() => deleteField(index)}>{t("settings.fields.delete")}</button>
                            </div>
                          </div>
                        ) : null}
                      </article>
                    );
                  })}
                </div>
              </section>

              <aside className="settings-side">
                <div className="settings-ai-chat" aria-label={t("settings.fields.assistantAria")}>
                  <div className="ai-chat-header">
                    <div>
                      <strong>{t("settings.fields.assistantTitle")}</strong>
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
                          <div className="proposal-field"><strong>{t("settings.fields.proposalField")}</strong><p>{field.name}</p></div>
                          <div className="proposal-field"><strong>{t("settings.fields.proposalDetail")}</strong><p>{field.description || t("settings.fields.noDetail")}</p></div>
                        </div>
                        <div className="actions">
                          <button className="primary compact" onClick={() => approveSuggestion(field.proposalId)}>{t("settings.fields.approve")}</button>
                          <button className="ghost compact" onClick={() => rejectSuggestion(field.proposalId)}>{t("settings.fields.reject")}</button>
                        </div>
                      </article>
                    ))}
                    {isGenerating ? <div className="bubble ai typing-bubble"><p>{t("common.loading")}</p></div> : null}
                  </div>
                  <div className="settings-chat-input">
                    <textarea ref={assistInputRef} value={assistInput} onChange={(event) => setAssistInput(event.target.value)} onKeyDown={handleAssistKeyDown} disabled={isGenerating} placeholder={t("settings.fields.inputPlaceholder")} />
                    <div className="toolbar">
                      <button className="ghost compact" onClick={approveAllSuggestions} disabled={pendingSuggestions.every((item) => item.status !== "pending")}>{t("settings.fields.approveAll")}</button>
                      <button className="primary compact" onClick={handleSendAssistMessage} disabled={!canSendAssistMessage}>{isGenerating ? t("settings.fields.generating") : t("settings.fields.send")}</button>
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
                  <h3>{t("settings.knowledge.title")}</h3>
                  <p className="form-help">{t("settings.knowledge.description")}</p>
                </div>
                <span className="status-pill muted">{t("settings.knowledge.count", { count: formatNumber(props.documents.length, locale) })}</span>
              </div>
              <KnowledgeDocumentsContent {...props} />
            </section>
          ) : null}

          <div className="actions sticky-actions">
            <button
              type="button"
              className="primary"
              data-guide="knowledge-confirm"
              onClick={() => props.onSaveSettings(getSettingsSaveTab(activeTab))}
              disabled={props.settingsSaveState === "saving"}
              aria-busy={props.settingsSaveState === "saving"}
            >
              {props.settingsSaveState === "saving" ? t("settings.saving") : t("settings.save")}
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
            aria-label={t("settings.template.saveAria")}
            onSubmit={(event) => {
              event.preventDefault();
              handleCreatePromptProfile().catch(() => undefined);
            }}
          >
            <div className="dialog-header">
              <div>
                <h2>{t("settings.template.title")}</h2>
                <p>{t("settings.template.description")}</p>
              </div>
            </div>
            <label className="field-group">
              <span>{t("settings.template.name")}</span>
              <input
                autoFocus
                value={savePromptTemplateName}
                onChange={(event) => setSavePromptTemplateName(event.target.value)}
                placeholder={t("settings.template.namePlaceholder")}
              />
            </label>
            <div className="dialog-actions">
              <button type="button" className="ghost" onClick={() => setIsSavePromptDialogOpen(false)}>{t("settings.template.cancel")}</button>
              <button type="submit" className="primary" disabled={!canSavePromptProfile}>{t("settings.template.save")}</button>
            </div>
          </form>
        </div>
      ) : null}
    </section>
  );
}
