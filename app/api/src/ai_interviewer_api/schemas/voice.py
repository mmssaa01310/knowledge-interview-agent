from typing import Literal

from pydantic import BaseModel, Field


class VoiceSessionCreate(BaseModel):
    provider: Literal["nova_sonic", "transcribe_polly"] = "transcribe_polly"


class VoiceTurnCreate(BaseModel):
    transcript: str
    turnType: Literal["ANSWER", "CONTROL"] = "ANSWER"
    answerToQuestionId: str | None = None
    clientTurnId: str | None = None
    expectedStateVersion: int | None = None
    startedAtMs: int | None = None
    endedAtMs: int | None = None


class VoiceTurnCancel(BaseModel):
    clientTurnId: str
    expectedStateVersion: int


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
