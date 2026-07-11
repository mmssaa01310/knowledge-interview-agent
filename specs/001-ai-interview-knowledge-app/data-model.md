# Data Model: AI Interview Knowledge Capture Current Baseline

## Common Base Fields

```ts
type BaseEntity = {
  id: string;
  tenantId: string;
  createdByUserId: string;
  updatedByUserId: string;
  ownerUserId?: string;
  createdAt: string;
  updatedAt: string;
  deletedAt?: string | null;
};
```

## Roles

```ts
type UserRole = "admin" | "knowledge_manager" | "interviewer" | "viewer";
```

## Core Entities

### KnowledgeDb

```ts
type KnowledgeDb = BaseEntity & {
  name: string;
  description?: string;
  language: "ja" | "en" | "multi";
  defaultModelId?: string;
  status: "active" | "archived";
  knowledgeCount: number;
};
```

### Knowledge

```ts
type Knowledge = BaseEntity & {
  knowledgeDbId: string;
  name: string;
  description?: string;
  summary?: string;
  systemPrompt?: string;
  purpose?: string;
  category?: string;
  targetBusiness?: string;
  targetEquipment?: string;
  language: "ja" | "en" | "multi";
  defaultModelId?: string;
  status: "active" | "archived";
  recordCount: number;
  documentCount: number;
  fieldCount: number;
};
```

### KnowledgeField

```ts
type KnowledgeField = BaseEntity & {
  knowledgeId: string;
  name: string;
  description?: string;
  inputType: string;
  required: boolean;
  askByAi: boolean;
  aiQuestionExamples: string[];
  aiAssistPrompt?: string;
  options: string[];
  displayOrder: number;
};
```

### InterviewRecord

```ts
type InterviewRecord = BaseEntity & {
  knowledgeId: string;
  knowledgeName: string;
  title: string;
  status: "draft" | "needs_review" | "approved" | "rejected" | "archived";
  targetEquipment?: string;
  targetProcess?: string;
  summary?: string;
  approvedFieldCount: number;
  unapprovedFieldCount: number;
  rejectedFieldCount: number;
};
```

### AiProposal

```ts
type AiProposal = BaseEntity & {
  recordId: string;
  knowledgeId: string;
  proposalType: string;
  status: "draft" | "needs_review" | "approved" | "rejected";
  structuredData: Record<string, unknown>;
  confidence: number;
  sourceMessageIds: string[];
  sourceDocumentChunkIds: string[];
  approvalMethod?: "single" | "record_bulk" | "list_bulk" | null;
};
```

### Document

```ts
type Document = BaseEntity & {
  knowledgeId: string;
  fileName: string;
  contentType: string;
  ingestionStatus:
    | "uploaded"
    | "queued"
    | "processing"
    | "text_extracted"
    | "chunked"
    | "embedding"
    | "indexed"
    | "completed"
    | "failed";
  progressPercent: number;
  chunkCount: number;
  lastIngestedAt?: string;
  errorMessage?: string;
};
```

### DocumentReadStatus

```ts
type DocumentReadStatus = BaseEntity & {
  documentId: string;
  userId: string;
  readStatus: "unread" | "opened" | "reading" | "read" | "acknowledged";
  readProgress: number;
  acknowledged: boolean;
  lastOpenedAt?: string;
  readAt?: string;
  acknowledgedAt?: string;
};
```

### AuditLog

```ts
type AuditLog = BaseEntity & {
  actorUserId: string;
  action: string;
  resourceType: string;
  resourceId: string;
  result: string;
  detail: Record<string, unknown>;
};
```

### LocalChatbot

```ts
type LocalChatbot = {
  id: string;
  name: string;
  referenceKnowledgeDbIds: string[];
  referenceKnowledgeIds: string[];
  referenceDocumentIds: string[];
  excludedDocumentIds: string[];
  modelId: string;
  searchLimit: number;
  confidenceThreshold: number;
};
```

## Relationships

- `KnowledgeDb` 1:N `Knowledge`
- `Knowledge` 1:N `KnowledgeField`
- `Knowledge` 1:N `InterviewRecord`
- `Knowledge` 1:N `Document`
- `InterviewRecord` 1:N `AiProposal`
- `Document` 1:N `DocumentReadStatus`

## Storage Notes

- API persistence is implemented through `InMemoryStore`.
- Chat sessions and chat messages are not persisted entities in the current baseline.
- Document chunks are not persisted entities in the current baseline, although worker-side ingestion states exist as a mock sequence.
