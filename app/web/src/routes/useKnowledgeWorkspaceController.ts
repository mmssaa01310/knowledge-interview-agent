import { useEffect, useMemo, useRef, useState } from "react";
import type { InterviewRecord, Knowledge, KnowledgeDb } from "@ai-interviewer/shared-types";
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
import { ApiError } from "../lib/api";
import {
  approveAllProposals,
  approveProposal,
  bulkApproveRecords,
  createRecord,
  createRecordMessage,
  deleteRecord,
  fetchInterviewState,
  fetchAccessibleRecords,
  fetchRecordInterviewContext,
  fetchProposals,
  fetchRecords,
  updateInterviewAnswer,
  updateRecord,
  type AiProposal
} from "../features/interviews/api/interviewApi";
import { useInterviewStream } from "../features/interviews/hooks/useInterviewStream";
import { isInterviewConfigurationComplete } from "../features/interviews/interviewConfiguration";
import type { KnowledgeLayoutProps } from "../types/pageProps";
import type { ChatMessage, DocumentReadState, InterviewAnswerTarget, InterviewState, InterviewStreamMetadata } from "../types/app";
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

const INTERVIEW_ERROR_REPLY = "一時的にAI応答を生成できませんでした。少し時間をおいて再度送信してください。";
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

function createAccessibleKnowledgeDb(knowledge: Knowledge, knowledgeCount: number): KnowledgeDb {
  return {
    id: knowledge.knowledgeDbId,
    tenantId: knowledge.tenantId,
    createdByUserId: knowledge.createdByUserId,
    updatedByUserId: knowledge.updatedByUserId,
    createdAt: knowledge.createdAt,
    updatedAt: knowledge.updatedAt,
    name: "ナレッジ領域",
    language: knowledge.language,
    status: "active",
    knowledgeCount,
  };
}

function getSettingsSaveErrorMessage(error: unknown, tabLabel: string) {
  if (error instanceof ApiError && error.status === 409) {
    if (error.detail === "interview_profile_change_not_allowed_after_start") {
      return `${tabLabel}を保存できませんでした。既に開始済みのインタビューがあるため、インタビュー用途は変更できません。用途を元に戻すか、新しいナレッジで設定してください。`;
    }
  }

  return `${tabLabel}を保存できませんでした。通信状態を確認して、もう一度お試しください。`;
}

