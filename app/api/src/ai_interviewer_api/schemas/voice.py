from pydantic import BaseModel, Field


class VoiceSessionCreate(BaseModel):
    provider: str = "nova_sonic"


class VoiceTurnCreate(BaseModel):
    transcript: str
    answerToQuestionId: str | None = None
    startedAtMs: int | None = None
    endedAtMs: int | None = None


class AssistantEventCreate(BaseModel):
    eventType: str
    responseId: str | None = None
    generation: int | None = None
    transcript: str | None = None
    detail: dict = Field(default_factory=dict)


class ConnectionEventCreate(BaseModel):
    eventType: str
    connectionStatus: str | None = None
    detail: dict = Field(default_factory=dict)
