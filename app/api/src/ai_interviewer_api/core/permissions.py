from fastapi import HTTPException

from ai_interviewer_api.auth.deps import UserContext


MANAGEMENT_ROLES = {"admin", "knowledge_manager"}
RECORD_READABLE_BY_INTERVIEWEE = {"in_progress", "submitted", "returned", "approved"}
RECORD_EDITABLE_BY_INTERVIEWEE = {"in_progress", "returned"}


def require_roles(user: UserContext, allowed: set[str]) -> None:
    if user.role not in allowed:
        raise HTTPException(status_code=403, detail="insufficient_role")


def ensure_tenant_scope(user: UserContext, tenant_id: str) -> None:
    if user.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="tenant_scope_mismatch")


def require_management_role(user: UserContext) -> None:
    require_roles(user, MANAGEMENT_ROLES)


def require_admin_role(user: UserContext) -> None:
    require_roles(user, {"admin"})


def ensure_record_access(record: dict, user: UserContext, *, operation: str = "read") -> None:
    ensure_tenant_scope(user, record["tenantId"])

    if user.role in MANAGEMENT_ROLES:
        return

    record_status = record.get("status", "draft")
    if user.role == "interviewer":
        if record.get("ownerUserId") != user.user_id:
            raise HTTPException(status_code=403, detail="record_not_assigned")
        if operation == "read" and record_status not in RECORD_READABLE_BY_INTERVIEWEE:
            raise HTTPException(status_code=403, detail="record_not_published")
        if operation in {"answer", "submit", "interview"} and record_status not in RECORD_EDITABLE_BY_INTERVIEWEE:
            raise HTTPException(status_code=409, detail="record_not_editable")
        if operation == "review":
            raise HTTPException(status_code=403, detail="record_review_forbidden")
        return

    if user.role == "viewer":
        if (
            record_status != "approved"
            or user.user_id not in (record.get("viewerUserIds") or [])
        ):
            raise HTTPException(status_code=403, detail="record_view_forbidden")
        if operation != "read":
            raise HTTPException(status_code=403, detail="record_read_only")
        return

    raise HTTPException(status_code=403, detail="insufficient_role")


def require_record_action(record: dict, user: UserContext, action: str) -> None:
    if action == "read":
        ensure_record_access(record, user, operation="read")
        return

    if action == "interview_read":
        ensure_record_access(record, user, operation="read")
        return

    if action in {"answer", "interview"}:
        ensure_record_access(record, user, operation="answer")
        return

    if action == "submit":
        ensure_record_access(record, user, operation="submit")
        if user.role == "interviewer" and record.get("status", "draft") not in {"in_progress", "returned"}:
            raise HTTPException(status_code=409, detail="record_not_submittable")
        return

    if action in {"review", "manage"}:
        require_management_role(user)
        ensure_record_access(record, user, operation="read")
        return

    raise HTTPException(status_code=500, detail="unknown_record_action")


def accessible_records(records: list[dict], user: UserContext) -> list[dict]:
    result: list[dict] = []
    for record in records:
        try:
            ensure_record_access(record, user, operation="read")
        except HTTPException:
            continue
        result.append(record)
    return result
