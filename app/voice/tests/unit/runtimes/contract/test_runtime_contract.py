import asyncio
import json
from types import SimpleNamespace

from aws_sdk_bedrock_runtime.models import (
    BidirectionalOutputPayloadPart,
    InvokeModelWithBidirectionalStreamOutputChunk,
)

from ai_interviewer_voice.runtimes.nova_sonic.runtime import NovaSonicRuntime
from ai_interviewer_voice.schemas.events import (
    AssistantSpeechEnded,
    AssistantSpeechStarted,
    AssistantTranscriptFinal,
    RuntimeClosed,
    RuntimeReady,
)
from ai_interviewer_voice.schemas.sessions import AssistantReply, VoiceRuntimeContext


class FakeInputStream:
    def __init__(self) -> None:
        self.sent = []
        self.closed = False

    async def send(self, event) -> None:
        self.sent.append(event)

    async def close(self) -> None:
        self.closed = True


class FakeOutputStream:
    def __init__(self, events) -> None:
        self._events = list(events)
        self.closed = False

    async def receive(self):
        if not self._events:
            return None
        return self._events.pop(0)

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        event = await self.receive()
        if event is None:
            raise StopAsyncIteration
        return event


class FakeDuplexStream:
    def __init__(self, output_events) -> None:
        self.input_stream = FakeInputStream()
        self.output_stream = FakeOutputStream(output_events)
        self.closed = False

    async def await_output(self):
        return SimpleNamespace(), self.output_stream

    async def close(self) -> None:
        self.closed = True
        await self.input_stream.close()
        await self.output_stream.close()


class FakeSdkClient:
    def __init__(self, stream) -> None:
        self._stream = stream
        self.last_input = None

    async def invoke_model_with_bidirectional_stream(self, input):
        self.last_input = input
        return self._stream


def _chunk(payload: dict) -> InvokeModelWithBidirectionalStreamOutputChunk:
    return InvokeModelWithBidirectionalStreamOutputChunk(
        value=BidirectionalOutputPayloadPart(bytes_=json.dumps(payload).encode("utf-8"))
    )


def test_nova_runtime_text_contract() -> None:
    async def run() -> tuple[list[object], FakeDuplexStream]:
        fake_stream = FakeDuplexStream(
            [
                _chunk({"event": {"completionStart": {"completionId": "c1"}}}),
                _chunk({"event": {"contentStart": {"contentName": "assistant-text-1", "completionId": "c1", "type": "TEXT", "role": "ASSISTANT", "additionalModelFields": {"generationStage": "FINAL"}}}}),
                _chunk({"event": {"textOutput": {"contentName": "assistant-text-1", "completionId": "c1", "text": "Connection test successful.", "additionalModelFields": {"generationStage": "FINAL"}}}}),
                _chunk({"event": {"contentEnd": {"contentName": "assistant-text-1", "completionId": "c1"}}}),
                _chunk({"event": {"completionEnd": {"completionId": "c1"}}}),
            ]
        )
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(fake_stream))
        context = VoiceRuntimeContext(
            voice_session_id="voice-session-1",
            record_id="record-1",
            provider="nova_sonic",
        )

        await runtime.start(context)
        await runtime.send_reply(
            AssistantReply(
                turn_id="turn-1",
                response_id="smoke-response-1",
                text="Say exactly: Connection test successful.",
                action="smoke_test",
                question_id=None,
                state_version=1,
            )
        )
        await asyncio.sleep(0.05)
        await runtime.close()

        events = []
        async for event in runtime.events():
            events.append(event)
        return events, fake_stream

    events, fake_stream = asyncio.run(run())

    assert any(isinstance(event, RuntimeReady) for event in events)
    assert any(isinstance(event, AssistantSpeechStarted) for event in events)
    assert any(isinstance(event, AssistantTranscriptFinal) for event in events)
    assert any(isinstance(event, AssistantSpeechEnded) for event in events)
    assert any(isinstance(event, RuntimeClosed) for event in events)
    assert fake_stream.input_stream.closed is True
    assert fake_stream.output_stream.closed is True


def test_nova_runtime_start_text_only_initialization() -> None:
    async def run() -> list[dict]:
        fake_stream = FakeDuplexStream([])
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(fake_stream))
        await runtime.start(
            VoiceRuntimeContext(
                voice_session_id="voice-session-1",
                record_id="record-1",
                provider="nova_sonic",
            )
        )
        await runtime.close()
        return [json.loads(sent.value.bytes_.decode("utf-8")) for sent in fake_stream.input_stream.sent]

    payloads = asyncio.run(run())
    assert [next(iter(item["event"].keys())) for item in payloads[:5]] == [
        "sessionStart",
        "promptStart",
        "contentStart",
        "textInput",
        "contentEnd",
    ]
