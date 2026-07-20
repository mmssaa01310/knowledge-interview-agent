import asyncio
import builtins
import json
import logging
from types import SimpleNamespace

import pytest
from aws_sdk_bedrock_runtime.models import (
    BidirectionalOutputPayloadPart,
    InvokeModelWithBidirectionalStreamOutputChunk,
    InvokeModelWithBidirectionalStreamOutputModelStreamErrorException,
    ModelStreamErrorException,
)

from ai_interviewer_voice.runtimes.nova_sonic.protocol.payloads import (
    SYSTEM_CONTENT_NAME,
    build_runtime_start_sequence,
    build_user_text_sequence,
    dumps_event_payload,
    sanitize_payload_for_debug,
)
from ai_interviewer_voice.runtimes.nova_sonic.config import NovaSonicRuntimeConfig
from ai_interviewer_voice.runtimes.nova_sonic.response_controller import ResponseAuthorizationState
from ai_interviewer_voice.runtimes.nova_sonic.runtime import NovaSonicRuntime
from ai_interviewer_voice.runtimes.nova_sonic.session_state import CompletionState, CompletionStatus, PendingToolCall
from ai_interviewer_voice.services.interview_bridge import InterviewApiError, InterviewBridgeResult
from ai_interviewer_voice.schemas.events import (
    AssistantAudioChunk,
    AssistantSpeechEnded,
    AssistantSpeechStarted,
    AssistantTranscriptFinal,
    RuntimeClosed,
    RuntimeError,
    RuntimeReady,
    UserSpeechEnded,
    UserSpeechStarted,
)
from ai_interviewer_voice.schemas.sessions import AssistantReply, VoiceRuntimeContext


class FakeInterviewBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.load_session_result = SimpleNamespace(
            voice_session_id="voice-session-1",
            record_id="record-1",
            owner_user_id="user-1",
            current_question_id="q-001",
            state_version=1,
            interview_status="active",
        )
        self.save_turn_result = SimpleNamespace(turn_id="turn-1", processing_status="pending", processing_mode="confirmation_reply")
        self.process_turn_result = InterviewBridgeResult(
            turn_id="turn-1",
            response_id="resp-1",
            reply_text="API reply",
            action="ask_followup",
            question_id="q-002",
            state_version=2,
            interview_status="active",
        )
        self.save_error: Exception | None = None
        self.process_error: Exception | None = None
        self.initial_claimed = False

    async def load_voice_session(self, voice_session_id: str):
        self.calls.append(("load_voice_session", {"voice_session_id": voice_session_id}))
        return self.load_session_result

    async def save_turn(self, voice_session_id: str, **kwargs):
        self.calls.append(("save_turn", {"voice_session_id": voice_session_id, **kwargs}))
        if self.save_error is not None:
            raise self.save_error
        return self.save_turn_result

    async def process_saved_turn(self, *, voice_session_id: str, turn_id: str):
        self.calls.append(("process_saved_turn", {"voice_session_id": voice_session_id, "turn_id": turn_id}))
        if self.process_error is not None:
            raise self.process_error
        return self.process_turn_result

    async def create_assistant_event(self, **kwargs):
        self.calls.append(("create_assistant_event", kwargs))

    async def claim_initial_reply(self, voice_session_id: str):
        self.calls.append(("claim_initial_reply", {"voice_session_id": voice_session_id}))
        self.initial_claimed = True
        return SimpleNamespace(
            claimed=True,
            initial_reply_text="これからインタビューを開始します。あなたの名前は？",
            initial_question_id="q-001",
            reason=None,
        )

    async def mark_initial_reply_sent(self, voice_session_id: str):
        self.calls.append(("mark_initial_reply_sent", {"voice_session_id": voice_session_id}))


class FakeInputStream:
    def __init__(self, *, fail_after: int | None = None) -> None:
        self.sent = []
        self.closed = False
        self.fail_after = fail_after

    async def send(self, event) -> None:
        if self.fail_after is not None and len(self.sent) >= self.fail_after:
            raise builtins.RuntimeError("send failed")
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
    def __init__(self, output_events, *, fail_after: int | None = None, await_delay: float = 0.0) -> None:
        self.input_stream = FakeInputStream(fail_after=fail_after)
        self.output_stream = FakeOutputStream(output_events)
        self.await_delay = await_delay
        self.closed = False

    async def await_output(self):
        if self.await_delay:
            await asyncio.sleep(self.await_delay)
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


def _chunk(payload: dict | bytes):
    if isinstance(payload, bytes):
        raw = payload
    else:
        raw = json.dumps(payload).encode("utf-8")
    return InvokeModelWithBidirectionalStreamOutputChunk(
        value=BidirectionalOutputPayloadPart(bytes_=raw)
    )


async def _collect_events(runtime: NovaSonicRuntime) -> list[object]:
    events = []
    async for event in runtime.events():
        events.append(event)
    return events


def _context() -> VoiceRuntimeContext:
    return VoiceRuntimeContext(
        voice_session_id="voice-session-1",
        record_id="record-1",
        provider="nova_sonic",
    )


def _reply(text: str = "Say exactly: Connection test successful.") -> AssistantReply:
    return AssistantReply(
        turn_id="turn-1",
        response_id="response-1",
        text=text,
        action="smoke_test",
        question_id=None,
        state_version=1,
    )


def _decode_sent_payloads(stream: FakeDuplexStream) -> list[dict]:
    return [json.loads(sent.value.bytes_.decode("utf-8")) for sent in stream.input_stream.sent]


def test_start_does_not_send_audio_content_and_starts_receiver() -> None:
    async def run() -> tuple[list[str], bool]:
        stream = FakeDuplexStream([])
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream))
        await runtime.start(_context())
        event_names = [next(iter(payload["event"].keys())) for payload in _decode_sent_payloads(stream)]
        started = runtime._receive_task is not None and not runtime._receive_task.done()
        await runtime.close()
        return event_names, started

    event_names, started = asyncio.run(run())
    assert event_names[:5] == ["sessionStart", "promptStart", "contentStart", "textInput", "contentEnd"]
    assert started is True


def test_system_prompt_payload_has_expected_role_and_types() -> None:
    payloads = build_runtime_start_sequence(
        prompt_name="prompt-1",
        system_content_name=SYSTEM_CONTENT_NAME,
        system_prompt='He said "hello".',
        endpointing_sensitivity="HIGH",
    )
    session_payload = payloads[0][1]["event"]["sessionStart"]
    content_start = payloads[2][1]["event"]["contentStart"]
    text_payload = json.loads(dumps_event_payload(payloads[3][1]).decode("utf-8"))

    assert isinstance(session_payload["inferenceConfiguration"]["maxTokens"], int)
    assert isinstance(session_payload["inferenceConfiguration"]["topP"], float)
    assert isinstance(session_payload["inferenceConfiguration"]["temperature"], float)
    assert session_payload["turnDetectionConfiguration"]["endpointingSensitivity"] == "HIGH"
    assert content_start["interactive"] is False
    assert content_start["role"] == "SYSTEM"
    assert text_payload["event"]["textInput"]["content"] == 'He said "hello".'


def test_forced_tool_prompt_start_includes_tool_use_output_configuration() -> None:
    payloads = build_runtime_start_sequence(
        prompt_name="prompt-1",
        system_content_name=SYSTEM_CONTENT_NAME,
        system_prompt="system",
        forced_tool_name="process_interview_turn",
    )

    prompt_start = payloads[1][1]["event"]["promptStart"]
    assert prompt_start["toolUseOutputConfiguration"] == {"mediaType": "application/json"}
    assert prompt_start["toolConfiguration"]["toolChoice"]["tool"]["name"] == "process_interview_turn"


def test_prompt_start_uses_configured_voice_id() -> None:
    payloads = build_runtime_start_sequence(
        prompt_name="prompt-1",
        system_content_name=SYSTEM_CONTENT_NAME,
        system_prompt="system",
        voice_id="tiffany",
    )

    prompt_start = payloads[1][1]["event"]["promptStart"]
    assert prompt_start["audioOutputConfiguration"]["voiceId"] == "tiffany"


def test_empty_voice_id_is_rejected() -> None:
    with pytest.raises(ValueError):
        NovaSonicRuntimeConfig(voice_id=" ")


def test_send_reply_uses_user_role_and_unique_content_name_with_stable_prompt_name() -> None:
    async def run() -> list[dict]:
        stream = FakeDuplexStream([])
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream))
        await runtime.start(_context())
        await runtime.send_reply(_reply("first"))
        await runtime.send_reply(_reply("second"))
        await runtime.close()
        return _decode_sent_payloads(stream)

    payloads = asyncio.run(run())
    first_user_start = payloads[5]["event"]["contentStart"]
    second_user_start = payloads[8]["event"]["contentStart"]
    first_user_text = payloads[6]["event"]["textInput"]
    second_user_text = payloads[9]["event"]["textInput"]

    assert first_user_start["interactive"] is True
    assert first_user_start["role"] == "USER"
    assert first_user_start["promptName"] == second_user_start["promptName"]
    assert first_user_start["contentName"] != second_user_start["contentName"]
    assert first_user_text["contentName"] == first_user_start["contentName"]
    assert second_user_text["contentName"] == second_user_start["contentName"]


def test_start_audio_input_is_explicit() -> None:
    async def run() -> list[str]:
        stream = FakeDuplexStream([])
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream))
        await runtime.start(_context())
        before = [next(iter(payload["event"].keys())) for payload in _decode_sent_payloads(stream)]
        await runtime.start_audio_input()
        after = [next(iter(payload["event"].keys())) for payload in _decode_sent_payloads(stream)]
        await runtime.close()
        return before, after

    before, after = asyncio.run(run())
    assert before[:5] == ["sessionStart", "promptStart", "contentStart", "textInput", "contentEnd"]
    assert after[-1] == "contentStart"


def test_push_audio_serializes_base64_json_not_raw_pcm() -> None:
    async def run() -> dict:
        pcm = bytes(2048)
        stream = FakeDuplexStream([])
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream))
        await runtime.start(_context())
        await runtime.start_audio_input()
        await runtime.push_audio(pcm)
        await runtime.close()
        payload = json.loads(stream.input_stream.sent[6].value.bytes_.decode("utf-8"))
        return payload

    payload = asyncio.run(run())
    audio_input = payload["event"]["audioInput"]
    assert audio_input["promptName"] == "prompt-voice-session-1"
    assert audio_input["contentName"] == "audio-1"
    assert isinstance(audio_input["content"], str)
    assert not audio_input["content"].startswith("b'")


