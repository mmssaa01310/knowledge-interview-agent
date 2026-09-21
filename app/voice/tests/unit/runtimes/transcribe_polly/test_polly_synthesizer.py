from __future__ import annotations

import asyncio
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


class ChunkedAudioStream:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = iter(chunks)
        self.read_sizes: list[int] = []
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return next(self._chunks, b"")

    def close(self) -> None:
        self.closed = True


class ChunkedPollyClient(StubPollyClient):
    def __init__(self, stream: ChunkedAudioStream) -> None:
        super().__init__()
        self.stream = stream

    def synthesize_speech(self, **kwargs):
        self.calls.append(kwargs)
        return {"AudioStream": self.stream}


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
    assert client.calls[0]["TextType"] == "ssml"
    assert client.calls[0]["Text"] == (
        '<speak><prosody rate="115%">こんにちは。</prosody></speak>'
    )


@pytest.mark.anyio
async def test_polly_synthesizer_escapes_text_and_accepts_preset_speed() -> None:
    client = StubPollyClient()
    synthesizer = PollySynthesizer(
        TranscribePollyRuntimeConfig(
            polly_voice_id="fast-test",
            polly_speech_rate="fast",
        ),
        client=client,
    )

    await synthesizer.synthesize('A & <B> "quoted"')

    assert client.calls[0]["Text"] == (
        '<speak><prosody rate="fast">A &amp; &lt;B&gt; "quoted"</prosody></speak>'
    )


def test_polly_synthesizer_rejects_invalid_speed() -> None:
    with pytest.raises(ValueError, match="POLLY_SPEECH_RATE"):
        PollySynthesizer(
            TranscribePollyRuntimeConfig(polly_speech_rate="201%"),
            client=StubPollyClient(),
        )


@pytest.mark.anyio
async def test_polly_synthesizer_cache_key_includes_speech_rate() -> None:
    fast_client = StubPollyClient()
    slow_client = StubPollyClient()
    fast = PollySynthesizer(
        TranscribePollyRuntimeConfig(
            polly_voice_id="cache-rate-test",
            polly_speech_rate="110%",
        ),
        client=fast_client,
    )
    slow = PollySynthesizer(
        TranscribePollyRuntimeConfig(
            polly_voice_id="cache-rate-test",
            polly_speech_rate="120%",
        ),
        client=slow_client,
    )

    await fast.synthesize("速度キャッシュを分ける")
    await slow.synthesize("速度キャッシュを分ける")

    assert len(fast_client.calls) == 1
    assert len(slow_client.calls) == 1


@pytest.mark.anyio
async def test_polly_synthesizer_uses_selected_portuguese_language_and_voice() -> None:
    client = StubPollyClient()
    synthesizer = PollySynthesizer(
        TranscribePollyRuntimeConfig(
            interview_locale="pt-BR",
            polly_language_code="pt-BR",
            polly_voice_id="Camila",
        ),
        client=client,
    )

    await synthesizer.synthesize("Olá.")

    assert client.calls[0]["LanguageCode"] == "pt-BR"
    assert client.calls[0]["VoiceId"] == "Camila"


@pytest.mark.anyio
async def test_polly_synthesizer_streams_pcm_fragments_and_closes_audio_stream() -> None:
    stream = ChunkedAudioStream([b"\x01\x00" * 16, b"\x02\x00" * 16])
    client = ChunkedPollyClient(stream)
    synthesizer = PollySynthesizer(
        TranscribePollyRuntimeConfig(polly_voice_id="stream-test"),
        client=client,
    )

    chunks = [chunk async for chunk in synthesizer.synthesize_stream("ストリーム")]

    assert chunks == [b"\x01\x00" * 16, b"\x02\x00" * 16]
    assert stream.read_sizes == [2048, 2048, 2048]
    assert stream.closed is True
    assert len(client.calls) == 1


@pytest.mark.anyio
async def test_polly_synthesizer_deduplicates_concurrent_synthesis_requests() -> None:
    client = StubPollyClient()
    synthesizer = PollySynthesizer(
        TranscribePollyRuntimeConfig(polly_voice_id="concurrent-test"),
        client=client,
    )

    first, second = await asyncio.gather(
        synthesizer.synthesize("同じ音声を同時生成"),
        synthesizer.synthesize("同じ音声を同時生成"),
    )

    assert first == second == bytes(640)
    assert len(client.calls) == 1
