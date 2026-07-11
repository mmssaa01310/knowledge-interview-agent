from ai_interviewer_api.routers.routes import health


def test_health() -> None:
    assert health() == {"status": "ok"}