def test_start_cleanup_runs_when_initial_event_send_fails() -> None:
    async def run() -> tuple[bool, bool, bool]:
        stream = FakeDuplexStream([], fail_after=2)
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream))
        with pytest.raises(builtins.RuntimeError):
            await runtime.start(_context())
        return stream.input_stream.closed, stream.output_stream.closed, stream.closed

    input_closed, output_closed, stream_closed = asyncio.run(run())
    assert input_closed is True
    assert output_closed is True
    assert stream_closed is True


def test_output_receive_maps_completion_text_audio_and_unknown_without_crash() -> None:
    async def run() -> tuple[list[object], object]:
        stream = FakeDuplexStream(
            [
                _chunk({"event": {"completionStart": {"completionId": "c1"}}}),
                _chunk({"event": {"contentStart": {"contentName": "response-1", "completionId": "c1", "type": "TEXT", "role": "ASSISTANT", "additionalModelFields": {"generationStage": "FINAL"}}}}),
                _chunk({"event": {"textOutput": {"contentName": "response-1", "completionId": "c1", "text": "Connection test successful.", "additionalModelFields": {"generationStage": "FINAL"}}}}),
                _chunk({"event": {"contentEnd": {"contentName": "response-1", "completionId": "c1"}}}),
                _chunk({"event": {"contentStart": {"contentName": "response-1-audio", "completionId": "c1", "type": "AUDIO", "role": "ASSISTANT"}}}),
                _chunk({"event": {"audioOutput": {"contentName": "response-1-audio", "completionId": "c1", "content": "AAE="}}}),
                _chunk({"event": {"contentEnd": {"contentName": "response-1-audio", "completionId": "c1"}}}),
                _chunk({"event": {"usageEvent": {"inputTokens": 10, "outputTokens": 4}}}),
                _chunk({"event": {"completionEnd": {"completionId": "c1"}}}),
                _chunk({"event": {"unexpectedThing": {"id": "x"}}}),
                _chunk({"event": {"completionStart": {"completionId": "c2"}}}),
                _chunk({"event": {"completionEnd": {"completionId": "c2"}}}),
            ]
        )
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream))
        collector = asyncio.create_task(_collect_events(runtime))
        await runtime.start(_context())
        await runtime.send_reply(_reply())
        await asyncio.sleep(0.2)
        await runtime.close()
        return await collector, runtime.observed_output

    events, observed = asyncio.run(run())
    assert any(isinstance(event, AssistantSpeechStarted) for event in events)
    assert any(isinstance(event, AssistantTranscriptFinal) for event in events)
    assert any(isinstance(event, AssistantAudioChunk) for event in events)
    assert any(isinstance(event, AssistantSpeechEnded) for event in events)
    assert not any(isinstance(event, RuntimeError) and event.detail.get("code") == "nova_sonic_unknown_event" for event in events)
    assert observed.completion_start_received is True
    assert observed.completion_end_received is True
    assert observed.assistant_text_output_received is True
    assert observed.assistant_audio_bytes == 2
    assert observed.unknown_event_count == 1
    assert observed.unknown_event_keys == ["unexpectedThing"]
    assert "usage_event" in observed.received_event_types


def test_output_receive_distinguishes_user_and_assistant_text() -> None:
    async def run() -> object:
        stream = FakeDuplexStream(
            [
                _chunk({"event": {"contentStart": {"contentName": "user-1", "type": "TEXT", "role": "USER"}}}),
                _chunk({"event": {"textOutput": {"contentName": "user-1", "content": "Please say connection test successful."}}}),
                _chunk({"event": {"contentEnd": {"contentName": "user-1"}}}),
                _chunk({"event": {"contentStart": {"contentName": "assistant-1", "type": "TEXT", "role": "ASSISTANT"}}}),
                _chunk({"event": {"textOutput": {"contentName": "assistant-1", "content": "Connection test successful."}}}),
                _chunk({"event": {"contentEnd": {"contentName": "assistant-1"}}}),
            ]
        )
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream))
        await runtime.start(_context())
        await asyncio.sleep(0.05)
        observed = runtime.observed_output
        await runtime.close()
        return observed

    observed = asyncio.run(run())
    assert observed.user_transcript_received is True
    assert observed.user_transcript_text_length == len("Please say connection test successful.")
    assert observed.assistant_text_output_received is False
    assert observed.unauthorized_text_received is True


def test_unknown_event_does_not_set_explicit_stream_error() -> None:
    async def run() -> object:
        stream = FakeDuplexStream(
            [
                _chunk({"event": {"mysteryEvent": {"content": "secret", "role": "ASSISTANT"}}}),
                _chunk({"event": {"completionStart": {"completionId": "c1"}}}),
                _chunk({"event": {"completionEnd": {"completionId": "c1", "stopReason": "FINISHED"}}}),
            ]
        )
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream))
        await runtime.start(_context())
        await asyncio.sleep(0.05)
        observed = runtime.observed_output
        await runtime.close()
        return observed

    observed = asyncio.run(run())
    assert observed.unknown_event_count == 1
    assert observed.explicit_stream_error is False
    assert observed.model_stream_error is False
    assert observed.completion_end_received is True
    assert observed.completion_stop_reason == "FINISHED"


def test_user_speech_events_map_to_common_runtime_events() -> None:
    async def run() -> list[object]:
        stream = FakeDuplexStream(
            [
                _chunk({"event": {"userSpeechStart": {"promptName": "prompt-1", "sessionId": "s1"}}}),
                _chunk({"event": {"userSpeechEnd": {"promptName": "prompt-1", "sessionId": "s1"}}}),
            ]
        )
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream))
        collector = asyncio.create_task(_collect_events(runtime))
        await runtime.start(_context())
        await asyncio.sleep(0.05)
        await runtime.close()
        return await collector

    events = asyncio.run(run())
    assert any(isinstance(event, UserSpeechStarted) for event in events)
    assert any(isinstance(event, UserSpeechEnded) for event in events)


def test_completion_status_transitions_to_output_complete_and_protocol_complete() -> None:
    async def run() -> object:
        stream = FakeDuplexStream(
            [
                _chunk({"event": {"completionStart": {"completionId": "c1"}}}),
                _chunk({"event": {"contentStart": {"contentName": "assistant-final-text", "completionId": "c1", "type": "TEXT", "role": "ASSISTANT", "additionalModelFields": {"generationStage": "FINAL"}}}}),
                _chunk({"event": {"textOutput": {"contentName": "assistant-final-text", "completionId": "c1", "content": "done", "additionalModelFields": {"generationStage": "FINAL"}}}}),
                _chunk({"event": {"contentEnd": {"contentName": "assistant-final-text", "completionId": "c1"}}}),
                _chunk({"event": {"contentStart": {"contentName": "assistant-audio", "completionId": "c1", "type": "AUDIO", "role": "ASSISTANT"}}}),
                _chunk({"event": {"audioOutput": {"contentName": "assistant-audio", "completionId": "c1", "content": "AAE="}}}),
                _chunk({"event": {"contentEnd": {"contentName": "assistant-audio", "completionId": "c1"}}}),
                _chunk({"event": {"completionEnd": {"completionId": "c1", "stopReason": "FINISHED"}}}),
            ]
        )
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream))
        await runtime.start(_context())
        await asyncio.sleep(0.05)
        observed = runtime.observed_output
        await runtime.close()
        return observed

    observed = asyncio.run(run())
    assert observed.assistant_final_text_end_ms is not None
    assert observed.assistant_audio_end_ms is not None
    assert observed.completion_status == "protocol_complete"
    assert observed.completion_end_received is True


def test_user_speech_start_closes_gate() -> None:
    async def run() -> object:
        stream = FakeDuplexStream(
            [_chunk({"event": {"userSpeechStart": {"promptName": "prompt-1", "sessionId": "s1"}}})]
        )
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream))
        await runtime.start(_context())
        await asyncio.sleep(0.05)
        observed = runtime.observed_output
        await runtime.close()
        return observed

    observed = asyncio.run(run())
    assert observed.response_authorization_state == "blocked"


def test_unauthorized_audio_is_not_forwarded_to_client_queue() -> None:
    async def run() -> tuple[list[object], object]:
        stream = FakeDuplexStream(
            [
                _chunk({"event": {"completionStart": {"completionId": "c1"}}}),
                _chunk({"event": {"contentStart": {"contentName": "assistant-audio", "completionId": "c1", "type": "AUDIO", "role": "ASSISTANT"}}}),
                _chunk({"event": {"audioOutput": {"contentName": "assistant-audio", "completionId": "c1", "content": "AAE="}}}),
                _chunk({"event": {"contentEnd": {"contentName": "assistant-audio", "completionId": "c1"}}}),
            ]
        )
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream))
        collector = asyncio.create_task(_collect_events(runtime))
        await runtime.start(_context())
        await asyncio.sleep(0.05)
        await runtime.close()
        return await collector, runtime.observed_output

    events, observed = asyncio.run(run())
    assert not any(isinstance(event, AssistantAudioChunk) for event in events)
    assert observed.unauthorized_audio_chunks == 1


def test_send_reply_binds_first_completion_started_after_send() -> None:
    async def run() -> object:
        stream = FakeDuplexStream(
            [
                _chunk({"event": {"completionStart": {"completionId": "c-before"}}}),
                _chunk({"event": {"contentStart": {"contentName": "u1", "completionId": "c-before", "type": "TEXT", "role": "USER"}}}),
                _chunk({"event": {"textOutput": {"contentName": "u1", "completionId": "c-before", "content": "It happens in the morning."}}}),
                _chunk({"event": {"contentEnd": {"contentName": "u1", "completionId": "c-before"}}}),
                _chunk({"event": {"completionStart": {"completionId": "c-approved"}}}),
                _chunk({"event": {"contentStart": {"contentName": "assistant-final", "completionId": "c-approved", "type": "TEXT", "role": "ASSISTANT", "additionalModelFields": {"generationStage": "FINAL"}}}}),
                _chunk({"event": {"textOutput": {"contentName": "assistant-final", "completionId": "c-approved", "content": "Thank you. Please tell me when this problem usually occurs.", "additionalModelFields": {"generationStage": "FINAL"}}}}),
                _chunk({"event": {"contentEnd": {"contentName": "assistant-final", "completionId": "c-approved"}}}),
                _chunk({"event": {"contentStart": {"contentName": "assistant-audio", "completionId": "c-approved", "type": "AUDIO", "role": "ASSISTANT"}}}),
                _chunk({"event": {"audioOutput": {"contentName": "assistant-audio", "completionId": "c-approved", "content": "AAE="}}}),
                _chunk({"event": {"contentEnd": {"contentName": "assistant-audio", "completionId": "c-approved"}}}),
            ]
        )
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream))
        await runtime.start(_context())
        await runtime.send_reply(
            AssistantReply(
                turn_id="turn-1",
                response_id="approved-response-1",
                text="Thank you. Please tell me when this problem usually occurs.",
                action="approved_reply",
                question_id=None,
                state_version=1,
            )
        )
        await asyncio.sleep(0.05)
        observed = runtime.observed_output
        await runtime.close()
        return observed

    observed = asyncio.run(run())
    assert observed.approved_completion_started is True
    assert observed.approved_completion_id == "c-approved"
    assert observed.approved_output_complete is True


