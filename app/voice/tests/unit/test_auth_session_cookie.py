import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request, Response

from ai_interviewer_voice.main import validate_cookie_origin
from ai_interviewer_voice.routers import webrtc


def test_voice_session_cookie_is_forwarded_for_authorization(monkeypatch):
    observed = {}

    async def authorize(session_id, *, bearer_token):
        observed.update(session_id=session_id, bearer_token=bearer_token)
        return SimpleNamespace(provider="openai_realtime")

    monkeypatch.setattr(webrtc._voice_session_service, "authorize_session", authorize)
    asyncio.run(webrtc.get_ice_config("voice-1", authorization=None, session="opaque-session"))
    assert observed == {"session_id": "voice-1", "bearer_token": "opaque-session"}


def test_voice_request_without_session_is_rejected():
    with pytest.raises(HTTPException) as error:
        webrtc._extract_bearer_token(None, None)
    assert error.value.status_code == 401


def test_voice_cookie_request_rejects_untrusted_origin_with_malformed_authorization():
    request = Request({"type": "http", "method": "POST", "path": "/voice/example", "headers": [(b"cookie", b"kikiori_session=test"), (b"origin", b"https://other.example"), (b"authorization", b"invalid")], "scheme": "http", "server": ("localhost", 8010), "query_string": b""})

    async def next_handler(_request):
        return Response(status_code=200)

    result = asyncio.run(validate_cookie_origin(request, next_handler))
    assert result.status_code == 403
