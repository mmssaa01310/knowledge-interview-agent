import asyncio
import json

from ai_interviewer_voice.runtimes.openai_realtime.client import OpenAIRealtimeProviderError
from ai_interviewer_voice.runtimes.openai_realtime.config import OpenAIRealtimeConfig
from ai_interviewer_voice.runtimes.openai_realtime.coordinator import OpenAIRealtimeSession
from ai_interviewer_voice.services.interview_bridge import InterviewBridgeResult
from ai_interviewer_voice.services.voice_session_service import AuthorizedVoiceSession


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


class FakeCallClient:
    async def hangup(self, call_id: str) -> None:
        return None


class FakeBridge:
    def __init__(self) -> None:
        self.processed: list[dict] = []

    async def claim_initial_reply(self, voice_session_id: str):
        return type("Claim", (), {"claimed": False, "initial_reply_text": None})()

    async def mark_initial_reply_sent(self, voice_session_id: str) -> None:
        return None

    async def mark_initial_reply_failed(self, voice_session_id: str) -> None:
        return None

    async def process_turn(self, **kwargs):
        self.processed.append(kwargs)
        return InterviewBridgeResult(
            turn_id="turn-1",
            response_id="response-1",
            reply_text="軸受温度を確認してください。",
            action="ask_followup",
            question_id="q-2",
            state_version=2,
            interview_status="active",
        )


class FakeVoiceSessionService:
    async def create_connection_event(self, *args, **kwargs) -> None:
        return None


def make_session(bridge: FakeBridge | None = None) -> OpenAIRealtimeSession:
    voice_session = AuthorizedVoiceSession(
        voice_session_id="voice-session-1",
        record_id="record-1",
        owner_user_id="user-1",
        provider="openai_realtime",
        status="active",
        current_question_id="q-1",
        state_version=1,
        interview_status="active",
    )
    return OpenAIRealtimeSession(
        voice_session=voice_session,
        call_id="call-1",
        config=OpenAIRealtimeConfig(api_key="sk-project-test-key", enabled=True),
        call_client=FakeCallClient(),
        interview_bridge=bridge or FakeBridge(),
        voice_session_service=FakeVoiceSessionService(),
        on_closed=lambda session: asyncio.sleep(0),
    )


def test_backend_reply_uses_explicit_response_without_default_conversation() -> None:
    async def run() -> dict:
        session = make_session()
        websocket = FakeWebSocket()
        session._websocket = websocket
        await session._send_backend_reply(
            reply_text="Backendの回答です。",
            response_id="response-1",
            turn_id="turn-1",
            question_id="q-2",
            kind="interview",
            interview_status="active",
        )
        return websocket.sent[0]

    event = asyncio.run(run())

    assert event["type"] == "response.create"
    response = event["response"]
    assert response["conversation"] == "none"
    assert response["output_modalities"] == ["audio"]
    assert response["parallel_tool_calls"] is False
    assert response["input"][0]["content"][0]["text"] == "Backendの回答です。"
    assert response["metadata"]["kikiori_response_id"] == "response-1"


def test_speech_start_cancels_active_response_for_barge_in() -> None:
    async def run() -> list[dict]:
        session = make_session()
        websocket = FakeWebSocket()
        session._websocket = websocket
        session._active_response_pending = True
        session._active_response_id = "resp-1"
        await session._handle_event({"type": "input_audio_buffer.speech_started"})
        return websocket.sent

    events = asyncio.run(run())

    assert events == [
        {"type": "response.cancel", "response_id": "resp-1"},
        {"type": "output_audio_buffer.clear"},
    ]


def test_final_transcript_is_forwarded_to_interview_bridge_then_replied() -> None:
    async def run() -> tuple[FakeBridge, list[dict]]:
        bridge = FakeBridge()
        session = make_session(bridge)
        websocket = FakeWebSocket()
        session._websocket = websocket
        await session._handle_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item-1",
                "transcript": "軸受の温度を見ています",
            }
        )
        await asyncio.gather(*session._turn_tasks)
        return bridge, websocket.sent

    bridge, events = asyncio.run(run())

    assert bridge.processed[0]["transcript"] == "軸受の温度を見ています"
    assert bridge.processed[0]["client_turn_id"] == "openai-item-1"
    assert events[0]["type"] == "response.create"
    assert events[0]["response"]["metadata"]["kikiori_interview_status"] == "active"


def test_dependency_error_is_explicit() -> None:
    error = OpenAIRealtimeProviderError("openai_realtime_dependency_missing")

    assert error.code == "openai_realtime_dependency_missing"
