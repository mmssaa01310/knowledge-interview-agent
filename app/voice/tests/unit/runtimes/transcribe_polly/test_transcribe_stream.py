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


class SequencedFakeClient:
    def __init__(self, streams: list[FakeDuplexStream]) -> None:
        self.streams = streams
        self.inputs = []

    async def start_stream_transcription(self, input):
        self.inputs.append(input)
        return self.streams[len(self.inputs) - 1]


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
                            start_time=1.25,
                            end_time=1.9,
                            alternatives=[
                                Alternative(
                                    transcript="設備が停止",
                                    items=[
                                        Item(content="設備", stable=True, confidence=0.8),
                                        Item(content="が", stable=True, confidence=0.7),
                                        Item(content="停止", stable=False, confidence=0.6),
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
    assert results[0].confidence == pytest.approx(0.7)
    assert results[0].start_time == pytest.approx(1.25)
    assert results[0].end_time == pytest.approx(1.9)
    await port.close()
    assert stream.closed is True


@pytest.mark.anyio
async def test_aws_transcribe_port_uses_selected_portuguese_language() -> None:
    stream = FakeDuplexStream()
    client = FakeClient(stream)
    port = AwsTranscribeStreamingPort(
        TranscribePollyRuntimeConfig(
            interview_locale="pt-BR",
            language_code="pt-BR",
        ),
        client=client,
    )

    await port.start(
        on_result=lambda result: asyncio.sleep(0),
        on_reconnecting=lambda attempt: asyncio.sleep(0),
        on_fatal_error=lambda exc: asyncio.sleep(0),
    )

    assert client.inputs[0].language_code.value == "pt-BR"
    await port.close()


@pytest.mark.anyio
async def test_aws_transcribe_port_applies_backpressure_when_queue_is_full() -> None:
    port = AwsTranscribeStreamingPort(
        TranscribePollyRuntimeConfig(reconnect_audio_buffer_ms=100),
        client=FakeClient(FakeDuplexStream()),
    )
    await port._audio_queue.put(bytes(3200))

    send_task = asyncio.create_task(port.send_audio(bytes(3200)))
    await asyncio.sleep(0)
    assert send_task.done() is False

    assert await port._audio_queue.get() == bytes(3200)
    port._audio_queue.task_done()
    await send_task
    assert await port._audio_queue.get() == bytes(3200)
    port._audio_queue.task_done()
    await port.close()


@pytest.mark.anyio
async def test_test_fault_injection_reconnects_to_a_second_aws_stream() -> None:
    first_stream = FakeDuplexStream()
    second_stream = FakeDuplexStream()
    client = SequencedFakeClient([first_stream, second_stream])
    reconnecting = []
    port = AwsTranscribeStreamingPort(
        TranscribePollyRuntimeConfig(test_reconnect_after_audio_chunks=2),
        client=client,
    )
    await port.start(
        on_result=lambda result: asyncio.sleep(0),
        on_reconnecting=lambda attempt: _append(reconnecting, attempt),
        on_fatal_error=lambda exc: asyncio.sleep(0),
    )

    try:
        await port.send_audio(bytes(3200))
        await port.send_audio(bytes(3200))
        for _ in range(100):
            if reconnecting and len(client.inputs) == 2 and first_stream.closed:
                break
            await asyncio.sleep(0.01)

        assert reconnecting == [1]
        assert first_stream.closed is True
        assert second_stream.closed is False
        assert len(client.inputs) == 2

        await port.send_audio(bytes(3200))
        for _ in range(100):
            if second_stream.input_stream.events:
                break
            await asyncio.sleep(0.01)
        assert len(second_stream.input_stream.events) == 1
    finally:
        await port.close()


async def _append(items: list, value) -> None:
    items.append(value)
