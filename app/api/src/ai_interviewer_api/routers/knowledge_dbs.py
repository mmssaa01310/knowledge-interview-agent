from fastapi import APIRouter, Depends

from ai_interviewer_api.auth.deps import UserContext, get_current_user
from ai_interviewer_api.core.permissions import require_management_role
from ai_interviewer_api.models.domain import KnowledgeDb
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.common import get_scoped_item
from ai_interviewer_api.schemas.requests import KnowledgeDbCreate, KnowledgeDbUpdate
from ai_interviewer_api.services.audit import write_audit_log

router = APIRouter(prefix="/api")


@router.get("/knowledge-dbs")
def list_knowledge_dbs(user: UserContext = Depends(get_current_user)) -> list[dict]:
    require_management_role(user)
    return [_enrich_knowledge_db(row, user) for row in store.list("knowledge_dbs", user.tenant_id)]


@router.post("/knowledge-dbs")
def create_knowledge_db(payload: KnowledgeDbCreate, user: UserContext = Depends(get_current_user)) -> dict:
    require_management_role(user)
    item = KnowledgeDb(
        tenantId=user.tenant_id,
        createdByUserId=user.user_id,
        updatedByUserId=user.user_id,
        **payload.model_dump(),
    )
    store.upsert("knowledge_dbs", item.model_dump())
    write_audit_log(user, "create", "knowledge_db", item.id, {"name": item.name})
    return _enrich_knowledge_db(item.model_dump(), user)


@router.get("/knowledge-dbs/{knowledge_db_id}")
def get_knowledge_db(knowledge_db_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    require_management_role(user)
    return _enrich_knowledge_db(get_scoped_item("knowledge_dbs", knowledge_db_id, user, "knowledge_db_not_found"), user)


@router.patch("/knowledge-dbs/{knowledge_db_id}")
def update_knowledge_db(
    knowledge_db_id: str,
    payload: KnowledgeDbUpdate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_management_role(user)
    item = get_knowledge_db(knowledge_db_id, user)
    for key, value in payload.model_dump(exclude_unset=True).items():
        item[key] = value
    item["updatedByUserId"] = user.user_id
    store.upsert("knowledge_dbs", item)
    write_audit_log(user, "update", "knowledge_db", knowledge_db_id, payload.model_dump(exclude_unset=True))
    return _enrich_knowledge_db(item, user)


@router.delete("/knowledge-dbs/{knowledge_db_id}")
def delete_knowledge_db(knowledge_db_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    require_management_role(user)
    get_knowledge_db(knowledge_db_id, user)
    store.delete("knowledge_dbs", knowledge_db_id)
    write_audit_log(user, "delete", "knowledge_db", knowledge_db_id, {})
    return {"deleted": True}


def _enrich_knowledge_db(row: dict, user: UserContext) -> dict:
    enriched = dict(row)
    enriched["knowledgeCount"] = len(
        [item for item in store.list("knowledges", user.tenant_id) if item["knowledgeDbId"] == row["id"]]
    )
    return enriched
