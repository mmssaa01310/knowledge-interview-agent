from http.cookies import SimpleCookie

import pytest
from fastapi import HTTPException, Request, Response

from ai_interviewer_api.auth.accounts import create_account, update_account
from ai_interviewer_api.auth import recover_admin
from ai_interviewer_api.auth.deps import get_current_user
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.core.config import settings
from ai_interviewer_api.routers.auth import LoginInput, UserCreate, UserUpdate, create_user, delete_user, edit_user, list_user_audit, list_users, list_users_page, login, logout
from ai_interviewer_api.routers.internal_voice import require_internal_api_token
from ai_interviewer_api.services.voice_interview import _ensure_session_accepts_turns
from ai_interviewer_api.services.audit import write_audit_log


PASSWORD = "example password 123"


def request(method: str, origin: str | None = None) -> Request:
    headers = [(b"origin", origin.encode())] if origin else []
    return Request({"type": "http", "method": method, "path": "/", "headers": headers, "client": ("127.0.0.1", 1234), "server": ("localhost", 8000), "scheme": "http", "query_string": b""})


def sign_in(login_id: str, password: str) -> tuple[str, Response]:
    response = Response()
    login(LoginInput(loginId=login_id, password=password), request("POST", "http://localhost:5173"), response)
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    return cookie["kikiori_session"].value, response


def authenticated(token: str, *, method: str = "GET", origin: str | None = None):
    return get_current_user(request(method, origin), authorization=None, session=token)


def test_login_user_management_and_revocation(clean_interview_store):
    create_account("admin", "管理者", "admin", PASSWORD)
    with pytest.raises(HTTPException) as missing:
        authenticated("")
    assert missing.value.status_code == 401
    with pytest.raises(HTTPException):
        get_current_user(request("GET"), authorization="Bearer dev-manager", session=None)
    with pytest.raises(HTTPException):
        sign_in("admin", "wrong password")
    admin_token, response = sign_in("admin", PASSWORD)
    assert "HttpOnly" in response.headers["set-cookie"]
    admin = authenticated(admin_token)
    assert admin.role == "admin"
    with pytest.raises(HTTPException) as csrf:
        authenticated(admin_token, method="POST", origin="https://other.example")
    assert csrf.value.status_code == 403

    created = create_user(UserCreate(loginId="member", displayName="対象者", role="interviewer", password=PASSWORD), admin)
    assert "passwordHash" not in created
    with pytest.raises(HTTPException) as duplicate:
        create_user(UserCreate(loginId="MEMBER", displayName="重複", role="viewer", password=PASSWORD), admin)
    assert duplicate.value.status_code == 409
    member_token, _ = sign_in("member", PASSWORD)
    member = authenticated(member_token)
    with pytest.raises(HTTPException) as forbidden:
        list_users(member)
    assert forbidden.value.status_code == 403

    record = {"id": "record-1", "tenantId": "tenant-demo", "ownerUserId": member.user_id, "status": "in_progress"}
    store.upsert("records", record)
    voice = {"id": "voice-1", "recordId": record["id"], "tenantId": member.tenant_id, "ownerUserId": member.user_id, "authSessionId": member.auth_session_id, "status": "active"}
    store.upsert("voice_sessions", voice)
    _ensure_session_accepts_turns(voice)
    require_internal_api_token("voice-1", settings.internal_api_token)
    edit_user(member.user_id, UserUpdate(displayName="変更後", role="viewer"), admin)
    with pytest.raises(HTTPException):
        authenticated(member_token)
    with pytest.raises(HTTPException):
        _ensure_session_accepts_turns(voice)
    with pytest.raises(HTTPException):
        require_internal_api_token("voice-1", settings.internal_api_token)
    member_token, _ = sign_in("member", PASSWORD)
    assert authenticated(member_token).role == "viewer"
    delete_user(member.user_id, admin)
    assert store.get("records", "record-1") == record
    with pytest.raises(HTTPException):
        authenticated(member_token)
    with pytest.raises(HTTPException):
        sign_in("member", PASSWORD)
    with pytest.raises(HTTPException) as last_admin:
        delete_user(admin.user_id, admin)
    assert last_admin.value.status_code == 409
    with pytest.raises(HTTPException) as last_admin_role:
        edit_user(admin.user_id, UserUpdate(role="viewer"), admin)
    assert last_admin_role.value.detail == "last_admin_required"
    create_account("second-admin", "管理者2", "admin", PASSWORD)
    with pytest.raises(HTTPException) as self_delete:
        delete_user(admin.user_id, admin)
    assert self_delete.value.detail == "cannot_delete_self"
    logout(Response(), session=admin_token, authorization=None, user=admin)
    with pytest.raises(HTTPException):
        authenticated(admin_token)


def test_password_reset_unlocks_account_and_invalidates_sessions(clean_interview_store):
    create_account("admin", "管理者", "admin", PASSWORD)
    admin_token, _ = sign_in("admin", PASSWORD)
    admin = authenticated(admin_token)
    member = create_user(UserCreate(loginId="member", displayName="対象者", role="interviewer", password=PASSWORD), admin)
    token, _ = sign_in("member", PASSWORD)
    for _ in range(5):
        with pytest.raises(HTTPException):
            sign_in("member", "incorrect password")
    with pytest.raises(HTTPException):
        sign_in("member", PASSWORD)
    edit_user(member["id"], UserUpdate(loginId="updated", password="new example password 456"), admin)
    with pytest.raises(HTTPException):
        authenticated(token)
    with pytest.raises(HTTPException):
        sign_in("member", PASSWORD)
    new_token, _ = sign_in("updated", "new example password 456")
    assert authenticated(new_token).user_id == member["id"]


