from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, SecretStr

from ai_interviewer_api.auth.deps import UserContext, get_current_user
from ai_interviewer_api.core.permissions import require_application_settings_admin
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.services.application_settings import (
    delete_gpt_live_api_key,
    get_gpt_live_api_key_status,
    save_gpt_live_api_key,
    verify_gpt_live_api_key,
)
from ai_interviewer_api.services.audit import write_audit_log
from ai_interviewer_api.auth.accounts import set_application_settings_admin


router = APIRouter(prefix="/api/application-settings")


class GptLiveApiKeyInput(BaseModel):
    apiKey: SecretStr = Field(min_length=20, max_length=512)


class ApplicationSettingsAdminInput(BaseModel):
    enabled: bool


@router.get("/gpt-live")
def get_gpt_live_settings(
    response: Response,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_application_settings_admin(user)
    response.headers["Cache-Control"] = "no-store"
    return get_gpt_live_api_key_status()


@router.post("/gpt-live/connection-check")
def check_gpt_live_connection(
    payload: GptLiveApiKeyInput,
    response: Response,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_application_settings_admin(user)
    response.headers["Cache-Control"] = "no-store"
    verify_gpt_live_api_key(payload.apiKey.get_secret_value())
    write_audit_log(
        user,
        "gpt_live_api_key_connection_check",
        "application_setting",
        "gpt-live-api-key",
        {"connected": True},
    )
    return {"connected": True}


@router.put("/gpt-live")
def update_gpt_live_settings(
    payload: GptLiveApiKeyInput,
    response: Response,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_application_settings_admin(user)
    response.headers["Cache-Control"] = "no-store"
    api_key = payload.apiKey.get_secret_value()
    verify_gpt_live_api_key(api_key)
    saved = save_gpt_live_api_key(api_key, user.user_id)
    write_audit_log(
        user,
        "gpt_live_api_key_update",
        "application_setting",
        "gpt-live-api-key",
        {"configured": True},
    )
    return saved


@router.delete("/gpt-live")
def remove_gpt_live_settings(
    response: Response,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_application_settings_admin(user)
    response.headers["Cache-Control"] = "no-store"
    delete_gpt_live_api_key()
    write_audit_log(
        user,
        "gpt_live_api_key_delete",
        "application_setting",
        "gpt-live-api-key",
        {"configured": False},
    )
    return {"configured": False, "maskedApiKey": None, "updatedAt": None}


@router.get("/admins")
def list_application_settings_admins(
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_application_settings_admin(user)
    admins = [
        {
            "id": row["id"],
            "displayName": row["displayName"],
            "loginId": row["loginId"],
            "applicationSettingsAdmin": bool(row.get("applicationSettingsAdmin")),
        }
        for row in store.list_all_users()
        if row.get("active") and row.get("role") == "admin"
    ]
    return {"items": admins}


@router.put("/admins/{target_user_id}")
def update_application_settings_admin(
    target_user_id: str,
    payload: ApplicationSettingsAdminInput,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_application_settings_admin(user)
    updated = set_application_settings_admin(
        target_user_id,
        actor_id=user.user_id,
        enabled=payload.enabled,
    )
    write_audit_log(
        user,
        "application_settings_admin_update",
        "user",
        target_user_id,
        {"enabled": payload.enabled},
    )
    return updated
