from fastapi.routing import APIRoute
from fastapi import HTTPException
import pytest

from ai_interviewer_api.auth.deps import get_current_user
from ai_interviewer_api.routers.live_sessions import router
from ai_interviewer_api.schemas.live import LiveSessionResponse
from ai_interviewer_api.schemas.live import LiveCaptureCreate
import ai_interviewer_api.routers.live_sessions as live_router


def test_live_session_router_exposes_authenticated_post_contract() -> None:
    route = next(route for route in router.routes if route.path == "/api/live/sessions")

    assert isinstance(route, APIRoute)
    assert router.prefix == "/api/live"
    assert route.methods == {"POST"}
    assert route.response_model is LiveSessionResponse
    assert any(dependency.call is get_current_user for dependency in route.dependant.dependencies)


def test_live_capture_requires_authentication() -> None:
    route = next(route for route in router.routes if route.path == "/api/live/captures")
    assert route.methods == {"POST"}
    assert any(dependency.call is get_current_user for dependency in route.dependant.dependencies)


@pytest.mark.parametrize("denied_stage", ["record_scope", "record_permission", "knowledge_access"])
def test_live_capture_checks_permissions_before_llm(monkeypatch, denied_stage) -> None:
    def deny():
        raise HTTPException(403, "forbidden")

    def scoped(table, *_args):
        if denied_stage == "record_scope":
            deny()
        return {"id": "r", "knowledgeId": "k"}

    monkeypatch.setattr(live_router, "get_scoped_item", scoped)
    monkeypatch.setattr(live_router, "require_record_action", lambda *_: deny() if denied_stage == "record_permission" else None)
    monkeypatch.setattr(live_router, "ensure_interviewer_knowledge_access", lambda *_: deny() if denied_stage == "knowledge_access" else None)
    monkeypatch.setattr(live_router, "process_live_capture", lambda *_, **__: pytest.fail("must not invoke LLM"))
    with pytest.raises(HTTPException) as error:
        live_router.capture_live_transcript(LiveCaptureCreate(
            record_id="r", capture_id="c", revision=1, fragments=[{"role": "user", "text": "回答"}],
        ), user=object())
    assert error.value.status_code == 403
