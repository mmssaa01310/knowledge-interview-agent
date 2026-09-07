from __future__ import annotations

import json
from urllib.parse import quote

import httpx


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
            "sdp": ("offer.sdp", offer_sdp, "application/sdp"),
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
