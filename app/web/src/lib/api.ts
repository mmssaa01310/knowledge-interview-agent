import type { InterviewPlan, InterviewRecord, Knowledge, KnowledgeDb, KnowledgeTag, UserRole } from "@ai-interviewer/shared-types";
import type {
  InterviewState,
  ProcessModelState,
  RetrievedSourceReference,
} from "../types/app";
import type {
  AdminDashboard,
  DashboardFilters,
  GuidanceDraft,
  LearningAnalysisDraft,
  LearningAnalysisUpdatePayload,
} from "../types/dashboard";

export const API_BASE_URL = "";
export const DEV_TOKEN_STORAGE_KEY = "ai-interviewer-dev-token";

type ApiRequestOptions = {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
};

export class ApiError extends Error {
  status?: number;
  detail?: string;

  constructor(message: string, options: { status?: number; detail?: string } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = options.status;
    this.detail = options.detail;
  }
}

async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  let response: Response;
  try {
    const devToken = getDevelopmentToken();
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: options.method ?? "GET",
      headers: {
        "content-type": "application/json",
        "x-dev-token": devToken
      },
      body: options.body ? JSON.stringify(options.body) : undefined
    });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "network_error";
    throw new ApiError(detail, { detail });
  }

  if (!response.ok) {
    const responseText = await response.text();
    let detail = responseText;
    try {
      const parsed = JSON.parse(responseText) as { detail?: unknown };
      detail = typeof parsed.detail === "string" ? parsed.detail : responseText;
    } catch {
      detail = responseText;
    }
    throw new ApiError(
      `${response.status} ${response.statusText}${detail ? `: ${detail}` : ""}`,
      { status: response.status, detail }
    );
  }

  return response.json() as Promise<T>;
}

export type UserProfile = {
  userId: string;
  tenantId: string;
  role: UserRole;
  displayName: string;
  /** UI表示言語。AIインタビューの言語とは独立して扱う。 */
  uiLocale?: string | null;
  /** 将来のAIインタビュー言語設定。uiLocale変更では更新しない。 */
  interviewLocale?: string | null;
  /** Localeとは独立した表示タイムゾーン。 */
  timezone?: string | null;
};

export function getDevelopmentToken() {
  if (typeof window === "undefined") return "dev-manager";
  try {
    return window.localStorage.getItem(DEV_TOKEN_STORAGE_KEY) ?? "dev-manager";
  } catch {
    return "dev-manager";
  }
}

export function setDevelopmentToken(token: string) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(DEV_TOKEN_STORAGE_KEY, token);
}

export function clearDevelopmentToken() {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(DEV_TOKEN_STORAGE_KEY);
  } catch {
    // Storageが使えない環境でも、ログアウト導線は継続する。
  }
}

export type DocumentSummary = {
  id: string;
  fileName: string;
  contentType: string;
  ingestionStatus: string;
  progressPercent: number;
  knowledgeId: string;
  knowledgeDbId?: string;
  createdByUserId?: string;
  createdAt?: string;
  chunkCount?: number;
  errorMessage?: string;
  lastIngestedAt?: string;
};

export type DocumentContent = {
  document: DocumentSummary;
  content: string;
};

export type KnowledgeField = {
  id?: string;
  name: string;
  description?: string;
  inputType: string;
  required: boolean;
  askByAi: boolean;
  retrievalPolicy?: "never" | "auto" | "required";
  aiQuestionExamples?: string[];
  questionPlan?: InterviewQuestionPlan;
  displayOrder: number;
};

export type InterviewPlanItem = {
  itemId: string;
  label: string;
  description?: string | null;
};

export type InterviewQuestionPlan = {
  version?: number;
  purpose?: string | null;
  requiredItems: InterviewPlanItem[];
  optionalItems?: InterviewPlanItem[];
  completionCriteria?: { mode: "all_required_items" };
};

export type AiProposal = {
  id: string;
  recordId: string;
  knowledgeId: string;
  status: "draft" | "needs_review" | "approved" | "rejected";
  proposalType?: string;
  structuredData: Record<string, unknown>;
  confidence: number;
  approvalMethod?: string | null;
};

export type FieldSuggestionResponse = {
  reply: string;
  fields: KnowledgeField[];
  interviewPlan?: InterviewPlan;
  modelId: string;
  bedrockInvoked?: boolean;
  retrievedSources?: RetrievedSourceReference[];
};

export type FieldSuggestionChatMessage = {
  role: "user" | "ai" | "assistant";
  content: string;
};

