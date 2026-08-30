from pydantic import BaseModel


class AssistantReply(BaseModel):
    turn_id: str
    response_id: str
    text: str
    action: str
    question_id: str | None
    state_version: int


class VoiceRuntimeContext(BaseModel):
    voice_session_id: str
    record_id: str
    provider: str
    interview_locale: str = "ja-JP"
