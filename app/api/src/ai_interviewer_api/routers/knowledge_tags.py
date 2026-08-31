from fastapi import APIRouter, Depends, HTTPException

from ai_interviewer_api.auth.deps import UserContext, get_current_user
from ai_interviewer_api.core.permissions import require_management_role
from ai_interviewer_api.repositories.knowledge_tags import (
    delete_knowledge_tag as repository_delete_knowledge_tag,
    rename_knowledge_tag,
    register_knowledge_tags,
    sync_knowledge_tags_from_knowledges,
)
from ai_interviewer_api.schemas.requests import KnowledgeTagCreate, KnowledgeTagUpdate
from ai_interviewer_api.services.audit import write_audit_log
from ai_interviewer_api.services.knowledge_tags import (
    KnowledgeTagValidationError,
    normalize_knowledge_tag,
)

router = APIRouter(prefix="/api")


@router.get("/knowledge-tags")
def list_knowledge_tags(user: UserContext = Depends(get_current_user)) -> list[dict]:
    require_management_role(user)
    # 既存データを初回表示時に取り込み、過去に作成済みのタグも候補から失わない。
    return sync_knowledge_tags_from_knowledges(user.tenant_id, user.user_id)


@router.post("/knowledge-tags")
def create_knowledge_tag(
    payload: KnowledgeTagCreate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_management_role(user)
    try:
        name = normalize_knowledge_tag(payload.name)
    except KnowledgeTagValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not name:
        raise HTTPException(status_code=422, detail="knowledge_tag_required")

    tags = register_knowledge_tags(user.tenant_id, user.user_id, [name])
    write_audit_log(user, "create", "knowledge_tag", name, {"name": name})
    return next(tag for tag in tags if tag.get("name", "").casefold() == name.casefold())


@router.patch("/knowledge-tags/{tag_id}")
def update_knowledge_tag(
    tag_id: str,
    payload: KnowledgeTagUpdate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_management_role(user)
    try:
        updated = rename_knowledge_tag(user.tenant_id, user.user_id, tag_id, payload.name)
    except ValueError as error:
        if str(error) == "knowledge_tag_already_exists":
            raise HTTPException(status_code=409, detail=str(error)) from error
        raise HTTPException(status_code=422, detail=str(error)) from error
    if updated is None:
        raise HTTPException(status_code=404, detail="knowledge_tag_not_found")
    write_audit_log(user, "update", "knowledge_tag", tag_id, {"name": updated["name"]})
    return updated


@router.delete("/knowledge-tags/{tag_id}")
def delete_knowledge_tag(tag_id: str, user: UserContext = Depends(get_current_user)) -> dict[str, bool]:
    require_management_role(user)
    deleted = repository_delete_knowledge_tag(user.tenant_id, user.user_id, tag_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail="knowledge_tag_not_found")
    write_audit_log(user, "delete", "knowledge_tag", tag_id, {"name": deleted.get("name")})
    return {"deleted": True}


__all__ = ["create_knowledge_tag", "delete_knowledge_tag", "list_knowledge_tags", "router", "update_knowledge_tag"]
