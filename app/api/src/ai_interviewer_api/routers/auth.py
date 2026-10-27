import os

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from ai_interviewer_api.auth.accounts import COOKIE_NAME, ROLES, SESSION_SECONDS, authenticate, create_account, public_user, revoke_session, update_account
from ai_interviewer_api.auth.deps import UserContext, get_current_user, validate_origin
from ai_interviewer_api.core.permissions import require_admin_role
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.services.audit import write_audit_log

router = APIRouter(prefix="/api")


class LoginInput(BaseModel):
    loginId: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=128)


class UserCreate(BaseModel):
    loginId: str = Field(min_length=1, max_length=128)
    displayName: str = Field(min_length=1, max_length=128)
    role: str
    password: str = Field(min_length=12, max_length=128)


class UserUpdate(BaseModel):
    loginId: str | None = Field(default=None, min_length=1, max_length=128)
    displayName: str | None = Field(default=None, min_length=1, max_length=128)
    role: str | None = None
    password: str | None = Field(default=None, min_length=12, max_length=128)


@router.post("/auth/login")
def login(payload: LoginInput, request: Request, response: Response) -> dict:
    validate_origin(request)
    token = authenticate(payload.loginId, payload.password, request.client.host if request.client else "unknown")
    secure = os.getenv("AUTH_COOKIE_SECURE", "false" if os.getenv("APP_ENV", "local") == "local" else "true").lower() == "true"
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_SECONDS, httponly=True, secure=secure, samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}


@router.post("/auth/logout")
def logout(response: Response, session: str | None = Cookie(default=None, alias=COOKIE_NAME), authorization: str | None = Header(default=None), user: UserContext = Depends(get_current_user)) -> dict:
    token = authorization.removeprefix("Bearer ").strip() if authorization and authorization.startswith("Bearer ") else session
    revoke_session(token)
    response.delete_cookie(COOKIE_NAME, path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}


@router.get("/users")
def list_users(user: UserContext = Depends(get_current_user)) -> list[dict]:
    require_admin_role(user)
    return [public_user(row) for row in store.list("users", user.tenant_id) if row.get("active")]


@router.get("/users/page")
def list_users_page(search: str = Query(default="", max_length=128), role: str = "", limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0), user: UserContext = Depends(get_current_user)) -> dict:
    require_admin_role(user)
    if role and role not in ROLES:
        raise HTTPException(status_code=422, detail="invalid_role")
    page = store.list_users_page(user.tenant_id, search.strip(), role, limit, offset)
    return {**page, "items": [public_user(row) for row in page["items"]]}


@router.get("/users/audit")
def list_user_audit(action: str = "", limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0), user: UserContext = Depends(get_current_user)) -> dict:
    require_admin_role(user)
    if action and action not in {"create", "update", "delete", "recover_password"}:
        raise HTTPException(status_code=422, detail="invalid_audit_action")
    page = store.list_user_audit_page(user.tenant_id, action, limit, offset)
    items = []
    for row in page["items"]:
        actor_id = str(row.get("actorUserId") or "")
        target_id = str(row.get("resourceId") or "")
        actor, target = store.get("users", actor_id), store.get("users", target_id)
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        fields = detail.get("fields") if isinstance(detail.get("fields"), list) else []
        recorded_role = detail.get("role")
        actor_name = detail.get("actorDisplayName") if isinstance(detail.get("actorDisplayName"), str) else (actor.get("displayName") if actor and actor.get("tenantId") == user.tenant_id else actor_id)
        target_name = detail.get("targetDisplayName") if isinstance(detail.get("targetDisplayName"), str) else (target.get("displayName") if target and target.get("tenantId") == user.tenant_id else target_id)
        if actor_id == "local-operator" and isinstance(detail.get("operator"), str):
            actor_name = f"{actor_id} ({detail['operator'][:128]})"
        items.append({"id": row["id"], "action": row.get("action"), "createdAt": row.get("createdAt"), "actorUserId": actor_id, "actorName": actor_name, "targetUserId": target_id, "targetName": target_name, "fields": [field for field in fields if isinstance(field, str) and field in {"loginId", "displayName", "role", "password"}], "role": recorded_role if isinstance(recorded_role, str) and recorded_role in ROLES else None, "result": row.get("result")})
    return {"items": items, "total": page["total"]}


@router.post("/users", status_code=201)
def create_user(payload: UserCreate, user: UserContext = Depends(get_current_user)) -> dict:
    require_admin_role(user)
    created = create_account(payload.loginId, payload.displayName, payload.role, payload.password, tenant_id=user.tenant_id, actor=user.user_id)
    write_audit_log(user, "create", "user", created["id"], {"role": created["role"], "actorDisplayName": user.display_name, "targetDisplayName": created["displayName"]})
    return created


@router.patch("/users/{user_id}")
def edit_user(user_id: str, payload: UserUpdate, user: UserContext = Depends(get_current_user)) -> dict:
    require_admin_role(user)
    if not payload.model_fields_set:
        raise HTTPException(status_code=422, detail="empty_update")
    updated = update_account(user_id, user.tenant_id, user.user_id, login_id=payload.loginId, display_name=payload.displayName, role=payload.role, password=payload.password)
    write_audit_log(user, "update", "user", user_id, {"fields": sorted(payload.model_fields_set), "actorDisplayName": user.display_name, "targetDisplayName": updated["displayName"]})
    return updated


@router.delete("/users/{user_id}")
def delete_user(user_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    require_admin_role(user)
    deleted = update_account(user_id, user.tenant_id, user.user_id, delete=True)
    write_audit_log(user, "delete", "user", user_id, {"actorDisplayName": user.display_name, "targetDisplayName": deleted["displayName"]})
    return {"ok": True}
