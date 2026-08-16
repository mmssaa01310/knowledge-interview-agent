from fastapi import APIRouter, Depends, Header, HTTPException

from ai_interviewer_api.core.config import settings
from ai_interviewer_api.schemas.voice import (
    AssistantEventCreate,
    ConnectionEventCreate,
    VoiceTurnIntentCreate,
    VoiceTurnCancel,
    VoiceTurnCreate,
)
from ai_interviewer_api.services.voice_interview import (
    cancel_voice_turn,
    claim_initial_reply,
    create_assistant_event,
    create_connection_event,
    create_voice_turn,
    classify_voice_turn_intent,
    mark_initial_reply_failed,
    mark_initial_reply_sent,
    process_voice_turn,
)
from ai_interviewer_api.services.voice_interview import (
    get_internal_voice_session as get_internal_voice_session_service,
)

router = APIRouter()


def require_internal_api_token(x_internal_api_token: str | None = Header(default=None)) -> None:
    if x_internal_api_token != settings.internal_api_token:
        raise HTTPException(status_code=401, detail="invalid_internal_api_token")


@router.post("/internal/voice-sessions/{voice_session_id}/turns")
def create_internal_voice_turn(
    voice_session_id: str,
    payload: VoiceTurnCreate,
    _: None = Depends(require_internal_api_token),
) -> dict:
    return create_voice_turn(voice_session_id, payload)


@router.post("/internal/voice-sessions/{voice_session_id}/turn-intent")
def classify_internal_voice_turn_intent(
    voice_session_id: str,
    payload: VoiceTurnIntentCreate,
    _: None = Depends(require_internal_api_token),
) -> dict:
    return classify_voice_turn_intent(voice_session_id, payload)


@router.post("/internal/voice-sessions/{voice_session_id}/turns/cancel")
def cancel_internal_voice_turn(
    voice_session_id: str,
    payload: VoiceTurnCancel,
    _: None = Depends(require_internal_api_token),
) -> dict:
    return cancel_voice_turn(voice_session_id, payload)


@router.get("/internal/voice-sessions/{voice_session_id}")
def get_internal_voice_session(
    voice_session_id: str,
    _: None = Depends(require_internal_api_token),
) -> dict:
    return get_internal_voice_session_service(voice_session_id)


@router.post("/internal/voice-sessions/{voice_session_id}/initial-reply-sent")
def mark_internal_initial_reply_sent(
    voice_session_id: str,
    _: None = Depends(require_internal_api_token),
) -> dict:
    return mark_initial_reply_sent(voice_session_id)


@router.post("/internal/voice-sessions/{voice_session_id}/initial-reply-failed")
def mark_internal_initial_reply_failed(
    voice_session_id: str,
    _: None = Depends(require_internal_api_token),
) -> dict:
    return mark_initial_reply_failed(voice_session_id, retryable=True)


@router.post("/internal/voice-sessions/{voice_session_id}/initial-reply/claim")
def claim_internal_initial_reply(
    voice_session_id: str,
    _: None = Depends(require_internal_api_token),
) -> dict:
    return claim_initial_reply(voice_session_id)


@router.post("/internal/voice-sessions/{voice_session_id}/turns/{turn_id}/process")
def process_internal_voice_turn(
    voice_session_id: str,
    turn_id: str,
    _: None = Depends(require_internal_api_token),
) -> dict:
    return process_voice_turn(voice_session_id, turn_id)


@router.post("/internal/voice-sessions/{voice_session_id}/assistant-events")
def create_internal_assistant_event(
    voice_session_id: str,
    payload: AssistantEventCreate,
    _: None = Depends(require_internal_api_token),
) -> dict:
    return create_assistant_event(voice_session_id, payload)


@router.post("/internal/voice-sessions/{voice_session_id}/connection-events")
def create_internal_connection_event(
    voice_session_id: str,
    payload: ConnectionEventCreate,
    _: None = Depends(require_internal_api_token),
) -> dict:
    return create_connection_event(voice_session_id, payload)
