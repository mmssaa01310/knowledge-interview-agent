from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx


class InterviewApiError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class VoiceSessionSnapshot:
    voice_session_id: str
    record_id: str
    owner_user_id: str | None
    current_question_id: str | None
    state_version: int
    interview_status: str
    interview_locale: str = "ja-JP"


@dataclass(frozen=True)
class InitialReplyClaimResult:
    claimed: bool
    initial_reply_text: str | None = None
    initial_question_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class VoiceTurnSaveResult:
    turn_id: str
    processing_status: str
    processing_mode: str = "answer_evaluation"


@dataclass(frozen=True)
class VoiceTurnIntentResult:
    turn_type: str


@dataclass(frozen=True)
class VoiceTurnProcessResult:
    turn_id: str
    response_id: str
    reply_text: str
    action: str
    question_id: str | None
    state_version: int
    interview_status: str
    retrieval_policy: str | None = None
    retrieval_executed: bool = False
    turn_type: str = "ANSWER"


class InterviewApiClient:
    def __init__(
        self,
        base_url: str,
        internal_api_token: str,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._internal_api_token = internal_api_token
        self._http_client = http_client
        self._owned_http_client: httpx.AsyncClient | None = None

    @property
    def headers(self) -> dict[str, str]:
        return {
            "x-internal-api-token": self._internal_api_token,
        }

    @asynccontextmanager
    async def _client(self) -> Any:
        if self._http_client is not None:
            yield self._http_client
            return
        if self._owned_http_client is None or self._owned_http_client.is_closed:
            self._owned_http_client = httpx.AsyncClient(base_url=self._base_url, headers=self.headers)
        yield self._owned_http_client

    async def get_voice_session(
        self,
        voice_session_id: str,
        *,
        timeout_seconds: float = 5.0,
    ) -> VoiceSessionSnapshot:
        async with self._client() as client:
            response = await self._request(
                client,
                "GET",
                f"/internal/voice-sessions/{voice_session_id}",
                timeout_seconds=timeout_seconds,
                failure_code="voice_session_lookup_failed",
            )
        payload = response.json()
        return VoiceSessionSnapshot(
            voice_session_id=str(payload["id"]),
            record_id=str(payload["recordId"]),
            owner_user_id=_optional_str(payload.get("ownerUserId")),
            current_question_id=_optional_str(payload.get("currentQuestionId")),
            state_version=int(payload.get("stateVersion") or 0),
            interview_status=str(payload.get("status") or "active"),
            interview_locale=str(payload.get("interviewLocale") or "ja-JP"),
        )

    async def claim_initial_reply(
        self,
        voice_session_id: str,
        *,
        timeout_seconds: float = 5.0,
    ) -> InitialReplyClaimResult:
        async with self._client() as client:
            response = await self._request(
                client,
                "POST",
                f"/internal/voice-sessions/{voice_session_id}/initial-reply/claim",
                timeout_seconds=timeout_seconds,
                failure_code="initial_reply_claim_failed",
            )
        payload = response.json()
        return InitialReplyClaimResult(
            claimed=bool(payload.get("claimed")),
            initial_reply_text=_optional_str(payload.get("initialReplyText")),
            initial_question_id=_optional_str(payload.get("initialQuestionId")),
            reason=_optional_str(payload.get("reason")),
        )

    async def mark_initial_reply_sent(
        self,
        voice_session_id: str,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        async with self._client() as client:
            await self._request(
                client,
                "POST",
                f"/internal/voice-sessions/{voice_session_id}/initial-reply-sent",
                timeout_seconds=timeout_seconds,
                failure_code="initial_reply_mark_sent_failed",
            )

    async def mark_initial_reply_failed(
        self,
        voice_session_id: str,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        async with self._client() as client:
            await self._request(
                client,
                "POST",
                f"/internal/voice-sessions/{voice_session_id}/initial-reply-failed",
                timeout_seconds=timeout_seconds,
                failure_code="initial_reply_mark_failed_failed",
            )

    async def save_turn(
        self,
        voice_session_id: str,
        *,
        transcript: str,
        answer_to_question_id: str | None,
        turn_type: str = "ANSWER",
        client_turn_id: str | None = None,
        expected_state_version: int | None = None,
        started_at_ms: int | None = None,
        ended_at_ms: int | None = None,
        timeout_seconds: float = 5.0,
    ) -> VoiceTurnSaveResult:
        async with self._client() as client:
            response = await self._request(
                client,
                "POST",
                f"/internal/voice-sessions/{voice_session_id}/turns",
                json={
                    "transcript": transcript,
                    "turnType": turn_type,
                    "answerToQuestionId": answer_to_question_id,
                    "clientTurnId": client_turn_id,
                    "expectedStateVersion": expected_state_version,
                    "startedAtMs": started_at_ms,
                    "endedAtMs": ended_at_ms,
                },
                timeout_seconds=timeout_seconds,
                failure_code="turn_save_failed",
            )
        payload = response.json()
        return VoiceTurnSaveResult(
            turn_id=str(payload["id"]),
            processing_status=str(payload.get("processingStatus") or "pending"),
            processing_mode=str(payload.get("processingMode") or "answer_evaluation"),
        )

    async def classify_voice_turn_intent(
        self,
        voice_session_id: str,
        *,
        transcript: str,
        answer_to_question_id: str | None,
        expected_state_version: int | None = None,
        timeout_seconds: float = 5.0,
    ) -> VoiceTurnIntentResult:
        async with self._client() as client:
            response = await self._request(
                client,
                "POST",
                f"/internal/voice-sessions/{voice_session_id}/turn-intent",
                json={
                    "transcript": transcript,
                    "answerToQuestionId": answer_to_question_id,
                    "expectedStateVersion": expected_state_version,
                },
                timeout_seconds=timeout_seconds,
                failure_code="turn_intent_classification_failed",
            )
        payload = response.json()
        turn_type = str(payload.get("turnType") or "").strip()
        if turn_type not in {"ANSWER", "CONTROL"}:
            raise InterviewApiError(
                "turn_intent_classification_failed",
                "invalid turn intent response",
            )
        return VoiceTurnIntentResult(turn_type=turn_type)

    async def cancel_turn(
        self,
        voice_session_id: str,
        *,
        client_turn_id: str,
        expected_state_version: int,
        timeout_seconds: float = 5.0,
    ) -> None:
        async with self._client() as client:
            await self._request(
                client,
                "POST",
                f"/internal/voice-sessions/{voice_session_id}/turns/cancel",
                json={
                    "clientTurnId": client_turn_id,
                    "expectedStateVersion": expected_state_version,
                },
                timeout_seconds=timeout_seconds,
                failure_code="turn_cancel_failed",
            )

    async def process_turn(
        self,
        voice_session_id: str,
        turn_id: str,
        *,
        timeout_seconds: float = 5.0,
    ) -> VoiceTurnProcessResult:
        async with self._client() as client:
            response = await self._request(
                client,
                "POST",
                f"/internal/voice-sessions/{voice_session_id}/turns/{turn_id}/process",
                timeout_seconds=timeout_seconds,
                failure_code="turn_process_failed",
            )
        payload = response.json()
        voice_session = payload.get("voiceSession") if isinstance(payload.get("voiceSession"), dict) else {}
        voice_turn = payload.get("voiceTurn") if isinstance(payload.get("voiceTurn"), dict) else {}
        return VoiceTurnProcessResult(
            turn_id=str(payload["turnId"]),
            response_id=str(payload["responseId"]),
            reply_text=str(payload["text"]),
            action=str(payload.get("action") or ""),
            question_id=_optional_str(payload.get("questionId")),
            state_version=int(payload.get("stateVersion") or 0),
            interview_status=str(voice_session.get("status") or "active"),
            retrieval_policy=_optional_str(payload.get("retrievalPolicy")),
            retrieval_executed=bool(payload.get("retrievalExecuted", False)),
            turn_type=str(voice_turn.get("turnType") or "ANSWER"),
        )

    async def create_assistant_event(
        self,
        voice_session_id: str,
        *,
        event_type: str,
        response_id: str | None,
        generation: int | None,
        transcript: str | None,
        detail: dict[str, Any],
        timeout_seconds: float = 5.0,
    ) -> None:
        async with self._client() as client:
            await self._request(
                client,
                "POST",
                f"/internal/voice-sessions/{voice_session_id}/assistant-events",
                json={
                    "eventType": event_type,
                    "responseId": response_id,
                    "generation": generation,
                    "transcript": transcript,
                    "detail": detail,
                },
                timeout_seconds=timeout_seconds,
                failure_code="assistant_event_failed",
            )

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        timeout_seconds: float,
        failure_code: str,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            response = await client.request(
                method,
                path,
                json=json,
                timeout=timeout_seconds,
                headers=self.headers if self._http_client is not None else None,
            )
        except httpx.TimeoutException as exc:
            raise InterviewApiError(f"{failure_code}_timeout", str(exc) or failure_code) from exc
        except httpx.HTTPError as exc:
            raise InterviewApiError(failure_code, str(exc) or failure_code) from exc

        if response.status_code == 401:
            raise InterviewApiError("unauthorized", "internal api unauthorized", status_code=401)
        if response.status_code == 403:
            raise InterviewApiError("unauthorized", "internal api forbidden", status_code=403)
        if response.status_code == 404:
            raise InterviewApiError("voice_session_closed", "voice session not found", status_code=404)
        if response.status_code == 409:
            detail = response.json().get("detail")
            code = str(detail or "turn_state_conflict")
            raise InterviewApiError(code, code, status_code=409)
        if response.status_code >= 400:
            raise InterviewApiError(
                failure_code,
                f"http {response.status_code}",
                status_code=response.status_code,
            )
        return response


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