def test_user_pages_and_audit_are_scoped_and_do_not_expose_passwords(clean_interview_store):
    create_account("admin", "管理者", "admin", PASSWORD)
    token, _ = sign_in("admin", PASSWORD)
    admin = authenticated(token)
    for index in range(3):
        store.upsert("users", {"id": f"user-{index}", "tenantId": admin.tenant_id, "loginId": f"member-{index}", "displayName": f"対象者{index}", "role": "interviewer", "active": True, "passwordHash": "private"})
    store.upsert("users", {"id": "other-tenant", "tenantId": "other", "loginId": "member-other", "displayName": "対象者", "role": "admin", "active": True, "passwordHash": "private"})
    first = list_users_page(search="member", role="interviewer", limit=2, offset=0, user=admin)
    second = list_users_page(search="member", role="interviewer", limit=2, offset=2, user=admin)
    assert (first["total"], first["activeCount"], first["adminCount"]) == (3, 4, 1)
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert all("passwordHash" not in item for item in first["items"])
    write_audit_log(admin, "update", "user", "user-0", {"fields": ["password", "role"], "password": "private"})
    history = list_user_audit(limit=20, offset=0, user=admin)
    assert history["total"] == 1
    assert history["items"][0]["fields"] == ["password", "role"]
    assert "private" not in str(history)
    assert list_user_audit(action="update", limit=20, offset=0, user=admin)["total"] == 1
    assert list_user_audit(action="delete", limit=20, offset=0, user=admin)["total"] == 0
    with pytest.raises(HTTPException) as invalid_action:
        list_user_audit(action="unknown", limit=20, offset=0, user=admin)
    assert invalid_action.value.status_code == 422
    with pytest.raises(HTTPException):
        list_users_page(search="", role="", limit=20, offset=0, user=authenticated_for_role("viewer", admin.tenant_id))
    with pytest.raises(HTTPException):
        list_user_audit(action="", limit=20, offset=0, user=authenticated_for_role("viewer", admin.tenant_id))


def authenticated_for_role(role: str, tenant_id: str):
    from ai_interviewer_api.auth.deps import UserContext
    return UserContext("viewer-id", tenant_id, role, "閲覧者")


def test_terminal_recovery_resets_only_admin_and_revokes_session(clean_interview_store, monkeypatch):
    admin = create_account("admin", "管理者", "admin", PASSWORD)
    token, _ = sign_in("admin", PASSWORD)
    new_password = "new recovery password 123"
    monkeypatch.setattr("builtins.input", lambda _prompt: "admin")
    monkeypatch.setattr(recover_admin.getpass, "getpass", lambda _prompt: new_password)
    monkeypatch.setattr(recover_admin.getpass, "getuser", lambda: "test-operator")
    recover_admin.main()
    with pytest.raises(HTTPException):
        authenticated(token)
    new_token, _ = sign_in("admin", new_password)
    assert authenticated(new_token).user_id == admin["id"]
    events = store.list("audit_logs", "tenant-demo")
    assert events[-1]["action"] == "recover_password"
    assert new_password not in str(events[-1])
    other = create_account("member", "対象者", "interviewer", PASSWORD)
    with pytest.raises(HTTPException) as forbidden:
        update_account(other["id"], "tenant-demo", "local-operator", password=new_password, require_admin_target=True)
    assert forbidden.value.status_code == 403
    monkeypatch.setattr("builtins.input", lambda _prompt: "member")
    with pytest.raises(SystemExit):
        recover_admin.main()


def test_terminal_recovery_rejects_mismatched_passwords(clean_interview_store, monkeypatch):
    create_account("admin", "管理者", "admin", PASSWORD)
    token, _ = sign_in("admin", PASSWORD)
    passwords = iter(["first password 123", "second password 456"])
    monkeypatch.setattr("builtins.input", lambda _prompt: "admin")
    monkeypatch.setattr(recover_admin.getpass, "getpass", lambda _prompt: next(passwords))
    with pytest.raises(SystemExit):
        recover_admin.main()
    assert authenticated(token).role == "admin"
    assert not store.list("audit_logs", "tenant-demo")


def test_audit_keeps_recorded_display_names_after_later_edits(clean_interview_store):
    create_account("admin", "管理者", "admin", PASSWORD)
    token, _ = sign_in("admin", PASSWORD)
    admin = authenticated(token)
    member = create_user(UserCreate(loginId="member", displayName="旧表示名", role="interviewer", password=PASSWORD), admin)
    edit_user(member["id"], UserUpdate(displayName="新表示名"), admin)
    history = list_user_audit(action="", limit=20, offset=0, user=admin)["items"]
    created = next(item for item in history if item["action"] == "create")
    updated = next(item for item in history if item["action"] == "update")
    assert created["actorName"] == "管理者" and created["targetName"] == "旧表示名"
    assert updated["targetName"] == "新表示名" and updated["fields"] == ["displayName"]
