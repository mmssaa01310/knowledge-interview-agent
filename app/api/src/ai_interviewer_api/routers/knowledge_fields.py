from fastapi import APIRouter, Depends

from ai_interviewer_api.auth.deps import UserContext, get_current_user
from ai_interviewer_api.core.permissions import require_knowledge_read_role, require_management_role
from ai_interviewer_api.models.domain import KnowledgeField
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.common import (
    ensure_interviewer_knowledge_access,
    get_scoped_item,
    interview_context_fields,
)
from ai_interviewer_api.schemas.requests import FieldSuggestionRequest, KnowledgeFieldCreate, KnowledgeFieldUpdate
from ai_interviewer_api.services.field_suggestions import suggest_fields_with_bedrock

router = APIRouter(prefix="/api")


@router.get("/knowledges/{knowledge_id}/fields")
def list_fields(knowledge_id: str, user: UserContext = Depends(get_current_user)) -> list[dict]:
    require_knowledge_read_role(user)
    knowledge = get_scoped_item("knowledges", knowledge_id, user, "knowledge_not_found")
    ensure_interviewer_knowledge_access(knowledge, user)
    fields = [
        row for row in store.list("knowledge_fields", user.tenant_id) if row["knowledgeId"] == knowledge_id
    ]
    return interview_context_fields(fields, user)


@router.post("/knowledges/{knowledge_id}/fields")
def create_field(
    knowledge_id: str,
    payload: KnowledgeFieldCreate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_management_role(user)
    get_scoped_item("knowledges", knowledge_id, user, "knowledge_not_found")
    field_data = payload.model_dump()
    # 質問項目はすべてAIが質問する。旧クライアントからfalseが送られても、
    # 保存データの意味を現在の仕様へ統一する。
    field_data["askByAi"] = True
    item = KnowledgeField(
        tenantId=user.tenant_id,
        createdByUserId=user.user_id,
        updatedByUserId=user.user_id,
        knowledgeId=knowledge_id,
        **field_data,
    )
    store.upsert("knowledge_fields", item.model_dump())
    return item.model_dump()


@router.post("/knowledges/{knowledge_id}/generate-fields")
def generate_fields(knowledge_id: str, user: UserContext = Depends(get_current_user)) -> list[dict]:
    require_management_role(user)
    get_scoped_item("knowledges", knowledge_id, user, "knowledge_not_found")
    return [
        {
            "name": "設備名",
            "inputType": "short_text",
            "required": True,
            "askByAi": True,
            "displayOrder": 1,
            "aiQuestionExamples": ["対象設備を教えてください。"],
        },
        {
            "name": "現象 / 症状",
            "inputType": "long_text",
            "required": True,
            "askByAi": True,
            "displayOrder": 2,
            "aiQuestionExamples": ["どのような現象が起きていますか？"],
        },
        {
            "name": "対処方法",
            "inputType": "long_text",
            "required": False,
            "askByAi": True,
            "displayOrder": 3,
            "aiQuestionExamples": ["どの処置で復旧しましたか？"],
        },
    ]


@router.post("/knowledges/{knowledge_id}/field-suggestions")
def suggest_fields(
    knowledge_id: str,
    payload: FieldSuggestionRequest,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_management_role(user)
    get_scoped_item("knowledges", knowledge_id, user, "knowledge_not_found")
    return suggest_fields_with_bedrock(payload, user, knowledge_id=knowledge_id)


@router.patch("/knowledge-fields/{field_id}")
def update_field(
    field_id: str,
    payload: KnowledgeFieldUpdate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_management_role(user)
    item = get_scoped_item("knowledge_fields", field_id, user, "knowledge_field_not_found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        if key == "askByAi":
            continue
        item[key] = value
    item["askByAi"] = True
    item["updatedByUserId"] = user.user_id
    store.upsert("knowledge_fields", item)
    return item


@router.delete("/knowledge-fields/{field_id}")
def delete_field(field_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    require_management_role(user)
    get_scoped_item("knowledge_fields", field_id, user, "knowledge_field_not_found")
    store.delete("knowledge_fields", field_id)
    return {"deleted": True}
