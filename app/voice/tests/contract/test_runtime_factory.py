import pytest

from ai_interviewer_voice.runtimes.nova_sonic.runtime import NovaSonicRuntime
from ai_interviewer_voice.services.runtime_factory import create_runtime


def test_create_runtime_returns_nova_runtime() -> None:
    runtime = create_runtime("nova_sonic")

    assert isinstance(runtime, NovaSonicRuntime)
    assert runtime.provider_name == "nova_sonic"


def test_create_runtime_rejects_unimplemented_provider() -> None:
    with pytest.raises(NotImplementedError):
        create_runtime("transcribe_polly")


def test_create_runtime_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError):
        create_runtime("unknown")