def test_forced_tool_use_waits_for_transcript_and_tool_then_sends_result() -> None:
    async def run() -> tuple[object, list[dict]]:
        stream = FakeDuplexStream(
            [
                _chunk({"event": {"completionStart": {"completionId": "c1"}}}),
                _chunk({"event": {"contentStart": {"contentName": "tool-1", "completionId": "c1", "type": "TOOL", "role": "TOOL"}}}),
                _chunk({"event": {"toolUse": {"completionId": "c1", "contentName": "tool-1", "toolUseId": "tool-use-1", "toolName": "process_interview_turn", "content": "{}"}}}),
                _chunk({"event": {"contentStart": {"contentName": "user-1", "completionId": "c1", "type": "TEXT", "role": "USER"}}}),
                _chunk({"event": {"textOutput": {"contentName": "user-1", "completionId": "c1", "content": "It happens every morning."}}}),
                _chunk({"event": {"contentEnd": {"contentName": "user-1", "completionId": "c1"}}}),
                _chunk({"event": {"contentEnd": {"contentName": "tool-1", "completionId": "c1", "type": "TOOL", "stopReason": "TOOL_USE"}}}),
            ]
        )
        runtime = NovaSonicRuntime(
            config=None,
            sdk_client=FakeSdkClient(stream),
        )
        runtime._config = runtime._config.__class__(
            enable_forced_tool_use=True,
            forced_tool_result_delay_ms=1,
        )
        await runtime.start(_context())
        await asyncio.sleep(0.05)
        payloads = _decode_sent_payloads(stream)
        observed = runtime.observed_output
        await runtime.close()
        return observed, payloads

    observed, payloads = asyncio.run(run())
    content_start_payloads = [payload for payload in payloads if "contentStart" in payload["event"]]
    tool_payloads = [payload for payload in payloads if "toolResult" in payload["event"]]
    assert observed.tool_use_received is True
    assert observed.tool_output_content_end_received is True
    assert observed.tool_result_sent is True
    assert observed.tool_result_sent_after_tool_content_end is True
    assert len(tool_payloads) == 1
    assert "toolUseId" not in tool_payloads[0]["event"]["toolResult"]
    assert isinstance(tool_payloads[0]["event"]["toolResult"]["content"], str)
    assert json.loads(tool_payloads[0]["event"]["toolResult"]["content"]) == {
        "reply_text": "ありがとうございます。通常どのような状況で発生するか教えてください。"
    }
    tool_result_start = next(
        payload["event"]["contentStart"]
        for payload in content_start_payloads
        if payload["event"]["contentStart"]["type"] == "TOOL"
        and payload["event"]["contentStart"]["role"] == "TOOL"
        and payload["event"]["contentStart"]["contentName"].startswith("tool-result-")
    )
    assert tool_result_start["interactive"] is False
    assert tool_result_start["toolResultInputConfiguration"]["toolUseId"] == "tool-use-1"
    tool_result_content_name = tool_payloads[0]["event"]["toolResult"]["contentName"]
    assert tool_result_start["contentName"] == tool_result_content_name
    tool_result_end = next(
        payload["event"]["contentEnd"]
        for payload in payloads
        if "contentEnd" in payload["event"]
        and payload["event"]["contentEnd"]["contentName"] == tool_result_content_name
    )
    assert tool_result_end["contentName"] == tool_result_content_name


def test_forced_tool_use_does_not_send_result_before_tool_content_end() -> None:
    async def run() -> list[dict]:
        stream = FakeDuplexStream(
            [
                _chunk({"event": {"completionStart": {"completionId": "c1"}}}),
                _chunk({"event": {"contentStart": {"contentName": "tool-1", "completionId": "c1", "type": "TOOL", "role": "TOOL"}}}),
                _chunk({"event": {"toolUse": {"completionId": "c1", "contentName": "tool-1", "toolUseId": "tool-use-1", "toolName": "process_interview_turn", "content": "{}"}}}),
                _chunk({"event": {"contentStart": {"contentName": "user-1", "completionId": "c1", "type": "TEXT", "role": "USER"}}}),
                _chunk({"event": {"textOutput": {"contentName": "user-1", "completionId": "c1", "content": "It happens every morning."}}}),
                _chunk({"event": {"contentEnd": {"contentName": "user-1", "completionId": "c1"}}}),
            ]
        )
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream))
        runtime._config = runtime._config.__class__(
            enable_forced_tool_use=True,
            forced_tool_result_delay_ms=1,
        )
        await runtime.start(_context())
        await asyncio.sleep(0.05)
        payloads = _decode_sent_payloads(stream)
        await runtime.close()
        return payloads

    payloads = asyncio.run(run())
    assert not any("toolResult" in payload["event"] for payload in payloads)


def test_initial_reply_uses_tool_result_without_saving_user_turn() -> None:
    async def run() -> tuple[list[dict], FakeInterviewBridge, object]:
        stream = FakeDuplexStream(
            [
                _chunk({"event": {"completionStart": {"completionId": "c-initial"}}}),
                _chunk({"event": {"contentStart": {"contentName": "tool-initial", "completionId": "c-initial", "type": "TOOL", "role": "TOOL"}}}),
                _chunk({"event": {"toolUse": {"completionId": "c-initial", "contentName": "tool-initial", "toolUseId": "tool-use-initial", "toolName": "process_interview_turn", "content": "{}"}}}),
                _chunk({"event": {"contentEnd": {"contentName": "tool-initial", "completionId": "c-initial", "type": "TOOL", "stopReason": "TOOL_USE"}}}),
                _chunk({"event": {"contentStart": {"contentName": "assistant-final", "completionId": "c-initial", "type": "TEXT", "role": "ASSISTANT", "additionalModelFields": {"generationStage": "FINAL"}}}}),
                _chunk({"event": {"textOutput": {"contentName": "assistant-final", "completionId": "c-initial", "content": "これからインタビューを開始します。あなたの名前は？", "additionalModelFields": {"generationStage": "FINAL"}}}}),
                _chunk({"event": {"contentEnd": {"contentName": "assistant-final", "completionId": "c-initial"}}}),
                _chunk({"event": {"contentStart": {"contentName": "assistant-audio", "completionId": "c-initial", "type": "AUDIO", "role": "ASSISTANT"}}}),
                _chunk({"event": {"audioOutput": {"contentName": "assistant-audio", "completionId": "c-initial", "content": "AAE="}}}),
                _chunk({"event": {"contentEnd": {"contentName": "assistant-audio", "completionId": "c-initial"}}}),
                _chunk({"event": {"completionEnd": {"completionId": "c-initial", "stopReason": "END_TURN"}}}),
            ]
        )
        bridge = FakeInterviewBridge()
        runtime = NovaSonicRuntime(
            config=NovaSonicRuntimeConfig(
                enable_forced_tool_use=True,
                forced_tool_result_delay_ms=1,
            ),
            sdk_client=FakeSdkClient(stream),
            interview_bridge=bridge,
        )
        await runtime.start(_context())
        await runtime.start_initial_reply(
            reply_text="これからインタビューを開始します。あなたの名前は？",
            question_id="q-001",
        )
        await asyncio.sleep(0.2)
        payloads = _decode_sent_payloads(stream)
        observed = runtime.observed_output
        await runtime.close()
        return payloads, bridge, observed

    payloads, bridge, observed = asyncio.run(run())
    text_inputs = [
        payload["event"]["textInput"]["content"]
        for payload in payloads
        if "textInput" in payload["event"]
    ]
    assert "これからインタビューを開始します。あなたの名前は？" not in text_inputs
    tool_results = [payload for payload in payloads if "toolResult" in payload["event"]]
    assert json.loads(tool_results[0]["event"]["toolResult"]["content"]) == {
        "reply_text": "これからインタビューを開始します。あなたの名前は？"
    }
    assert "save_turn" not in [call[0] for call in bridge.calls]
    assert "process_saved_turn" not in [call[0] for call in bridge.calls]
    assert observed.tool_result_sent is True


