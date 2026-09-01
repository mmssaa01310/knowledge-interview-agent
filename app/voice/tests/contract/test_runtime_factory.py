import pytest

from ai_interviewer_voice.runtimes.nova_sonic.runtime import NovaSonicRuntime
from ai_interviewer_voice.runtimes.transcribe_polly.runtime import (
    TranscribePollyRuntime,
)
from ai_interviewer_voice.services.runtime_factory import create_runtime


def test_create_runtime_returns_nova_runtime() -> None:
    runtime = create_runtime("nova_sonic")

    assert isinstance(runtime, NovaSonicRuntime)
    assert runtime.provider_name == "nova_sonic"


def test_create_runtime_configures_nova_for_selected_interview_locale() -> None:
    runtime = create_runtime("nova_sonic", "en-US")

    assert isinstance(runtime, NovaSonicRuntime)
    assert "English (en-US)" in runtime._config.system_prompt
    assert runtime._config.interview_error_reply_text == "Something went wrong. Please try again."


def test_create_runtime_returns_transcribe_polly_runtime() -> None:
    runtime = create_runtime("transcribe_polly")

    assert isinstance(runtime, TranscribePollyRuntime)
    assert runtime.provider_name == "transcribe_polly"
    assert runtime.output_sample_rate_hz == 16000
    assert runtime._interview_bridge._turn_process_timeout_seconds == 30


def test_create_runtime_configures_brazilian_portuguese_for_transcribe_and_polly() -> None:
    runtime = create_runtime("transcribe_polly", "pt-BR")

    assert isinstance(runtime, TranscribePollyRuntime)
    assert runtime._config.interview_locale == "pt-BR"
    assert runtime._config.language_code == "pt-BR"
    assert runtime._config.polly_language_code == "pt-BR"
    assert runtime._config.polly_voice_id == "Camila"
    assert runtime._config.listen_ack_text == "Certo."


def test_create_runtime_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError):
        create_runtime("unknown")
