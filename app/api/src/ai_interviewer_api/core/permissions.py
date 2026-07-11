from fastapi import HTTPException

from ai_interviewer_api.auth.deps import UserContext


def require_roles(user: UserContext, allowed: set[str]) -> None:
    if user.role not in allowed:
        raise HTTPException(status_code=403, detail="insufficient_role")


def ensure_tenant_scope(user: UserContext, tenant_id: str) -> None:
    if user.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="tenant_scope_mismatch")