def test_forced_tool_use_calls_interview_bridge_and_uses_reply_text() -> None:
    async def run() -> tuple[object, list[dict], FakeInterviewBridge]:
        stream = FakeDuplexStream(
            [
                _chunk({"event": {"completionStart": {"completionId": "c1"}}}),
                _chunk({"event": {"contentStart": {"contentName": "tool-1", "completionId": "c1", "type": "TOOL", "role": "TOOL"}}}),
                _chunk({"event": {"toolUse": {"completionId": "c1", "contentName": "tool-1", "toolUseId": "tool-use-1", "toolName": "process_interview_turn", "content": "{}"}}}),
                _chunk({"event": {"contentStart": {"contentName": "user-1", "completionId": "c1", "type": "TEXT", "role": "USER"}}}),
                _chunk({"event": {"textOutput": {"contentName": "user-1", "completionId": "c1", "content": "It happens every morning."}}}),
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
        await runtime.start(_context())
        await asyncio.sleep(0.08)
        payloads = _decode_sent_payloads(stream)
        observed = runtime.observed_output
        await runtime.close()
        return observed, payloads, bridge

    observed, payloads, bridge = asyncio.run(run())
    assert [call[0] for call in bridge.calls[:3]] == [
        "load_voice_session",
        "save_turn",
        "process_saved_turn",
    ]
    assert bridge.calls[1][1]["answer_to_question_id"] == "q-001"
    tool_result_payload = next(payload for payload in payloads if "toolResult" in payload["event"])
    assert json.loads(tool_result_payload["event"]["toolResult"]["content"]) == {"reply_text": "API reply"}
    assert observed.turn_saved is True
    assert observed.interview_process_called is True
    assert observed.interview_process_completed is True
    assert observed.reply_text_present is True


def _obsolete_normal_answer_sends_confirmation_preface_before_process_completes() -> None:
    async def run() -> tuple[list[dict], FakeInterviewBridge]:
        stream = FakeDuplexStream(
            [
                _chunk({"event": {"completionStart": {"completionId": "c1"}}}),
                _chunk({"event": {"contentStart": {"contentName": "tool-1", "completionId": "c1", "type": "TOOL", "role": "TOOL"}}}),
                _chunk({"event": {"toolUse": {"completionId": "c1", "contentName": "tool-1", "toolUseId": "tool-use-1", "toolName": "process_interview_turn", "content": "{}"}}}),
                _chunk({"event": {"contentStart": {"contentName": "user-1", "completionId": "c1", "type": "TEXT", "role": "USER"}}}),
                _chunk({"event": {"textOutput": {"contentName": "user-1", "completionId": "c1", "content": "My answer."}}}),
                _chunk({"event": {"contentEnd": {"contentName": "user-1", "completionId": "c1"}}}),
                _chunk({"event": {"contentEnd": {"contentName": "tool-1", "completionId": "c1", "type": "TOOL", "stopReason": "TOOL_USE"}}}),
                _chunk({"event": {"contentStart": {"contentName": "assistant-final", "completionId": "c1", "type": "TEXT", "role": "ASSISTANT", "additionalModelFields": {"generationStage": "FINAL"}}}}),
                _chunk({"event": {"textOutput": {"contentName": "assistant-final", "completionId": "c1", "content": "確認します。", "additionalModelFields": {"generationStage": "FINAL"}}}}),
                _chunk({"event": {"contentEnd": {"contentName": "assistant-final", "completionId": "c1"}}}),
                _chunk({"event": {"contentStart": {"contentName": "assistant-audio", "completionId": "c1", "type": "AUDIO", "role": "ASSISTANT"}}}),
                _chunk({"event": {"audioOutput": {"contentName": "assistant-audio", "completionId": "c1", "content": "AAE="}}}),
                _chunk({"event": {"contentEnd": {"contentName": "assistant-audio", "completionId": "c1"}}}),
                _chunk({"event": {"completionEnd": {"completionId": "c1", "stopReason": "END_TURN"}}}),
            ]
        )
        bridge = FakeInterviewBridge()
        release = asyncio.Event()
        bridge.save_turn_result = SimpleNamespace(turn_id="turn-1", processing_status="pending", processing_mode="answer_evaluation")

        async def delayed_process_saved_turn(*, voice_session_id: str, turn_id: str):
            bridge.calls.append(("process_saved_turn", {"voice_session_id": voice_session_id, "turn_id": turn_id}))
            await release.wait()
            return bridge.process_turn_result

        bridge.process_saved_turn = delayed_process_saved_turn  # type: ignore[method-assign]

        runtime = NovaSonicRuntime(
            sdk_client=FakeSdkClient(stream),
            interview_bridge=bridge,
        )
        runtime._config = runtime._config.__class__(
            enable_forced_tool_use=True,
            forced_tool_result_delay_ms=1,
        )
        await runtime.start(_context())
        await asyncio.sleep(0.08)
        first_payloads = _decode_sent_payloads(stream)
        await runtime.notify_assistant_playback_started(response_id="preface-response", generation=runtime.current_generation)
        await runtime.notify_assistant_playback_drained(response_id="preface-response", generation=runtime.current_generation)
        release.set()
        await runtime._completion_lifecycle.finalize_authorized_completion_once("c1", reason="assistant_speech_ended")
        await asyncio.sleep(0.3)
        second_payloads = _decode_sent_payloads(stream)
        await runtime.close()
        return [*first_payloads, *second_payloads[len(first_payloads):]], bridge

    payloads, bridge = asyncio.run(run())
    tool_result_payload = next(payload for payload in payloads if "toolResult" in payload["event"])
    assert json.loads(tool_result_payload["event"]["toolResult"]["content"]) == {"reply_text": "確認します。"}
    text_inputs = [payload["event"]["textInput"]["content"] for payload in payloads if "textInput" in payload["event"]]
    assert "API reply" in text_inputs
    tool_result_index = next(
        index for index, payload in enumerate(payloads) if "toolResult" in payload["event"]
    )
    api_reply_index = next(
        index
        for index, payload in enumerate(payloads)
        if payload.get("event", {}).get("textInput", {}).get("content") == "API reply"
    )
    assert api_reply_index > tool_result_index
    assert [call[0] for call in bridge.calls[:3]] == [
        "load_voice_session",
        "save_turn",
        "process_saved_turn",
    ]


def _obsolete_reused_completion_state_resets_preface_flags_for_followup_reply() -> None:
    async def run() -> CompletionState:
        stream = FakeDuplexStream([])
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream))
        await runtime.start(_context())
        runtime._response_controller.on_user_speech_started()
        runtime._response_controller.on_user_transcript_final()
        await runtime.send_reply(
            AssistantReply(
                turn_id="preface-turn",
                response_id="preface-response",
                text="確認します。",
                action="ask_confirmation_preface",
                question_id="q-001",
                state_version=1,
            )
        )
        await runtime._protocol_dispatcher.handle_stream_event(_chunk({"event": {"completionStart": {"completionId": "c1"}}}))
        completion_state = runtime._completion_registry.completion_states["c1"]
        completion_state.assistant_audio_chunks = 3
        completion_state.assistant_final_text_received = True
        completion_state.assistant_audio_end_received = True
        completion_state.assistant_final_text_end_received = True
        completion_state.completion_end_received = True
        completion_state.stop_reason = "END_TURN"
        completion_state.status = CompletionStatus.PROTOCOL_COMPLETE
        completion_state.spoken_transcript = "確認します。"
        runtime._response_controller.on_user_speech_started()
        runtime._response_controller.on_user_transcript_final()
        await runtime.send_reply(
            AssistantReply(
                turn_id="followup-turn",
                response_id="followup-response",
                text="所属も教えてください。",
                action="ask_followup",
                question_id="q-001",
                state_version=2,
            )
        )
        await runtime._protocol_dispatcher.handle_stream_event(_chunk({"event": {"completionStart": {"completionId": "c1"}}}))
        rebound_state = runtime._completion_registry.completion_states["c1"]
        await runtime.close()
        return rebound_state

    rebound_state = asyncio.run(run())
    assert rebound_state.response_id == "followup-response"
    assert rebound_state.assistant_audio_chunks == 0
    assert rebound_state.assistant_final_text_received is False
    assert rebound_state.assistant_audio_end_received is False
    assert rebound_state.assistant_final_text_end_received is False
    assert rebound_state.completion_end_received is False
    assert rebound_state.stop_reason is None
    assert rebound_state.status == CompletionStatus.GENERATING
    assert rebound_state.spoken_transcript == ""


def _obsolete_confirmation_preface_drain_does_not_reopen_gate_before_evaluation_reply(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> tuple[str, list[dict], FakeInterviewBridge]:
        stream = FakeDuplexStream(
            [
                _chunk({"event": {"completionStart": {"completionId": "c1"}}}),
                _chunk({"event": {"contentStart": {"contentName": "tool-1", "completionId": "c1", "type": "TOOL", "role": "TOOL"}}}),
                _chunk({"event": {"toolUse": {"completionId": "c1", "contentName": "tool-1", "toolUseId": "tool-use-1", "toolName": "process_interview_turn", "content": "{}"}}}),
                _chunk({"event": {"contentStart": {"contentName": "user-1", "completionId": "c1", "type": "TEXT", "role": "USER"}}}),
                _chunk({"event": {"textOutput": {"contentName": "user-1", "completionId": "c1", "content": "宮崎です"}}}),
                _chunk({"event": {"contentEnd": {"contentName": "user-1", "completionId": "c1"}}}),
                _chunk({"event": {"contentEnd": {"contentName": "tool-1", "completionId": "c1", "type": "TOOL", "stopReason": "TOOL_USE"}}}),
                _chunk({"event": {"contentStart": {"contentName": "assistant-final", "completionId": "c1", "type": "TEXT", "role": "ASSISTANT", "additionalModelFields": {"generationStage": "FINAL"}}}}),
                _chunk({"event": {"textOutput": {"contentName": "assistant-final", "completionId": "c1", "content": "確認します。", "additionalModelFields": {"generationStage": "FINAL"}}}}),
                _chunk({"event": {"contentEnd": {"contentName": "assistant-final", "completionId": "c1"}}}),
                _chunk({"event": {"contentStart": {"contentName": "assistant-audio", "completionId": "c1", "type": "AUDIO", "role": "ASSISTANT"}}}),
                _chunk({"event": {"audioOutput": {"contentName": "assistant-audio", "completionId": "c1", "content": "AAE="}}}),
                _chunk({"event": {"contentEnd": {"contentName": "assistant-audio", "completionId": "c1"}}}),
                _chunk({"event": {"completionEnd": {"completionId": "c1", "stopReason": "END_TURN"}}}),
            ]
        )
        bridge = FakeInterviewBridge()
        release = asyncio.Event()
        bridge.save_turn_result = SimpleNamespace(
            turn_id="turn-1",
            processing_status="pending",
            processing_mode="answer_evaluation",
        )

        async def delayed_process_saved_turn(*, voice_session_id: str, turn_id: str):
            bridge.calls.append(("process_saved_turn", {"voice_session_id": voice_session_id, "turn_id": turn_id}))
            await release.wait()
            return bridge.process_turn_result

        bridge.process_saved_turn = delayed_process_saved_turn  # type: ignore[method-assign]

        runtime = NovaSonicRuntime(
            sdk_client=FakeSdkClient(stream),
            interview_bridge=bridge,
        )
        runtime._config = runtime._config.__class__(
            enable_forced_tool_use=True,
            forced_tool_result_delay_ms=1,
        )
        await runtime.start(_context())
        await asyncio.sleep(0.08)
        await runtime.notify_assistant_playback_started(response_id="preface-response", generation=runtime.current_generation)
        await runtime.notify_assistant_playback_drained(response_id="preface-response", generation=runtime.current_generation)
        await asyncio.sleep(0.25)
        state_before_release = runtime.input_state
        release.set()
        await asyncio.sleep(0.08)
        await runtime.notify_assistant_playback_started(response_id="resp-1", generation=runtime.current_generation)
        payloads = _decode_sent_payloads(stream)
        await runtime.close()
        return state_before_release, payloads, bridge

    caplog.set_level(logging.INFO)
    state_before_release, payloads, bridge = asyncio.run(run())
    assert state_before_release in {"ANSWER_PROCESSING", "ASSISTANT_SPEAKING"}
    text_inputs = [payload["event"]["textInput"]["content"] for payload in payloads if "textInput" in payload["event"]]
    assert text_inputs.count("API reply") == 1
    assert [call[0] for call in bridge.calls[:3]] == [
        "load_voice_session",
        "save_turn",
        "process_saved_turn",
    ]
    assert "evaluation_reply_playback_started" in caplog.text


def _obsolete_preface_output_complete_does_not_send_evaluation_reply_immediately() -> None:
    async def run() -> tuple[list[dict], bool]:
        stream = FakeDuplexStream([])
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream), interview_bridge=FakeInterviewBridge())
        await runtime.start(_context())
        pending = PendingToolCall(completion_id="c1", processing_mode="answer_evaluation")
        runtime._pending_turn_store.put(pending)
        runtime._pending_turn_store.put_evaluation(pending)
        pending.processing_mode = "answer_evaluation"
        pending.queued_followup_reply = InterviewBridgeResult(
            turn_id="turn-1",
            response_id="resp-1",
            reply_text="API reply",
            action="ask_followup",
            question_id="q-001",
            state_version=2,
            interview_status="active",
        )
        completion_state = runtime._completion_registry.resolve_completion_state("c1")
        assert completion_state is not None
        completion_state.authorized = True
        completion_state.response_id = "preface-response"
        completion_state.generation = 1
        completion_state.status = CompletionStatus.OUTPUT_COMPLETE
        await runtime._completion_lifecycle.maybe_complete_session_after_authorized_output(completion_state)
        payloads = _decode_sent_payloads(stream)
        queued_reply_still_present = runtime._pending_turn_store.get_evaluation("c1").queued_followup_reply is not None
        await runtime.close()
        return payloads, queued_reply_still_present

    payloads, queued_reply_still_present = asyncio.run(run())
    text_inputs = [payload["event"]["textInput"]["content"] for payload in payloads if "textInput" in payload["event"]]
    assert "API reply" not in text_inputs
    assert queued_reply_still_present is True


