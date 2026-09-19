import logging
from time import monotonic, time

from fastapi import APIRouter, Depends

from ai_interviewer_api.auth.deps import UserContext, get_current_user
from ai_interviewer_api.core.interview_configuration import require_interview_configuration
from ai_interviewer_api.core.permissions import require_record_action
from ai_interviewer_api.routers.common import ensure_interviewer_knowledge_access, get_scoped_item
from ai_interviewer_api.schemas.live import (
    LiveDelegationCreate,
    LiveCaptureCreate,
    LiveSessionCreate,
    LiveSessionResponse,
)
from ai_interviewer_api.services.live_interview import (
    build_live_interview_context,
    process_live_delegation,
)
from ai_interviewer_api.services.live_capture import process_live_capture
from ai_interviewer_api.services.live_session import create_live_session


router = APIRouter(prefix="/api/live")
logger = logging.getLogger(__name__)


@router.post("/captures")
def capture_live_transcript(
    payload: LiveCaptureCreate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    record = get_scoped_item("records", payload.record_id, user, "record_not_found")
    require_record_action(record, user, "interview")
    knowledge = get_scoped_item("knowledges", record["knowledgeId"], user, "knowledge_not_found")
    ensure_interviewer_knowledge_access(knowledge, user)
    require_interview_configuration(knowledge)
    return process_live_capture(payload, record=record, knowledge=knowledge, user=user)


@router.post("/sessions", response_model=LiveSessionResponse)
def create_live_web_session(
    payload: LiveSessionCreate,
    user: UserContext = Depends(get_current_user),
) -> LiveSessionResponse:
    started_at = monotonic()
    logger.info(
        "voice_startup_stage source=api voice_session_id=- provider=gpt_live "
        "stage=live_route_request_received monotonic_ms=%s timestamp_ms=%s",
        round(monotonic() * 1000, 3),
        round(time() * 1000),
    )
    interview_context = None
    if payload.record_id:
        record = get_scoped_item("records", payload.record_id, user, "record_not_found")
        require_record_action(record, user, "interview")
        knowledge = get_scoped_item(
            "knowledges",
            record["knowledgeId"],
            user,
            "knowledge_not_found",
        )
        ensure_interviewer_knowledge_access(knowledge, user)
        require_interview_configuration(knowledge)
        context_started_at = monotonic()
        interview_context = build_live_interview_context(record, knowledge, user)
        logger.info(
            "voice_startup_stage source=api voice_session_id=- provider=gpt_live "
            "stage=live_interview_context_ready monotonic_ms=%s timestamp_ms=%s elapsed_ms=%s",
            round(monotonic() * 1000, 3),
            round(time() * 1000),
            round((monotonic() - context_started_at) * 1000, 1),
        )
    logger.info(
        "voice_startup_stage source=api voice_session_id=- provider=gpt_live "
        "stage=live_route_validation_ready monotonic_ms=%s timestamp_ms=%s elapsed_ms=%s",
        round(monotonic() * 1000, 3),
        round(time() * 1000),
        round((monotonic() - started_at) * 1000, 1),
    )
    return create_live_session(payload, interview_context=interview_context)


@router.post("/delegations")
def apply_live_delegation(
    payload: LiveDelegationCreate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    record = get_scoped_item("records", payload.record_id, user, "record_not_found")
    require_record_action(record, user, "interview")
    knowledge = get_scoped_item(
        "knowledges",
        record["knowledgeId"],
        user,
        "knowledge_not_found",
    )
    ensure_interviewer_knowledge_access(knowledge, user)
    require_interview_configuration(knowledge)
    return process_live_delegation(payload, record=record, user=user)
