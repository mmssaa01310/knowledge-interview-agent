from fastapi import APIRouter, Depends

from ai_interviewer_api.auth.deps import UserContext, get_current_user
from ai_interviewer_api.core.permissions import require_roles
from ai_interviewer_api.models.domain import Knowledge
from ai_interviewer_api.models.base import utc_now
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
    for key, value in payload.model_dump(exclude_unset=True).items():
        item[key] = value
    item["updatedByUserId"] = user.user_id
    item["updatedAt"] = utc_now()
    store.upsert("knowledges", item)
    write_audit_log(user, "update", "knowledge", knowledge_id, payload.model_dump(exclude_unset=True))
    return enrich_knowledge(item, user)


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
