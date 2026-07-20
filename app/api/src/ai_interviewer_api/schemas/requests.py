from pydantic import BaseModel, Field


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
    summary: str | None = None
    systemPrompt: str | None = None
    purpose: str | None = None
    category: str | None = None
    targetBusiness: str | None = None
    targetEquipment: str | None = None
    language: str = "ja"
    defaultModelId: str | None = None


class KnowledgeUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    summary: str | None = None
    systemPrompt: str | None = None
    purpose: str | None = None
    category: str | None = None
    targetBusiness: str | None = None
    targetEquipment: str | None = None
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


class RecordUpdate(BaseModel):
    title: str | None = None
    status: str | None = None
    targetEquipment: str | None = None
    targetProcess: str | None = None
    summary: str | None = None


class InterviewAnswerUpdate(BaseModel):
    answerSummary: str = Field(min_length=1, max_length=10000)


class BulkApproveRequest(BaseModel):
    recordIds: list[str]


class ChatMessageCreate(BaseModel):
    content: str
    answerToQuestionId: str | None = None
    modelId: str | None = None
    referenceKnowledgeDbIds: list[str] = Field(default_factory=list)
    referenceKnowledgeIds: list[str] = Field(default_factory=list)
    referenceDocumentIds: list[str] = Field(default_factory=list)
    excludedDocumentIds: list[str] = Field(default_factory=list)
    searchLimit: int = 5
    confidenceThreshold: float = 0.7


class DocumentCreate(BaseModel):
    fileName: str
    contentType: str = "application/octet-stream"


class ReadStatusUpdate(BaseModel):
    readStatus: str
    readProgress: int = 0