def _obsolete_preface_completion_finished_sends_evaluation_reply_after_reset() -> None:
    async def run() -> tuple[list[dict], str]:
        stream = FakeDuplexStream([])
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream), interview_bridge=FakeInterviewBridge())
        await runtime.start(_context())
        completion_state = runtime._completion_registry.resolve_completion_state("c1")
        assert completion_state is not None
        completion_state.authorized = True
        completion_state.response_id = "preface-response"
        completion_state.generation = 0
        completion_state.status = CompletionStatus.PROTOCOL_COMPLETE
        pending = PendingToolCall(completion_id="c1", processing_mode="answer_evaluation")
        runtime._pending_turn_store.put(pending)
        pending.preface_output_complete = True
        pending.queued_followup_reply = InterviewBridgeResult(
            turn_id="turn-1",
            response_id="resp-1",
            reply_text="API reply",
            action="ask_followup",
            question_id="q-001",
            state_version=2,
            interview_status="active",
        )
        await runtime._completion_lifecycle.finalize_authorized_completion_once("c1", reason="assistant_speech_ended")
        payloads = _decode_sent_payloads(stream)
        auth_state = runtime._response_controller.authorization_state.value
        await runtime.close()
        return payloads, auth_state

    payloads, auth_state = asyncio.run(run())
    text_inputs = [payload["event"]["textInput"]["content"] for payload in payloads if "textInput" in payload["event"]]
    assert "API reply" in text_inputs
    assert auth_state != ResponseAuthorizationState.BLOCKED.value


def _obsolete_completion_end_sends_followup_without_assistant_speech_ended() -> None:
    async def run() -> int:
        stream = FakeDuplexStream([])
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream), interview_bridge=FakeInterviewBridge())
        await runtime.start(_context())
        completion_state = runtime._completion_registry.resolve_completion_state("c1")
        assert completion_state is not None
        completion_state.authorized = True
        completion_state.response_id = "preface-response"
        completion_state.generation = 0
        pending = PendingToolCall(completion_id="c1", processing_mode="answer_evaluation")
        runtime._pending_turn_store.put(pending)
        pending.preface_output_complete = True
        pending.queued_followup_reply = InterviewBridgeResult(
            turn_id="turn-1",
            response_id="resp-1",
            reply_text="API reply",
            action="ask_followup",
            question_id="q-001",
            state_version=2,
            interview_status="active",
        )
        await runtime._protocol_dispatcher.handle_stream_event(
            _chunk({"event": {"completionEnd": {"completionId": "c1", "stopReason": "end"}}})
        )
        payloads = _decode_sent_payloads(stream)
        await runtime.close()
        return sum(
            1
            for payload in payloads
            if payload["event"].get("textInput", {}).get("content") == "API reply"
        )

    assert asyncio.run(run()) == 1


def _obsolete_content_end_sends_followup_without_completion_end() -> None:
    async def run() -> int:
        stream = FakeDuplexStream([])
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream), interview_bridge=FakeInterviewBridge())
        await runtime.start(_context())
        completion_state = runtime._completion_registry.resolve_completion_state("c1")
        assert completion_state is not None
        completion_state.authorized = True
        completion_state.response_id = "preface-response"
        completion_state.generation = 0
        runtime._completion_registry.set_active_completion_id("c1")
        runtime._completion_registry.content_states["assistant-audio"] = SimpleNamespace(
            completion_id="c1",
            role="ASSISTANT",
            content_type="AUDIO",
            generation_stage="FINAL",
            content_id="assistant-audio",
        )
        runtime._completion_registry.content_states["assistant-text"] = SimpleNamespace(
            completion_id="c1",
            role="ASSISTANT",
            content_type="TEXT",
            generation_stage="FINAL",
            content_id="assistant-text",
        )
        pending = PendingToolCall(completion_id="c1", processing_mode="answer_evaluation")
        runtime._pending_turn_store.put(pending)
        pending.preface_output_complete = True
        pending.queued_followup_reply = InterviewBridgeResult(
            turn_id="turn-1",
            response_id="resp-1",
            reply_text="API reply",
            action="ask_followup",
            question_id="q-001",
            state_version=2,
            interview_status="active",
        )
        await runtime._protocol_dispatcher.handle_stream_event(
            _chunk({"event": {"contentEnd": {"contentName": "assistant-audio", "type": "AUDIO", "role": "ASSISTANT"}}})
        )
        await runtime._protocol_dispatcher.handle_stream_event(
            _chunk({"event": {"contentEnd": {"contentName": "assistant-text", "type": "TEXT", "role": "ASSISTANT", "additionalModelFields": {"generationStage": "FINAL"}}}})
        )
        payloads = _decode_sent_payloads(stream)
        await runtime.close()
        return sum(
            1
            for payload in payloads
            if payload["event"].get("textInput", {}).get("content") == "API reply"
        )

    assert asyncio.run(run()) == 1


def _obsolete_content_end_and_completion_end_finalize_only_once() -> None:
    async def run() -> tuple[int, bool]:
        stream = FakeDuplexStream([])
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream), interview_bridge=FakeInterviewBridge())
        await runtime.start(_context())
        completion_state = runtime._completion_registry.resolve_completion_state("c1")
        assert completion_state is not None
        completion_state.authorized = True
        completion_state.response_id = "preface-response"
        completion_state.generation = 0
        runtime._completion_registry.set_active_completion_id("c1")
        runtime._completion_registry.content_states["assistant-audio"] = SimpleNamespace(
            completion_id="c1",
            role="ASSISTANT",
            content_type="AUDIO",
            generation_stage="FINAL",
            content_id="assistant-audio",
        )
        runtime._completion_registry.content_states["assistant-text"] = SimpleNamespace(
            completion_id="c1",
            role="ASSISTANT",
            content_type="TEXT",
            generation_stage="FINAL",
            content_id="assistant-text",
        )
        pending = PendingToolCall(completion_id="c1", processing_mode="answer_evaluation")
        runtime._pending_turn_store.put(pending)
        pending.preface_output_complete = True
        pending.queued_followup_reply = InterviewBridgeResult(
            turn_id="turn-1",
            response_id="resp-1",
            reply_text="API reply",
            action="ask_followup",
            question_id="q-001",
            state_version=2,
            interview_status="active",
        )
        await runtime._protocol_dispatcher.handle_stream_event(
            _chunk({"event": {"contentEnd": {"contentName": "assistant-audio", "type": "AUDIO", "role": "ASSISTANT"}}})
        )
        await runtime._protocol_dispatcher.handle_stream_event(
            _chunk({"event": {"contentEnd": {"contentName": "assistant-text", "type": "TEXT", "role": "ASSISTANT", "additionalModelFields": {"generationStage": "FINAL"}}}})
        )
        await runtime._protocol_dispatcher.handle_stream_event(
            _chunk({"event": {"completionEnd": {"completionId": "c1", "stopReason": "end"}}})
        )
        payloads = _decode_sent_payloads(stream)
        finalized = runtime._completion_registry.completion_states["c1"].finalized
        await runtime.close()
        return sum(
            1
            for payload in payloads
            if payload["event"].get("textInput", {}).get("content") == "API reply"
        ), finalized

    send_count, finalized = asyncio.run(run())
    assert send_count == 1
    assert finalized is True


def test_authorize_failure_does_not_send_user_text() -> None:
    async def run() -> list[dict]:
        stream = FakeDuplexStream([])
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream))
        await runtime.start(_context())

        def fail_authorize(*args, **kwargs):
            return None

        runtime._response_controller.authorize = fail_authorize  # type: ignore[method-assign]
        with pytest.raises(builtins.RuntimeError, match="authorization failed"):
            await runtime.send_reply(_reply("blocked"))
        payloads = _decode_sent_payloads(stream)
        await runtime.close()
        return payloads

    payloads = asyncio.run(run())
    text_inputs = [payload["event"]["textInput"]["content"] for payload in payloads if "textInput" in payload["event"]]
    assert "blocked" not in text_inputs


