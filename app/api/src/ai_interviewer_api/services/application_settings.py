"""Shared application settings with encrypted, server-only secret storage."""
from __future__ import annotations

import base64
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException

from ai_interviewer_api.core.config import settings
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.repositories.store import store


APPLICATION_SETTINGS_TENANT_ID = "system"
GPT_LIVE_SETTING_ID = "gpt-live-api-key"
_GPT_LIVE_KEY_AAD = b"kikiori:application-settings:gpt-live-api-key:v1"
_GPT_LIVE_KEY_PREFIX = "v1"
_VISIBLE_KEY_PREFIX_LENGTH = 8
_VISIBLE_KEY_SUFFIX_LENGTH = 4


def _mask_gpt_live_api_key(api_key: str) -> str:
    prefix = api_key[:_VISIBLE_KEY_PREFIX_LENGTH]
    suffix = api_key[-_VISIBLE_KEY_SUFFIX_LENGTH:]
    masked_length = max(0, len(api_key) - len(prefix) - len(suffix))
    return f"{prefix}{'*' * masked_length}{suffix}"


def _encryption_key() -> bytes:
    encoded_key = settings.gpt_live_settings_encryption_key.strip()
    if not encoded_key:
        raise HTTPException(status_code=503, detail="gpt_live_encryption_key_unavailable")
    try:
        key = base64.b64decode(encoded_key, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise HTTPException(status_code=503, detail="gpt_live_encryption_key_unavailable") from exc
    if len(key) != 32:
        raise HTTPException(status_code=503, detail="gpt_live_encryption_key_unavailable")
    return key


def _encrypt_api_key(api_key: str) -> str:
    nonce = os.urandom(12)
    encrypted = AESGCM(_encryption_key()).encrypt(
        nonce,
        api_key.encode("utf-8"),
        _GPT_LIVE_KEY_AAD,
    )
    return ":".join(
        (
            _GPT_LIVE_KEY_PREFIX,
            base64.urlsafe_b64encode(nonce).decode("ascii"),
            base64.urlsafe_b64encode(encrypted).decode("ascii"),
        )
    )


def _decrypt_api_key(value: str) -> str:
    try:
        version, encoded_nonce, encoded_ciphertext = value.split(":", 2)
        if version != _GPT_LIVE_KEY_PREFIX:
            raise ValueError("unsupported_ciphertext_version")
        nonce = base64.urlsafe_b64decode(encoded_nonce.encode("ascii"))
        ciphertext = base64.urlsafe_b64decode(encoded_ciphertext.encode("ascii"))
        if len(nonce) != 12:
            raise ValueError("invalid_nonce")
        plaintext = AESGCM(_encryption_key()).decrypt(
            nonce,
            ciphertext,
            _GPT_LIVE_KEY_AAD,
        )
        return plaintext.decode("utf-8")
    except HTTPException:
        raise
    except (InvalidTag, UnicodeDecodeError, ValueError, base64.binascii.Error) as exc:
        raise HTTPException(status_code=503, detail="gpt_live_credential_unavailable") from exc


def get_gpt_live_api_key_status() -> dict[str, Any]:
    setting = store.get("application_settings", GPT_LIVE_SETTING_ID)
    if not setting:
        return {"configured": False, "maskedApiKey": None, "updatedAt": None}
    if setting.get("tenantId") != APPLICATION_SETTINGS_TENANT_ID:
        raise HTTPException(status_code=503, detail="gpt_live_credential_unavailable")
    encrypted_value = setting.get("encryptedValue")
    if not isinstance(encrypted_value, str):
        raise HTTPException(status_code=503, detail="gpt_live_credential_unavailable")
    api_key = _decrypt_api_key(encrypted_value)
    return {
        "configured": True,
        "maskedApiKey": _mask_gpt_live_api_key(api_key),
        "updatedAt": setting.get("updatedAt"),
    }


def get_gpt_live_api_key() -> str | None:
    setting = store.get("application_settings", GPT_LIVE_SETTING_ID)
    if not setting:
        return None
    if setting.get("tenantId") != APPLICATION_SETTINGS_TENANT_ID:
        raise HTTPException(status_code=503, detail="gpt_live_credential_unavailable")
    encrypted_value = setting.get("encryptedValue")
    if not isinstance(encrypted_value, str):
        raise HTTPException(status_code=503, detail="gpt_live_credential_unavailable")
    return _decrypt_api_key(encrypted_value)


def verify_gpt_live_api_key(api_key: str) -> None:
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, timeout=10.0, max_retries=0)
        client.models.retrieve("gpt-live-1")
    except Exception:
        # Never include provider exception details, request data, or credentials
        # in a response or log entry.
        raise HTTPException(
            status_code=422,
            detail="gpt_live_api_key_connection_failed",
        ) from None


def save_gpt_live_api_key(api_key: str, actor_user_id: str) -> dict[str, Any]:
    encrypted_value = _encrypt_api_key(api_key)
    now = utc_now()
    item = {
        "id": GPT_LIVE_SETTING_ID,
        "tenantId": APPLICATION_SETTINGS_TENANT_ID,
        "encryptedValue": encrypted_value,
        "updatedByUserId": actor_user_id,
        "updatedAt": now,
    }
    store.upsert("application_settings", item)
    return {
        "configured": True,
        "maskedApiKey": _mask_gpt_live_api_key(api_key),
        "updatedAt": now,
    }


def delete_gpt_live_api_key() -> None:
    store.delete("application_settings", GPT_LIVE_SETTING_ID)