export function useKnowledgeWorkspaceController(args: UseKnowledgeWorkspaceControllerArgs) {
  function buildDefaultRecordTitle() {
    const base = selectedKnowledge?.targetEquipment || selectedKnowledge?.name || "新規インタビュー記録";
    const timestamp = new Date().toLocaleString("ja-JP", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit"
    });
    return `${base} インタビュー ${timestamp}`;
  }

  const [user, setUser] = useState<UserProfile | null>(null);
  const [knowledgeDbs, setKnowledgeDbs] = useState<KnowledgeDb[]>([]);
  const [knowledges, setKnowledges] = useState<Knowledge[]>([]);
  const [records, setRecords] = useState<InterviewRecord[]>([]);
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [fields, setFields] = useState<KnowledgeField[]>([]);
  const [proposals, setProposals] = useState<AiProposal[]>([]);
  const [selectedRecordIds, setSelectedRecordIds] = useState<string[]>([]);
  const [newRecordTitle, setNewRecordTitle] = useState("");
  const [newDocumentName, setNewDocumentName] = useState("");
  const [settingsName, setSettingsName] = useState("");
  const [settingsDescription, setSettingsDescription] = useState("");
  const [settingsSystemPrompt, setSettingsSystemPrompt] = useState("");
  const [settingsCategory, setSettingsCategory] = useState("");
  const [settingsTargetBusiness, setSettingsTargetBusiness] = useState("");
  const [settingsTargetEquipment, setSettingsTargetEquipment] = useState("");
  const [settingsLanguage, setSettingsLanguage] = useState<KnowledgeDb["language"]>("ja");
  const [settingsDefaultModelId, setSettingsDefaultModelId] = useState("");
  const [settingsInterviewPlan, setSettingsInterviewPlan] = useState<Knowledge["interviewPlan"]>(undefined);
  const [draftFields, setDraftFields] = useState<KnowledgeField[]>([]);
  const [settingsNotice, setSettingsNotice] = useState("");
  const [settingsSaveState, setSettingsSaveState] = useState<"idle" | "saving" | "success" | "error">("idle");
  const [isPreparingKnowledgeCreation, setIsPreparingKnowledgeCreation] = useState(false);
  const [knowledgeCreationError, setKnowledgeCreationError] = useState("");
  const [chatInput, setChatInput] = useState("");
  const [interviewMessages, setInterviewMessages] = useState<ChatMessage[]>([]);
  const [interviewState, setInterviewState] = useState<InterviewState | null>(null);
  const [interviewStreamMetadata, setInterviewStreamMetadata] = useState<InterviewStreamMetadata | null>(null);
  const [streamingInterviewReply, setStreamingInterviewReply] = useState("");
  const [isInterviewStreaming, setIsInterviewStreaming] = useState(false);
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
  }

  const interviewStream = useInterviewStream({
    onDelta: (chunk) => setStreamingInterviewReply((current) => `${current}${chunk}`),
    onStreamEnd: (metadata) => {
      setInterviewStreamMetadata(metadata);
      if (metadata?.error) {
        const pending = pendingInterviewSubmissionRef.current;
        setInterviewMessages((messages) => mergeVoiceMessages(messages, [{
          id: `interview-error-${pending?.clientMessageId ?? Date.now()}`,
          recordId: selectedRecordId,
          role: "assistant",
          text: INTERVIEW_ERROR_REPLY,
        }]));
        setStreamingInterviewReply("");
        setIsInterviewStreaming(false);
        setRecordNotice("回答処理に失敗しました。内容を確認して、もう一度送信してください。");
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
      setRecordNotice("AI応答の受信に失敗しました");
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

    const [nextRecords, nextDocuments, nextFields] = await Promise.all([
      fetchRecords(nextKnowledgeId),
      fetchDocuments(nextKnowledgeId),
      fetchKnowledgeFields(nextKnowledgeId)
    ]);
    setRecords(nextRecords);
    setDocuments(nextDocuments.map((document) => ({
      ...document,
      knowledgeDbId: document.knowledgeDbId ?? knowledgeDbId
    })));
    setFields(nextFields);
    setDraftFields(nextFields);
    if (nextRecords[0]) {
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
      createAccessibleKnowledgeDb(dbKnowledges[0], dbKnowledges.length)
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
    } else if (!routeKnowledgeId && args.route.name !== "knowledge-db") {
      args.navigate(`${knowledgeBasePath}/interview`);
    } else if (routeKnowledgeId !== selectedAccessibleKnowledge.id && args.route.name !== "knowledge-db") {
      args.navigate(`${knowledgeBasePath}/interview`);
    }
  }

  async function refresh() {
    const profile = await fetchMe();
    setUser(profile);
    if (args.route.name === "login") {
      return;
    }

    const isRecordOnlyUser = profile.role === "interviewer" || profile.role === "viewer";
    if (isRecordOnlyUser) {
      await loadAccessibleKnowledgeWorkspace();
      return;
    }

    if (args.route.name === "settings") {
      if (profile.role === "admin") return;
      args.navigate("/knowledge-dbs");
    }

    const dbs = await fetchKnowledgeDbs();
    setKnowledgeDbs(dbs);
    const knowledgeIndex = await loadKnowledgeIndex(dbs);
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
      const nextKnowledges = await loadKnowledgeWorkspace(nextKnowledgeDbId, initialKnowledgeId, knowledgeIndex);
      const openedKnowledge = nextKnowledges.find((knowledge) => knowledge.id === initialKnowledgeId)
        ?? nextKnowledges[0];
      if (!openedKnowledge) {
        return;
      }
      if (args.route.name === "knowledge-dbs") {
        args.navigate(`/knowledge-dbs/${nextKnowledgeDbId}/knowledges/${openedKnowledge.id}/interview`);
        return;
      }
      if (
        args.route.name === "knowledge-db"
        || args.route.name === "knowledge-new"
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
          name: "ナレッジ領域",
          description: "ナレッジを管理する内部領域",
          category: "knowledge",
          language: "ja"
        });
        dbs = [defaultDb];
        setKnowledgeDbs(dbs);
      }
      args.navigate(`/knowledge-dbs/${dbs[0].id}/knowledges/new`);
    } catch (error) {
      console.error("Failed to prepare knowledge creation", error);
      setKnowledgeCreationError("ナレッジ作成の準備に失敗しました。もう一度お試しください。");
    } finally {
      setIsPreparingKnowledgeCreation(false);
    }
  }

  async function handleCreateKnowledge(payload: {
    name: string;
    description?: string;
    purpose?: string;
  }, knowledgeDbId?: string) {
    const targetKnowledgeDb = knowledgeDbId
      ? knowledgeDbs.find((db) => db.id === knowledgeDbId) ?? null
      : selectedKnowledgeDb;
    if (!targetKnowledgeDb) {
      setKnowledgeCreationError("ナレッジの保存先を確認できませんでした。");
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
      language: settingsLanguage,
      defaultModelId: settingsDefaultModelId || undefined
    }).then(async (knowledge) => {
      await loadKnowledgeWorkspace(targetKnowledgeDb.id, knowledge.id);
      args.navigate(`/knowledge-dbs/${targetKnowledgeDb.id}/knowledges/${knowledge.id}/settings`);
    }).catch((error) => {
      console.error("Failed to create knowledge", error);
      setKnowledgeCreationError("ナレッジを作成できませんでした。もう一度お試しください。");
    });
  }

  async function handleDeleteKnowledge(knowledgeId: string) {
    if (!selectedKnowledgeDb) return;
    if (!window.confirm("このナレッジを削除します。関連する記録・文書の参照に注意してください。")) return;
    await deleteKnowledge(knowledgeId);
    const nextKnowledges = await loadKnowledgeWorkspace(selectedKnowledgeDb.id);
    const nextKnowledgeId = nextKnowledges[0]?.id;
    args.navigate(nextKnowledgeId ? `/knowledge-dbs/${selectedKnowledgeDb.id}/knowledges/${nextKnowledgeId}/interview` : `/knowledge-dbs/${selectedKnowledgeDb.id}`);
  }

  async function handleSaveSettings(activeTab: "fields" | "execution") {
    const tabLabel = activeTab === "execution"
      ? "インタビュー設定"
      : "質問項目";
    if (!selectedKnowledgeDb || !selectedKnowledge || settingsSaveState === "saving") return;

    setSettingsSaveState("saving");
    setSettingsNotice(`${tabLabel}を保存しています…`);
    try {
      const interviewPlan = activeTab === "execution"
        ? settingsInterviewPlan ?? {
            version: 1,
            profile: "fixed_form" as const,
            modelId: "global.openai.gpt-5.6-luna" as const,
          }
        : settingsInterviewPlan ?? null;
      await updateKnowledge(selectedKnowledge.id, {
        name: settingsName,
        description: settingsDescription,
        systemPrompt: settingsSystemPrompt.trim() || null,
        category: settingsCategory,
        targetBusiness: settingsTargetBusiness,
        targetEquipment: settingsTargetEquipment,
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
      await loadKnowledgeWorkspace(selectedKnowledgeDb.id, selectedKnowledge.id);
      setSettingsSaveState("success");
      setSettingsNotice(`${tabLabel}を保存しました`);
    } catch (error) {
      console.error("Failed to save knowledge settings", error);
      setSettingsSaveState("error");
      setSettingsNotice(getSettingsSaveErrorMessage(error, tabLabel));
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
      setRecordNotice("インタビュー設定で用途と実行モデルを保存してから、記録を作成してください。");
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
          ? "インタビュー設定で用途と実行モデルを保存してから、記録を作成してください。"
          : "記録を作成できませんでした。通信状態を確認して、もう一度お試しください。",
      );
    }
  }

  async function handleDeleteRecord(recordId: string) {
    if (!selectedKnowledgeDb || !selectedKnowledge) return;
    if (!window.confirm("この記録を削除します。")) return;
    try {
      await deleteRecord(recordId);
      await loadKnowledgeWorkspace(selectedKnowledgeDb.id, selectedKnowledge.id);
      setRecordNotice("記録を削除しました");
    } catch (error) {
      console.error("Failed to delete interview record", error);
      setRecordNotice("記録を削除できませんでした。権限と通信状態を確認してください。");
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
          ? "記録を確認待ちにしました"
          : status === "approved"
            ? "記録を承認しました"
            : status === "returned"
              ? "記録を修正依頼にしました"
              : "記録を更新しました",
      );
    } catch (error) {
      console.error("Failed to change record status", error);
      setRecordNotice("記録の状態を変更できませんでした");
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
          ? "記録を承認しました"
          : status === "returned"
            ? "記録を修正依頼にしました"
            : "記録を更新しました",
      );
    } catch (error) {
      console.error("Failed to change record status", error);
      setRecordNotice("記録の状態を変更できませんでした");
    }
  }

  async function handleBulkApproveRecords() {
    if (!selectedRecordIds.length) return;
    confirmApproveAll({
      message: `${selectedRecordIds.length}件の記録で承認可能なAI提案のみ一括承認します。対象外条件は自動で除外されます。`,
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
    setRecordNotice("インタビュー状態は自動保存されています");
  }

  async function handleSaveInterviewAnswer(fieldId: string, recordAnswer: string) {
    if (!selectedRecord?.id) return;
    try {
      await updateInterviewAnswer(selectedRecord.id, fieldId, recordAnswer);
      await loadInterviewSnapshot(selectedRecord.id);
      setRecordNotice("回答を保存しました");
    } catch (error) {
      console.error("Failed to save interview answer", error);
      setRecordNotice("回答を保存できませんでした");
    }
  }

  function handleDeleteInterviewAnswers() {
    if (!selectedRecord?.id) return;
    if (!window.confirm("この質問と回答の入力内容を削除します。")) return;

    setStructuredDraft({});
    setInterviewAnswerOverrides({});
    setRecordNotice("質問と回答を削除しました");
  }

  function handleDeleteInterviewChat() {
    if (!selectedRecord?.id) return;
    if (!window.confirm("このチャット履歴を削除します。")) return;

    setChatInput("");
    setInterviewMessages([]);
    setInterviewState(null);
    setInterviewStreamMetadata(null);
    setStreamingInterviewReply("");
    setInterviewAnswerOverrides({});
    setDeletedExtraQuestionIds([]);
    pendingInterviewSubmissionRef.current = null;
    setIsInterviewStreaming(false);
    setRecordNotice("チャットを削除しました");
  }

  function handleStartInterview() {
    if (!selectedRecord || isInterviewStreaming) return;
    pendingInterviewSubmissionRef.current = null;
    setStreamingInterviewReply("");
    setInterviewStreamMetadata(null);
    setIsInterviewStreaming(true);
    setRecordNotice("");
    interviewStream.start(selectedRecord.id);
  }

  async function handleSendInterviewMessage(target?: InterviewAnswerTarget | null) {
    if (!selectedRecord || !chatInput.trim() || isInterviewStreaming) return;
    const content = chatInput.trim();
    const previousPending = pendingInterviewSubmissionRef.current;
    const isRetry = previousPending?.content === content;
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
      setRecordNotice("メッセージを送信できませんでした");
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
      setRecordNotice("インタビュー状態の取得に失敗しました");
    });
  }

  async function handleApproveOne(proposalId: string) {
    await approveProposal(proposalId);
    if (selectedRecord) await refreshSelectedRecord(selectedRecord.id);
  }

  async function handleApproveAllForRecord() {
    if (!selectedRecord) return;
    confirmApproveAll({
      message: "承認可能なAI提案のみ全承認します。必須項目未入力、信頼度不足、権限なし、差し戻し済み、エラー、承認済みは対象外です。",
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
    if (!selectedRecord?.id) {
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
    loadInterviewSnapshot(selectedRecord.id).catch(() => {
      setRecordNotice("インタビュー状態の取得に失敗しました");
    });
  }, [selectedKnowledge?.id, selectedRecord?.id]);

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
    proposals,
    chatInput,
    setChatInput,
    interviewMessages,
    interviewState,
    interviewStreamMetadata,
    streamingInterviewReply,
    isInterviewStreaming,
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
    onStartInterview: handleStartInterview,
    onSendInterviewMessage: handleSendInterviewMessage,
    onAppendInterviewMessage: appendRealtimeVoiceInterviewMessage,
    onRefreshInterviewSnapshot: refreshInterviewSnapshotForSelectedRecord,
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