def test_send_reply_logs_completion_start_timeout_when_nova_does_not_start(caplog: pytest.LogCaptureFixture) -> None:
    async def run() -> None:
        stream = FakeDuplexStream([])
        runtime = NovaSonicRuntime(
            config=NovaSonicRuntimeConfig(reply_completion_start_timeout_seconds=0.01),
            sdk_client=FakeSdkClient(stream),
        )
        await runtime.start(_context())
        runtime._response_controller.on_user_speech_started()
        runtime._response_controller.on_user_transcript_final()
        await runtime.send_reply(
            AssistantReply(
                turn_id="turn-1",
                response_id="resp-timeout",
                text="API reply",
                action="ask_followup",
                question_id="q-001",
                state_version=2,
            )
        )
        await asyncio.sleep(0.03)
        await runtime.close()

    with caplog.at_level(logging.WARNING):
        asyncio.run(run())
    assert "assistant_reply_completion_start_timeout" in caplog.text


def _obsolete_send_reply_failure_keeps_queued_evaluation_reply() -> None:
    async def run() -> PendingToolCall:
        stream = FakeDuplexStream([])
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream), interview_bridge=FakeInterviewBridge())
        await runtime.start(_context())
        pending = PendingToolCall(
            completion_id="c1",
            processing_mode="answer_evaluation",
            queued_followup_reply=InterviewBridgeResult(
                turn_id="turn-1",
                response_id="resp-1",
                reply_text="API reply",
                action="ask_followup",
                question_id="q-001",
                state_version=2,
                interview_status="active",
            ),
            preface_output_complete=True,
        )
        pending.confirmation_preface_completion_finished_at_ms = 1
        runtime._pending_turn_store.put_evaluation(pending)

        attempts = 0

        async def fail_send_reply(reply: AssistantReply) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("send failed")

        runtime.send_reply = fail_send_reply  # type: ignore[method-assign]
        await runtime._reply_sequence.try_send(pending)
        await asyncio.sleep(0.25)
        await runtime.close()
        return pending

    pending = asyncio.run(run())
    assert pending.queued_followup_reply is None
    assert pending.evaluation_reply_sent is True
    assert pending.evaluation_reply_dispatching is False


def _obsolete_metrics_tracking_is_registered_before_send_reply_starts() -> None:
    async def run() -> bool:
        stream = FakeDuplexStream([])
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream), interview_bridge=FakeInterviewBridge())
        await runtime.start(_context())
        pending = PendingToolCall(
            completion_id="c1",
            processing_mode="answer_evaluation",
            queued_followup_reply=InterviewBridgeResult(
                turn_id="turn-1",
                response_id="resp-1",
                reply_text="API reply",
                action="ask_followup",
                question_id="q-001",
                state_version=2,
                interview_status="active",
            ),
            preface_output_complete=True,
        )
        pending.confirmation_preface_completion_finished_at_ms = 1
        runtime._pending_turn_store.put_evaluation(pending)
        metrics_visible_before_send = False

        async def observe_send_reply(reply: AssistantReply) -> None:
            nonlocal metrics_visible_before_send
            metrics_visible_before_send = runtime._evaluation_reply_metrics_by_response_id.get(reply.response_id) is pending

        runtime.send_reply = observe_send_reply  # type: ignore[method-assign]
        await runtime._reply_sequence.try_send(pending)
        runtime._stream = None
        await runtime.close()
        return metrics_visible_before_send

    assert asyncio.run(run()) is True


def _obsolete_evaluation_reply_is_sent_once_when_evaluation_finishes_before_preface_completion() -> None:
    async def run() -> tuple[int, bool]:
        stream = FakeDuplexStream([])
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream), interview_bridge=FakeInterviewBridge())
        await runtime.start(_context())
        pending = PendingToolCall(
            completion_id="c1",
            processing_mode="answer_evaluation",
            queued_followup_reply=InterviewBridgeResult(
                turn_id="turn-1",
                response_id="resp-1",
                reply_text="API reply",
                action="ask_followup",
                question_id="q-001",
                state_version=2,
                interview_status="active",
            ),
            preface_output_complete=True,
        )
        runtime._pending_turn_store.put(pending)
        runtime._pending_turn_store.put_evaluation(pending)
        await runtime._reply_sequence.try_send(pending)
        before_completion_sent = pending.evaluation_reply_sent

        completion_state = runtime._completion_registry.resolve_completion_state("c1")
        assert completion_state is not None
        completion_state.authorized = True
        completion_state.response_id = "preface-response"
        completion_state.generation = 0
        completion_state.status = CompletionStatus.PROTOCOL_COMPLETE
        await runtime._completion_lifecycle.finalize_authorized_completion_once("c1", reason="assistant_speech_ended")

        payloads = _decode_sent_payloads(stream)
        text_inputs = [
            payload["event"]["textInput"]["content"]
            for payload in payloads
            if "textInput" in payload["event"]
        ]
        await runtime.close()
        return text_inputs.count("API reply"), before_completion_sent

    send_count, before_completion_sent = asyncio.run(run())
    assert before_completion_sent is False
    assert send_count == 1


def _obsolete_old_generation_completion_end_does_not_finish_new_followup_reply() -> None:
    async def run() -> tuple[bool, bool]:
        stream = FakeDuplexStream([])
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream), interview_bridge=FakeInterviewBridge())
        await runtime.start(_context())

        old_completion = runtime._completion_registry.resolve_completion_state("c1")
        assert old_completion is not None
        old_completion.authorized = True
        old_completion.response_id = "preface-response"
        old_completion.generation = 0
        old_completion.status = CompletionStatus.PROTOCOL_COMPLETE

        pending = PendingToolCall(
            completion_id="c1",
            processing_mode="answer_evaluation",
            queued_followup_reply=InterviewBridgeResult(
                turn_id="turn-1",
                response_id="resp-1",
                reply_text="API reply",
                action="ask_followup",
                question_id="q-001",
                state_version=2,
                interview_status="active",
            ),
            preface_output_complete=True,
        )
        runtime._pending_turn_store.put(pending)
        runtime._pending_turn_store.put_evaluation(pending)
        await runtime._completion_lifecycle.finalize_authorized_completion_once("c1", reason="assistant_speech_ended")

        runtime._response_controller.on_user_speech_started()
        runtime._response_controller.on_user_transcript_final()
        await runtime.send_reply(
            AssistantReply(
                turn_id="turn-2",
                response_id="resp-1",
                text="API reply",
                action="ask_followup",
                question_id="q-001",
                state_version=2,
            )
        )
        await runtime._protocol_dispatcher.handle_stream_event(_chunk({"event": {"completionStart": {"completionId": "c2"}}}))
        new_completion = runtime._completion_registry.completion_states["c2"]
        old_event = AssistantSpeechEnded(response_id="preface-response", generation=0)

        accepted_before = runtime._response_controller.accepts_speech_ended(old_event, completion_id="c1")
        await runtime._completion_lifecycle.finalize_authorized_completion_once("c1", reason="assistant_speech_ended")
        new_active = runtime._response_controller.active_completion_id
        await runtime.close()
        return accepted_before, new_active == "c2"

    accepted_before, new_active_kept = asyncio.run(run())
    assert accepted_before is False
    assert new_active_kept is True


def _obsolete_close_cancels_pending_evaluation_retry_task() -> None:
    async def run() -> bool:
        stream = FakeDuplexStream([])
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream), interview_bridge=FakeInterviewBridge())
        await runtime.start(_context())
        pending = PendingToolCall(
            completion_id="c1",
            processing_mode="answer_evaluation",
            queued_followup_reply=InterviewBridgeResult(
                turn_id="turn-1",
                response_id="resp-1",
                reply_text="API reply",
                action="ask_followup",
                question_id="q-001",
                state_version=2,
                interview_status="active",
            ),
            preface_output_complete=True,
        )
        pending.confirmation_preface_completion_finished_at_ms = 1
        runtime._pending_turn_store.put_evaluation(pending)

        async def fail_send_reply(reply: AssistantReply) -> None:
            raise RuntimeError("send failed")

        runtime.send_reply = fail_send_reply  # type: ignore[method-assign]
        await runtime._reply_sequence.try_send(pending)
        retry_task = pending.evaluation_retry_task
        assert retry_task is not None
        await runtime.close()
        return retry_task.cancelled()

    assert asyncio.run(run()) is True


def _obsolete_completion_state_generation_is_retained_after_controller_generation_changes() -> None:
    async def run() -> int | None:
        stream = FakeDuplexStream([])
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream))
        await runtime.start(_context())
        runtime._response_controller.on_user_speech_started()
        runtime._response_controller.on_user_transcript_final()
        await runtime.send_reply(
            AssistantReply(
                turn_id="turn-1",
                response_id="resp-1",
                text="確認します。",
                action="ask_confirmation_preface",
                question_id="q-001",
                state_version=1,
            )
        )
        await runtime._protocol_dispatcher.handle_stream_event(_chunk({"event": {"completionStart": {"completionId": "c1"}}}))
        completion_generation = runtime._completion_registry.completion_states["c1"].generation
        runtime._response_controller.on_user_speech_started()
        await runtime.close()
        return completion_generation

    completion_generation = asyncio.run(run())
    assert completion_generation == 1


