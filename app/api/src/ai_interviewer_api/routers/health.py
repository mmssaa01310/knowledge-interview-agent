from fastapi import APIRouter, Depends

from ai_interviewer_api.auth.deps import UserContext, get_current_user

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/me")
def me(user: UserContext = Depends(get_current_user)) -> dict:
    return {
        "userId": user.user_id,
        "tenantId": user.tenant_id,
        "role": user.role,
        "displayName": user.display_name,
        "timezone": user.timezone,
    }
