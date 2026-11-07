from typing import Literal

from pydantic import Field

from ai_interviewer_api.models.base import BaseEntity
from ai_interviewer_api.models.interview_plan import InterviewPlan, InterviewQuestionPlan


class KnowledgeDb(BaseEntity):
    name: str
    description: str | None = None
    language: Literal["ja", "en", "multi"] = "ja"
    defaultModelId: str | None = None
    status: Literal["active", "archived"] = "active"
    knowledgeCount: int = 0


class KnowledgeTag(BaseEntity):
    name: str


class Knowledge(BaseEntity):
    knowledgeDbId: str
    name: str
    description: str | None = None
    systemPrompt: str | None = None
    purpose: str | None = None
    interviewPlan: InterviewPlan | None = None
    category: str | None = None
    targetBusiness: str | None = None
    targetEquipment: str | None = None
    tags: list[str] = Field(default_factory=list)
    language: Literal["ja", "en", "multi"] = "ja"
    defaultModelId: str | None = None
    status: Literal["active", "archived"] = "active"
    recordCount: int = 0
    documentCount: int = 0
    fieldCount: int = 0


class InterviewPromptProfile(BaseEntity):
    name: str
    description: str | None = None
    prompt: str
    status: Literal["active", "archived"] = "active"


class KnowledgeField(BaseEntity):
    knowledgeId: str
    name: str
    description: str | None = None
    inputType: str
    required: bool
    askByAi: bool
    retrievalPolicy: Literal["never", "auto", "required"] = "auto"
    aiQuestionExamples: list[str] = Field(default_factory=list)
    aiAssistPrompt: str | None = None
    questionPlan: InterviewQuestionPlan | None = None
    options: list[str] = Field(default_factory=list)
    displayOrder: int


class InterviewRecord(BaseEntity):
    knowledgeId: str
    knowledgeName: str
    title: str
    interviewLocale: InterviewLocale | None = None
    status: Literal["draft", "in_progress", "submitted", "returned", "approved"] = "draft"
    targetEquipment: str | None = None
    targetProcess: str | None = None
    reviewNote: str | None = None
    viewerUserIds: list[str] = Field(default_factory=list)
    approvedFieldCount: int = 0
    unapprovedFieldCount: int = 0
    rejectedFieldCount: int = 0


class AiProposal(BaseEntity):
    recordId: str
    knowledgeId: str
    proposalType: str = "field_update"
    status: str = "needs_review"
    structuredData: dict
    confidence: float = 0.84
    sourceMessageIds: list[str] = Field(default_factory=list)
    sourceDocumentChunkIds: list[str] = Field(default_factory=list)
    approvalMethod: str | None = None


class Document(BaseEntity):
    knowledgeId: str
    fileName: str
    contentType: str
    sourceType: Literal["file", "prior_knowledge"] = "file"
    title: str | None = None
    knowledgeType: Literal["known_fact", "glossary"] | None = None
    contentFormat: Literal["text", "markdown"] | None = None
    content: str | None = None
    ingestionStatus: str = "uploaded"
    progressPercent: int = 0
    chunkCount: int = 0
    lastIngestedAt: str | None = None
    errorMessage: str | None = None


class DocumentReadStatus(BaseEntity):
    documentId: str
    userId: str
    readStatus: str = "unread"
    readProgress: int = 0
    acknowledged: bool = False
    lastOpenedAt: str | None = None
    readAt: str | None = None
    acknowledgedAt: str | None = None