def test_local_preface_then_original_tool_result_continues_same_completion() -> None:
    async def run() -> tuple[list[dict], list[object], PendingToolCall, CompletionState, FakeInterviewBridge, int]:
        stream = FakeDuplexStream([])
        bridge = FakeInterviewBridge()
        release_evaluation = asyncio.Event()
        bridge.save_turn_result = SimpleNamespace(
            turn_id="turn-1",
            processing_status="pending",
            processing_mode="answer_evaluation",
        )

        async def delayed_process_saved_turn(*, voice_session_id: str, turn_id: str):
            bridge.calls.append(
                ("process_saved_turn", {"voice_session_id": voice_session_id, "turn_id": turn_id})
            )
            await release_evaluation.wait()
            return bridge.process_turn_result

        bridge.process_saved_turn = delayed_process_saved_turn  # type: ignore[method-assign]
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream), interview_bridge=bridge)
        runtime._config = runtime._config.__class__(
            enable_forced_tool_use=True,
            forced_tool_result_delay_ms=0,
        )
        await runtime.start(_context())

        for event in [
            _chunk({"event": {"completionStart": {"completionId": "c1"}}}),
            _chunk({"event": {"contentStart": {"contentName": "user-1", "completionId": "c1", "type": "TEXT", "role": "USER"}}}),
            _chunk({"event": {"textOutput": {"contentName": "user-1", "completionId": "c1", "content": "田中です"}}}),
            _chunk({"event": {"contentEnd": {"contentName": "user-1", "completionId": "c1"}}}),
            _chunk({"event": {"contentStart": {"contentName": "tool-1", "completionId": "c1", "type": "TOOL", "role": "TOOL"}}}),
            _chunk({"event": {"toolUse": {"completionId": "c1", "contentName": "tool-1", "toolUseId": "original-tool-use-1", "toolName": "process_interview_turn", "content": "{}"}}}),
            _chunk({"event": {"contentEnd": {"contentName": "tool-1", "completionId": "c1", "type": "TOOL", "stopReason": "TOOL_USE"}}}),
        ]:
            await runtime._protocol_dispatcher.handle_stream_event(event)

        pending = runtime._pending_turn_store.get("c1")
        assert pending is not None
        assert pending.local_preface_response_id == "local-preface-response:turn-1"
        assert pending.local_preface_generation is not None
        completion_state = runtime._completion_registry.lookup_completion_state("c1")
        assert completion_state is not None
        assert completion_state.finalized is False
        assert not any("toolResult" in payload["event"] for payload in _decode_sent_payloads(stream))

        await runtime.notify_assistant_playback_drained(
            response_id=pending.local_preface_response_id,
            generation=pending.local_preface_generation,
        )
        assert completion_state.finalized is False
        release_evaluation.set()
        await asyncio.sleep(0.05)

        assert completion_state.generation is not None
        assert completion_state.generation != pending.local_preface_generation
        for event in [
            _chunk({"event": {"contentStart": {"contentName": "assistant-final", "completionId": "c1", "type": "TEXT", "role": "ASSISTANT", "additionalModelFields": {"generationStage": "FINAL"}}}}),
            _chunk({"event": {"textOutput": {"contentName": "assistant-final", "completionId": "c1", "content": "API reply", "additionalModelFields": {"generationStage": "FINAL"}}}}),
            _chunk({"event": {"contentEnd": {"contentName": "assistant-final", "completionId": "c1"}}}),
            _chunk({"event": {"contentStart": {"contentName": "assistant-audio", "completionId": "c1", "type": "AUDIO", "role": "ASSISTANT"}}}),
            _chunk({"event": {"audioOutput": {"contentName": "assistant-audio", "completionId": "c1", "content": "AAE="}}}),
            _chunk({"event": {"contentEnd": {"contentName": "assistant-audio", "completionId": "c1"}}}),
            _chunk({"event": {"completionEnd": {"completionId": "c1", "stopReason": "END_TURN"}}}),
        ]:
            await runtime._protocol_dispatcher.handle_stream_event(event)

        queued_events: list[object] = []
        while not runtime._event_queue.empty():
            queued_events.append(runtime._event_queue.get_nowait())
        completion_start_count = runtime.observed_output.received_event_types.count("completion_start")
        payloads = _decode_sent_payloads(stream)
        await runtime.close()
        return payloads, queued_events, pending, completion_state, bridge, completion_start_count

    payloads, events, pending, completion_state, bridge, completion_start_count = asyncio.run(run())
    tool_results = [payload["event"]["toolResult"] for payload in payloads if "toolResult" in payload["event"]]
    assert len(tool_results) == 1
    tool_result_start = next(
        payload["event"]["contentStart"]
        for payload in payloads
        if payload.get("event", {}).get("contentStart", {}).get("type") == "TOOL"
        and "toolResultInputConfiguration" in payload["event"]["contentStart"]
    )
    assert tool_result_start["toolResultInputConfiguration"]["toolUseId"] == "original-tool-use-1"
    assert json.loads(tool_results[0]["content"]) == {"reply_text": "API reply"}
    assert all(
        payload.get("event", {}).get("textInput", {}).get("content") not in {"確認します。", "API reply"}
        for payload in payloads
    )
    assert any(
        isinstance(event, AssistantAudioChunk)
        and event.response_id == "local-preface-response:turn-1"
        and event.sample_rate_hz == 24000
        for event in events
    )
    local_speech_ended = next(
        event
        for event in events
        if isinstance(event, AssistantSpeechEnded)
        and event.response_id == "local-preface-response:turn-1"
    )
    assert local_speech_ended.audio_duration_ms == 880
    assert any(
        isinstance(event, AssistantAudioChunk)
        and event.response_id == "resp-1"
        and event.completion_id == "c1"
        for event in events
    )
    assert pending.result_sent is True
    assert completion_state.finalized is True
    assert completion_start_count == 1
    local_event = next(
        detail
        for name, detail in bridge.calls
        if name == "create_assistant_event"
        and detail.get("response_id") == "local-preface-response:turn-1"
    )
    assert local_event["transcript"] == "確認します。"
    assert local_event["detail"]["source"] == "local_fixed_preface"


def test_forced_tool_use_processes_three_turns_without_closing_runtime() -> None:
    class ThreeTurnBridge(FakeInterviewBridge):
        def __init__(self) -> None:
            super().__init__()
            self._turn_counter = 0

        async def save_turn(self, voice_session_id: str, **kwargs):
            self._turn_counter += 1
            turn_id = f"turn-{self._turn_counter}"
            self.calls.append(("save_turn", {"voice_session_id": voice_session_id, **kwargs, "turn_id": turn_id}))
            return SimpleNamespace(turn_id=turn_id, processing_status="pending")

        async def process_saved_turn(self, *, voice_session_id: str, turn_id: str):
            self.calls.append(("process_saved_turn", {"voice_session_id": voice_session_id, "turn_id": turn_id}))
            return InterviewBridgeResult(
                turn_id=turn_id,
                response_id=f"resp-{self._turn_counter}",
                reply_text=f"API reply {self._turn_counter}",
                action="ask_followup",
                question_id=f"q-00{self._turn_counter + 1}",
                state_version=self._turn_counter + 1,
                interview_status="active",
            )

    async def run() -> tuple[list[dict], ThreeTurnBridge, bool, list[object]]:
        stream = FakeDuplexStream([])
        bridge = ThreeTurnBridge()
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream), interview_bridge=bridge)
        runtime._config = runtime._config.__class__(
            enable_forced_tool_use=True,
            forced_tool_result_delay_ms=0,
        )
        collector = asyncio.create_task(_collect_events(runtime))
        await runtime.start(_context())

        async def drive_turn(index: int, answer: str, reply: str) -> None:
            completion_id = f"c-{index}"
            events = [
                _chunk({"event": {"completionStart": {"completionId": completion_id}}}),
                _chunk({"event": {"contentStart": {"contentName": f"tool-{index}", "completionId": completion_id, "type": "TOOL", "role": "TOOL"}}}),
                _chunk({"event": {"toolUse": {"completionId": completion_id, "contentName": f"tool-{index}", "toolUseId": f"tool-use-{index}", "toolName": "process_interview_turn", "content": "{}"}}}),
                _chunk({"event": {"userSpeechStart": {"promptName": "prompt-1", "sessionId": "s1"}}}),
                _chunk({"event": {"contentStart": {"contentName": f"user-{index}", "completionId": completion_id, "type": "TEXT", "role": "USER"}}}),
                _chunk({"event": {"textOutput": {"contentName": f"user-{index}", "completionId": completion_id, "content": answer}}}),
                _chunk({"event": {"userSpeechEnd": {"promptName": "prompt-1", "sessionId": "s1"}}}),
                _chunk({"event": {"contentEnd": {"contentName": f"user-{index}", "completionId": completion_id}}}),
                _chunk({"event": {"contentEnd": {"contentName": f"tool-{index}", "completionId": completion_id, "type": "TOOL", "stopReason": "TOOL_USE"}}}),
            ]
            for event in events:
                await runtime._protocol_dispatcher.handle_stream_event(event)
            await asyncio.sleep(0.05)
            assistant_events = [
                _chunk({"event": {"contentStart": {"contentName": f"assistant-final-{index}", "completionId": completion_id, "type": "TEXT", "role": "ASSISTANT", "additionalModelFields": {"generationStage": "FINAL"}}}}),
                _chunk({"event": {"textOutput": {"contentName": f"assistant-final-{index}", "completionId": completion_id, "content": reply, "additionalModelFields": {"generationStage": "FINAL"}}}}),
                _chunk({"event": {"contentEnd": {"contentName": f"assistant-final-{index}", "completionId": completion_id}}}),
                _chunk({"event": {"contentStart": {"contentName": f"assistant-audio-{index}", "completionId": completion_id, "type": "AUDIO", "role": "ASSISTANT"}}}),
                _chunk({"event": {"audioOutput": {"contentName": f"assistant-audio-{index}", "completionId": completion_id, "content": "AAE="}}}),
            ]
            for event in assistant_events:
                await runtime._protocol_dispatcher.handle_stream_event(event)
            await runtime.notify_assistant_playback_started(response_id=f"resp-{index}", generation=runtime.current_generation)
            await runtime._protocol_dispatcher.handle_stream_event(_chunk({"event": {"contentEnd": {"contentName": f"assistant-audio-{index}", "completionId": completion_id}}}))
            await runtime._protocol_dispatcher.handle_stream_event(_chunk({"event": {"completionEnd": {"completionId": completion_id, "stopReason": "END_TURN"}}}))
            await asyncio.sleep(0.05)
            runtime._completion_lifecycle.reset_turn_state_after_assistant_ended(completion_id)
            await runtime.notify_assistant_playback_drained(response_id=f"resp-{index}", generation=runtime.current_generation)
            await asyncio.sleep(0.25)

        await drive_turn(1, "First answer.", "API reply 1")
        await drive_turn(2, "Second answer.", "API reply 2")
        await drive_turn(3, "Third answer.", "API reply 3")

        runtime_open_before_close = not runtime._closed
        payloads = _decode_sent_payloads(stream)
        await runtime.close()
        return payloads, bridge, runtime_open_before_close, await collector

    payloads, bridge, runtime_open_before_close, events = asyncio.run(run())
    tool_payloads = [payload for payload in payloads if "toolResult" in payload["event"]]
    assert len(tool_payloads) == 3
    assert [json.loads(payload["event"]["toolResult"]["content"]) for payload in tool_payloads] == [
        {"reply_text": "API reply 1"},
        {"reply_text": "API reply 2"},
        {"reply_text": "API reply 3"},
    ]
    save_calls = [call for call in bridge.calls if call[0] == "save_turn"]
    process_calls = [call for call in bridge.calls if call[0] == "process_saved_turn"]
    assert len(save_calls) == 3
    assert len(process_calls) == 3
    assert save_calls[0][1]["answer_to_question_id"] == "q-001"
    assert len([event for event in events if isinstance(event, AssistantSpeechEnded)]) >= 1
    assert runtime_open_before_close is True


