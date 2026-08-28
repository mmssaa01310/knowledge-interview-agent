from dataclasses import dataclass
from fastapi import Header, HTTPException


@dataclass
class UserContext:
    user_id: str
    tenant_id: str
    role: str
    display_name: str


DEV_TOKENS = {
    "dev-admin": UserContext("user-admin", "tenant-demo", "admin", "管理者"),
    "dev-manager": UserContext("user-manager", "tenant-demo", "knowledge_manager", "ナレッジ管理者"),
    "dev-interviewer": UserContext("user-interviewer", "tenant-demo", "interviewer", "インタビュー対象者"),
    "dev-viewer": UserContext("user-viewer", "tenant-demo", "viewer", "閲覧者"),
}


def get_current_user(
    authorization: str | None = Header(default=None),
    x_dev_token: str | None = Header(default="dev-manager"),
) -> UserContext:
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        if token in DEV_TOKENS:
            return DEV_TOKENS[token]
    if x_dev_token and x_dev_token in DEV_TOKENS:
        return DEV_TOKENS[x_dev_token]
    raise HTTPException(status_code=401, detail="invalid_token")
