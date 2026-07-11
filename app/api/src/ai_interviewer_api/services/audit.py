from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.models.base import AuditLog
from ai_interviewer_api.repositories.store import store


def write_audit_log(user: UserContext, action: str, resource_type: str, resource_id: str, detail: dict) -> None:
    log = AuditLog(
        tenantId=user.tenant_id,
        createdByUserId=user.user_id,
        updatedByUserId=user.user_id,
        actorUserId=user.user_id,
        action=action,
        resourceType=resource_type,
        resourceId=resource_id,
        result="success",
        detail=detail,
    )
    store.upsert("audit_logs", log.model_dump())
