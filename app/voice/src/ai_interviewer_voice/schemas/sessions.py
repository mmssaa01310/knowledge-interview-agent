from pydantic import BaseModel, Field


class AssistantReply(BaseModel):
    turn_id: str
    response_id: str
    text: str
    action: str
    question_id: str | None
    state_version: int
    latency_metrics: dict[str, float | int] = Field(default_factory=dict)


class VoiceRuntimeContext(BaseModel):
    voice_session_id: str
    record_id: str
    provider: str
    interview_locale: str = "ja-JP"
