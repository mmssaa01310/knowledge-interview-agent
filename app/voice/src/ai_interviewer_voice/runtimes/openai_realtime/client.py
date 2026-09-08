from __future__ import annotations

import json
import logging
from urllib.parse import quote

import httpx


logger = logging.getLogger(__name__)


class OpenAIRealtimeProviderError(RuntimeError):
    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class OpenAIRealtimeCallClient:
    """Small HTTP boundary for the current Realtime WebRTC Calls API."""

    _base_url = "https://api.openai.com/v1"

    def __init__(self, api_key: str, *, timeout_seconds: float = 15.0) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    async def create_call(
        self,
        *,
        offer_sdp: str,
        session: dict,
    ) -> tuple[str, str]:
        if not self._api_key.strip():
            raise OpenAIRealtimeProviderError("openai_realtime_secret_missing")
        headers = {"Authorization": f"Bearer {self._api_key}"}
        files = {
            # The Calls API expects SDP as a multipart form field. Omitting
            # the filename keeps this part a form value (the curl reference
            # uses ``sdp=<offer.sdp``), rather than an uploaded file part.
            "sdp": (None, offer_sdp, "application/sdp"),
            "session": (None, json.dumps(session, ensure_ascii=False), "application/json"),
        }
        try:
            async with httpx.AsyncClient(base_url=self._base_url, headers=headers) as client:
                response = await client.post(
                    "/realtime/calls",
                    files=files,
                    timeout=self._timeout_seconds,
                )
        except httpx.TimeoutException as exc:
            raise OpenAIRealtimeProviderError("openai_realtime_call_timeout") from exc
        except httpx.HTTPError as exc:
            raise OpenAIRealtimeProviderError("openai_realtime_network_error") from exc

        if response.status_code >= 400:
            self._log_upstream_error(response)
        if response.status_code in {401, 403}:
            raise OpenAIRealtimeProviderError(
                "openai_realtime_authentication_failed",
                status_code=response.status_code,
            )
        if response.status_code == 402:
            raise OpenAIRealtimeProviderError(
                "openai_realtime_credit_exhausted",
                status_code=response.status_code,
            )
        if response.status_code == 404:
            raise OpenAIRealtimeProviderError(
                "openai_realtime_model_unavailable",
                status_code=response.status_code,
            )
        if response.status_code == 429:
            raise OpenAIRealtimeProviderError(
                "openai_realtime_rate_limited",
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            raise OpenAIRealtimeProviderError(
                "openai_realtime_call_failed",
                status_code=response.status_code,
            )

        location = response.headers.get("location", "").rstrip("/")
        call_id = location.rsplit("/", 1)[-1] if location else ""
        if not call_id or call_id == location:
            raise OpenAIRealtimeProviderError("openai_realtime_call_id_missing")
        return call_id, response.text

    def _log_upstream_error(self, response: httpx.Response) -> None:
        """Log only structured upstream error metadata, never request secrets."""
        error_type = error_code = error_param = error_message = None
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            payload = None
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            error_type = _safe_log_value(error.get("type"))
            error_code = _safe_log_value(error.get("code"))
            error_param = _safe_log_value(error.get("param"))
            error_message = _safe_log_value(error.get("message"))
        if error_message is None and payload is None:
            error_message = "non_json_error_response"
        logger.warning(
            "openai_realtime_call_creation_failed status=%s error_type=%s "
            "error_code=%s error_param=%s error_message=%s request_id=%s "
            "content_type=%s",
            response.status_code,
            error_type,
            error_code,
            error_param,
            error_message,
            response.headers.get("x-request-id") or response.headers.get("request-id"),
            response.headers.get("content-type"),
        )

    async def hangup(self, call_id: str) -> None:
        if not self._api_key.strip() or not call_id.strip():
            return
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx.AsyncClient(base_url=self._base_url, headers=headers) as client:
                await client.post(
                    f"/realtime/calls/{quote(call_id, safe='')}/hangup",
                    timeout=self._timeout_seconds,
                )
        except httpx.HTTPError:
            # The call may already have ended. Never expose the API key or the
            # response body while cleaning up an abandoned session.
            return


def _safe_log_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\n", " ").replace("\r", " ").strip()
    return text[:500] if text else None