export type InterviewStateResponse = {
  status: "in_progress" | "completed";
  interviewState: InterviewState;
  messages: Array<{
    id: string;
    role: "user" | "assistant";
    content: string;
    questionId?: string;
    questionType?: "configured_field" | "follow_up" | "structured";
    fieldId?: string | null;
    answerToQuestionId?: string;
    answerToFieldId?: string | null;
    turnType?: "ANSWER" | "CONTROL";
    voiceSessionId?: string | null;
    voiceTurnId?: string | null;
    voiceResponseId?: string | null;
    isActualUtterance?: boolean;
    targetType?: string | null;
    targetId?: string | null;
    candidateSource?: "user_statement" | "assistant_proposal" | "document_reference" | null;
    candidateValue?: string | null;
    candidateSourceIds?: string[];
    retrievedSources?: RetrievedSourceReference[];
    messageType?: "process_model_edit_command" | "process_model_edit_reply" | string;
    processCommandId?: string | null;
    instructionSummary?: string | null;
    updatedTargets?: Array<"requirements" | "flowchart" | "sequence" | string>;
    processChangeSummary?: string | null;
    processUpdatedPoints?: string[];
    processVersion?: number | null;
  }>;
  structuredDraft: Record<string, string>;
};

export async function fetchMe() {
  return apiRequest<UserProfile>("/api/me");
}

export async function fetchKnowledgeDbs() {
  return apiRequest<KnowledgeDb[]>("/api/knowledge-dbs");
}

export async function fetchKnowledgeTags() {
  return apiRequest<KnowledgeTag[]>("/api/knowledge-tags");
}

export async function createKnowledgeTag(name: string) {
  return apiRequest<KnowledgeTag>("/api/knowledge-tags", {
    method: "POST",
    body: { name }
  });
}

export async function updateKnowledgeTag(tagId: string, name: string) {
  return apiRequest<KnowledgeTag>(`/api/knowledge-tags/${tagId}`, {
    method: "PATCH",
    body: { name }
  });
}

export async function deleteKnowledgeTag(tagId: string) {
  return apiRequest<{ deleted: boolean }>(`/api/knowledge-tags/${tagId}`, { method: "DELETE" });
}

export async function createKnowledgeDb(payload: {
  name: string;
  description?: string;
  category?: string;
  targetBusiness?: string;
  targetEquipment?: string;
  language?: "ja" | "en" | "multi";
}) {
  return apiRequest<KnowledgeDb>("/api/knowledge-dbs", {
    method: "POST",
    body: payload
  });
}

export async function fetchKnowledges(knowledgeDbId: string) {
  return apiRequest<Knowledge[]>(`/api/knowledge-dbs/${knowledgeDbId}/knowledges`);
}

export async function createKnowledge(
  knowledgeDbId: string,
  payload: {
    name: string;
    description?: string;
    purpose?: string;
    category?: string;
    targetBusiness?: string;
    targetEquipment?: string;
    tags?: string[];
    language?: "ja" | "en" | "multi";
    defaultModelId?: string;
  }
) {
  return apiRequest<Knowledge>(`/api/knowledge-dbs/${knowledgeDbId}/knowledges`, {
    method: "POST",
    body: payload
  });
}

export async function updateKnowledge(
  knowledgeId: string,
  payload: Partial<Omit<Knowledge, "systemPrompt">> & { systemPrompt?: string | null }
) {
  return apiRequest<Knowledge>(`/api/knowledges/${knowledgeId}`, {
    method: "PATCH",
    body: payload
  });
}

export async function deleteKnowledge(knowledgeId: string) {
  return apiRequest<{ deleted: boolean }>(`/api/knowledges/${knowledgeId}`, { method: "DELETE" });
}

export async function fetchRecords(knowledgeId: string) {
  return apiRequest<InterviewRecord[]>(`/api/knowledges/${knowledgeId}/records`);
}

export async function fetchAccessibleRecords() {
  return apiRequest<InterviewRecord[]>("/api/records");
}

export async function fetchAdminDashboard(filters: DashboardFilters = {}) {
  const query = new URLSearchParams();
  if (filters.dateFrom) query.set("dateFrom", filters.dateFrom);
  if (filters.dateTo) query.set("dateTo", filters.dateTo);
  if (filters.knowledgeId) query.set("knowledgeId", filters.knowledgeId);
  if (filters.profile) query.set("profile", filters.profile);
  if (filters.recordStatus) query.set("recordStatus", filters.recordStatus);
  const queryString = query.toString();
  return apiRequest<AdminDashboard>(`/api/admin/dashboard${queryString ? `?${queryString}` : ""}`);
}

