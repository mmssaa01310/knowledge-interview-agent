import { useEffect, useMemo, useState } from "react";
import type { InterviewRecord, Knowledge, KnowledgeDb } from "@ai-interviewer/shared-types";
import { confirmApproveAll } from "../components/ui/ApproveAllDialog";
import {
  createKnowledge,
  createKnowledgeDb,
  createKnowledgeRecordSummaryDraft,
  createDemoDataset,
  createKnowledgeField,
  deleteKnowledge,
  deleteKnowledgeDb,
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
  approveAllProposals,
  approveProposal,
  bulkApproveRecords,
  createRecord,
  createRecordMessage,
  createRecordSummaryProposal,
  deleteRecord,
  fetchProposals,
  fetchRecords,
  updateRecord,
  type AiProposal
} from "../features/interviews/api/interviewApi";
import { useInterviewStream } from "../features/interviews/hooks/useInterviewStream";
import type { KnowledgeLayoutProps } from "../types/pageProps";
import type { ChatMessage, DocumentReadState } from "../types/app";
import type { CreateKnowledgeDbDialogProps } from "./CreateKnowledgeDbDialog";
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

export function useKnowledgeWorkspaceController(args: UseKnowledgeWorkspaceControllerArgs) {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [knowledgeDbs, setKnowledgeDbs] = useState<KnowledgeDb[]>([]);
  const [knowledges, setKnowledges] = useState<Knowledge[]>([]);
  const [records, setRecords] = useState<InterviewRecord[]>([]);
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [fields, setFields] = useState<KnowledgeField[]>([]);
  const [proposals, setProposals] = useState<AiProposal[]>([]);
  const [selectedRecordIds, setSelectedRecordIds] = useState<string[]>([]);
  const [overviewSummaryDraft, setOverviewSummaryDraft] = useState("");
  const [isGeneratingOverviewSummary, setIsGeneratingOverviewSummary] = useState(false);
  const [newDbName, setNewDbName] = useState("");
  const [newDbDescription, setNewDbDescription] = useState("");
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
  const [draftFields, setDraftFields] = useState<KnowledgeField[]>([]);
  const [settingsNotice, setSettingsNotice] = useState("");
  const [isCreateKnowledgeDbDialogOpen, setIsCreateKnowledgeDbDialogOpen] = useState(false);
  const [isCreatingKnowledgeDb, setIsCreatingKnowledgeDb] = useState(false);
  const [createKnowledgeDbError, setCreateKnowledgeDbError] = useState("");
  const [chatInput, setChatInput] = useState("");
  const [interviewMessages, setInterviewMessages] = useState<ChatMessage[]>([
    { role: "ai", text: "ヒアリング項目に沿って、現場で起きたことを順番に確認します。" }
  ]);
  const [structuredDraft, setStructuredDraft] = useState<Record<string, string>>({});
  const [summaryDraft, setSummaryDraft] = useState("");
  const [isGeneratingSummary, setIsGeneratingSummary] = useState(false);
  const [recordNotice, setRecordNotice] = useState("");
  const [documentReadStates, setDocumentReadStates] = useState<Record<string, DocumentReadState>>({});

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

  const sortedFields = useMemo(
    () => [...fields].sort((a, b) => a.displayOrder - b.displayOrder),
    [fields]
  );

  async function refreshSelectedRecord(recordId: string) {
    setProposals(await fetchProposals(recordId));
  }

  const interviewStream = useInterviewStream({
    onDelta: (message) => setInterviewMessages((messages) => [...messages, message]),
    onProposalCreated: () => {
      if ("recordId" in args.route) {
        refreshSelectedRecord(args.route.recordId).catch(() => undefined);
      }
    }
  });

  async function loadKnowledgeDbs() {
    const [profile, dbs] = await Promise.all([fetchMe(), fetchKnowledgeDbs()]);
    setUser(profile);
    setKnowledgeDbs(dbs);
    return dbs;
  }

  async function loadKnowledgeWorkspace(knowledgeDbId: string, knowledgeId?: string) {
    const nextKnowledges = await fetchKnowledges(knowledgeDbId);
    setKnowledges(nextKnowledges);

    const nextKnowledgeId = knowledgeId ?? nextKnowledges[0]?.id;
    if (!nextKnowledgeId) {
      setRecords([]);
      setDocuments([]);
      setFields([]);
      setDraftFields([]);
      setProposals([]);
      return nextKnowledges;
    }

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

  async function refresh() {
    const dbs = await loadKnowledgeDbs();
    const routeKnowledgeDbExists = routeKnowledgeDbId
      ? dbs.some((db: KnowledgeDb) => db.id === routeKnowledgeDbId)
      : false;
    const nextKnowledgeDbId = routeKnowledgeDbExists ? routeKnowledgeDbId : dbs[0]?.id;

    if (routeKnowledgeDbId && !routeKnowledgeDbExists) {
      args.navigate(nextKnowledgeDbId ? `/knowledge-dbs/${nextKnowledgeDbId}` : "/knowledge");
    }

    if (nextKnowledgeDbId) {
      const nextKnowledges = await loadKnowledgeWorkspace(nextKnowledgeDbId, routeKnowledgeId);
      if (
        args.route.name === "knowledge-db"
        || args.route.name === "knowledge-new"
        || routeKnowledgeId
        || nextKnowledges.length === 0
      ) {
        return;
      }

      args.navigate(`/knowledge-dbs/${nextKnowledgeDbId}/knowledges/${nextKnowledges[0].id}`);
    } else {
      setKnowledges([]);
      setRecords([]);
      setDocuments([]);
      setFields([]);
      setDraftFields([]);
      setProposals([]);
    }
  }

  async function createAndOpenKnowledgeDb(name: string, description: string) {
    if (isCreatingKnowledgeDb) return false;

    setIsCreatingKnowledgeDb(true);
    setCreateKnowledgeDbError("");
    try {
      const db = await createKnowledgeDb({
        name,
        description: description || undefined,
        category: "knowledge",
        language: "ja"
      });
      await loadKnowledgeDbs();
      await loadKnowledgeWorkspace(db.id);
      args.navigate(`/knowledge-dbs/${db.id}`);
      return true;
    } catch (error) {
      console.error("Failed to create knowledge DB", error);
      setCreateKnowledgeDbError("ナレッジDBを作成できませんでした。");
      return false;
    } finally {
      setIsCreatingKnowledgeDb(false);
    }
  }

  function openCreateKnowledgeDbDialog() {
    setNewDbName("");
    setNewDbDescription("");
    setCreateKnowledgeDbError("");
    setIsCreateKnowledgeDbDialogOpen(true);
  }

  function closeCreateKnowledgeDbDialog() {
    if (isCreatingKnowledgeDb) return;
    setIsCreateKnowledgeDbDialogOpen(false);
    setNewDbName("");
    setNewDbDescription("");
    setCreateKnowledgeDbError("");
  }

  async function handleRegisterKnowledgeDb() {
    const name = newDbName.trim();
    if (!name) {
      setCreateKnowledgeDbError("ナレッジ名を入力してください。");
      return;
    }

    const created = await createAndOpenKnowledgeDb(name, newDbDescription.trim());
    if (created) {
      setIsCreateKnowledgeDbDialogOpen(false);
      setNewDbName("");
      setNewDbDescription("");
    }
  }

  async function handleDeleteKnowledgeDb(knowledgeDbId: string) {
    if (!window.confirm("このナレッジDBを削除します。関連する画面から参照できなくなります。")) return;
    await deleteKnowledgeDb(knowledgeDbId);
    const dbs = await loadKnowledgeDbs();
    args.navigate(dbs[0] ? `/knowledge-dbs/${dbs[0].id}` : "/knowledge");
  }

  async function handleCreateDemoData() {
    const db = await createDemoDataset();
    await loadKnowledgeDbs();
    args.navigate(`/knowledge-dbs/${db.id}`);
  }

  async function handleCreateKnowledge(payload: {
    name: string;
    description?: string;
    purpose?: string;
  }) {
    if (!selectedKnowledgeDb) return;
    const knowledge = await createKnowledge(selectedKnowledgeDb.id, {
      name: payload.name,
      description: payload.description,
      purpose: payload.purpose,
      category: settingsCategory || undefined,
      targetBusiness: settingsTargetBusiness || undefined,
      targetEquipment: settingsTargetEquipment || undefined,
      language: settingsLanguage,
      defaultModelId: settingsDefaultModelId || undefined
    });
    await loadKnowledgeWorkspace(selectedKnowledgeDb.id, knowledge.id);
    args.navigate(`/knowledge-dbs/${selectedKnowledgeDb.id}/knowledges/${knowledge.id}`);
  }

  async function handleDeleteKnowledge(knowledgeId: string) {
    if (!selectedKnowledgeDb) return;
    if (!window.confirm("このナレッジを削除します。関連する記録・文書の参照に注意してください。")) return;
    await deleteKnowledge(knowledgeId);
    const nextKnowledges = await loadKnowledgeWorkspace(selectedKnowledgeDb.id);
    const nextKnowledgeId = nextKnowledges[0]?.id;
    args.navigate(nextKnowledgeId ? `/knowledge-dbs/${selectedKnowledgeDb.id}/knowledges/${nextKnowledgeId}` : `/knowledge-dbs/${selectedKnowledgeDb.id}`);
  }

  async function handleGenerateOverviewSummary() {
    if (!selectedKnowledge) return;
    setIsGeneratingOverviewSummary(true);
    try {
      const draft = await createKnowledgeRecordSummaryDraft(selectedKnowledge.id);
      setOverviewSummaryDraft(draft.summary);
      setRecordNotice("概要のAI要約候補を作成しました。確認して保存してください。");
    } finally {
      setIsGeneratingOverviewSummary(false);
    }
  }

  async function handleSaveOverviewSummary() {
    if (!selectedKnowledgeDb || !selectedKnowledge) return;
    await updateKnowledge(selectedKnowledge.id, {
      summary: overviewSummaryDraft.trim() || null
    });
    await loadKnowledgeDbs();
    await loadKnowledgeWorkspace(selectedKnowledge.id);
    setRecordNotice("概要の記録要約を保存しました");
  }

  function handleRevertOverviewSummary() {
    setOverviewSummaryDraft(selectedKnowledge?.summary ?? "");
    setRecordNotice("概要の記録要約を前の状態に戻しました");
  }

  async function handleSaveSettings(activeTab: "basic" | "fields" | "assist") {
    if (!selectedKnowledgeDb || !selectedKnowledge) return;
    await updateKnowledge(selectedKnowledge.id, {
      name: settingsName,
      description: settingsDescription,
      systemPrompt: settingsSystemPrompt.trim() || null,
      category: settingsCategory,
      targetBusiness: settingsTargetBusiness,
      targetEquipment: settingsTargetEquipment,
      language: settingsLanguage,
      defaultModelId: settingsDefaultModelId
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
    const tabLabel = activeTab === "assist"
      ? "AI設定"
      : activeTab === "fields"
        ? "ヒアリング項目"
        : "基本設定";
    setSettingsNotice(`${tabLabel}を保存しました`);
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
    if (!selectedKnowledgeDb || !selectedKnowledge || !newRecordTitle.trim()) return;
    const record = await createRecord(selectedKnowledge.id, {
      title: newRecordTitle.trim(),
      targetEquipment: selectedKnowledge.targetEquipment,
      targetProcess: selectedKnowledge.targetBusiness
    });
    setNewRecordTitle("");
    await loadKnowledgeWorkspace(selectedKnowledgeDb.id, selectedKnowledge.id);
    args.navigate(`/knowledge-dbs/${selectedKnowledgeDb.id}/knowledges/${selectedKnowledge.id}/records/${record.id}`);
  }

  async function handleDeleteRecord(recordId: string) {
    if (!selectedKnowledgeDb || !selectedKnowledge) return;
    if (!window.confirm("この記録を削除します。")) return;
    await deleteRecord(recordId);
    await loadKnowledgeWorkspace(selectedKnowledgeDb.id, selectedKnowledge.id);
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

  async function handleSendInterviewMessage() {
    if (!selectedRecord || !chatInput.trim()) return;
    const content = chatInput.trim();
    setChatInput("");
    setInterviewMessages((messages) => [...messages, { role: "user", text: content }]);
    await createRecordMessage(selectedRecord.id, content);
    interviewStream.start(selectedRecord.id);
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

  async function handleGenerateRecordSummary() {
    if (!selectedRecord) return;
    setIsGeneratingSummary(true);
    try {
      const proposal = await createRecordSummaryProposal(selectedRecord.id);
      const summary = proposal.structuredData.summary;
      if (typeof summary === "string") {
        setSummaryDraft(summary);
        setRecordNotice("要約候補を生成しました");
      }
      await refreshSelectedRecord(selectedRecord.id);
    } finally {
      setIsGeneratingSummary(false);
    }
  }

  async function handleSaveRecordSummary() {
    if (!selectedRecord) return;
    await updateRecord(selectedRecord.id, { summary: summaryDraft.trim() || undefined });
    await loadKnowledgeWorkspace(selectedKnowledgeDb?.id ?? "", selectedKnowledge?.id);
    setRecordNotice("要約を保存しました");
  }

  function handleRevertRecordSummary() {
    setSummaryDraft(selectedRecord?.summary ?? "");
    setRecordNotice("要約の変更を元に戻しました");
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
  }, [routeKnowledgeDbId, routeKnowledgeId]);

  useEffect(() => {
    if (!selectedKnowledge) return;
    setSettingsName(selectedKnowledge.name);
    setSettingsDescription(selectedKnowledge.description ?? "");
    setSettingsSystemPrompt(selectedKnowledge.systemPrompt ?? "");
    setOverviewSummaryDraft(selectedKnowledge.summary ?? "");
    setSettingsCategory(selectedKnowledge.category ?? "");
    setSettingsTargetBusiness(selectedKnowledge.targetBusiness ?? "");
    setSettingsTargetEquipment(selectedKnowledge.targetEquipment ?? "");
    setSettingsLanguage(selectedKnowledge.language);
    setSettingsDefaultModelId(selectedKnowledge.defaultModelId ?? "");
    setSettingsNotice("");
  }, [selectedKnowledge?.id]);

  useEffect(() => {
    if (!settingsNotice) return;
    const timeoutId = window.setTimeout(() => setSettingsNotice(""), 3000);
    return () => window.clearTimeout(timeoutId);
  }, [settingsNotice]);

  useEffect(() => {
    setSummaryDraft(selectedRecord?.summary ?? "");
  }, [selectedRecord?.id, selectedRecord?.summary]);

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
    if ("recordId" in args.route) refreshSelectedRecord(args.route.recordId).catch(() => undefined);
  }, ["recordId" in args.route ? args.route.recordId : ""]);

  const knowledgeLayoutProps: KnowledgeLayoutProps = {
    route: args.route,
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
    settingsNotice,
    newDbName,
    setNewDbName,
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
    overviewSummaryDraft,
    setOverviewSummaryDraft,
    isGeneratingOverviewSummary,
    chatInput,
    setChatInput,
    interviewMessages,
    structuredDraft,
    setStructuredDraft,
    summaryDraft,
    setSummaryDraft,
    isGeneratingSummary,
    recordNotice,
    setRecordNotice,
    navigate: args.navigate,
    onCreateKnowledgeDb: openCreateKnowledgeDbDialog,
    isCreatingKnowledgeDb,
    createKnowledgeDbError: "",
    onDeleteKnowledgeDb: handleDeleteKnowledgeDb,
    onCreateKnowledge: handleCreateKnowledge,
    onDeleteKnowledge: handleDeleteKnowledge,
    onGenerateOverviewSummary: handleGenerateOverviewSummary,
    onSaveOverviewSummary: handleSaveOverviewSummary,
    onRevertOverviewSummary: handleRevertOverviewSummary,
    onCreateDemoData: handleCreateDemoData,
    onSaveSettings: handleSaveSettings,
    onClearSettingsNotice: () => setSettingsNotice(""),
    onCreateDocument: handleCreateDocument,
    onCreateRecord: handleCreateRecord,
    onDeleteRecord: handleDeleteRecord,
    onBulkApproveRecords: handleBulkApproveRecords,
    onSendInterviewMessage: handleSendInterviewMessage,
    onGenerateRecordSummary: handleGenerateRecordSummary,
    onSaveRecordSummary: handleSaveRecordSummary,
    onRevertRecordSummary: handleRevertRecordSummary,
    onApproveOne: handleApproveOne,
    onRejectProposal: handleRejectProposal,
    onRemoveProposal: handleRemoveProposal,
    onApproveAllForRecord: handleApproveAllForRecord
  };

  const createKnowledgeDbDialogProps: CreateKnowledgeDbDialogProps = {
    isOpen: isCreateKnowledgeDbDialogOpen,
    isCreating: isCreatingKnowledgeDb,
    error: createKnowledgeDbError,
    name: newDbName,
    description: newDbDescription,
    onNameChange: (value) => {
      setNewDbName(value);
      if (createKnowledgeDbError) setCreateKnowledgeDbError("");
    },
    onDescriptionChange: setNewDbDescription,
    onClose: closeCreateKnowledgeDbDialog,
    onSubmit: handleRegisterKnowledgeDb
  };

  return {
    user,
    knowledgeDbs,
    knowledges,
    documents,
    selectedKnowledgeDb,
    knowledgeLayoutProps,
    openCreateKnowledgeDbDialog,
    isCreatingKnowledgeDb,
    createKnowledgeDbDialogProps
  };
}