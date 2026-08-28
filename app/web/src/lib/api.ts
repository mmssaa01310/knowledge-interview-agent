import type { InterviewPlan, InterviewRecord, Knowledge, KnowledgeDb } from "@ai-interviewer/shared-types";
import type { ChatMessage, InterviewState } from "../types/app";

export const API_BASE_URL = "";

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
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: options.method ?? "GET",
      headers: {
        "content-type": "application/json",
        "x-dev-token": "dev-manager"
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
  role: string;
  displayName: string;
};

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

export type ChatAnswerResponse = {
  answer: string;
  citations: string[];
};

export type FieldSuggestionResponse = {
  reply: string;
  fields: KnowledgeField[];
  interviewPlan?: InterviewPlan;
  modelId: string;
  bedrockInvoked?: boolean;
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
    candidateSource?: "user_statement" | "assistant_proposal" | null;
  }>;
  structuredDraft: Record<string, string>;
};

export async function fetchMe() {
  return apiRequest<UserProfile>("/api/me");
}

export async function fetchKnowledgeDbs() {
  return apiRequest<KnowledgeDb[]>("/api/knowledge-dbs");
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

export async function updateKnowledgeDb(knowledgeDbId: string, payload: Partial<KnowledgeDb>) {
  return apiRequest<KnowledgeDb>(`/api/knowledge-dbs/${knowledgeDbId}`, {
    method: "PATCH",
    body: payload
  });
}

export async function deleteKnowledgeDb(knowledgeDbId: string) {
  return apiRequest<{ deleted: boolean }>(`/api/knowledge-dbs/${knowledgeDbId}`, { method: "DELETE" });
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
  payload: Partial<Omit<Knowledge, "summary" | "systemPrompt">> & {
    summary?: string | null;
    systemPrompt?: string | null;
  }
) {
  return apiRequest<Knowledge>(`/api/knowledges/${knowledgeId}`, {
    method: "PATCH",
    body: payload
  });
}

export async function createKnowledgeRecordSummaryDraft(knowledgeId: string) {
  return apiRequest<{ summary: string; status: "draft" }>(`/api/knowledges/${knowledgeId}/record-summary-draft`, {
    method: "POST"
  });
}

export async function deleteKnowledge(knowledgeId: string) {
  return apiRequest<{ deleted: boolean }>(`/api/knowledges/${knowledgeId}`, { method: "DELETE" });
}

export async function fetchRecords(knowledgeId: string) {
  return apiRequest<InterviewRecord[]>(`/api/knowledges/${knowledgeId}/records`);
}

export async function createRecord(
  knowledgeId: string,
  payload: { title: string; targetEquipment?: string; targetProcess?: string }
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

export async function createDocument(
  knowledgeId: string,
  payload: { fileName: string; contentType: string }
) {
  return apiRequest<DocumentSummary>(`/api/knowledges/${knowledgeId}/documents`, {
    method: "POST",
    body: payload
  });
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
      candidateSource?: "user_statement" | "assistant_proposal" | null;
    };
  }>(`/api/records/${recordId}/messages`, {
    method: "POST",
    body: payload
  });
}

export async function fetchInterviewState(recordId: string) {
  return apiRequest<InterviewStateResponse>(`/api/records/${recordId}/interview-state`);
}

export async function answerChat(
  chatId: string,
  payload: {
    content: string;
    modelId?: string;
    referenceKnowledgeDbIds?: string[];
    referenceKnowledgeIds?: string[];
    referenceDocumentIds?: string[];
    excludedDocumentIds?: string[];
    searchLimit?: number;
    confidenceThreshold?: number;
  }
) {
  return apiRequest<ChatAnswerResponse>(`/api/chats/${chatId}/messages`, {
    method: "POST",
    body: payload
  });
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

export async function createRecordSummaryProposal(recordId: string) {
  return apiRequest<AiProposal>(`/api/records/${recordId}/summary-proposals`, { method: "POST" });
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

export async function createDemoDataset() {
  const knowledgeDb = await apiRequest<KnowledgeDb>("/api/knowledge-dbs", {
    method: "POST",
    body: {
      name: "保全ノウハウ DB",
      description: "圧入工程と搬送設備の暗黙知を集める",
      category: "maintenance",
      targetBusiness: "保全",
      targetEquipment: "圧入機A",
      language: "ja"
    }
  });

  await apiRequest(`/api/knowledge-dbs/${knowledgeDb.id}/fields`, {
    method: "POST",
    body: {
      name: "現象",
      inputType: "long_text",
      required: true,
      askByAi: true,
      displayOrder: 1
    }
  });

  const record = await apiRequest<InterviewRecord>(`/api/knowledge-dbs/${knowledgeDb.id}/records`, {
    method: "POST",
    body: {
      title: "圧入機A 朝一の荷重ばらつき",
      targetEquipment: "圧入機A",
      targetProcess: "圧入工程"
    }
  });

  await apiRequest(`/api/records/${record.id}/messages`, {
    method: "POST",
    body: {
      content: "圧入荷重が朝一と段取り替え後に不安定になります"
    }
  });

  await apiRequest(`/api/knowledge-dbs/${knowledgeDb.id}/documents`, {
    method: "POST",
    body: {
      fileName: "圧入機A_保全手順.pdf",
      contentType: "application/pdf"
    }
  });

  return knowledgeDb;
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
