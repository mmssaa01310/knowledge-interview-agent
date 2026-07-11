from fastapi import HTTPException

from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.core.permissions import ensure_tenant_scope
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.repositories.store import store


APPROVABLE_STATUSES = {"draft", "needs_review"}
MIN_APPROVAL_CONFIDENCE = 0.7


def get_scoped_item(table: str, item_id: str, user: UserContext, not_found_detail: str) -> dict:
    item = store.get(table, item_id)
    if not item:
        raise HTTPException(status_code=404, detail=not_found_detail)
    ensure_tenant_scope(user, item["tenantId"])
    return item


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
    if proposal.get("proposalType") == "record_summary":
        record = get_scoped_item("records", proposal["recordId"], user, "record_not_found")
        record["summary"] = str(proposal.get("structuredData", {}).get("summary", "")).strip()
        record["updatedByUserId"] = user.user_id
        record["updatedAt"] = utc_now()
        store.upsert("records", record)
    return proposal
