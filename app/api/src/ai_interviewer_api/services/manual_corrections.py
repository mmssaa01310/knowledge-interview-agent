from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.core.permissions import MANAGEMENT_ROLES, ensure_tenant_scope
from ai_interviewer_api.services.interview_state_transition import commit_interview_state


INTERVIEWER_CORRECTION_STATUSES = {"in_progress", "returned"}
MANAGEMENT_CORRECTION_STATUSES = {"submitted", "returned", "approved"}


def require_manual_correction_access(record: Mapping[str, Any], user: UserContext) -> None:
    ensure_tenant_scope(user, str(record.get("tenantId") or ""))
    status = str(record.get("status") or "draft")
    if user.role in MANAGEMENT_ROLES:
        if status in MANAGEMENT_CORRECTION_STATUSES:
            return
        raise HTTPException(status_code=409, detail="record_not_in_review")
    if user.role == "interviewer":
        if record.get("ownerUserId") != user.user_id:
            raise HTTPException(status_code=403, detail="record_not_assigned")
        if status in INTERVIEWER_CORRECTION_STATUSES:
            return
        raise HTTPException(status_code=409, detail="record_not_editable")
    raise HTTPException(status_code=403, detail="record_read_only")


def commit_manual_correction_state(
    state: dict[str, Any],
    record: dict[str, Any],
    user: UserContext,
    *,
    source: str,
    expected_state_version: int,
) -> None:
    reset_approved = record.get("status") == "approved"
    try:
        commit_interview_state(
            state,
            user,
            source=source,
            expected_state_version=expected_state_version,
            reset_approved_record_for_review=reset_approved,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail="interview_state_version_conflict",
        ) from exc
    if reset_approved:
        record["status"] = "submitted"
        record["updatedByUserId"] = user.user_id
        record["updatedAt"] = state["updatedAt"]
