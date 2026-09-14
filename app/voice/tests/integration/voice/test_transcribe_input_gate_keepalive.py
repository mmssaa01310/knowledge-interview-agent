from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace

from aws_sdk_transcribe_streaming.models import AudioStreamAudioEvent
import pytest

from ai_interviewer_voice.runtimes.transcribe_polly.config import (
    TranscribePollyRuntimeConfig,
)
from ai_interviewer_voice.runtimes.transcribe_polly.runtime import (
    TranscribePollyRuntime,
)
from ai_interviewer_voice.runtimes.transcribe_polly.transcribe_stream import (
    AwsTranscribeStreamingPort,
)
from ai_interviewer_voice.schemas.audio import AudioFrame
from ai_interviewer_voice.schemas.sessions import VoiceRuntimeContext


class FakeInputStream:
    def __init__(self) -> None:
        self.events: list[AudioStreamAudioEvent] = []

    async def send(self, event: AudioStreamAudioEvent) -> None:
        self.events.append(event)

    async def close(self) -> None:
        return None


class FakeOutputStream:
    def __init__(self) -> None:
        self._events: asyncio.Queue[object | None] = asyncio.Queue()

    def __aiter__(self) -> AsyncIterator[object]:
        return self

    async def __anext__(self) -> object:
        event = await self._events.get()
        if event is None:
            raise StopAsyncIteration
        return event

    async def close(self) -> None:
        await self._events.put(None)


class FakeDuplexStream:
    def __init__(self) -> None:
        self.input_stream = FakeInputStream()
        self.output_stream = FakeOutputStream()

    async def await_output(self) -> tuple[object, FakeOutputStream]:
        return SimpleNamespace(), self.output_stream

    async def close(self) -> None:
        await self.input_stream.close()
        await self.output_stream.close()


class FakeTranscribeClient:
    def __init__(self, stream: FakeDuplexStream) -> None:
        self.stream = stream

    async def start_stream_transcription(self, request: object) -> FakeDuplexStream:
        return self.stream


class FakePolly:
    async def synthesize(self, text: str) -> bytes:
        return bytes(640)

    async def get_cached(self, text: str) -> bytes | None:
        return None

    async def warm(self, texts: tuple[str, ...]) -> None:
        return None


def _frame(sample: int) -> AudioFrame:
    pcm = sample.to_bytes(2, "little", signed=True) * 320
    return AudioFrame(pcm=pcm, sample_rate_hz=16000, channels=1)


async def _wait_for_audio_events(stream: FakeInputStream, count: int) -> None:
    async def wait() -> None:
        while len(stream.events) < count:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout=1)


@pytest.mark.anyio
async def test_closed_runtime_gate_sends_only_silence_and_resumes_live_audio() -> None:
    stream = FakeDuplexStream()
    transcribe = AwsTranscribeStreamingPort(
        TranscribePollyRuntimeConfig(),
        client=FakeTranscribeClient(stream),  # type: ignore[arg-type]
    )
    runtime = TranscribePollyRuntime(transcribe=transcribe, polly=FakePolly())
    await runtime.start(
        VoiceRuntimeContext(
            voice_session_id="vs-keepalive",
            record_id="record-1",
            provider="transcribe_polly",
        )
    )

    runtime._close_input_gate(reason="backend_processing")
    microphone_frame = _frame(1200)
    for _ in range(5):
        await runtime.push_audio(microphone_frame)
    await _wait_for_audio_events(stream.input_stream, 1)

    silence_event = stream.input_stream.events[0]
    assert silence_event.value.audio_chunk == bytes(3200)
    assert runtime._input_available is False
    assert runtime._turn_active is False

    await runtime._resume_input_after_formal_reply(
        "ANSWER_LISTENING",
        reason="test_gate_open",
    )
    for _ in range(5):
        await runtime.push_audio(microphone_frame)
    await _wait_for_audio_events(stream.input_stream, 2)

    live_audio_event = stream.input_stream.events[1]
    assert live_audio_event.value.audio_chunk == microphone_frame.pcm * 5
    assert runtime._input_available is True
    await runtime.close()
