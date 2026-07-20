import asyncio
import json
from types import SimpleNamespace

from aws_sdk_bedrock_runtime.models import (
    BidirectionalOutputPayloadPart,
    InvokeModelWithBidirectionalStreamOutputChunk,
)

from ai_interviewer_voice.runtimes.nova_sonic.runtime import NovaSonicRuntime
from ai_interviewer_voice.services.interview_bridge import InterviewBridgeResult
from ai_interviewer_voice.schemas.sessions import VoiceRuntimeContext


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

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        await asyncio.sleep(0.005)
        return self._events.pop(0)


class FakeDuplexStream:
    def __init__(self, output_events) -> None:
        self.input_stream = FakeInputStream()
        self.output_stream = FakeOutputStream(output_events)

    async def await_output(self):
        return SimpleNamespace(), self.output_stream

    async def close(self) -> None:
        await self.input_stream.close()
        await self.output_stream.close()


class FakeSdkClient:
    def __init__(self, stream) -> None:
        self.stream = stream

    async def invoke_model_with_bidirectional_stream(self, input):
        return self.stream


class FakeInterviewBridge:
    def __init__(self) -> None:
        self.calls = []

    async def load_voice_session(self, voice_session_id: str):
        self.calls.append(("load_voice_session", voice_session_id))
        return SimpleNamespace(
            voice_session_id=voice_session_id,
            record_id="record-1",
            owner_user_id="user-1",
            current_question_id="q-001",
            state_version=1,
            interview_status="active",
        )

    async def save_turn(self, voice_session_id: str, **kwargs):
        self.calls.append(("save_turn", kwargs))
        return SimpleNamespace(turn_id="turn-1", processing_status="pending")

    async def process_saved_turn(self, *, voice_session_id: str, turn_id: str):
        self.calls.append(("process_saved_turn", {"voice_session_id": voice_session_id, "turn_id": turn_id}))
        return InterviewBridgeResult(
            turn_id="turn-1",
            response_id="resp-1",
            reply_text="API reply",
            action="ask_followup",
            question_id="q-002",
            state_version=2,
            interview_status="completed",
        )

    async def create_assistant_event(self, **kwargs):
        self.calls.append(("create_assistant_event", kwargs["event_type"]))


def _chunk(payload: dict) -> InvokeModelWithBidirectionalStreamOutputChunk:
    return InvokeModelWithBidirectionalStreamOutputChunk(
        value=BidirectionalOutputPayloadPart(bytes_=json.dumps(payload).encode("utf-8"))
    )


def test_forced_tool_interview_flow_uses_bridge_and_records_assistant_events() -> None:
    async def run() -> tuple[FakeInterviewBridge, list[dict], object]:
        stream = FakeDuplexStream(
            [
                _chunk({"event": {"completionStart": {"completionId": "c1"}}}),
                _chunk({"event": {"contentStart": {"contentName": "tool-1", "completionId": "c1", "type": "TOOL", "role": "TOOL"}}}),
                _chunk({"event": {"toolUse": {"completionId": "c1", "contentName": "tool-1", "toolUseId": "tool-use-1", "toolName": "process_interview_turn", "content": "{}"}}}),
                _chunk({"event": {"contentStart": {"contentName": "user-1", "completionId": "c1", "type": "TEXT", "role": "USER"}}}),
                _chunk({"event": {"textOutput": {"contentName": "user-1", "completionId": "c1", "content": "朝に発生します"}}}),
                _chunk({"event": {"contentEnd": {"contentName": "user-1", "completionId": "c1"}}}),
                _chunk({"event": {"contentEnd": {"contentName": "tool-1", "completionId": "c1", "type": "TOOL", "stopReason": "TOOL_USE"}}}),
                _chunk({"event": {"contentStart": {"contentName": "assistant-final", "completionId": "c1", "type": "TEXT", "role": "ASSISTANT", "additionalModelFields": {"generationStage": "FINAL"}}}}),
                _chunk({"event": {"textOutput": {"contentName": "assistant-final", "completionId": "c1", "content": "API reply", "additionalModelFields": {"generationStage": "FINAL"}}}}),
                _chunk({"event": {"contentEnd": {"contentName": "assistant-final", "completionId": "c1"}}}),
                _chunk({"event": {"contentStart": {"contentName": "assistant-audio", "completionId": "c1", "type": "AUDIO", "role": "ASSISTANT"}}}),
                _chunk({"event": {"audioOutput": {"contentName": "assistant-audio", "completionId": "c1", "content": "AAE="}}}),
                _chunk({"event": {"contentEnd": {"contentName": "assistant-audio", "completionId": "c1"}}}),
                _chunk({"event": {"completionEnd": {"completionId": "c1", "stopReason": "END_TURN"}}}),
            ]
        )
        bridge = FakeInterviewBridge()
        runtime = NovaSonicRuntime(
            sdk_client=FakeSdkClient(stream),
            interview_bridge=bridge,
        )
        runtime._config = runtime._config.__class__(
            enable_forced_tool_use=True,
            forced_tool_result_delay_ms=1,
        )
        await runtime.start(
            VoiceRuntimeContext(
                voice_session_id="voice-session-1",
                record_id="record-1",
                provider="nova_sonic",
            )
        )
        await asyncio.sleep(0.1)
        payloads = [json.loads(sent.value.bytes_.decode("utf-8")) for sent in stream.input_stream.sent]
        observed = runtime.observed_output
        await runtime.close()
        return bridge, payloads, observed

    bridge, payloads, observed = asyncio.run(run())
    assert bridge.calls[:3] == [
        ("load_voice_session", "voice-session-1"),
        ("save_turn", {"transcript": "朝に発生します", "answer_to_question_id": "q-001"}),
        ("process_saved_turn", {"voice_session_id": "voice-session-1", "turn_id": "turn-1"}),
    ]
    tool_result_payload = next(payload for payload in payloads if "toolResult" in payload["event"])
    assert json.loads(tool_result_payload["event"]["toolResult"]["content"]) == {"reply_text": "API reply"}
    assert "create_assistant_event" in [call[0] for call in bridge.calls]
    assert observed.approved_output_complete is True
    assert observed.approved_protocol_complete is True
