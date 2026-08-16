from __future__ import annotations
from io import BytesIO

import pytest

from ai_interviewer_voice.runtimes.transcribe_polly.config import (
    TranscribePollyRuntimeConfig,
)
from ai_interviewer_voice.runtimes.transcribe_polly.polly_synthesizer import (
    PollySynthesizer,
)


class StubPollyClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def synthesize_speech(self, **kwargs):
        self.calls.append(kwargs)
        return {"AudioStream": BytesIO(bytes(640))}


@pytest.mark.anyio
async def test_polly_synthesizer_requests_neural_16khz_and_caches() -> None:
    client = StubPollyClient()
    synthesizer = PollySynthesizer(
        TranscribePollyRuntimeConfig(polly_voice_id="Kazuha"),
        client=client,
    )

    first = await synthesizer.synthesize("こんにちは。")
    second = await synthesizer.synthesize("こんにちは。")

    assert first == second == bytes(640)
    assert len(client.calls) == 1
    assert client.calls[0]["Engine"] == "neural"
    assert client.calls[0]["VoiceId"] == "Kazuha"
    assert client.calls[0]["OutputFormat"] == "pcm"
    assert client.calls[0]["SampleRate"] == "16000"
