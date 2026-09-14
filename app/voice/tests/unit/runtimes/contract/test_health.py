from ai_interviewer_voice.config import settings
from ai_interviewer_voice.routers.health import health


def test_health() -> None:
    response = health()

    assert response["status"] == "ok"
    assert response["service"] == "voice"
    assert response["provider"] == settings.runtime_provider
