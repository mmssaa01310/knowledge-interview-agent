"""Application-owned accounts and opaque server-side sessions."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from uuid import uuid4

from fastapi import HTTPException

from ai_interviewer_api.repositories.store import store

TENANT_ID = "tenant-demo"
ROLES = {"admin", "knowledge_manager", "interviewer", "viewer"}
SESSION_SECONDS = 12 * 60 * 60
COOKIE_NAME = "kikiori_session"


def normalize_login(value: str) -> str:
    return value.strip().casefold()


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=5, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt, _ = encoded.split("$")
        return algorithm == "scrypt" and hmac.compare_digest(hash_password(password, bytes.fromhex(salt)), encoded)
    except (ValueError, TypeError):
        return False


def validate_password(password: str) -> None:
    if len(password) < 12 or len(password) > 128 or len(password.encode("utf-8")) > 512:
        raise HTTPException(status_code=422, detail="password_length_invalid")


_DUMMY_HASH = hash_password("invalid account password", b"\x00" * 16)


def public_user(user: dict) -> dict:
    return {key: user[key] for key in ("id", "loginId", "displayName", "role", "active")}


def _find_login(login_id: str) -> dict | None:
    normalized = normalize_login(login_id)
    return next((u for u in store.list("users", TENANT_ID) if u.get("active") and normalize_login(u["loginId"]) == normalized), None)


def create_account(login_id: str, display_name: str, role: str, password: str, *, tenant_id: str = TENANT_ID, actor: str | None = None) -> dict:
    login_id, display_name = login_id.strip(), display_name.strip()
    if not login_id or len(login_id) > 128 or not display_name or len(display_name) > 128 or role not in ROLES:
        raise HTTPException(status_code=422, detail="invalid_user")
    validate_password(password)
    password_hash = hash_password(password)
    with store.live_capture_lock("accounts-global"):
        if _find_login(login_id):
            raise HTTPException(status_code=409, detail="login_id_exists")
        user_id = str(uuid4())
        user = {"id": user_id, "tenantId": tenant_id, "loginId": login_id, "displayName": display_name, "role": role, "active": True, "passwordHash": password_hash, "sessionVersion": 0, "failedAttempts": 0, "lockedUntil": 0, "createdByUserId": actor or user_id, "updatedByUserId": actor or user_id}
        store.upsert("users", user)
    return public_user(user)


def authenticate(login_id: str, password: str, client_ip: str) -> str:
    with store.live_capture_lock("accounts-global"):
        attempt_id = hashlib.sha256(client_ip.encode()).hexdigest()
        attempt = store.get("auth_attempts", attempt_id) or {"id": attempt_id, "tenantId": TENANT_ID, "count": 0, "until": 0}
        if float(attempt["until"]) <= time.time():
            attempt.update(count=0, until=time.time() + 15 * 60)
        if attempt["count"] >= 20:
            raise HTTPException(status_code=429, detail="login_rate_limited")
        user = _find_login(login_id)
        if not user or float(user.get("lockedUntil", 0)) > time.time():
            verify_password(password, _DUMMY_HASH)
            attempt["count"] += 1
            store.upsert("auth_attempts", attempt)
            raise HTTPException(status_code=401, detail="invalid_credentials")
        if not verify_password(password, user["passwordHash"]):
            attempt["count"] += 1
            store.upsert("auth_attempts", attempt)
            attempts = int(user.get("failedAttempts", 0)) + 1
            user["failedAttempts"] = attempts
            if attempts >= 5:
                user["lockedUntil"] = time.time() + 15 * 60
                user["failedAttempts"] = 0
            store.upsert("users", user)
            raise HTTPException(status_code=401, detail="invalid_credentials")
        user["failedAttempts"] = 0
        user["lockedUntil"] = 0
        store.upsert("users", user)
        store.delete("auth_attempts", attempt_id)
        token = secrets.token_urlsafe(32)
        store.upsert("auth_sessions", {"id": hashlib.sha256(token.encode()).hexdigest(), "tenantId": user["tenantId"], "userId": user["id"], "sessionVersion": user["sessionVersion"], "expiresAt": time.time() + SESSION_SECONDS})
        return token


def get_session_user(token: str | None) -> dict | None:
    if not token or len(token) > 256:
        return None
    return get_session_user_by_id(hashlib.sha256(token.encode()).hexdigest())


def get_session_user_by_id(session_id: str) -> dict | None:
    session = store.get("auth_sessions", session_id)
    if not session or float(session.get("expiresAt", 0)) <= time.time():
        return None
    user = store.get("users", session["userId"])
    if not user or not user.get("active") or user["tenantId"] != session["tenantId"] or user.get("sessionVersion") != session.get("sessionVersion"):
        return None
    return user


def revoke_session(token: str | None) -> None:
    if token and len(token) <= 256:
        store.delete("auth_sessions", hashlib.sha256(token.encode()).hexdigest())


def update_account(user_id: str, tenant_id: str, actor_id: str, *, login_id: str | None = None, display_name: str | None = None, role: str | None = None, password: str | None = None, delete: bool = False, require_admin_target: bool = False) -> dict:
    password_hash = None
    if password is not None:
        validate_password(password)
        password_hash = hash_password(password)
    with store.live_capture_lock("accounts-global"):
        user = store.get("users", user_id)
        if not user or user.get("tenantId") != tenant_id or not user.get("active"):
            raise HTTPException(status_code=404, detail="user_not_found")
        if require_admin_target and user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="admin_recovery_target_required")
        if (delete or (role is not None and role != "admin")) and user["role"] == "admin":
            admins = [u for u in store.list("users", tenant_id) if u.get("active") and u.get("role") == "admin"]
            if len(admins) <= 1:
                raise HTTPException(status_code=409, detail="last_admin_required")
        if delete and user_id == actor_id:
            raise HTTPException(status_code=409, detail="cannot_delete_self")
        login_changed = False
        if login_id is not None:
            login_id = login_id.strip()
            if not login_id or len(login_id) > 128:
                raise HTTPException(status_code=422, detail="invalid_user")
            other = _find_login(login_id)
            if other and other["id"] != user_id:
                raise HTTPException(status_code=409, detail="login_id_exists")
            login_changed = login_id != user["loginId"]
            user["loginId"] = login_id
        if display_name is not None:
            if not display_name.strip() or len(display_name.strip()) > 128:
                raise HTTPException(status_code=422, detail="invalid_user")
            user["displayName"] = display_name.strip()
        role_changed = role is not None and role != user["role"]
        if role is not None:
            if role not in ROLES:
                raise HTTPException(status_code=422, detail="invalid_user")
            user["role"] = role
        if password_hash is not None:
            user["passwordHash"] = password_hash
            user["failedAttempts"] = 0
            user["lockedUntil"] = 0
        if delete:
            user["active"] = False
        if delete or login_changed or role_changed or password_hash is not None:
            user["sessionVersion"] = int(user.get("sessionVersion", 0)) + 1
        user["updatedByUserId"] = actor_id
        store.upsert("users", user)
    return public_user(user)
