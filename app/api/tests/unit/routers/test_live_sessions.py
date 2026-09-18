from fastapi.routing import APIRoute

from ai_interviewer_api.auth.deps import get_current_user
from ai_interviewer_api.routers.live_sessions import router
from ai_interviewer_api.schemas.live import LiveSessionResponse


def test_live_session_router_exposes_authenticated_post_contract() -> None:
    route = next(route for route in router.routes if route.path == "/api/live/sessions")

    assert isinstance(route, APIRoute)
    assert router.prefix == "/api/live"
    assert route.methods == {"POST"}
    assert route.response_model is LiveSessionResponse
    assert any(dependency.call is get_current_user for dependency in route.dependant.dependencies)
