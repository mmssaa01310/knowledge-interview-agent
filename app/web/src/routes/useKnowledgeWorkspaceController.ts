import { useEffect, useMemo, useRef, useState } from "react";
import type { InterviewLocale, InterviewRecord, Knowledge, KnowledgeDb, UserRole } from "@ai-interviewer/shared-types";
import { confirmApproveAll } from "../components/ui/ApproveAllDialog";
import {
  createKnowledge,
  createKnowledgeDb,
  createKnowledgeField,
  deleteKnowledge,
  deleteKnowledgeField,
  fetchKnowledgeDbs,
  fetchKnowledgeFields,
  fetchKnowledges,
  fetchMe,
  updateKnowledge,
  updateKnowledgeField,
  type KnowledgeField,
  type UserProfile
} from "../features/knowledge/api/knowledgeApi";
import {
  createDocument,
  fetchDocuments,
  type DocumentSummary
} from "../features/documents/api/documentApi";
import {
  ApiError,
  fetchPublishedGuidance,
} from "../lib/api";
import { useI18n, type Translate } from "../i18n";
import {
  approveAllProposals,
  approveProposal,
  bulkApproveRecords,
  createRecord,
  createRecordMessage,
  deleteRecord,
  editProcessModel,
  fetchInterviewState,
  fetchAccessibleRecords,
  fetchRecordInterviewContext,
  fetchProposals,
  fetchRecords,
  saveProcessModel,
  updateInterviewAnswer,
  updateRecord,
  type AiProposal
} from "../features/interviews/api/interviewApi";
import { useInterviewStream } from "../features/interviews/hooks/useInterviewStream";
import {
  DEFAULT_INTERVIEW_MODEL_ID,
  isInterviewConfigurationComplete,
} from "../features/interviews/interviewConfiguration";
import type { KnowledgeLayoutProps } from "../types/pageProps";
import type {
  ChatMessage,
  DocumentReadState,
  InterviewAnswerTarget,
  InterviewState,
  InterviewStreamMetadata,
  ProcessModelState,
} from "../types/app";
import type { GuidanceDraft } from "../types/dashboard";
import { getRouteKnowledgeDbId, getRouteKnowledgeId } from "./routeUtils";
import type { Route } from "./routeTypes";

function inferDocumentContentType(fileName: string) {
  const normalized = fileName.toLowerCase();

  if (normalized.endsWith(".pdf")) return "application/pdf";
  if (normalized.endsWith(".doc") || normalized.endsWith(".docx")) {
    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  }
  if (normalized.endsWith(".xls") || normalized.endsWith(".xlsx")) {
    return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  }
  if (normalized.endsWith(".ppt") || normalized.endsWith(".pptx")) {
    return "application/vnd.openxmlformats-officedocument.presentationml.presentation";
  }
  if (normalized.endsWith(".md")) return "text/markdown";
  return "text/plain";
}

type UseKnowledgeWorkspaceControllerArgs = {
  route: Route;
  navigate: (path: string) => void;
};

