import json

import pytest

from ai_interviewer_voice.runtimes.openai_realtime.config import (
    OpenAIRealtimeConfig,
    OpenAIRealtimeConfigurationError,
)


def make_config(**overrides) -> OpenAIRealtimeConfig:
    values = {
        "api_key": "sk-project-test-key",
        "enabled": True,
    }
    values.update(overrides)
    return OpenAIRealtimeConfig(**values)


def test_session_payload_uses_semantic_vad_without_automatic_response() -> None:
    payload = make_config().session_payload()

    assert payload["type"] == "realtime"
    assert payload["model"] == "gpt-realtime-2"
    assert payload["output_modalities"] == ["audio"]
    assert payload["parallel_tool_calls"] is False
    assert payload["audio"]["input"]["turn_detection"] == {
        "type": "semantic_vad",
        "eagerness": "low",
        "create_response": False,
        "interrupt_response": False,
    }
    assert "reasoning" not in payload


def test_reasoning_is_omitted_unless_configured() -> None:
    payload = make_config(reasoning_effort="low").session_payload()

    assert payload["reasoning"] == {"effort": "low"}
    assert "model" not in make_config(reasoning_effort="low").sideband_update_payload()
    assert make_config(reasoning_effort="low").sideband_update_payload()["type"] == "realtime"


def test_secret_is_not_in_session_payload() -> None:
    secret = "sk-project-secret-value"
    payload = make_config(api_key=secret).session_payload()

    assert secret not in json.dumps(payload, ensure_ascii=False)


@pytest.mark.parametrize(
    ("overrides", "error_code"),
    [
        ({"enabled": False}, "openai_realtime_disabled"),
        ({"api_key": ""}, "openai_realtime_secret_missing"),
        ({"api_key": "sk-admin-test-key"}, "openai_realtime_admin_key_unsupported"),
        ({"turn_detection": "unknown"}, "openai_realtime_turn_detection_invalid"),
        ({"semantic_eagerness": "very_low"}, "openai_realtime_semantic_eagerness_invalid"),
        ({"reasoning_effort": "none"}, "openai_realtime_reasoning_effort_invalid"),
        ({"reasoning_effort": "unsupported"}, "openai_realtime_reasoning_effort_invalid"),
    ],
)
def test_invalid_configuration_fails_before_call_creation(overrides, error_code) -> None:
    with pytest.raises(OpenAIRealtimeConfigurationError, match=error_code):
        make_config(**overrides).session_payload()
