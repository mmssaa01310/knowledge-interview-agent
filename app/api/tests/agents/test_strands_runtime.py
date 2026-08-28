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


def test_create_bedrock_model_omits_temperature_for_gpt_56_profiles(monkeypatch) -> None:
    captured: dict = {}

    def fake_bedrock_model(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(strands_runtime, "BedrockModel", fake_bedrock_model)

    strands_runtime.create_bedrock_model(
        model_id="global.openai.gpt-5.6-luna",
        temperature=0.0,
    )

    assert captured["model_id"] == "global.openai.gpt-5.6-luna"
    assert "temperature" not in captured


def test_create_bedrock_model_keeps_temperature_for_other_models(monkeypatch) -> None:
    captured: dict = {}

    def fake_bedrock_model(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(strands_runtime, "BedrockModel", fake_bedrock_model)

    strands_runtime.create_bedrock_model(
        model_id="apac.amazon.nova-pro-v1:0",
        temperature=0.0,
    )

    assert captured["model_id"] == "apac.amazon.nova-pro-v1:0"
    assert captured["temperature"] == 0.0


def test_invoke_voice_bedrock_text_uses_plain_converse_response(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeClient:
        def converse(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(kwargs)
            return {"output": {"message": {"content": [{"text": '{"outcome":"CONFIRM"}'}]}}}

    strands_runtime._voice_bedrock_runtime_client.cache_clear()
    monkeypatch.setattr(strands_runtime.boto3, "client", lambda *args, **kwargs: FakeClient())

    result = strands_runtime.invoke_voice_bedrock_text(
        system_prompt="JSONのみ",
        prompt="判定",
        max_tokens=32,
    )

    assert result == '{"outcome":"CONFIRM"}'
    assert calls[0]["modelId"] == settings.voice_bedrock_model_id
    assert calls[0]["inferenceConfig"]["maxTokens"] == 32
    strands_runtime._voice_bedrock_runtime_client.cache_clear()
