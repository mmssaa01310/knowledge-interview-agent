from fastapi import APIRouter, Depends, HTTPException

from ai_interviewer_api.auth.deps import UserContext, get_current_user
from ai_interviewer_api.core.permissions import (
    is_active_record,
    require_knowledge_read_role,
    require_management_role,
)
from ai_interviewer_api.models.domain import Knowledge
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.repositories.knowledge_tags import register_knowledge_tags
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.common import (
    ensure_interviewer_knowledge_access,
    ensure_interviewer_knowledge_db_access,
    get_scoped_item,
    interview_context_knowledge,
)
from ai_interviewer_api.schemas.requests import KnowledgeCreate, KnowledgeUpdate
from ai_interviewer_api.services.audit import write_audit_log
from ai_interviewer_api.services.knowledge_tags import (
    KnowledgeTagValidationError,
    normalize_knowledge_tags,
)

router = APIRouter(prefix="/api")


def enrich_knowledge(row: dict, user: UserContext) -> dict:
    knowledge_id = row["id"]
    enriched = interview_context_knowledge(row, user)
    enriched.setdefault("tags", [])
    records = [
        item
        for item in store.list("records", user.tenant_id)
        if item["knowledgeId"] == knowledge_id and is_active_record(item)
    ]
    if user.role == "interviewer":
        records = [item for item in records if item.get("ownerUserId") == user.user_id]
    enriched["recordCount"] = len(records)
    enriched["documentCount"] = len(
        [item for item in store.list("documents", user.tenant_id) if item["knowledgeId"] == knowledge_id]
    )
    enriched["fieldCount"] = len(
        [item for item in store.list("knowledge_fields", user.tenant_id) if item["knowledgeId"] == knowledge_id]
    )
    return enriched


@router.get("/knowledge-dbs/{knowledge_db_id}/knowledges")
def list_knowledges(knowledge_db_id: str, user: UserContext = Depends(get_current_user)) -> list[dict]:
    require_knowledge_read_role(user)
    knowledge_db = get_scoped_item("knowledge_dbs", knowledge_db_id, user, "knowledge_db_not_found")
    ensure_interviewer_knowledge_db_access(knowledge_db, user)
    rows = [
        row
        for row in store.list("knowledges", user.tenant_id)
        if row["knowledgeDbId"] == knowledge_db_id
    ]
    if user.role == "interviewer":
        rows = [row for row in rows if row.get("status", "active") == "active"]
    return [
        enrich_knowledge(row, user)
        for row in rows
    ]


@router.post("/knowledge-dbs/{knowledge_db_id}/knowledges")
def create_knowledge(
    knowledge_db_id: str,
    payload: KnowledgeCreate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_management_role(user)
    get_scoped_item("knowledge_dbs", knowledge_db_id, user, "knowledge_db_not_found")
    payload_data = payload.model_dump()
    payload_data["tags"] = _normalize_tags_for_api(payload_data.get("tags"))
    register_knowledge_tags(user.tenant_id, user.user_id, payload_data["tags"])
    item = Knowledge(
        tenantId=user.tenant_id,
        createdByUserId=user.user_id,
        updatedByUserId=user.user_id,
        knowledgeDbId=knowledge_db_id,
        **payload_data,
    )
    store.upsert("knowledges", item.model_dump())
    write_audit_log(user, "create", "knowledge", item.id, {"name": item.name, "knowledgeDbId": knowledge_db_id})
    return enrich_knowledge(item.model_dump(), user)


@router.get("/knowledges/{knowledge_id}")
def get_knowledge(knowledge_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    require_knowledge_read_role(user)
    item = get_scoped_item("knowledges", knowledge_id, user, "knowledge_not_found")
    ensure_interviewer_knowledge_access(item, user)
    return enrich_knowledge(item, user)


@router.patch("/knowledges/{knowledge_id}")
def update_knowledge(
    knowledge_id: str,
    payload: KnowledgeUpdate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_management_role(user)
    item = get_scoped_item("knowledges", knowledge_id, user, "knowledge_not_found")
    requested_updates = payload.model_dump(exclude_unset=True)
    if "tags" in requested_updates:
        requested_updates["tags"] = _normalize_tags_for_api(requested_updates["tags"])
        register_knowledge_tags(user.tenant_id, user.user_id, requested_updates["tags"])
    if "interviewPlan" in requested_updates:
        current_profile = _resolve_interview_profile(item.get("interviewPlan"))
        requested_profile = _resolve_interview_profile(requested_updates.get("interviewPlan"))
        if current_profile != requested_profile and _has_started_interview_for_knowledge(knowledge_id, user):
            raise HTTPException(
                status_code=409,
                detail="interview_profile_change_not_allowed_after_start",
            )
    for key, value in requested_updates.items():
        item[key] = value
    item["updatedByUserId"] = user.user_id
    item["updatedAt"] = utc_now()
    store.upsert("knowledges", item)
    write_audit_log(user, "update", "knowledge", knowledge_id, payload.model_dump(exclude_unset=True))
    return enrich_knowledge(item, user)


def _normalize_tags_for_api(tags: list[str] | None) -> list[str]:
    try:
        return normalize_knowledge_tags(tags)
    except KnowledgeTagValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _resolve_interview_profile(plan: object) -> str:
    if isinstance(plan, dict):
        profile = plan.get("profile")
        if profile in {"fixed_form", "business_process", "system_requirement"}:
            return str(profile)
    return "fixed_form"


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


@router.delete("/knowledges/{knowledge_id}")
def delete_knowledge(knowledge_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    require_management_role(user)
    get_scoped_item("knowledges", knowledge_id, user, "knowledge_not_found")
    store.delete("knowledges", knowledge_id)
    write_audit_log(user, "delete", "knowledge", knowledge_id, {})
    return {"deleted": True}
