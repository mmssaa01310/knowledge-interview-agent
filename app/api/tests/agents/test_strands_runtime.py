from __future__ import annotations

from ai_interviewer_api.agents.common import strands_runtime
from ai_interviewer_api.core.config import settings


def test_voice_evaluation_bedrock_model_uses_short_timeouts_without_retry(
    monkeypatch,
) -> None:
    captured: dict = {}

    def fake_bedrock_model(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(strands_runtime, "BedrockModel", fake_bedrock_model)

    strands_runtime.create_voice_evaluation_bedrock_model()

    config = captured["boto_client_config"]
    assert captured["model_id"] == settings.voice_bedrock_model_id
    assert captured["temperature"] == 0.0
    assert captured["max_tokens"] == 600
    assert config.connect_timeout == 0.5
    assert config.read_timeout == 1.8
    assert config.retries["total_max_attempts"] == 1
    assert config.retries["mode"] == "standard"
    assert settings.voice_answer_evaluation_deadline_seconds == 2.0
    assert settings.voice_answer_evaluation_deadline_seconds <= 2.0
