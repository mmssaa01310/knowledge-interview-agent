import pytest

from ai_interviewer_api.schemas.voice import VoiceSessionCreate


@pytest.mark.parametrize("provider", ["transcribe_polly", "nova_sonic", "openai_realtime"])
def test_voice_session_schema_accepts_all_supported_providers(provider: str) -> None:
    assert VoiceSessionCreate(provider=provider).provider == provider


def test_voice_session_schema_keeps_transcribe_polly_as_default() -> None:
    assert VoiceSessionCreate().provider == "transcribe_polly"
