from fastapi import APIRouter, Depends

from ai_interviewer_api.auth.deps import UserContext, get_current_user
from ai_interviewer_api.core.interview_configuration import require_interview_configuration
from ai_interviewer_api.core.permissions import require_record_action
from ai_interviewer_api.routers.common import ensure_interviewer_knowledge_access, get_scoped_item
from ai_interviewer_api.schemas.live import (
    LiveDelegationCreate,
    LiveSessionCreate,
    LiveSessionResponse,
)
from ai_interviewer_api.services.live_interview import (
    build_live_interview_context,
    process_live_delegation,
)
from ai_interviewer_api.services.live_session import create_live_session


router = APIRouter(prefix="/api/live")


@router.post("/sessions", response_model=LiveSessionResponse)
def create_live_web_session(
    payload: LiveSessionCreate,
    user: UserContext = Depends(get_current_user),
) -> LiveSessionResponse:
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
        interview_context = build_live_interview_context(record, knowledge, user)
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
