export type UserRole = "admin" | "knowledge_manager" | "interviewer" | "viewer";

export type ApprovalStatus = "draft" | "needs_review" | "approved" | "rejected";

export type DocumentIngestionStatus =
  | "uploaded"
  | "queued"
  | "processing"
  | "text_extracted"
  | "chunked"
  | "embedding"
  | "indexed"
  | "completed"
  | "failed";

export type BaseEntity = {
  id: string;
  tenantId: string;
  createdByUserId: string;
  updatedByUserId: string;
  ownerUserId?: string;
  createdAt: string;
  updatedAt: string;
  deletedAt?: string | null;
};

export type KnowledgeDb = BaseEntity & {
  name: string;
  description?: string;
  language: "ja" | "en" | "multi";
  defaultModelId?: string;
  status: "active" | "archived";
  knowledgeCount: number;
};

export type InterviewPlan = {
  version?: number;
  purpose?: string | null;
  profile?: "fixed_form" | "business_process" | "system_requirement";
  modelId?: "global.openai.gpt-5.6-terra" | "global.openai.gpt-5.6-luna" | null;
  /** AIインタビューの言語。Web UIのuiLocaleとは独立して扱う。 */
  interviewLocale?: string | null;
};

export type Knowledge = BaseEntity & {
  knowledgeDbId: string;
  name: string;
  description?: string;
  systemPrompt?: string;
  purpose?: string;
  interviewPlan?: InterviewPlan | null;
  targetEquipment?: string;
  targetBusiness?: string;
  category?: string;
  tags: string[];
  language: "ja" | "en" | "multi";
  defaultModelId?: string;
  status: "active" | "archived";
  recordCount: number;
  documentCount: number;
  fieldCount: number;
};

export type InterviewPromptProfile = BaseEntity & {
  name: string;
  description?: string;
  prompt: string;
  status: "active" | "archived";
};

export type InterviewRecord = BaseEntity & {
  knowledgeId: string;
  knowledgeName: string;
  title: string;
  status: "draft" | "in_progress" | "submitted" | "returned" | "approved";
  targetEquipment?: string;
  targetProcess?: string;
  reviewNote?: string;
  viewerUserIds: string[];
  approvedFieldCount: number;
  unapprovedFieldCount: number;
  rejectedFieldCount: number;
};
