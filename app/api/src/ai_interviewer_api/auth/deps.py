from dataclasses import dataclass
import os
import hashlib
from fastapi import Cookie, Header, HTTPException, Request

from ai_interviewer_api.auth.accounts import COOKIE_NAME, get_session_user


@dataclass
class UserContext:
    user_id: str
    tenant_id: str
    role: str
    display_name: str
    timezone: str = "Asia/Tokyo"
    auth_session_id: str | None = None


DEV_TOKENS = {
    "dev-admin": UserContext("user-admin", "tenant-demo", "admin", "管理者"),
    "dev-manager": UserContext("user-manager", "tenant-demo", "knowledge_manager", "ナレッジ管理者"),
    "dev-interviewer": UserContext("user-interviewer", "tenant-demo", "interviewer", "インタビュー対象者"),
    "dev-viewer": UserContext("user-viewer", "tenant-demo", "viewer", "閲覧者"),
}


def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> UserContext:
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    elif session:
        token = session
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            validate_origin(request, required=True)
    user = get_session_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="invalid_token")
    return UserContext(user["id"], user["tenantId"], user["role"], user["displayName"], auth_session_id=hashlib.sha256(token.encode()).hexdigest())


def validate_origin(request: Request, *, required: bool = False) -> None:
    origin = request.headers.get("origin")
    defaults = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174"
    allowed = {item.strip().rstrip("/") for item in os.getenv("AUTH_ALLOWED_ORIGINS", defaults).split(",") if item.strip()}
    if (required and not origin) or (origin and origin.rstrip("/") not in allowed):
        raise HTTPException(status_code=403, detail="invalid_origin")
