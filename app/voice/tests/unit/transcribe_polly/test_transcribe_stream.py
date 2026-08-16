from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from aws_sdk_transcribe_streaming.models import (
    Alternative,
    AudioStreamAudioEvent,
    Item,
    Result,
    Transcript,
    TranscriptEvent,
    TranscriptResultStreamTranscriptEvent,
)

from ai_interviewer_voice.runtimes.transcribe_polly.config import (
    TranscribePollyRuntimeConfig,
)
from ai_interviewer_voice.runtimes.transcribe_polly.transcribe_stream import (
    AwsTranscribeStreamingPort,
)


class FakeInputStream:
    def __init__(self) -> None:
        self.events = []
        self.closed = False

    async def send(self, event) -> None:
        self.events.append(event)

    async def close(self) -> None:
        self.closed = True


class FakeOutputStream:
    def __init__(self) -> None:
        self.queue = asyncio.Queue()
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        event = await self.queue.get()
        if event is None:
            raise StopAsyncIteration
        return event

    async def close(self) -> None:
        self.closed = True


class FakeDuplexStream:
    def __init__(self) -> None:
        self.input_stream = FakeInputStream()
        self.output_stream = FakeOutputStream()
        self.closed = False

    async def await_output(self):
        return SimpleNamespace(), self.output_stream

    async def close(self) -> None:
        self.closed = True
        await self.input_stream.close()
        await self.output_stream.close()


class FakeClient:
    def __init__(self, stream: FakeDuplexStream) -> None:
        self.stream = stream
        self.inputs = []

    async def start_stream_transcription(self, input):
        self.inputs.append(input)
        return self.stream


@pytest.mark.anyio
async def test_aws_transcribe_port_uses_stabilized_ja_jp_and_maps_results() -> None:
    stream = FakeDuplexStream()
    client = FakeClient(stream)
    results = []

    async def collect_result(result) -> None:
        results.append(result)

    port = AwsTranscribeStreamingPort(
        TranscribePollyRuntimeConfig(),
        client=client,
    )
    await port.start(
        on_result=collect_result,
        on_reconnecting=lambda attempt: asyncio.sleep(0),
        on_fatal_error=lambda exc: asyncio.sleep(0),
    )

    request = client.inputs[0]
    assert request.language_code.value == "ja-JP"
    assert request.media_sample_rate_hertz == 16000
    assert request.enable_partial_results_stabilization is True
    assert request.partial_results_stability.value == "medium"

    await port.send_audio(bytes(3200))
    for _ in range(20):
        if stream.input_stream.events:
            break
        await asyncio.sleep(0)
    sent = stream.input_stream.events[0]
    assert isinstance(sent, AudioStreamAudioEvent)
    assert sent.value.audio_chunk == bytes(3200)

    await stream.output_stream.queue.put(
        TranscriptResultStreamTranscriptEvent(
            TranscriptEvent(
                transcript=Transcript(
                    results=[
                        Result(
                            result_id="r-1",
                            is_partial=True,
                            alternatives=[
                                Alternative(
                                    transcript="設備が停止",
                                    items=[
                                        Item(content="設備", stable=True),
                                        Item(content="が", stable=True),
                                        Item(content="停止", stable=False),
                                    ],
                                )
                            ],
                        )
                    ]
                )
            )
        )
    )
    for _ in range(20):
        if results:
            break
        await asyncio.sleep(0)

    assert results[0].text == "設備が停止"
    assert results[0].stable_text == "設備が"
    assert results[0].is_partial is True
    await port.close()
    assert stream.closed is True
