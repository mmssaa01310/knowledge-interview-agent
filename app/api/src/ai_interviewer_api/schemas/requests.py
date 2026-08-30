from typing import Any, Literal

from pydantic import BaseModel, Field

from ai_interviewer_api.models.interview_plan import InterviewPlan, InterviewQuestionPlan


class KnowledgeDbCreate(BaseModel):
    name: str
    description: str | None = None
    language: str = "ja"


class KnowledgeDbUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    language: str | None = None
    status: str | None = None


class KnowledgeCreate(BaseModel):
    name: str
    description: str | None = None
    systemPrompt: str | None = None
    purpose: str | None = None
    interviewPlan: InterviewPlan | None = None
    category: str | None = None
    targetBusiness: str | None = None
    targetEquipment: str | None = None
    tags: list[str] = Field(default_factory=list)
    language: str = "ja"
    defaultModelId: str | None = None


class KnowledgeUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    systemPrompt: str | None = None
    purpose: str | None = None
    interviewPlan: InterviewPlan | None = None
    category: str | None = None
    targetBusiness: str | None = None
    targetEquipment: str | None = None
    tags: list[str] | None = None
    language: str | None = None
    defaultModelId: str | None = None
    status: str | None = None


class InterviewPromptProfileCreate(BaseModel):
    name: str
    description: str | None = None
    prompt: str


class InterviewPromptProfileUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    prompt: str | None = None
    status: str | None = None


class KnowledgeFieldCreate(BaseModel):
    name: str
    description: str | None = None
    inputType: str = "short_text"
    required: bool = False
    askByAi: bool = True
    retrievalPolicy: str = "auto"
    aiQuestionExamples: list[str] = Field(default_factory=list)
    options: list[str] = Field(default_factory=list)
    displayOrder: int = 0
    questionPlan: InterviewQuestionPlan | None = None


class KnowledgeFieldUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    inputType: str | None = None
    required: bool | None = None
    askByAi: bool | None = None
    retrievalPolicy: str | None = None
    aiQuestionExamples: list[str] | None = None
    options: list[str] | None = None
    displayOrder: int | None = None
    questionPlan: InterviewQuestionPlan | None = None


class FieldSuggestionContext(BaseModel):
    name: str | None = None
    description: str | None = None
    systemPrompt: str | None = None
    category: str | None = None
    targetBusiness: str | None = None
    targetEquipment: str | None = None
    language: str = "ja"
    defaultModelId: str | None = None


class FieldSuggestionChatMessage(BaseModel):
    role: str
    content: str


class FieldSuggestionRequest(BaseModel):
    content: str
    context: FieldSuggestionContext = Field(default_factory=FieldSuggestionContext)
    existingFields: list[KnowledgeFieldCreate] = Field(default_factory=list)
    recentMessages: list[FieldSuggestionChatMessage] = Field(default_factory=list)
    maxFields: int = 8


class RecordCreate(BaseModel):
    title: str
    targetEquipment: str | None = None
    targetProcess: str | None = None
    ownerUserId: str | None = None
    viewerUserIds: list[str] = Field(default_factory=list)


class RecordUpdate(BaseModel):
    title: str | None = None
    status: Literal["draft", "in_progress", "submitted", "returned", "approved"] | None = None
    targetEquipment: str | None = None
    targetProcess: str | None = None
    reviewNote: str | None = None
    ownerUserId: str | None = None
    viewerUserIds: list[str] | None = None


class InterviewAnswerUpdate(BaseModel):
    recordAnswer: str | None = Field(default=None, min_length=1, max_length=10000)
    answerSummary: str | None = Field(default=None, min_length=1, max_length=10000)


class BulkApproveRequest(BaseModel):
    recordIds: list[str]


class ChatMessageCreate(BaseModel):
    content: str
    clientMessageId: str | None = None
    stateVersion: int | None = None
    answerToQuestionId: str | None = None
    targetType: str | None = None
    targetId: str | None = None
    turnType: Literal["ANSWER", "CONTROL"] | None = None


class ProcessModelUpdate(BaseModel):
    baseProcessVersion: int = Field(default=0, ge=0)
    baseStateVersion: int | None = Field(default=None, ge=0)
    processState: dict[str, Any] = Field(default_factory=dict)


class ProcessModelCommand(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)
    baseProcessVersion: int = Field(default=0, ge=0)
    baseStateVersion: int | None = Field(default=None, ge=0)


class DocumentCreate(BaseModel):
    fileName: str
    contentType: str = "application/octet-stream"


class ReadStatusUpdate(BaseModel):
    readStatus: str
    readProgress: int = 0