export async function fetchLearningAnalyses(knowledgeId?: string) {
  const query = knowledgeId ? `?knowledgeId=${encodeURIComponent(knowledgeId)}` : "";
  return apiRequest<LearningAnalysisDraft[]>(`/api/admin/learning-analysis${query}`);
}

export async function generateLearningAnalysis(filters: DashboardFilters) {
  return apiRequest<LearningAnalysisDraft>("/api/admin/learning-analysis", {
    method: "POST",
    body: filters,
  });
}

export async function updateLearningAnalysis(
  analysisId: string,
  payload: LearningAnalysisUpdatePayload,
) {
  return apiRequest<LearningAnalysisDraft>(`/api/admin/learning-analysis/${analysisId}`, {
    method: "PATCH",
    body: payload,
  });
}

export async function reviewLearningAnalysis(analysisId: string) {
  return apiRequest<LearningAnalysisDraft>(`/api/admin/learning-analysis/${analysisId}/review`, {
    method: "POST",
  });
}

export async function fetchPublishedGuidance(recordId: string) {
  return apiRequest<GuidanceDraft[]>(`/api/records/${recordId}/guidance`);
}

export async function fetchRecordInterviewContext(recordId: string) {
  return apiRequest<{
    record: InterviewRecord;
    knowledge: Knowledge;
    fields: KnowledgeField[];
  }>(`/api/records/${recordId}/interview-context`);
}

export async function createRecord(
  knowledgeId: string,
  payload: {
    title: string;
    interviewLocale?: InterviewRecord["interviewLocale"];
    targetEquipment?: string;
    targetProcess?: string;
    ownerUserId?: string;
    viewerUserIds?: string[];
  }
) {
  return apiRequest<InterviewRecord>(`/api/knowledges/${knowledgeId}/records`, {
    method: "POST",
    body: payload
  });
}

export async function deleteRecord(recordId: string) {
  return apiRequest<{ deleted: boolean }>(`/api/records/${recordId}`, { method: "DELETE" });
}

export async function fetchDocuments(knowledgeId: string) {
  return apiRequest<DocumentSummary[]>(`/api/knowledges/${knowledgeId}/documents`);
}

export async function fetchDocumentContent(documentId: string) {
  return apiRequest<DocumentContent>(`/api/documents/${documentId}/content`);
}

export async function deleteDocument(documentId: string) {
  return apiRequest<{ deleted: boolean }>(`/api/documents/${documentId}`, { method: "DELETE" });
}

export async function createDocument(
  knowledgeId: string,
  payload: { fileName: string; contentType: string }
) {
  return apiRequest<DocumentSummary>(`/api/knowledges/${knowledgeId}/documents`, {
    method: "POST",
    body: payload
  });
}

