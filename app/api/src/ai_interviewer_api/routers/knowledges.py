from fastapi import APIRouter, Depends, HTTPException

from ai_interviewer_api.auth.deps import UserContext, get_current_user
from ai_interviewer_api.core.config import settings
from ai_interviewer_api.core.permissions import require_roles
from ai_interviewer_api.models.domain import Knowledge
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.models.interview_plan import STRUCTURED_INTERVIEW_MODEL_IDS
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.common import get_scoped_item
from ai_interviewer_api.schemas.requests import KnowledgeCreate, KnowledgeUpdate
from ai_interviewer_api.services.audit import write_audit_log
from ai_interviewer_api.services.ai_interview import summarize_knowledge_records

router = APIRouter(prefix="/api")


def enrich_knowledge(row: dict, user: UserContext) -> dict:
    knowledge_id = row["id"]
    enriched = dict(row)
    enriched["recordCount"] = len(
        [item for item in store.list("records", user.tenant_id) if item["knowledgeId"] == knowledge_id]
    )
    enriched["documentCount"] = len(
        [item for item in store.list("documents", user.tenant_id) if item["knowledgeId"] == knowledge_id]
    )
    enriched["fieldCount"] = len(
        [item for item in store.list("knowledge_fields", user.tenant_id) if item["knowledgeId"] == knowledge_id]
    )
    return enriched


@router.get("/knowledge-dbs/{knowledge_db_id}/knowledges")
def list_knowledges(knowledge_db_id: str, user: UserContext = Depends(get_current_user)) -> list[dict]:
    get_scoped_item("knowledge_dbs", knowledge_db_id, user, "knowledge_db_not_found")
    return [
        enrich_knowledge(row, user)
        for row in store.list("knowledges", user.tenant_id)
        if row["knowledgeDbId"] == knowledge_db_id
    ]


@router.post("/knowledge-dbs/{knowledge_db_id}/knowledges")
def create_knowledge(
    knowledge_db_id: str,
    payload: KnowledgeCreate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_roles(user, {"admin", "knowledge_manager"})
    get_scoped_item("knowledge_dbs", knowledge_db_id, user, "knowledge_db_not_found")
    item = Knowledge(
        tenantId=user.tenant_id,
        createdByUserId=user.user_id,
        updatedByUserId=user.user_id,
        knowledgeDbId=knowledge_db_id,
        **payload.model_dump(),
    )
    store.upsert("knowledges", item.model_dump())
    write_audit_log(user, "create", "knowledge", item.id, {"name": item.name, "knowledgeDbId": knowledge_db_id})
    return enrich_knowledge(item.model_dump(), user)


@router.get("/knowledges/{knowledge_id}")
def get_knowledge(knowledge_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    return enrich_knowledge(get_scoped_item("knowledges", knowledge_id, user, "knowledge_not_found"), user)


@router.patch("/knowledges/{knowledge_id}")
def update_knowledge(
    knowledge_id: str,
    payload: KnowledgeUpdate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_roles(user, {"admin", "knowledge_manager"})
    item = get_scoped_item("knowledges", knowledge_id, user, "knowledge_not_found")
    requested_updates = payload.model_dump(exclude_unset=True)
    if "interviewPlan" in requested_updates:
        current_profile = _resolve_interview_profile(item.get("interviewPlan"))
        requested_profile = _resolve_interview_profile(requested_updates.get("interviewPlan"))
        if current_profile != requested_profile and _has_started_interview_for_knowledge(knowledge_id, user):
            raise HTTPException(
                status_code=409,
                detail="interview_profile_change_not_allowed_after_start",
            )
        current_model = _resolve_interview_model_id(item.get("interviewPlan"))
        requested_model = _resolve_interview_model_id(requested_updates.get("interviewPlan"))
        if current_model != requested_model and _has_started_interview_for_knowledge(knowledge_id, user):
            raise HTTPException(
                status_code=409,
                detail="interview_model_change_not_allowed_after_start",
            )
    for key, value in requested_updates.items():
        item[key] = value
    item["updatedByUserId"] = user.user_id
    item["updatedAt"] = utc_now()
    store.upsert("knowledges", item)
    write_audit_log(user, "update", "knowledge", knowledge_id, payload.model_dump(exclude_unset=True))
    return enrich_knowledge(item, user)


def _resolve_interview_profile(plan: object) -> str:
    if isinstance(plan, dict):
        profile = plan.get("profile")
        if profile in {"fixed_form", "business_process", "system_requirement"}:
            return str(profile)
    return "fixed_form"


def _resolve_interview_model_id(plan: object) -> str:
    if isinstance(plan, dict):
        model_id = plan.get("modelId")
        if model_id in STRUCTURED_INTERVIEW_MODEL_IDS:
            return str(model_id)
    return settings.structured_interview_model_id


def _has_started_interview_for_knowledge(knowledge_id: str, user: UserContext) -> bool:
    record_ids = {
        row.get("id")
        for row in store.list("records", user.tenant_id)
        if row.get("knowledgeId") == knowledge_id
    }
    return any(
        row.get("recordId") in record_ids
        and (
            bool(row.get("askedQuestions"))
            or bool(row.get("lastProcessedUserMessageId"))
            or bool(row.get("currentQuestionId"))
        )
        for row in store.list("interview_states", user.tenant_id)
    )


@router.post("/knowledges/{knowledge_id}/record-summary-draft")
def create_record_summary_draft(
    knowledge_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_roles(user, {"admin", "knowledge_manager", "interviewer"})
    knowledge = get_scoped_item("knowledges", knowledge_id, user, "knowledge_not_found")
    return {
        "summary": summarize_knowledge_records(knowledge, user),
        "status": "draft",
    }


@router.delete("/knowledges/{knowledge_id}")
def delete_knowledge(knowledge_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    require_roles(user, {"admin", "knowledge_manager"})
    get_scoped_item("knowledges", knowledge_id, user, "knowledge_not_found")
    store.delete("knowledges", knowledge_id)
    write_audit_log(user, "delete", "knowledge", knowledge_id, {})
    return {"deleted": True}
