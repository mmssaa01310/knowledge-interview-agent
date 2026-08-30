from fastapi import HTTPException

from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.core.permissions import (
    MANAGEMENT_ROLES,
    ensure_record_access,
    ensure_tenant_scope,
)
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.repositories.store import store


APPROVABLE_STATUSES = {"draft", "needs_review"}
MIN_APPROVAL_CONFIDENCE = 0.7


def get_scoped_item(table: str, item_id: str, user: UserContext, not_found_detail: str) -> dict:
    item = store.get(table, item_id)
    if not item:
        raise HTTPException(status_code=404, detail=not_found_detail)
    ensure_tenant_scope(user, item["tenantId"])
    if table == "records":
        ensure_record_access(item, user, operation="read")
    return item


def ensure_interviewer_knowledge_db_access(knowledge_db: dict, user: UserContext) -> None:
    """Limit self-service users to active knowledge areas in their tenant."""
    ensure_tenant_scope(user, knowledge_db["tenantId"])
    if user.role == "interviewer" and knowledge_db.get("status", "active") != "active":
        raise HTTPException(status_code=404, detail="knowledge_db_not_found")


def ensure_interviewer_knowledge_access(knowledge: dict, user: UserContext) -> None:
    """Limit self-service users to active knowledge definitions in active areas."""
    ensure_tenant_scope(user, knowledge["tenantId"])
    if user.role != "interviewer":
        return
    if knowledge.get("status", "active") != "active":
        raise HTTPException(status_code=404, detail="knowledge_not_found")

    knowledge_db = store.get("knowledge_dbs", knowledge["knowledgeDbId"])
    if not knowledge_db:
        raise HTTPException(status_code=404, detail="knowledge_not_found")
    ensure_interviewer_knowledge_db_access(knowledge_db, user)


def interview_context_knowledge(knowledge: dict, user: UserContext) -> dict:
    """Return the knowledge fields that an interview participant may see."""
    result = dict(knowledge)
    result.setdefault("tags", [])
    if user.role in MANAGEMENT_ROLES:
        return result

    result.pop("systemPrompt", None)
    result.pop("defaultModelId", None)
    plan = result.get("interviewPlan")
    if isinstance(plan, dict):
        result["interviewPlan"] = {
            "profile": plan.get("profile"),
            "modelId": plan.get("modelId"),
        }
    return result


def interview_context_fields(fields: list[dict], user: UserContext) -> list[dict]:
    """Return the field metadata required to conduct an interview."""
    if user.role in MANAGEMENT_ROLES:
        return fields

    public_keys = {
        "id",
        "tenantId",
        "createdByUserId",
        "updatedByUserId",
        "knowledgeId",
        "name",
        "description",
        "inputType",
        "required",
        "askByAi",
        "aiQuestionExamples",
        "options",
        "displayOrder",
    }
    return [{key: value for key, value in field.items() if key in public_keys} for field in fields]


def proposal_skip_reason(proposal: dict) -> str | None:
    if proposal["status"] not in APPROVABLE_STATUSES:
        return "status_not_approvable"
    if proposal.get("confidence", 0) < MIN_APPROVAL_CONFIDENCE:
        return "confidence_too_low"
    if not proposal.get("structuredData"):
        return "required_data_missing"
    return None


def approve_proposal_item(proposal: dict, user: UserContext, approval_method: str) -> dict:
    proposal["status"] = "approved"
    proposal["approvalMethod"] = approval_method
    proposal["updatedByUserId"] = user.user_id
    proposal["updatedAt"] = utc_now()
    store.upsert("proposals", proposal)
    return proposal
