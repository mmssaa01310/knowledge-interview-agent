import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import ai_interviewer_api.services.live_session as live_session_module
from ai_interviewer_api.schemas.live import LiveSessionCreate


def test_create_live_session_passes_shared_secret_key_to_openai(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class FakeLive:
        def create(self, **_kwargs):
            return SimpleNamespace(
                session=SimpleNamespace(id="live_test"),
                transport=SimpleNamespace(type="webrtc", sdp="v=0\r\nanswer"),
            )

    class FakeOpenAI:
        def __init__(self, *, api_key: str):
            captured["api_key"] = api_key
            self.live = FakeLive()

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setattr(
        live_session_module,
        "settings",
        SimpleNamespace(openai_secret_key="shared-server-key"),
    )

    live_session_module.create_live_session(LiveSessionCreate(offer_sdp="v=0\r\noffer"))

    assert captured["api_key"] == "shared-server-key"


def test_create_live_session_does_not_fallback_to_raw_http(monkeypatch) -> None:
    class FakeClient:
        def post(self, *_args, **_kwargs):
            pytest.fail("GPT-Live must use client.live.create")

    monkeypatch.setattr(
        live_session_module,
        "settings",
        SimpleNamespace(openai_secret_key="server-only-test-key"),
    )

    with pytest.raises(HTTPException) as error:
        live_session_module.create_live_session(
            LiveSessionCreate(offer_sdp="v=0\r\noffer"),
            client_factory=lambda: FakeClient(),
        )

    assert error.value.status_code == 502
    assert error.value.detail == "gpt_live_session_creation_failed"


def test_create_live_session_uses_gpt_live_webrtc_and_short_prompt(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeLive:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                session=SimpleNamespace(id="live_test"),
                transport=SimpleNamespace(type="webrtc", sdp="v=0\r\nanswer"),
            )

    class FakeClient:
        live = FakeLive()

    monkeypatch.setattr(
        live_session_module,
        "settings",
        SimpleNamespace(openai_secret_key="server-only-test-key"),
    )

    result = live_session_module.create_live_session(
        LiveSessionCreate(offer_sdp="  v=0\r\noffer\r\n  "),
        client_factory=lambda: FakeClient(),
    )

    assert result.model_dump() == {
        "session": {"id": "live_test"},
        "transport": {"type": "webrtc", "sdp": "v=0\r\nanswer"},
    }
    assert captured["transport"] == {"type": "webrtc", "sdp": "  v=0\r\noffer\r\n  "}
    assert captured["session"]["model"] == "gpt-live-1"
    assert captured["session"]["audio"] == {"output": {"voice": "marin"}}
    assert "speech_stopped" not in captured["session"]["instructions"]
    assert "response.completed" not in captured["session"]["instructions"]


def test_create_live_session_adds_server_owned_checklist_delegation(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeLive:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                session=SimpleNamespace(id="live_test"),
                transport=SimpleNamespace(type="webrtc", sdp="v=0\r\nanswer"),
            )

    class FakeClient:
        live = FakeLive()

    monkeypatch.setattr(
        live_session_module,
        "settings",
        SimpleNamespace(openai_secret_key="server-only-test-key"),
    )
    live_session_module.create_live_session(
        LiveSessionCreate(offer_sdp="v=0\r\noffer"),
        client_factory=lambda: FakeClient(),
        interview_context={
            "fields": [
                {
                    "label": "基本プロフィール",
                    "required_items": [{"label": "お名前"}, {"label": "所属"}],
                    "missing_required_items": ["お名前", "所属"],
                },
            ],
        },
    )

    assert captured["session"]["delegation"] == {"type": "client"}
    assert "基本プロフィール" in captured["session"]["instructions"]
    assert "お名前、所属" in captured["session"]["instructions"]
    assert "without waiting for the application result" in captured["session"]["instructions"]
    assert "After delegation, wait" not in captured["session"]["instructions"]


def test_create_live_session_requires_server_key(monkeypatch) -> None:
    monkeypatch.setattr(
        live_session_module,
        "settings",
        SimpleNamespace(openai_secret_key=""),
    )

    with pytest.raises(HTTPException) as error:
        live_session_module.create_live_session(LiveSessionCreate(offer_sdp="v=0\r\noffer"))

    assert error.value.status_code == 503
    assert error.value.detail == "openai_api_key_missing"


def test_create_live_session_rejects_non_sdp_offer(monkeypatch) -> None:
    monkeypatch.setattr(
        live_session_module,
        "settings",
        SimpleNamespace(openai_secret_key="server-only-test-key"),
    )

    with pytest.raises(HTTPException) as error:
        live_session_module.create_live_session(LiveSessionCreate(offer_sdp="not-sdp"))

    assert error.value.status_code == 422
    assert error.value.detail == "invalid_sdp_offer"


def test_create_live_session_maps_openai_failure_to_bad_gateway(monkeypatch) -> None:
    class FakeLive:
        def create(self, **_kwargs):
            raise RuntimeError("upstream unavailable")

    class FakeClient:
        live = FakeLive()

    monkeypatch.setattr(
        live_session_module,
        "settings",
        SimpleNamespace(openai_secret_key="server-only-test-key"),
    )

    with pytest.raises(HTTPException) as error:
        live_session_module.create_live_session(
            LiveSessionCreate(offer_sdp="v=0\r\noffer"),
            client_factory=lambda: FakeClient(),
        )

    assert error.value.status_code == 502
    assert error.value.detail == "gpt_live_session_creation_failed"


@pytest.mark.parametrize(
    ("response", "expected_detail"),
    [
        (SimpleNamespace(session=SimpleNamespace(id="live_test"), transport=None), "gpt_live_invalid_response"),
        (
            SimpleNamespace(
                session=SimpleNamespace(id="live_test"),
                transport=SimpleNamespace(type="websocket", sdp="v=0\r\nanswer"),
            ),
            "gpt_live_invalid_transport",
        ),
        (
            SimpleNamespace(
                session=SimpleNamespace(id="live_test"),
                transport=SimpleNamespace(type="webrtc", sdp="not-sdp"),
            ),
            "gpt_live_invalid_response",
        ),
    ],
)
def test_create_live_session_rejects_invalid_openai_response(
    monkeypatch,
    response,
    expected_detail: str,
) -> None:
    class FakeLive:
        def create(self, **_kwargs):
            return response

    class FakeClient:
        live = FakeLive()

    monkeypatch.setattr(
        live_session_module,
        "settings",
        SimpleNamespace(openai_secret_key="server-only-test-key"),
    )

    with pytest.raises(HTTPException) as error:
        live_session_module.create_live_session(
            LiveSessionCreate(offer_sdp="v=0\r\noffer"),
            client_factory=lambda: FakeClient(),
        )

    assert error.value.status_code == 502
    assert error.value.detail == expected_detail
