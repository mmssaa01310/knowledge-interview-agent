from fastapi import APIRouter, Depends

from ai_interviewer_api.auth.deps import UserContext, get_current_user
from ai_interviewer_api.schemas.voice import VoiceSessionCreate
from ai_interviewer_api.services.voice_interview import (
    create_voice_session,
    get_voice_session,
    stop_voice_session,
)


router = APIRouter(prefix="/api")


@router.post("/records/{record_id}/voice-sessions")
def create_record_voice_session(
    record_id: str,
    payload: VoiceSessionCreate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    return create_voice_session(record_id, payload, user)


@router.get("/voice-sessions/{voice_session_id}")
def get_record_voice_session(
    voice_session_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    return get_voice_session(voice_session_id, user)


@router.post("/voice-sessions/{voice_session_id}/stop")
def stop_record_voice_session(
    voice_session_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    return stop_voice_session(voice_session_id, user)