def test_turn_save_failure_does_not_call_process_and_returns_error_tool_result() -> None:
    async def run() -> tuple[list[dict], FakeInterviewBridge]:
        stream = FakeDuplexStream(
            [
                _chunk({"event": {"completionStart": {"completionId": "c1"}}}),
                _chunk({"event": {"contentStart": {"contentName": "tool-1", "completionId": "c1", "type": "TOOL", "role": "TOOL"}}}),
                _chunk({"event": {"toolUse": {"completionId": "c1", "contentName": "tool-1", "toolUseId": "tool-use-1", "toolName": "process_interview_turn", "content": "{}"}}}),
                _chunk({"event": {"contentStart": {"contentName": "user-1", "completionId": "c1", "type": "TEXT", "role": "USER"}}}),
                _chunk({"event": {"textOutput": {"contentName": "user-1", "completionId": "c1", "content": "It happens every morning."}}}),
                _chunk({"event": {"contentEnd": {"contentName": "user-1", "completionId": "c1"}}}),
                _chunk({"event": {"contentEnd": {"contentName": "tool-1", "completionId": "c1", "type": "TOOL", "stopReason": "TOOL_USE"}}}),
            ]
        )
        bridge = FakeInterviewBridge()
        bridge.save_error = InterviewApiError("turn_save_failed", "save failed")
        runtime = NovaSonicRuntime(
            sdk_client=FakeSdkClient(stream),
            interview_bridge=bridge,
        )
        runtime._config = runtime._config.__class__(
            enable_forced_tool_use=True,
            forced_tool_result_delay_ms=1,
            interview_error_reply_text="処理に失敗しました。もう一度お願いします。",
        )
        await runtime.start(_context())
        await asyncio.sleep(0.08)
        payloads = _decode_sent_payloads(stream)
        await runtime.close()
        return payloads, bridge

    payloads, bridge = asyncio.run(run())
    assert [call[0] for call in bridge.calls] == ["load_voice_session", "save_turn"]
    tool_result_payload = next(payload for payload in payloads if "toolResult" in payload["event"])
    assert json.loads(tool_result_payload["event"]["toolResult"]["content"]) == {
        "reply_text": "処理に失敗しました。もう一度お願いします。"
    }


def test_completion_end_marks_approved_output_complete_when_protocol_completes() -> None:
    async def run() -> object:
        stream = FakeDuplexStream(
            [
                _chunk({"event": {"completionStart": {"completionId": "c1"}}}),
                _chunk({"event": {"contentStart": {"contentName": "assistant-final", "completionId": "c1", "type": "TEXT", "role": "ASSISTANT", "additionalModelFields": {"generationStage": "FINAL"}}}}),
                _chunk({"event": {"textOutput": {"contentName": "assistant-final", "completionId": "c1", "content": "done", "additionalModelFields": {"generationStage": "FINAL"}}}}),
                _chunk({"event": {"contentEnd": {"contentName": "assistant-final", "completionId": "c1"}}}),
                _chunk({"event": {"contentStart": {"contentName": "assistant-audio", "completionId": "c1", "type": "AUDIO", "role": "ASSISTANT"}}}),
                _chunk({"event": {"audioOutput": {"contentName": "assistant-audio", "completionId": "c1", "content": "AAE="}}}),
                _chunk({"event": {"contentEnd": {"contentName": "assistant-audio", "completionId": "c1"}}}),
                _chunk({"event": {"completionEnd": {"completionId": "c1", "stopReason": "END_TURN"}}}),
            ]
        )
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream))
        await runtime.start(_context())
        await runtime.send_reply(
            AssistantReply(
                turn_id="turn-1",
                response_id="approved-response-1",
                text="done",
                action="approved_reply",
                question_id=None,
                state_version=1,
            )
        )
        await asyncio.sleep(0.05)
        observed = runtime.observed_output
        await runtime.close()
        return observed

    observed = asyncio.run(run())
    assert observed.approved_output_complete is True
    assert observed.approved_protocol_complete is True


def test_model_stream_error_includes_stage_and_event_history() -> None:
    async def run() -> list[object]:
        stream = FakeDuplexStream(
            [
                _chunk({"event": {"completionStart": {"completionId": "c1"}}}),
                InvokeModelWithBidirectionalStreamOutputModelStreamErrorException(
                    value=ModelStreamErrorException(message="stream failed")
                ),
            ]
        )
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream))
        collector = asyncio.create_task(_collect_events(runtime))
        await runtime.start(_context())
        await runtime.send_reply(_reply())
        await asyncio.sleep(0.05)
        await runtime.close()
        return await collector

    events = asyncio.run(run())
    error = next(event for event in events if isinstance(event, RuntimeError))
    assert error.detail["code"] == "nova_sonic_model_stream_error"
    assert error.detail["stage"] == "user_text_content_end_sent"
    assert error.detail["received_event_types"] == ["completion_start", "model_stream_error"]


def test_close_is_idempotent() -> None:
    async def run() -> list[object]:
        stream = FakeDuplexStream([])
        runtime = NovaSonicRuntime(sdk_client=FakeSdkClient(stream))
        collector = asyncio.create_task(_collect_events(runtime))
        await runtime.start(_context())
        await runtime.close()
        await runtime.close()
        return await collector

    events = asyncio.run(run())
    assert sum(1 for event in events if isinstance(event, RuntimeClosed)) == 1


def test_payload_debug_sanitizer_redacts_text_and_removes_none() -> None:
    payload = {
        "event": {
            "textInput": {
                "promptName": "prompt-1",
                "contentName": "user-text-1",
                "content": "こんにちは",
                "optional": None,
            }
        }
    }

    sanitized = sanitize_payload_for_debug(payload)
    assert sanitized["event"]["textInput"]["content"] == "<redacted>"
    assert "optional" not in sanitized["event"]["textInput"]


def test_build_user_text_sequence_json_types_are_preserved() -> None:
    payloads = build_user_text_sequence(
        prompt_name="prompt-1",
        content_name="content-1",
        text='Say "hello"',
    )
    encoded = [json.loads(dumps_event_payload(payload).decode("utf-8")) for _, payload in payloads]
    start_payload = encoded[0]["event"]["contentStart"]

    assert start_payload["interactive"] is True
    assert start_payload["textInputConfiguration"]["mediaType"] == "text/plain"
    assert encoded[1]["event"]["textInput"]["content"] == 'Say "hello"'


def test_browser_playback_drain_controls_input_gate_and_ignores_overlap_transcripts() -> None:
    async def run() -> tuple[str, list[str]]:
        stream = FakeDuplexStream([])
        bridge = FakeInterviewBridge()
        bridge.save_turn_result = SimpleNamespace(
            turn_id="turn-1",
            processing_status="pending",
            processing_mode="answer_evaluation",
        )
        runtime = NovaSonicRuntime(
            sdk_client=FakeSdkClient(stream),
            interview_bridge=bridge,
        )
        runtime._config = runtime._config.__class__(
            enable_forced_tool_use=True,
            forced_tool_result_delay_ms=0,
        )
        await runtime.start(_context())

        first_turn = [
            _chunk({"event": {"completionStart": {"completionId": "c1"}}}),
            _chunk({"event": {"contentStart": {"contentName": "tool-1", "completionId": "c1", "type": "TOOL", "role": "TOOL"}}}),
            _chunk({"event": {"toolUse": {"completionId": "c1", "contentName": "tool-1", "toolUseId": "tool-use-1", "toolName": "process_interview_turn", "content": "{}"}}}),
            _chunk({"event": {"userSpeechStart": {"promptName": "prompt-1", "sessionId": "s1"}}}),
            _chunk({"event": {"contentStart": {"contentName": "user-1", "completionId": "c1", "type": "TEXT", "role": "USER"}}}),
            _chunk({"event": {"textOutput": {"contentName": "user-1", "completionId": "c1", "content": "最初の回答"}}}),
            _chunk({"event": {"userSpeechEnd": {"promptName": "prompt-1", "sessionId": "s1"}}}),
            _chunk({"event": {"contentEnd": {"contentName": "user-1", "completionId": "c1"}}}),
            _chunk({"event": {"contentEnd": {"contentName": "tool-1", "completionId": "c1", "type": "TOOL", "stopReason": "TOOL_USE"}}}),
        ]
        for event in first_turn:
            await runtime._protocol_dispatcher.handle_stream_event(event)
        await asyncio.sleep(0.05)

        await runtime.notify_assistant_playback_started(response_id="resp-1", generation=runtime.current_generation)

        overlap = [
            _chunk({"event": {"contentStart": {"contentName": "user-echo-1", "completionId": "c1", "type": "TEXT", "role": "USER"}}}),
            _chunk({"event": {"textOutput": {"contentName": "user-echo-1", "completionId": "c1", "content": "はい"}}}),
            _chunk({"event": {"contentEnd": {"contentName": "user-echo-1", "completionId": "c1"}}}),
        ]
        for event in overlap:
            await runtime._protocol_dispatcher.handle_stream_event(event)
        await asyncio.sleep(0.01)

        bridge.save_turn_result = SimpleNamespace(
            turn_id="turn-2",
            processing_status="pending",
            processing_mode="confirmation_reply",
        )
        runtime._completion_lifecycle.reset_turn_state_after_assistant_ended("c1")
        runtime._response_controller.on_completion_finished("c1")
        await runtime.notify_assistant_playback_drained(response_id="resp-1", generation=runtime.current_generation)
        await asyncio.sleep(0.25)

        calls = [call[0] for call in bridge.calls]
        state = runtime.input_state
        await runtime.close()
        return state, calls

    state, calls = asyncio.run(run())
    assert calls.count("save_turn") == 1
    assert calls.count("process_saved_turn") == 1
    assert state in {"ANSWER_LISTENING", "ASSISTANT_SPEAKING", "ANSWER_PROCESSING", "CONFIRMATION_LISTENING"}
