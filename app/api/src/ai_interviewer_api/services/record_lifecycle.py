from typing import Any

from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.services.audit import write_audit_log


def sync_record_status_after_interview(
    record: dict[str, Any],
    interview_status: object,
    user: UserContext,
) -> bool:
    """Submit an in-progress record after its interview state is complete."""

    if interview_status != "completed" or record.get("status") != "in_progress":
        return False

    previous_status = record["status"]
    record["status"] = "submitted"
    record["updatedByUserId"] = user.user_id
    record["updatedAt"] = utc_now()
    store.upsert("records", record)
    write_audit_log(
        user,
        "record_status_change",
        "record",
        str(record["id"]),
        {
            "from": previous_status,
            "to": "submitted",
            "reason": "interview_completed",
        },
    )
    return True