function createInterviewClientMessageId() {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `client-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function isSameInterviewAnswerTarget(
  left: InterviewAnswerTarget | null | undefined,
  right: InterviewAnswerTarget | null | undefined,
) {
  if (!left || !right) return left === right;
  return left.questionId === right.questionId
    && (left.fieldId ?? null) === (right.fieldId ?? null)
    && (left.targetType ?? null) === (right.targetType ?? null)
    && (left.targetId ?? null) === (right.targetId ?? null);
}

const LAST_KNOWLEDGE_ID_STORAGE_KEY = "ai-interviewer.last-knowledge-id";

function getLastKnowledgeId() {
  try {
    return window.localStorage.getItem(LAST_KNOWLEDGE_ID_STORAGE_KEY);
  } catch {
    return null;
  }
}

function rememberKnowledge(knowledgeId: string) {
  try {
    window.localStorage.setItem(LAST_KNOWLEDGE_ID_STORAGE_KEY, knowledgeId);
  } catch {
    // localStorageが利用できない環境でもインタビュー操作は継続する。
  }
}

function createAccessibleKnowledgeDb(knowledge: Knowledge, knowledgeCount: number, t: Translate): KnowledgeDb {
  return {
    id: knowledge.knowledgeDbId,
    tenantId: knowledge.tenantId,
    createdByUserId: knowledge.createdByUserId,
    updatedByUserId: knowledge.updatedByUserId,
    createdAt: knowledge.createdAt,
    updatedAt: knowledge.updatedAt,
    name: t("navigation.knowledgeArea"),
    language: knowledge.language,
    status: "active",
    knowledgeCount,
  };
}

function getSettingsSaveErrorMessage(error: unknown, tabLabel: string, t: Translate) {
  if (error instanceof ApiError && error.status === 409) {
    if (error.detail === "interview_profile_change_not_allowed_after_start") {
      return t("errors.settingsLocked", { tab: tabLabel });
    }
  }

  return t("errors.settingsSave", { tab: tabLabel });
}

export function useKnowledgeWorkspaceController(args: UseKnowledgeWorkspaceControllerArgs) {
  const { t, locale } = useI18n();

  function buildDefaultRecordTitle() {
    const base = selectedKnowledge?.targetEquipment || selectedKnowledge?.name || t("navigation.newKnowledge");
    const timestamp = new Date().toLocaleString(locale, {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit"
    });
    return `${base} ${t("navigation.interview")} ${timestamp}`;
  }

  const [user, setUser] = useState<UserProfile | null>(null);
  const [knowledgeDbs, setKnowledgeDbs] = useState<KnowledgeDb[]>([]);
  const [knowledges, setKnowledges] = useState<Knowledge[]>([]);
  const [records, setRecords] = useState<InterviewRecord[]>([]);
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [fields, setFields] = useState<KnowledgeField[]>([]);
  const [proposals, setProposals] = useState<AiProposal[]>([]);
  const [publishedGuidance, setPublishedGuidance] = useState<GuidanceDraft[]>([]);
  const [selectedRecordIds, setSelectedRecordIds] = useState<string[]>([]);
  const [newRecordTitle, setNewRecordTitle] = useState("");
  const [newDocumentName, setNewDocumentName] = useState("");
  const [settingsName, setSettingsName] = useState("");
  const [settingsDescription, setSettingsDescription] = useState("");
  const [settingsSystemPrompt, setSettingsSystemPrompt] = useState("");
  const [settingsCategory, setSettingsCategory] = useState("");
  const [settingsTargetBusiness, setSettingsTargetBusiness] = useState("");
  const [settingsTargetEquipment, setSettingsTargetEquipment] = useState("");
  const [settingsTags, setSettingsTags] = useState<string[]>([]);
  const [settingsLanguage, setSettingsLanguage] = useState<KnowledgeDb["language"]>("ja");
  const [settingsDefaultModelId, setSettingsDefaultModelId] = useState("");
  const [settingsInterviewPlan, setSettingsInterviewPlan] = useState<Knowledge["interviewPlan"]>(undefined);
  const [draftFields, setDraftFields] = useState<KnowledgeField[]>([]);
  const [settingsNotice, setSettingsNotice] = useState("");
  const [settingsSaveState, setSettingsSaveState] = useState<"idle" | "saving" | "success" | "error">("idle");
  const [isPreparingKnowledgeCreation, setIsPreparingKnowledgeCreation] = useState(false);
  const [knowledgeCreationError, setKnowledgeCreationError] = useState("");
  const [newlyCreatedKnowledgeId, setNewlyCreatedKnowledgeId] = useState<string | null>(null);
  const [chatInput, setChatInput] = useState("");
  const [interviewMessages, setInterviewMessages] = useState<ChatMessage[]>([]);
  const [interviewState, setInterviewState] = useState<InterviewState | null>(null);
  const [interviewStreamMetadata, setInterviewStreamMetadata] = useState<InterviewStreamMetadata | null>(null);
  const [streamingInterviewReply, setStreamingInterviewReply] = useState("");
  const [isInterviewStreaming, setIsInterviewStreaming] = useState(false);
  const [interviewError, setInterviewError] = useState(false);
  const [structuredDraft, setStructuredDraft] = useState<Record<string, string>>({});
  const [interviewAnswerOverrides, setInterviewAnswerOverrides] = useState<Record<string, string>>({});
  const [deletedExtraQuestionIds, setDeletedExtraQuestionIds] = useState<string[]>([]);
  const [recordNotice, setRecordNotice] = useState("");
  const [documentReadStates, setDocumentReadStates] = useState<Record<string, DocumentReadState>>({});
  const pendingInterviewSubmissionRef = useRef<{
    content: string;
    target: InterviewAnswerTarget | null;
    clientMessageId: string;
  } | null>(null);

  const routeKnowledgeDbId = getRouteKnowledgeDbId(args.route);
  const routeKnowledgeId = getRouteKnowledgeId(args.route);
  const selectedKnowledgeDb = routeKnowledgeDbId
    ? knowledgeDbs.find((db) => db.id === routeKnowledgeDbId) ?? null
    : null;
  const selectedKnowledge = routeKnowledgeId
    ? knowledges.find((knowledge) => knowledge.id === routeKnowledgeId) ?? null
    : null;
  const selectedRecordId = "recordId" in args.route ? args.route.recordId : undefined;
  const selectedRecord = selectedRecordId ? records.find((record) => record.id === selectedRecordId) ?? null : null;

  function markRecordAsSubmitted(recordId: string) {
    setRecords((currentRecords) => currentRecords.map((record) => (
      record.id === recordId && record.status === "in_progress"
        ? { ...record, status: "submitted" }
        : record
    )));
  }

  const sortedFields = useMemo(
    () => [...fields].sort((a, b) => a.displayOrder - b.displayOrder),
    [fields]
  );

  async function refreshSelectedRecord(recordId: string) {
    if (user && !["admin", "knowledge_manager"].includes(user.role)) {
      setProposals([]);
      return;
    }
    setProposals(await fetchProposals(recordId));
  }

  async function refreshPublishedGuidance(recordId: string) {
    if (user?.role !== "interviewer") {
      setPublishedGuidance([]);
      return;
    }
    try {
      setPublishedGuidance(await fetchPublishedGuidance(recordId));
    } catch {
      // 学習案内の取得失敗で、インタビュー記録の表示を止めない。
      setPublishedGuidance([]);
    }
  }

  async function loadInterviewSnapshot(recordId: string) {
    const snapshot = await fetchInterviewState(recordId);
    setInterviewState(snapshot.interviewState);
    if (snapshot.interviewState.status === "completed") {
      markRecordAsSubmitted(recordId);
    }
    const snapshotMessages = snapshot.messages
      .filter((message) => message.isActualUtterance !== false)
      .map((message) => ({
        id: message.id,
        recordId,
        role: normalizeInterviewMessageRole(message.role),
        text: message.content,
        questionId: message.questionId,
        questionType: message.questionType,
        fieldId: message.fieldId,
        answerToQuestionId: message.answerToQuestionId,
        answerToFieldId: message.answerToFieldId,
        targetType: message.targetType,
        targetId: message.targetId,
        turnType: message.turnType,
        voiceSessionId: message.voiceSessionId,
        voiceTurnId: message.voiceTurnId,
        voiceResponseId: message.voiceResponseId,
        candidateSource: message.candidateSource,
        isActualUtterance: message.isActualUtterance,
        isLegacy: !message.questionId && !message.answerToQuestionId && !message.turnType,
      }));
    setInterviewMessages((messages) => mergeVoiceMessages(messages, snapshotMessages));
    setStructuredDraft(snapshot.structuredDraft ?? {});
    setInterviewError(false);
    return snapshot.interviewState;
  }

  const interviewStream = useInterviewStream({
    onDelta: (chunk) => setStreamingInterviewReply((current) => `${current}${chunk}`),
    onStreamEnd: (metadata) => {
      setInterviewStreamMetadata(metadata);
      if (metadata?.error) {
        setInterviewError(true);
        const pending = pendingInterviewSubmissionRef.current;
        setInterviewMessages((messages) => mergeVoiceMessages(messages, [{
          id: `interview-error-${pending?.clientMessageId ?? Date.now()}`,
          recordId: selectedRecordId,
          role: "assistant",
          text: t("errors.interviewReplyFailed"),
        }]));
        setStreamingInterviewReply("");
        setIsInterviewStreaming(false);
        setRecordNotice(t("errors.interviewReplyFailed"));
        if (pending) {
          setChatInput(pending.content);
        }
        return;
      }
      if (metadata?.assistantMessage) {
        const assistantMessage = metadata.assistantMessage as ChatMessage & { content?: string };
        setInterviewMessages((messages) => mergeVoiceMessages(messages, [{
          id: assistantMessage.id,
          recordId: selectedRecordId,
          role: assistantMessage.role === "user" ? "user" : "assistant",
          text: assistantMessage.text ?? assistantMessage.content ?? metadata.reply,
          questionId: assistantMessage.questionId,
          questionType: assistantMessage.questionType,
          fieldId: assistantMessage.fieldId,
          answerToQuestionId: assistantMessage.answerToQuestionId,
          answerToFieldId: assistantMessage.answerToFieldId,
          targetType: assistantMessage.targetType,
          targetId: assistantMessage.targetId,
          candidateSource: assistantMessage.candidateSource,
        }]));
      }
      if (metadata?.interviewState) {
        setInterviewState(metadata.interviewState);
      }
      if (metadata?.status === "completed" || metadata?.interviewState?.status === "completed") {
        if (selectedRecordId) {
          markRecordAsSubmitted(selectedRecordId);
        }
      }
      if (metadata?.structuredDraft) {
        setStructuredDraft(metadata.structuredDraft);
      }
      setStreamingInterviewReply("");
      pendingInterviewSubmissionRef.current = null;
      setIsInterviewStreaming(false);
      setInterviewError(false);
    },
    onProposalCreated: () => {
      if ("recordId" in args.route) {
        refreshSelectedRecord(args.route.recordId).catch(() => undefined);
      }
    },
    onError: () => {
      const pending = pendingInterviewSubmissionRef.current;
      setStreamingInterviewReply("");
      setIsInterviewStreaming(false);
      setInterviewError(true);
      setRecordNotice(t("errors.interviewResponseReceiveFailed"));
      if (pending) {
        setChatInput(pending.content);
      }
    }
  });

  async function loadKnowledgeDbs() {
    const [profile, dbs] = await Promise.all([fetchMe(), fetchKnowledgeDbs()]);
    setUser(profile);
    setKnowledgeDbs(dbs);
    return dbs;
  }

  async function loadKnowledgeIndex(dbs: KnowledgeDb[]) {
    const groupedKnowledges = await Promise.all(dbs.map((db) => fetchKnowledges(db.id)));
    const nextKnowledges = groupedKnowledges.flat();
    setKnowledges(nextKnowledges);
    return nextKnowledges;
  }

  async function loadKnowledgeWorkspace(
    knowledgeDbId: string,
    knowledgeId?: string,
    knowledgeIndex?: Knowledge[],
    accessRole: UserRole | null = user?.role ?? null,
  ) {
    const nextKnowledges = knowledgeIndex
      ? knowledgeIndex.filter((knowledge) => knowledge.knowledgeDbId === knowledgeDbId)
      : await fetchKnowledges(knowledgeDbId);
    setKnowledges((current) => [
      ...current.filter((knowledge) => knowledge.knowledgeDbId !== knowledgeDbId),
      ...nextKnowledges,
    ]);

    const nextKnowledgeId = knowledgeId ?? nextKnowledges[0]?.id;
    if (!nextKnowledgeId) {
      setRecords([]);
      setDocuments([]);
      setFields([]);
      setDraftFields([]);
      setProposals([]);
      return nextKnowledges;
    }
    rememberKnowledge(nextKnowledgeId);

    const isInterviewer = accessRole === "interviewer";
    const [nextRecords, nextDocuments, nextFields] = await Promise.all([
      isInterviewer
        ? fetchAccessibleRecords().then((accessible) => accessible.filter((record) => record.knowledgeId === nextKnowledgeId))
        : fetchRecords(nextKnowledgeId),
      isInterviewer ? Promise.resolve([] as DocumentSummary[]) : fetchDocuments(nextKnowledgeId),
      fetchKnowledgeFields(nextKnowledgeId)
    ]);
    setRecords(nextRecords);
    setDocuments(nextDocuments.map((document) => ({
      ...document,
      knowledgeDbId: document.knowledgeDbId ?? knowledgeDbId
    })));
    setFields(nextFields);
    setDraftFields(nextFields);
    if (!isInterviewer && nextRecords[0]) {
      setProposals(await fetchProposals(nextRecords[0].id));
    } else {
      setProposals([]);
    }
    return nextKnowledges;
  }

  async function loadAccessibleKnowledgeWorkspace() {
    const accessibleRecords = await fetchAccessibleRecords();
    const contextEntries = await Promise.all(
      accessibleRecords.map(async (record) => {
        try {
          return {
            record,
            context: await fetchRecordInterviewContext(record.id),
          };
        } catch (error) {
          console.error(`Failed to load interview context for record ${record.id}`, error);
          return null;
        }
      }),
    );
    const validEntries = contextEntries.filter(
      (entry): entry is NonNullable<typeof entry> => entry !== null,
    );
    const recordsByKnowledge = new Map<string, InterviewRecord[]>();
    const knowledgeById = new Map<string, Knowledge>();

    validEntries.forEach(({ record, context }) => {
      const knowledgeId = context.knowledge.id;
      const currentRecords = recordsByKnowledge.get(knowledgeId) ?? [];
      recordsByKnowledge.set(knowledgeId, [...currentRecords, record]);
      if (!knowledgeById.has(knowledgeId)) {
        knowledgeById.set(knowledgeId, context.knowledge);
      }
    });

    const nextKnowledges = [...knowledgeById.values()].map((knowledge) => ({
      ...knowledge,
      recordCount: recordsByKnowledge.get(knowledge.id)?.length ?? 0,
    }));
    const recordsByDb = new Map<string, Knowledge[]>();
    nextKnowledges.forEach((knowledge) => {
      const currentKnowledges = recordsByDb.get(knowledge.knowledgeDbId) ?? [];
      recordsByDb.set(knowledge.knowledgeDbId, [...currentKnowledges, knowledge]);
    });
    const nextKnowledgeDbs = [...recordsByDb.values()].map((dbKnowledges) => (
      createAccessibleKnowledgeDb(dbKnowledges[0], dbKnowledges.length, t)
    ));

    setKnowledgeDbs(nextKnowledgeDbs);
    setKnowledges(nextKnowledges);
    if (nextKnowledges.length === 0) {
      setRecords([]);
      setDocuments([]);
      setFields([]);
      setDraftFields([]);
      setSelectedRecordIds([]);
      setProposals([]);
      return;
    }

    const rememberedKnowledgeId = getLastKnowledgeId();
    const selectedAccessibleKnowledge = nextKnowledges.find((knowledge) => knowledge.id === routeKnowledgeId)
      ?? nextKnowledges.find((knowledge) => knowledge.id === rememberedKnowledgeId)
      ?? nextKnowledges[0];
    const selectedAccessibleRecords = recordsByKnowledge.get(selectedAccessibleKnowledge.id) ?? [];
    const selectedContextEntry = validEntries.find(
      (entry) => entry.context.knowledge.id === selectedAccessibleKnowledge.id,
    );

    rememberKnowledge(selectedAccessibleKnowledge.id);
    setRecords(selectedAccessibleRecords);
    setDocuments([]);
    setFields(selectedContextEntry?.context.fields ?? []);
    setDraftFields(selectedContextEntry?.context.fields ?? []);
    setSelectedRecordIds([]);
    setProposals([]);

    const knowledgeBasePath = `/knowledge-dbs/${selectedAccessibleKnowledge.knowledgeDbId}/knowledges/${selectedAccessibleKnowledge.id}`;
    if (args.route.name === "knowledge-settings" || args.route.name === "knowledge-documents" || args.route.name === "knowledge-new") {
      args.navigate(`${knowledgeBasePath}/interview`);
    } else if (!routeKnowledgeId && args.route.name !== "knowledge-db" && args.route.name !== "knowledge-dbs") {
      args.navigate(`${knowledgeBasePath}/interview`);
    } else if (
      routeKnowledgeId !== selectedAccessibleKnowledge.id
      && args.route.name !== "knowledge-db"
      && args.route.name !== "knowledge-dbs"
    ) {
      args.navigate(`${knowledgeBasePath}/interview`);
    }
  }

  async function refresh() {
    if (args.route.name === "login" || args.route.name === "help") {
      return;
    }
    const profile = await fetchMe();
    setUser(profile);

    if (profile.role === "viewer") {
      await loadAccessibleKnowledgeWorkspace();
      return;
    }

    if (args.route.name === "dashboard" && !["admin", "knowledge_manager"].includes(profile.role)) {
      args.navigate("/knowledge-dbs");
      return;
    }

    if (args.route.name === "settings") {
      if (profile.role === "admin") return;
      args.navigate("/knowledge-dbs");
    }

    const dbs = await fetchKnowledgeDbs();
    setKnowledgeDbs(dbs);
    const knowledgeIndex = await loadKnowledgeIndex(dbs);
    if (args.route.name === "dashboard") {
      setRecords([]);
      setDocuments([]);
      setFields([]);
      setDraftFields([]);
      setProposals([]);
      return;
    }
    const routeKnowledgeDbExists = routeKnowledgeDbId
      ? dbs.some((db: KnowledgeDb) => db.id === routeKnowledgeDbId)
      : false;
    const lastKnowledgeId = !routeKnowledgeDbId ? getLastKnowledgeId() : null;
    const lastKnowledge = lastKnowledgeId
      ? knowledgeIndex.find((knowledge) => knowledge.id === lastKnowledgeId)
      : undefined;
    const nextKnowledgeDbId = routeKnowledgeDbExists
      ? routeKnowledgeDbId
      : lastKnowledge?.knowledgeDbId ?? dbs[0]?.id;

    if (routeKnowledgeDbId && !routeKnowledgeDbExists) {
      args.navigate(nextKnowledgeDbId ? `/knowledge-dbs/${nextKnowledgeDbId}` : "/knowledge");
    }

    if (nextKnowledgeDbId) {
      const routeKnowledgeForDb = routeKnowledgeId
        && knowledgeIndex.some((knowledge) => knowledge.id === routeKnowledgeId && knowledge.knowledgeDbId === nextKnowledgeDbId)
        ? routeKnowledgeId
        : undefined;
      const rememberedKnowledgeForDb = lastKnowledge?.knowledgeDbId === nextKnowledgeDbId
        ? lastKnowledge.id
        : undefined;
      const initialKnowledgeId = routeKnowledgeForDb
        ?? (args.route.name === "knowledge-dbs" ? rememberedKnowledgeForDb : undefined);
      const nextKnowledges = await loadKnowledgeWorkspace(nextKnowledgeDbId, initialKnowledgeId, knowledgeIndex, profile.role);
      const openedKnowledge = nextKnowledges.find((knowledge) => knowledge.id === initialKnowledgeId)
        ?? nextKnowledges[0];
      if (!openedKnowledge) {
        return;
      }
      if (
        args.route.name === "knowledge-db"
        || args.route.name === "knowledge-new"
        || args.route.name === "knowledge-dbs"
        || routeKnowledgeForDb
        || nextKnowledges.length === 0
      ) {
        return;
      }

      args.navigate(`/knowledge-dbs/${nextKnowledgeDbId}/knowledges/${openedKnowledge.id}/interview`);
    } else {
      setKnowledges([]);
      setRecords([]);
      setDocuments([]);
      setFields([]);
      setDraftFields([]);
      setProposals([]);
    }
  }

  async function openCreateKnowledge() {
    if (isPreparingKnowledgeCreation) return;

    setIsPreparingKnowledgeCreation(true);
    setKnowledgeCreationError("");
    try {
      let dbs = knowledgeDbs;
      if (dbs.length === 0) {
        dbs = await loadKnowledgeDbs();
      }
      if (dbs.length === 0) {
        const defaultDb = await createKnowledgeDb({
          name: t("navigation.knowledgeArea"),
          description: t("navigation.knowledgeAreaDescription"),
          category: "knowledge",
          language: "ja"
        });
        dbs = [defaultDb];
        setKnowledgeDbs(dbs);
      }
      args.navigate(`/knowledge-dbs/${dbs[0].id}/knowledges/new`);
    } catch (error) {
      console.error("Failed to prepare knowledge creation", error);
      setKnowledgeCreationError(t("errors.knowledgePrepareFailed"));
    } finally {
      setIsPreparingKnowledgeCreation(false);
    }
  }

  async function handleCreateKnowledge(payload: {
    name: string;
    description?: string;
    purpose?: string;
    tags?: string[];
  }, knowledgeDbId?: string) {
    const targetKnowledgeDb = knowledgeDbId
      ? knowledgeDbs.find((db) => db.id === knowledgeDbId) ?? null
      : selectedKnowledgeDb;
    if (!targetKnowledgeDb) {
      setKnowledgeCreationError(t("errors.knowledgeTargetMissing"));
      return;
    }

    setKnowledgeCreationError("");
    createKnowledge(targetKnowledgeDb.id, {
      name: payload.name,
      description: payload.description,
      purpose: payload.purpose,
      category: settingsCategory || undefined,
      targetBusiness: settingsTargetBusiness || undefined,
      targetEquipment: settingsTargetEquipment || undefined,
      tags: payload.tags,
      language: settingsLanguage,
      defaultModelId: settingsDefaultModelId || undefined
    }).then(async (knowledge) => {
      setNewlyCreatedKnowledgeId(knowledge.id);
      await loadKnowledgeWorkspace(targetKnowledgeDb.id, knowledge.id);
      args.navigate(`/knowledge-dbs/${targetKnowledgeDb.id}/knowledges/${knowledge.id}/settings`);
    }).catch((error) => {
      console.error("Failed to create knowledge", error);
      setKnowledgeCreationError(t("errors.knowledgeCreateFailed"));
    });
  }

  async function handleDeleteKnowledge(knowledgeId: string) {
    if (!selectedKnowledgeDb) return;
    if (!window.confirm(t("errors.knowledgeDeleteConfirm"))) return;
    await deleteKnowledge(knowledgeId);
    const nextKnowledges = await loadKnowledgeWorkspace(selectedKnowledgeDb.id);
    const nextKnowledgeId = nextKnowledges[0]?.id;
    args.navigate(nextKnowledgeId ? `/knowledge-dbs/${selectedKnowledgeDb.id}/knowledges/${nextKnowledgeId}/interview` : `/knowledge-dbs/${selectedKnowledgeDb.id}`);
  }

  async function handleSaveSettings(activeTab: "fields" | "execution") {
    const tabLabel = activeTab === "execution"
      ? t("settings.title")
      : t("settings.tabs.fields");
    if (!selectedKnowledgeDb || !selectedKnowledge || settingsSaveState === "saving") return;

    setSettingsSaveState("saving");
    setSettingsNotice(t("settings.messages.saving", { tab: tabLabel }));
    try {
      const interviewPlan = activeTab === "execution"
        ? {
            ...(settingsInterviewPlan ?? {}),
            version: settingsInterviewPlan?.version ?? 1,
            profile: settingsInterviewPlan?.profile ?? "fixed_form",
            modelId: settingsInterviewPlan?.modelId ?? DEFAULT_INTERVIEW_MODEL_ID,
          }
        : settingsInterviewPlan ?? null;
      await updateKnowledge(selectedKnowledge.id, {
        name: settingsName,
        description: settingsDescription,
        systemPrompt: settingsSystemPrompt.trim() || null,
        category: settingsCategory,
        targetBusiness: settingsTargetBusiness,
        targetEquipment: settingsTargetEquipment,
        tags: settingsTags,
        language: settingsLanguage,
        defaultModelId: settingsDefaultModelId,
        interviewPlan,
      });

      const existingIds = fields.map((field) => field.id).filter(Boolean);
      const draftIds = draftFields.map((field) => field.id).filter(Boolean);
      await Promise.all(
        existingIds
          .filter((fieldId) => fieldId && !draftIds.includes(fieldId))
          .map((fieldId) => deleteKnowledgeField(fieldId as string))
      );
      await Promise.all(
        draftFields.map((field, index) => {
          const payload = { ...field, displayOrder: index + 1 };
          return field.id
            ? updateKnowledgeField(field.id, payload)
            : createKnowledgeField(selectedKnowledge.id, payload);
        })
      );
      await loadKnowledgeDbs();
      await loadKnowledgeWorkspace(selectedKnowledgeDb.id, selectedKnowledge.id, undefined, user?.role);
      setSettingsSaveState("success");
      setSettingsNotice(t("settings.messages.saved", { tab: tabLabel }));
    } catch (error) {
      console.error("Failed to save knowledge settings", error);
      setSettingsSaveState("error");
      setSettingsNotice(getSettingsSaveErrorMessage(error, tabLabel, t));
    }
  }

  async function handleCreateDocument() {
    if (!selectedKnowledgeDb || !selectedKnowledge || !newDocumentName.trim()) return;
    await createDocument(selectedKnowledge.id, {
      fileName: newDocumentName.trim(),
      contentType: inferDocumentContentType(newDocumentName.trim())
    });
    setNewDocumentName("");
    await loadKnowledgeWorkspace(selectedKnowledgeDb.id, selectedKnowledge.id);
  }

  async function handleCreateRecord() {
    if (!selectedKnowledgeDb || !selectedKnowledge) return;
    if (!isInterviewConfigurationComplete(selectedKnowledge)) {
      setRecordNotice(t("errors.configurationRequired"));
      args.navigate(`/knowledge-dbs/${selectedKnowledgeDb.id}/knowledges/${selectedKnowledge.id}/settings`);
      return;
    }
    const title = newRecordTitle.trim() || buildDefaultRecordTitle();
    try {
      const record = await createRecord(selectedKnowledge.id, {
        title,
        targetEquipment: selectedKnowledge.targetEquipment,
        targetProcess: selectedKnowledge.targetBusiness
      });
      setNewRecordTitle("");
      await loadKnowledgeWorkspace(selectedKnowledgeDb.id, selectedKnowledge.id);
      args.navigate(`/knowledge-dbs/${selectedKnowledgeDb.id}/knowledges/${selectedKnowledge.id}/records/${record.id}`);
    } catch (error) {
      console.error("Failed to create interview record", error);
      setRecordNotice(
        error instanceof ApiError && error.detail === "interview_configuration_required"
          ? t("errors.configurationRequired")
          : t("errors.recordCreate"),
      );
    }
  }

  async function handleDeleteRecord(recordId: string) {
    if (!selectedKnowledgeDb || !selectedKnowledge) return;
    if (!window.confirm(t("errors.recordDeleteConfirm"))) return;
    try {
      await deleteRecord(recordId);
      await loadKnowledgeWorkspace(selectedKnowledgeDb.id, selectedKnowledge.id);
      setRecordNotice(t("errors.recordDeleted"));
    } catch (error) {
      console.error("Failed to delete interview record", error);
      setRecordNotice(t("errors.recordDeleteFailed"));
    }
  }

  async function handleChangeRecordStatus(
    status: InterviewRecord["status"],
    reviewNote?: string,
  ) {
    if (!selectedRecord) return;
    try {
      await updateRecord(selectedRecord.id, { status, reviewNote });
      if (selectedKnowledgeDb && selectedKnowledge) {
        await loadKnowledgeWorkspace(selectedKnowledgeDb.id, selectedKnowledge.id);
      }
      setRecordNotice(
        status === "submitted"
          ? t("errors.recordStatusSubmitted")
          : status === "approved"
            ? t("errors.recordStatusApproved")
            : status === "returned"
              ? t("errors.recordStatusReturned")
              : t("errors.recordStatusUpdated"),
      );
    } catch (error) {
      console.error("Failed to change record status", error);
      setRecordNotice(t("errors.recordStatusFailed"));
    }
  }

  async function handleChangeRecordStatusForRecord(
    recordId: string,
    status: InterviewRecord["status"],
    reviewNote?: string,
  ) {
    if (!selectedKnowledgeDb || !selectedKnowledge) return;
    try {
      await updateRecord(recordId, { status, reviewNote });
      await loadKnowledgeWorkspace(selectedKnowledgeDb.id, selectedKnowledge.id);
      setRecordNotice(
        status === "approved"
          ? t("errors.recordStatusApproved")
          : status === "returned"
            ? t("errors.recordStatusReturned")
            : t("errors.recordStatusUpdated"),
      );
    } catch (error) {
      console.error("Failed to change record status", error);
      setRecordNotice(t("errors.recordStatusFailed"));
    }
  }

  async function handleBulkApproveRecords() {
    if (!selectedRecordIds.length) return;
    confirmApproveAll({
      message: t("errors.bulkApproveConfirm", { count: selectedRecordIds.length }),
      onConfirm: () => {
        bulkApproveRecords(selectedRecordIds).then(async () => {
          setSelectedRecordIds([]);
          if (selectedKnowledgeDb && selectedKnowledge) {
            await loadKnowledgeWorkspace(selectedKnowledgeDb.id, selectedKnowledge.id);
          }
        });
      }
    });
  }

  function handleSaveInterviewDraft() {
    if (!selectedRecord?.id) return;
    setRecordNotice(t("errors.interviewAutosaved"));
  }

  async function handleSaveProcessModel(
    processState: ProcessModelState,
    baseProcessVersion: number,
    baseStateVersion: number,
  ) {
    if (!selectedRecord?.id) {
      throw new Error("record_not_selected");
    }
    const response = await saveProcessModel(selectedRecord.id, {
      processState,
      baseProcessVersion,
      baseStateVersion,
    });
    await loadInterviewSnapshot(selectedRecord.id);
    return response.interviewState;
  }

  async function handleEditProcessModel(
    instruction: string,
    baseProcessVersion: number,
    baseStateVersion: number,
  ) {
    if (!selectedRecord?.id) {
      throw new Error("record_not_selected");
    }
    const response = await editProcessModel(selectedRecord.id, {
      instruction,
      baseProcessVersion,
      baseStateVersion,
    });
    await loadInterviewSnapshot(selectedRecord.id);
    return {
      interviewState: response.interviewState,
      reply: response.reply,
    };
  }

  async function handleSaveInterviewAnswer(fieldId: string, recordAnswer: string) {
    if (!selectedRecord?.id) return;
    try {
      await updateInterviewAnswer(selectedRecord.id, fieldId, recordAnswer);
      await loadInterviewSnapshot(selectedRecord.id);
      setRecordNotice(t("errors.answerSaved"));
    } catch (error) {
      console.error("Failed to save interview answer", error);
      setRecordNotice(t("errors.answerSaveFailed"));
    }
  }

  function handleDeleteInterviewAnswers() {
    if (!selectedRecord?.id) return;
    if (!window.confirm(t("errors.answersDeleteConfirm"))) return;

    setStructuredDraft({});
    setInterviewAnswerOverrides({});
    setRecordNotice(t("errors.answersDeleted"));
  }

  function handleDeleteInterviewChat() {
    if (!selectedRecord?.id) return;
    if (!window.confirm(t("errors.chatDeleteConfirm"))) return;

    setChatInput("");
    setInterviewMessages([]);
    setInterviewState(null);
    setInterviewStreamMetadata(null);
    setStreamingInterviewReply("");
    setInterviewAnswerOverrides({});
    setDeletedExtraQuestionIds([]);
    pendingInterviewSubmissionRef.current = null;
    setIsInterviewStreaming(false);
    setInterviewError(false);
    setRecordNotice(t("errors.chatDeleted"));
  }

  async function handleSaveInterviewLocale(interviewLocale: InterviewLocale): Promise<boolean> {
    if (!selectedRecord || selectedRecord.interviewLocale === interviewLocale) {
      return Boolean(selectedRecord);
    }
    try {
      const updatedRecord = await updateRecord(selectedRecord.id, { interviewLocale });
      setRecords((currentRecords) => currentRecords.map((record) => (
        record.id === updatedRecord.id ? updatedRecord : record
      )));
      return true;
    } catch (error) {
      console.error("Failed to save interview locale", error);
      setRecordNotice(t("errors.interviewLanguageUpdateFailed"));
      return false;
    }
  }

  async function handleStartInterview(interviewLocale?: InterviewLocale) {
    if (!selectedRecord || isInterviewStreaming) return;

    let recordId = selectedRecord.id;
    if (interviewLocale && !(await handleSaveInterviewLocale(interviewLocale))) {
      return;
    }
    pendingInterviewSubmissionRef.current = null;
    setStreamingInterviewReply("");
    setInterviewStreamMetadata(null);
    setInterviewError(false);
    setIsInterviewStreaming(true);
    setRecordNotice("");
    interviewStream.start(recordId);
  }

  async function handleSendInterviewMessage(
    target?: InterviewAnswerTarget | null,
    contentOverride?: string,
    interviewLocale?: InterviewLocale,
  ) {
    const content = (contentOverride ?? chatInput).trim();
    if (!selectedRecord || !content || isInterviewStreaming) return;
    const interviewHasStarted = Boolean(
      interviewMessages.length
        || interviewState?.currentQuestionId
        || interviewState?.askedQuestions.length
        || interviewState?.lastProcessedUserMessageId,
    );
    if (interviewLocale && !interviewHasStarted && !(await handleSaveInterviewLocale(interviewLocale))) {
      return;
    }
    const previousPending = pendingInterviewSubmissionRef.current;
    const isRetry = previousPending?.content === content
      && (!target || isSameInterviewAnswerTarget(previousPending.target, target));
    const effectiveTarget = isRetry ? previousPending?.target ?? null : target ?? null;
    const clientMessageId = isRetry && previousPending
      ? previousPending.clientMessageId
      : createInterviewClientMessageId();
    pendingInterviewSubmissionRef.current = {
      content,
      target: effectiveTarget,
      clientMessageId,
    };
    setChatInput("");
    setStreamingInterviewReply("");
    setInterviewStreamMetadata(null);
    setInterviewError(false);
    setIsInterviewStreaming(true);
    try {
      const response = await createRecordMessage(selectedRecord.id, {
        content,
        clientMessageId,
        stateVersion: interviewState?.stateVersion ?? null,
        turnType: effectiveTarget ? "ANSWER" : "CONTROL",
        answerToQuestionId: effectiveTarget?.questionId ?? null,
        targetType: effectiveTarget?.targetType ?? null,
        targetId: effectiveTarget?.targetId ?? null,
      });
      const userMessage = response.recordMessage;
      setInterviewMessages((messages) => mergeVoiceMessages(messages, [{
        id: userMessage.id,
        recordId: selectedRecord.id,
        role: userMessage.role === "assistant" ? "assistant" : "user",
        text: userMessage.content,
        questionId: userMessage.questionId,
        questionType: userMessage.questionType,
        fieldId: userMessage.fieldId,
        answerToQuestionId: userMessage.answerToQuestionId,
        answerToFieldId: userMessage.answerToFieldId,
        targetType: userMessage.targetType,
        targetId: userMessage.targetId,
        turnType: userMessage.turnType,
        candidateSource: userMessage.candidateSource,
      }]));
      interviewStream.start(selectedRecord.id);
    } catch (error) {
      console.error("Failed to send interview message", error);
      setStreamingInterviewReply("");
      setIsInterviewStreaming(false);
      setInterviewError(true);
      setRecordNotice(t("errors.messageSendFailed"));
      setChatInput(content);
    }
  }

  function appendRealtimeVoiceInterviewMessage(message: ChatMessage) {
    const scopedMessage = {
      ...message,
      recordId: message.recordId ?? selectedRecord?.id,
    };
    setInterviewMessages((messages) => {
      const index = messages.findIndex((item) => isSameInterviewMessage(item, scopedMessage));
      if (index === -1) {
        return [...messages, scopedMessage];
      }
      const next = [...messages];
      next[index] = {
        ...next[index],
        ...scopedMessage,
      };
      return next;
    });
  }

  function refreshInterviewSnapshotForSelectedRecord() {
    if (!selectedRecord?.id) {
      return;
    }
    loadInterviewSnapshot(selectedRecord.id).catch(() => {
      setInterviewError(true);
      setRecordNotice(t("errors.interviewStateLoadFailed"));
    });
  }

  async function handleApproveOne(proposalId: string) {
    await approveProposal(proposalId);
    if (selectedRecord) await refreshSelectedRecord(selectedRecord.id);
  }

  async function handleApproveAllForRecord() {
    if (!selectedRecord) return;
    confirmApproveAll({
      message: t("errors.recordApproveAllConfirm"),
      onConfirm: () => {
        approveAllProposals(selectedRecord.id).then(() => refreshSelectedRecord(selectedRecord.id));
      }
    });
  }

  function handleUpdateDocumentReadState(documentId: string, nextState: DocumentReadState["readStatus"]) {
    const now = new Date().toISOString();
    setDocumentReadStates((current) => ({
      ...current,
      [documentId]: {
        readStatus: nextState,
        readProgress: nextState === "opened" ? 25 : nextState === "reading" ? 60 : 100,
        acknowledged: nextState === "acknowledged",
        lastOpenedAt: now,
        readAt: nextState === "read" || nextState === "acknowledged" ? now : current[documentId]?.readAt,
        acknowledgedAt: nextState === "acknowledged" ? now : current[documentId]?.acknowledgedAt
      }
    }));
  }

  function handleRejectProposal(proposalId: string) {
    setProposals((items) => items.map((proposal) => (
      proposal.id === proposalId ? { ...proposal, status: "rejected" } : proposal
    )));
  }

  function handleRemoveProposal(proposalId: string) {
    setProposals((items) => items.filter((proposal) => proposal.id !== proposalId));
  }

  useEffect(() => {
    refresh().catch(() => undefined);
  }, [args.route.name, routeKnowledgeDbId, routeKnowledgeId, selectedRecordId]);

  useEffect(() => {
    if (!selectedKnowledge) return;
    setSettingsName(selectedKnowledge.name);
    setSettingsDescription(selectedKnowledge.description ?? "");
    setSettingsSystemPrompt(selectedKnowledge.systemPrompt ?? "");
    setSettingsCategory(selectedKnowledge.category ?? "");
    setSettingsTargetBusiness(selectedKnowledge.targetBusiness ?? "");
    setSettingsTargetEquipment(selectedKnowledge.targetEquipment ?? "");
    setSettingsTags(selectedKnowledge.tags ?? []);
    setSettingsLanguage(selectedKnowledge.language);
    setSettingsDefaultModelId(selectedKnowledge.defaultModelId ?? "");
    setSettingsInterviewPlan(selectedKnowledge.interviewPlan ?? undefined);
    setSettingsNotice("");
    setSettingsSaveState("idle");
  }, [selectedKnowledge?.id]);

  useEffect(() => {
    if (!settingsNotice || settingsSaveState !== "success") return;
    const timeoutId = window.setTimeout(() => {
      setSettingsNotice("");
      setSettingsSaveState("idle");
    }, 3000);
    return () => window.clearTimeout(timeoutId);
  }, [settingsNotice, settingsSaveState]);

  useEffect(() => {
    setChatInput("");
    setIsInterviewStreaming(false);
    setStreamingInterviewReply("");
    setInterviewError(false);
    if (!selectedRecord?.id) {
      setPublishedGuidance([]);
      setInterviewState(null);
      setStructuredDraft({});
      setInterviewAnswerOverrides({});
      setDeletedExtraQuestionIds([]);
      setInterviewMessages([]);
      setInterviewStreamMetadata(null);
      pendingInterviewSubmissionRef.current = null;
      return;
    }

    setInterviewState(null);
    setStructuredDraft({});
    setInterviewAnswerOverrides({});
    setDeletedExtraQuestionIds([]);
    setInterviewMessages([]);
    setInterviewStreamMetadata(null);
    pendingInterviewSubmissionRef.current = null;
    setPublishedGuidance([]);
    loadInterviewSnapshot(selectedRecord.id).catch(() => {
      setRecordNotice(t("errors.interviewStateLoadFailed"));
    });
    void refreshPublishedGuidance(selectedRecord.id);
  }, [selectedKnowledge?.id, selectedRecord?.id, user?.role]);

  useEffect(() => {
    setDocumentReadStates((current) => ({
      ...Object.fromEntries(documents.map((document) => [document.id, current[document.id] ?? {
        readStatus: "unread",
        readProgress: 0,
        acknowledged: false
      }])),
      ...current
    }));
  }, [documents]);

  useEffect(() => {
    if ("recordId" in args.route && user && ["admin", "knowledge_manager"].includes(user.role)) {
      refreshSelectedRecord(args.route.recordId).catch(() => undefined);
    }
  }, ["recordId" in args.route ? args.route.recordId : "", user?.role]);

  const knowledgeLayoutProps: KnowledgeLayoutProps = {
    route: args.route,
    user,
    knowledgeDbs,
    knowledges,
    selectedKnowledgeDb,
    selectedKnowledge,
    records,
    documents,
    sortedFields,
    draftFields,
    setDraftFields,
    settingsName,
    setSettingsName,
    settingsDescription,
    setSettingsDescription,
    settingsSystemPrompt,
    setSettingsSystemPrompt,
    settingsCategory,
    setSettingsCategory,
    settingsTargetBusiness,
    setSettingsTargetBusiness,
    settingsTargetEquipment,
    setSettingsTargetEquipment,
    settingsTags,
    setSettingsTags,
    settingsLanguage,
    setSettingsLanguage,
    settingsDefaultModelId,
    setSettingsDefaultModelId,
    settingsInterviewPlan,
    setSettingsInterviewPlan,
    settingsNotice,
    settingsSaveState,
    newRecordTitle,
    setNewRecordTitle,
    newDocumentName,
    setNewDocumentName,
    selectedRecordIds,
    setSelectedRecordIds,
    documentReadStates,
    onUpdateDocumentReadState: handleUpdateDocumentReadState,
    selectedRecord,
    publishedGuidance,
    proposals,
    chatInput,
    setChatInput,
    interviewMessages,
    interviewState,
    interviewStreamMetadata,
    streamingInterviewReply,
    isInterviewStreaming,
    interviewError,
    structuredDraft,
    setStructuredDraft,
    interviewAnswerOverrides,
    setInterviewAnswerOverrides,
    deletedExtraQuestionIds,
    setDeletedExtraQuestionIds,
    recordNotice,
    setRecordNotice,
    navigate: args.navigate,
    onOpenCreateKnowledge: openCreateKnowledge,
    isPreparingKnowledgeCreation,
    knowledgeCreationError,
    knowledgeCreationNotice: newlyCreatedKnowledgeId === selectedKnowledge?.id,
    onDismissKnowledgeCreationNotice: () => setNewlyCreatedKnowledgeId(null),
    onCreateKnowledge: handleCreateKnowledge,
    onDeleteKnowledge: handleDeleteKnowledge,
    onSaveSettings: handleSaveSettings,
    onClearSettingsNotice: () => {
      setSettingsNotice("");
      setSettingsSaveState("idle");
    },
    onCreateDocument: handleCreateDocument,
    onCreateRecord: handleCreateRecord,
    onDeleteRecord: handleDeleteRecord,
    onChangeRecordStatus: handleChangeRecordStatus,
    onChangeRecordStatusForRecord: handleChangeRecordStatusForRecord,
    onBulkApproveRecords: handleBulkApproveRecords,
    onSaveInterviewDraft: handleSaveInterviewDraft,
    onSaveInterviewAnswer: handleSaveInterviewAnswer,
    onDeleteInterviewAnswers: handleDeleteInterviewAnswers,
    onDeleteInterviewChat: handleDeleteInterviewChat,
    onSaveInterviewLocale: handleSaveInterviewLocale,
    onStartInterview: handleStartInterview,
    onSendInterviewMessage: handleSendInterviewMessage,
    onAppendInterviewMessage: appendRealtimeVoiceInterviewMessage,
    onRefreshInterviewSnapshot: refreshInterviewSnapshotForSelectedRecord,
    onSaveProcessModel: handleSaveProcessModel,
    onEditProcessModel: handleEditProcessModel,
    onApproveOne: handleApproveOne,
    onRejectProposal: handleRejectProposal,
    onRemoveProposal: handleRemoveProposal,
    onApproveAllForRecord: handleApproveAllForRecord
  };

  return {
    user,
    knowledgeDbs,
    knowledges,
    documents,
    selectedKnowledgeDb,
    knowledgeLayoutProps
  };
}

function isSameInterviewMessage(current: ChatMessage, incoming: ChatMessage) {
  const currentRecordId = current.recordId ?? null;
  const incomingRecordId = incoming.recordId ?? null;
  if (currentRecordId !== incomingRecordId) {
    return false;
  }
  if (normalizeInterviewMessageRole(current.role) !== normalizeInterviewMessageRole(incoming.role)) {
    return false;
  }
  if (
    normalizeInterviewMessageRole(current.role) === "assistant"
    && normalizeInterviewMessageRole(incoming.role) === "assistant"
    && current.voiceResponseId
    && incoming.voiceResponseId
  ) {
    return current.voiceResponseId === incoming.voiceResponseId
      && (current.voiceSessionId ?? incoming.voiceSessionId ?? null) === (incoming.voiceSessionId ?? current.voiceSessionId ?? null);
  }
  if (
    normalizeInterviewMessageRole(current.role) === "user"
    && normalizeInterviewMessageRole(incoming.role) === "user"
  ) {
    const sameVoiceSession =
      (current.voiceSessionId ?? incoming.voiceSessionId ?? null) ===
      (incoming.voiceSessionId ?? current.voiceSessionId ?? null);
    if (current.voiceTurnId && incoming.voiceTurnId) {
      return current.voiceTurnId === incoming.voiceTurnId && sameVoiceSession;
    }
    return Boolean(
      sameVoiceSession
      && current.text === incoming.text
      && (current.answerToQuestionId ?? current.questionId ?? null) ===
        (incoming.answerToQuestionId ?? incoming.questionId ?? null),
    );
  }
  return Boolean(current.id && incoming.id && current.id === incoming.id);
}

function normalizeInterviewMessageRole(role: ChatMessage["role"]): "user" | "assistant" {
  return role === "user" ? "user" : "assistant";
}

function mergeVoiceMessages(current: ChatMessage[], incoming: ChatMessage[]) {
  const merged: ChatMessage[] = [];
  for (const message of [...current, ...incoming]) {
    const index = merged.findIndex((item) => isSameInterviewMessage(item, message));
    if (index === -1) {
      merged.push(message);
      continue;
    }
    merged[index] = {
      ...merged[index],
      ...message,
    };
  }
  return merged;
}
