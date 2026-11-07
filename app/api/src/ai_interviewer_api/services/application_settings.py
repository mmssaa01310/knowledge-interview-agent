"""Shared application settings with encrypted, server-only secret storage."""
from __future__ import annotations

import base64
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from openai import OpenAI

from ai_interviewer_api.core.config import settings
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.repositories.store import store


APPLICATION_SETTINGS_TENANT_ID = "system"
OPENAI_SETTING_ID = "openai-api-key"
LEGACY_GPT_LIVE_SETTING_ID = "gpt-live-api-key"
_OPENAI_KEY_AAD = b"kikiori:application-settings:openai-api-key:v1"
_LEGACY_GPT_LIVE_KEY_AAD = b"kikiori:application-settings:gpt-live-api-key:v1"
_CIPHERTEXT_VERSION = "v1"
_VISIBLE_KEY_PREFIX_LENGTH = 8
_VISIBLE_KEY_SUFFIX_LENGTH = 4


def _mask_openai_api_key(api_key: str) -> str:
    prefix = api_key[:_VISIBLE_KEY_PREFIX_LENGTH]
    suffix = api_key[-_VISIBLE_KEY_SUFFIX_LENGTH:]
    masked_length = max(0, len(api_key) - len(prefix) - len(suffix))
    return f"{prefix}{'*' * masked_length}{suffix}"


def _encryption_key() -> bytes:
    encoded_key = settings.openai_settings_encryption_key.strip()
    if not encoded_key:
        raise HTTPException(status_code=503, detail="openai_encryption_key_unavailable")
    try:
        key = base64.b64decode(encoded_key, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise HTTPException(status_code=503, detail="openai_encryption_key_unavailable") from exc
    if len(key) != 32:
        raise HTTPException(status_code=503, detail="openai_encryption_key_unavailable")
    return key


def _encrypt_api_key(api_key: str, *, aad: bytes = _OPENAI_KEY_AAD) -> str:
    nonce = os.urandom(12)
    encrypted = AESGCM(_encryption_key()).encrypt(
        nonce,
        api_key.encode("utf-8"),
        aad,
    )
    return ":".join(
        (
            _CIPHERTEXT_VERSION,
            base64.urlsafe_b64encode(nonce).decode("ascii"),
            base64.urlsafe_b64encode(encrypted).decode("ascii"),
        )
    )


def _decrypt_api_key(value: str, *, aad: bytes = _OPENAI_KEY_AAD) -> str:
    try:
        version, encoded_nonce, encoded_ciphertext = value.split(":", 2)
        if version != _CIPHERTEXT_VERSION:
            raise ValueError("unsupported_ciphertext_version")
        nonce = base64.urlsafe_b64decode(encoded_nonce.encode("ascii"))
        ciphertext = base64.urlsafe_b64decode(encoded_ciphertext.encode("ascii"))
        if len(nonce) != 12:
            raise ValueError("invalid_nonce")
        plaintext = AESGCM(_encryption_key()).decrypt(
            nonce,
            ciphertext,
            aad,
        )
        return plaintext.decode("utf-8")
    except HTTPException:
        raise
    except (InvalidTag, UnicodeDecodeError, ValueError, base64.binascii.Error) as exc:
        raise HTTPException(status_code=503, detail="openai_credential_unavailable") from exc


def _stored_openai_setting() -> tuple[dict[str, Any] | None, bool]:
    setting = store.get("application_settings", OPENAI_SETTING_ID)
    if setting:
        return setting, False
    return store.get("application_settings", LEGACY_GPT_LIVE_SETTING_ID), True


def get_openai_api_key_status() -> dict[str, Any]:
    setting, is_legacy = _stored_openai_setting()
    if not setting:
        return {"configured": False, "maskedApiKey": None, "updatedAt": None}
    if setting.get("tenantId") != APPLICATION_SETTINGS_TENANT_ID:
        raise HTTPException(status_code=503, detail="openai_credential_unavailable")
    encrypted_value = setting.get("encryptedValue")
    if not isinstance(encrypted_value, str):
        raise HTTPException(status_code=503, detail="openai_credential_unavailable")
    api_key = _decrypt_api_key(
        encrypted_value,
        aad=_LEGACY_GPT_LIVE_KEY_AAD if is_legacy else _OPENAI_KEY_AAD,
    )
    return {
        "configured": True,
        "maskedApiKey": _mask_openai_api_key(api_key),
        "updatedAt": setting.get("updatedAt"),
    }


def get_openai_api_key() -> str | None:
    setting, is_legacy = _stored_openai_setting()
    if not setting:
        return None
    if setting.get("tenantId") != APPLICATION_SETTINGS_TENANT_ID:
        raise HTTPException(status_code=503, detail="openai_credential_unavailable")
    encrypted_value = setting.get("encryptedValue")
    if not isinstance(encrypted_value, str):
        raise HTTPException(status_code=503, detail="openai_credential_unavailable")
    return _decrypt_api_key(
        encrypted_value,
        aad=_LEGACY_GPT_LIVE_KEY_AAD if is_legacy else _OPENAI_KEY_AAD,
    )


def verify_openai_api_key(api_key: str) -> None:
    try:
        client = OpenAI(api_key=api_key, timeout=10.0, max_retries=0)
        for model_id in ("gpt-6-luna", "gpt-6.1-terra", "gpt-live-1"):
            client.models.retrieve(model_id)
    except Exception:
        raise HTTPException(
            status_code=422,
            detail="openai_api_key_connection_failed",
        ) from None


def save_openai_api_key(api_key: str, actor_user_id: str) -> dict[str, Any]:
    encrypted_value = _encrypt_api_key(api_key)
    now = utc_now()
    item = {
        "id": OPENAI_SETTING_ID,
        "tenantId": APPLICATION_SETTINGS_TENANT_ID,
        "encryptedValue": encrypted_value,
        "updatedByUserId": actor_user_id,
        "updatedAt": now,
    }
    store.upsert("application_settings", item)
    store.delete("application_settings", LEGACY_GPT_LIVE_SETTING_ID)
    return {
        "configured": True,
        "maskedApiKey": _mask_openai_api_key(api_key),
        "updatedAt": now,
    }


def delete_openai_api_key() -> None:
    store.delete("application_settings", OPENAI_SETTING_ID)
    store.delete("application_settings", LEGACY_GPT_LIVE_SETTING_ID)