export async function uploadDocument(knowledgeId: string, file: File) {
  const formData = new FormData();
  formData.append("file", file);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/knowledges/${knowledgeId}/documents/upload`, {
      method: "POST",
      headers: {
        "x-dev-token": getDevelopmentToken()
      },
      body: formData
    });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "network_error";
    throw new ApiError(detail, { detail });
  }

  if (!response.ok) {
    const responseText = await response.text();
    let detail = responseText;
    try {
      const parsed = JSON.parse(responseText) as { detail?: unknown };
      detail = typeof parsed.detail === "string" ? parsed.detail : responseText;
    } catch {
      detail = responseText;
    }
    throw new ApiError(
      `${response.status} ${response.statusText}${detail ? `: ${detail}` : ""}`,
      { status: response.status, detail }
    );
  }

  return response.json() as Promise<DocumentSummary>;
}

export async function fetchKnowledgeFields(knowledgeId: string) {
  return apiRequest<KnowledgeField[]>(`/api/knowledges/${knowledgeId}/fields`);
}

export async function createKnowledgeField(knowledgeId: string, payload: KnowledgeField) {
  return apiRequest<KnowledgeField>(`/api/knowledges/${knowledgeId}/fields`, {
    method: "POST",
    body: payload
  });
}

export async function updateKnowledgeField(fieldId: string, payload: Partial<KnowledgeField>) {
  return apiRequest<KnowledgeField>(`/api/knowledge-fields/${fieldId}`, {
    method: "PATCH",
    body: payload
  });
}

export async function deleteKnowledgeField(fieldId: string) {
  return apiRequest<{ deleted: boolean }>(`/api/knowledge-fields/${fieldId}`, { method: "DELETE" });
}

export async function fetchProposals(recordId: string) {
  return apiRequest<AiProposal[]>(`/api/records/${recordId}/proposals`);
}

export async function createRecordMessage(
  recordId: string,
  payload: {
    content: string;
    clientMessageId?: string;
    stateVersion?: number | null;
    answerToQuestionId?: string | null;
    targetType?: string | null;
    targetId?: string | null;
    turnType?: "ANSWER" | "CONTROL";
  }
) {
  return apiRequest<{
    message: string;
    proposalId: string | null;
    recordMessage: {
      id: string;
      clientMessageId?: string | null;
      role: "user" | "assistant";
      content: string;
      questionId?: string;
      questionType?: "configured_field" | "follow_up" | "structured";
      fieldId?: string | null;
      answerToQuestionId?: string;
      answerToFieldId?: string | null;
      turnType?: "ANSWER" | "CONTROL";
      targetType?: string | null;
      targetId?: string | null;
      candidateSource?: "user_statement" | "assistant_proposal" | "document_reference" | null;
      candidateValue?: string | null;
      candidateSourceIds?: string[];
    };
  }>(`/api/records/${recordId}/messages`, {
    method: "POST",
    body: payload
  });
}

export async function fetchInterviewState(recordId: string) {
  return apiRequest<InterviewStateResponse>(`/api/records/${recordId}/interview-state`);
}

export async function saveProcessModel(
  recordId: string,
  payload: { baseProcessVersion: number; baseStateVersion?: number; processState: ProcessModelState },
) {
  return apiRequest<InterviewStateResponse>(`/api/records/${recordId}/process-model`, {
    method: "PATCH",
    body: payload,
  });
}

export async function editProcessModel(
  recordId: string,
  payload: { instruction: string; baseProcessVersion: number; baseStateVersion?: number },
) {
  return apiRequest<InterviewStateResponse & { reply: string }>(
    `/api/records/${recordId}/process-model/commands`,
    {
      method: "POST",
      body: payload,
    },
  );
}

export async function suggestKnowledgeFields(
  knowledgeId: string,
  payload: {
    content: string;
    context: {
      name?: string;
      description?: string;
      category?: string;
      targetBusiness?: string;
      targetEquipment?: string;
      language: string;
      defaultModelId?: string;
      systemPrompt?: string;
    };
    existingFields: KnowledgeField[];
    recentMessages?: FieldSuggestionChatMessage[];
    maxFields?: number;
  }
) {
  return apiRequest<FieldSuggestionResponse>(`/api/knowledges/${knowledgeId}/field-suggestions`, {
    method: "POST",
    body: payload
  });
}

export async function approveProposal(proposalId: string) {
  return apiRequest<AiProposal>(`/api/proposals/${proposalId}/approve`, { method: "POST" });
}

export async function updateRecord(recordId: string, payload: Partial<InterviewRecord>) {
  return apiRequest<InterviewRecord>(`/api/records/${recordId}`, {
    method: "PATCH",
    body: payload
  });
}

export async function updateInterviewAnswer(recordId: string, fieldId: string, recordAnswer: string) {
  return apiRequest<{
    recordId: string;
    fieldId: string;
    answerState: "CONFIRMED";
    recordAnswer: string;
  }>(`/api/records/${recordId}/interview-answers/${fieldId}`, {
    method: "PATCH",
    body: { recordAnswer }
  });
}

export async function approveAllProposals(recordId: string) {
  return apiRequest<{ approvedCount: number; skippedCount: number }>(
    `/api/records/${recordId}/approve-all-proposals`,
    { method: "POST" }
  );
}

export async function bulkApproveRecords(recordIds: string[]) {
  return apiRequest<{ approvedCount: number; recordResults: Array<{ recordId: string; approvedCount: number }> }>(
    "/api/records/bulk-approve",
    {
      method: "POST",
      body: { recordIds }
    }
  );
}

export async function resetDevVoiceDemo() {
  return apiRequest<{
    knowledgeDbId: string;
    knowledgeId: string;
    recordId: string;
  }>("/api/dev/voice-demo/reset", { method: "POST" });
}

export async function resetDevSystemRequirementDemo() {
  return apiRequest<{
    knowledgeDbId: string;
    knowledgeId: string;
    recordId: string;
  }>("/api/dev/system-requirement-demo/reset", { method: "POST" });
}
