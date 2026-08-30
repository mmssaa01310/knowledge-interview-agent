from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException


@dataclass(frozen=True)
class AuthorizedVoiceSession:
    voice_session_id: str
    record_id: str
    owner_user_id: str
    provider: str
    status: str
    current_question_id: str | None
    state_version: int
    interview_status: str
    interview_locale: str = "ja-JP"
    initial_reply_text: str | None = None
    initial_question_id: str | None = None
    initial_reply_status: str | None = None
    initial_reply_sent_at: str | None = None


@dataclass(frozen=True)
class InitialReplyClaim:
    claimed: bool
    initial_reply_text: str | None = None
    initial_question_id: str | None = None
    reason: str | None = None


class VoiceSessionService:
    def __init__(
        self,
        *,
        api_base_url: str,
        internal_api_token: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_base_url = api_base_url.rstrip("/")
        self._internal_api_token = internal_api_token
        self._http_client = http_client

    async def authorize_session(
        self,
        voice_session_id: str,
        *,
        bearer_token: str,
        timeout_seconds: float = 5.0,
    ) -> AuthorizedVoiceSession:
        session = await self.get_session(
            voice_session_id,
            bearer_token=bearer_token,
            timeout_seconds=timeout_seconds,
        )
        if session.status in {"stopped", "completed"}:
            raise HTTPException(status_code=409, detail=f"voice_session_{session.status}")
        if session.current_question_id is None:
            raise HTTPException(status_code=409, detail="voice_session_missing_current_question")
        return session

    async def get_session(
        self,
        voice_session_id: str,
        *,
        bearer_token: str,
        timeout_seconds: float = 5.0,
    ) -> AuthorizedVoiceSession:
        headers = {
            "Authorization": f"Bearer {bearer_token}",
        }
        async with self._client(headers=headers) as client:
            try:
                response = await client.get(
                    f"/api/voice-sessions/{voice_session_id}",
                    timeout=timeout_seconds,
                )
            except httpx.TimeoutException as exc:
                raise HTTPException(status_code=504, detail="voice_session_lookup_timeout") from exc
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=502, detail="voice_session_lookup_failed") from exc

        if response.status_code == 401:
            raise HTTPException(status_code=401, detail="invalid_token")
        if response.status_code == 403:
            raise HTTPException(status_code=403, detail="voice_session_forbidden")
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="voice_session_not_found")
        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail="voice_session_lookup_failed")

        payload = response.json()
        status = str(payload.get("status") or "active")
        current_question_id = payload.get("currentQuestionId")
        owner_user_id = str(payload.get("ownerUserId") or "")
        if not owner_user_id:
            raise HTTPException(status_code=409, detail="voice_session_missing_owner")

        return AuthorizedVoiceSession(
            voice_session_id=str(payload["id"]),
            record_id=str(payload["recordId"]),
            owner_user_id=owner_user_id,
            provider=str(payload.get("provider") or "nova_sonic"),
            status=status,
            current_question_id=str(current_question_id) if current_question_id is not None else None,
            state_version=int(payload.get("stateVersion") or 0),
            interview_status=status,
            interview_locale=str(payload.get("interviewLocale") or "ja-JP"),
            initial_reply_text=_optional_str(payload.get("initialReplyText")),
            initial_question_id=_optional_str(payload.get("initialQuestionId")),
            initial_reply_status=_optional_str(payload.get("initialReplyStatus")),
            initial_reply_sent_at=_optional_str(payload.get("initialReplySentAt")),
        )

    async def mark_initial_reply_sent(
        self,
        voice_session_id: str,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        headers = {
            "x-internal-api-token": self._internal_api_token,
        }
        async with self._client(headers=headers) as client:
            try:
                response = await client.post(
                    f"/internal/voice-sessions/{voice_session_id}/initial-reply-sent",
                    timeout=timeout_seconds,
                )
                response.raise_for_status()
            except httpx.HTTPError:
                return

    async def claim_initial_reply(
        self,
        voice_session_id: str,
        *,
        timeout_seconds: float = 5.0,
    ) -> InitialReplyClaim:
        headers = {
            "x-internal-api-token": self._internal_api_token,
        }
        async with self._client(headers=headers) as client:
            try:
                response = await client.post(
                    f"/internal/voice-sessions/{voice_session_id}/initial-reply/claim",
                    timeout=timeout_seconds,
                )
                response.raise_for_status()
            except httpx.HTTPError:
                return InitialReplyClaim(claimed=False, reason="claim_failed")
        payload = response.json()
        return InitialReplyClaim(
            claimed=bool(payload.get("claimed")),
            initial_reply_text=_optional_str(payload.get("initialReplyText")),
            initial_question_id=_optional_str(payload.get("initialQuestionId")),
            reason=_optional_str(payload.get("reason")),
        )

    async def mark_initial_reply_failed(
        self,
        voice_session_id: str,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        headers = {
            "x-internal-api-token": self._internal_api_token,
        }
        async with self._client(headers=headers) as client:
            try:
                response = await client.post(
                    f"/internal/voice-sessions/{voice_session_id}/initial-reply-failed",
                    timeout=timeout_seconds,
                )
                response.raise_for_status()
            except httpx.HTTPError:
                return

    async def create_connection_event(
        self,
        voice_session_id: str,
        *,
        event_type: str,
        connection_status: str | None,
        detail: dict[str, Any],
        timeout_seconds: float = 5.0,
    ) -> None:
        headers = {
            "x-internal-api-token": self._internal_api_token,
        }
        async with self._client(headers=headers) as client:
            try:
                response = await client.post(
                    f"/internal/voice-sessions/{voice_session_id}/connection-events",
                    json={
                        "eventType": event_type,
                        "connectionStatus": connection_status,
                        "detail": detail,
                    },
                    timeout=timeout_seconds,
                )
                response.raise_for_status()
            except httpx.HTTPError:
                return

    @asynccontextmanager
    async def _client(self, headers: dict[str, str]):
        if self._http_client is not None:
            yield self._http_client
            return
        async with httpx.AsyncClient(base_url=self._api_base_url, headers=headers) as client:
            yield client


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
