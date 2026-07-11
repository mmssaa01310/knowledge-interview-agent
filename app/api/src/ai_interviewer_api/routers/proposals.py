from fastapi import APIRouter, Depends, HTTPException

from ai_interviewer_api.auth.deps import UserContext, get_current_user
from ai_interviewer_api.core.permissions import require_roles
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.common import approve_proposal_item, get_scoped_item, proposal_skip_reason
from ai_interviewer_api.schemas.requests import BulkApproveRequest
from ai_interviewer_api.services.audit import write_audit_log

router = APIRouter(prefix="/api")


@router.get("/records/{record_id}/proposals")
def list_proposals(record_id: str, user: UserContext = Depends(get_current_user)) -> list[dict]:
    get_scoped_item("records", record_id, user, "record_not_found")
    return [row for row in store.list("proposals", user.tenant_id) if row["recordId"] == record_id]


@router.post("/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    require_roles(user, {"admin", "knowledge_manager", "interviewer"})
    proposal = get_scoped_item("proposals", proposal_id, user, "proposal_not_found")
    skip_reason = proposal_skip_reason(proposal)
    if skip_reason:
        raise HTTPException(status_code=409, detail=skip_reason)
    proposal = approve_proposal_item(proposal, user, "single")
    write_audit_log(
        user,
        "approve_single",
        "proposal",
        proposal_id,
        {"approvalMethod": "single", "status": "approved", "aiProposalValue": proposal["structuredData"]},
    )
    return proposal


@router.post("/records/{record_id}/approve-all-proposals")
def approve_all(record_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    require_roles(user, {"admin", "knowledge_manager"})
    get_scoped_item("records", record_id, user, "record_not_found")
    proposals = [row for row in store.list("proposals", user.tenant_id) if row["recordId"] == record_id]
    approved = 0
    skipped_items = []
    for proposal in proposals:
        skip_reason = proposal_skip_reason(proposal)
        if skip_reason:
            skipped_items.append({"proposalId": proposal["id"], "reason": skip_reason})
            continue
        approve_proposal_item(proposal, user, "record_bulk")
        approved += 1
    write_audit_log(
        user,
        "approve_record_bulk",
        "record",
        record_id,
        {
            "approvalMethod": "record_bulk",
            "successCount": approved,
            "failedCount": 0,
            "failureReasons": skipped_items,
        },
    )
    return {
        "approvedCount": approved,
        "skippedCount": len(skipped_items),
        "failedCount": 0,
        "skippedItems": skipped_items,
        "failedItems": [],
    }


@router.post("/records/bulk-approve")
def bulk_approve(payload: BulkApproveRequest, user: UserContext = Depends(get_current_user)) -> dict:
    require_roles(user, {"admin", "knowledge_manager"})
    total_approved = 0
    results = []
    for record_id in payload.recordIds:
        get_scoped_item("records", record_id, user, "record_not_found")
        proposals = [row for row in store.list("proposals", user.tenant_id) if row["recordId"] == record_id]
        approved = 0
        skipped_items = []
        for proposal in proposals:
            skip_reason = proposal_skip_reason(proposal)
            if skip_reason:
                skipped_items.append({"proposalId": proposal["id"], "reason": skip_reason})
                continue
            approve_proposal_item(proposal, user, "list_bulk")
            approved += 1
        total_approved += approved
        results.append({"recordId": record_id, "approvedCount": approved, "skippedItems": skipped_items})
    write_audit_log(
        user,
        "approve_list_bulk",
        "records",
        "bulk",
        {"approvalMethod": "list_bulk", "successCount": total_approved, "recordResults": results},
    )
    return {"approvedCount": total_approved, "recordResults": results}
