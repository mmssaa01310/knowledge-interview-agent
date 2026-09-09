import asyncio
import json

import ai_interviewer_voice.runtimes.openai_realtime.coordinator as coordinator_module
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


def make_session(
    bridge: FakeBridge | None = None,
    *,
    initial_reply_text: str | None = None,
    config: OpenAIRealtimeConfig | None = None,
) -> OpenAIRealtimeSession:
    voice_session = AuthorizedVoiceSession(
        voice_session_id="voice-session-1",
        record_id="record-1",
        owner_user_id="user-1",
        provider="openai_realtime",
        status="active",
        current_question_id="q-1",
        state_version=1,
        interview_status="active",
        initial_reply_text=initial_reply_text,
        initial_question_id="q-1" if initial_reply_text else None,
        initial_reply_status="pending" if initial_reply_text else None,
    )
    return OpenAIRealtimeSession(
        voice_session=voice_session,
        call_id="call-1",
        config=config or OpenAIRealtimeConfig(api_key="sk-project-test-key", enabled=True),
        call_client=FakeCallClient(),
        interview_bridge=bridge or FakeBridge(),
        voice_session_service=FakeVoiceSessionService(),
        on_closed=lambda session: asyncio.sleep(0),
    )


def test_transport_start_does_not_wait_for_browser_dependent_initial_dispatch() -> None:
    async def run() -> None:
        session = make_session(initial_reply_text="最初の質問です。")

        async def sideband() -> None:
            session._transport_ready.set()
            # session.created may arrive only after the browser gets the SDP.
            await asyncio.Event().wait()

        session._run_sideband = sideband
        try:
            await asyncio.wait_for(session.start(), timeout=0.1)
            assert not session._conversation_ready.is_set()
            assert not session._initial_question_dispatched
        finally:
            await session.close(reason="test_cleanup")

    asyncio.run(run())


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


def test_duplicate_final_transcript_event_is_processed_once() -> None:
    async def run() -> tuple[FakeBridge, list[dict]]:
        bridge = FakeBridge()
        session = make_session(bridge)
        websocket = FakeWebSocket()
        session._websocket = websocket
        event = {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "item-duplicate",
            "transcript": "こんにちは。",
        }
        await session._handle_event(event)
        await session._handle_event(event)
        await asyncio.gather(*session._turn_tasks)
        return bridge, websocket.sent

    bridge, events = asyncio.run(run())

    assert len(bridge.processed) == 1
    assert len([event for event in events if event["type"] == "response.create"]) == 1


def test_duplicate_trace_counts_provider_event_and_unique_turn_once() -> None:
    async def run() -> tuple[FakeBridge, OpenAIRealtimeSession]:
        bridge = FakeBridge()
        session = make_session(bridge)
        websocket = FakeWebSocket()
        session._websocket = websocket
        event = {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "item-trace",
            "transcript": "こんにちは。",
        }
        await session._handle_event(event)
        await session._handle_event(event)
        await asyncio.gather(*session._turn_tasks)
        return bridge, session

    bridge, session = asyncio.run(run())

    assert len(bridge.processed) == 1
    assert session._transcript_final_count == 2
    assert session._unique_transcript_item_ids == {"item-trace"}
    assert session._process_turn_task_count == 1
    assert session._backend_process_count == 1
    assert session._response_create_count == 1


def test_answer_turn_waits_for_initial_reply_task() -> None:
    class InitialBridge(FakeBridge):
        async def claim_initial_reply(self, voice_session_id: str):
            return type(
                "Claim",
                (),
                {"claimed": True, "initial_reply_text": "最初の質問です。", "initial_question_id": "q-1"},
            )()

    async def run() -> list[dict]:
        session = make_session(InitialBridge())
        websocket = FakeWebSocket()
        session._websocket = websocket
        session._initial_task = asyncio.create_task(session._send_initial_reply())
        await session._process_turn(item_id="item-2", transcript="回答です。")
        return websocket.sent

    events = asyncio.run(run())

    response_events = [event for event in events if event["type"] == "response.create"]
    assert [event["response"]["metadata"]["kikiori_kind"] for event in response_events] == [
        "initial",
        "interview",
    ]


def test_started_session_waits_for_initial_dispatch_before_processing_turn() -> None:
    class InitialBridge(FakeBridge):
        async def claim_initial_reply(self, voice_session_id: str):
            return type(
                "Claim",
                (),
                {
                    "claimed": True,
                    "initial_reply_text": "これから開始します。最初の質問です。",
                    "initial_question_id": "q-1",
                    "reason": None,
                },
            )()

    async def run() -> tuple[InitialBridge, OpenAIRealtimeSession, list[dict]]:
        bridge = InitialBridge()
        session = make_session(bridge, initial_reply_text="これから開始します。最初の質問です。")
        websocket = FakeWebSocket()
        session._websocket = websocket
        session._startup_enforced = True
        await session._initialize_once()
        assert [event["type"] for event in websocket.sent] == ["session.update"]

        turn_task = asyncio.create_task(
            session._process_turn(item_id="item-before-initial", transcript="すぐ回答します。")
        )
        await asyncio.sleep(0)
        assert bridge.processed == []
        assert [event["type"] for event in websocket.sent] == ["session.update"]
        assert session._pending_first_user_turns == {
            "item-before-initial": "すぐ回答します。"
        }

        await session._handle_event({"type": "session.created"})
        assert session._initial_task is None
        await session._handle_event({"type": "session.updated"})
        assert session._initial_task is not None
        await session._initial_task
        await turn_task
        return bridge, session, websocket.sent

    bridge, session, events = asyncio.run(run())

    assert len(bridge.processed) == 1
    assert session._pending_first_user_turns == {}
    response_events = [event for event in events if event["type"] == "response.create"]
    assert [event["response"]["metadata"]["kikiori_kind"] for event in response_events] == [
        "initial",
        "interview",
    ]
    assert session._startup_state == "READY_FOR_USER_TURN"
    assert session._timeline["session_created_received"]
    assert session._timeline["session_update_sent"]
    assert session._timeline["session_updated_received"]
    assert session._timeline["initial_question_ready"]
    assert session._timeline["initial_response_create"]
    assert session._timeline["ready_for_user_turn"]


def test_sideband_update_starts_initialization_without_session_created() -> None:
    class InitialBridge(FakeBridge):
        async def claim_initial_reply(self, voice_session_id: str):
            return type(
                "Claim",
                (),
                {
                    "claimed": True,
                    "initial_reply_text": "これから開始します。最初の質問です。",
                    "initial_question_id": "q-1",
                    "reason": None,
                },
            )()

    async def run() -> tuple[InitialBridge, OpenAIRealtimeSession, list[dict]]:
        bridge = InitialBridge()
        session = make_session(bridge, initial_reply_text="これから開始します。最初の質問です。")
        websocket = FakeWebSocket()
        session._websocket = websocket

        await session._initialize_once()
        await session._handle_event({"type": "session.updated"})
        assert session._initial_task is not None
        await session._initial_task
        return bridge, session, websocket.sent

    bridge, session, events = asyncio.run(run())

    assert len(bridge.processed) == 0
    assert [event["type"] for event in events] == ["session.update", "response.create"]
    assert session._session_created_received is False
    assert session._session_updated_received is True
    assert session._initialization_state == "INITIAL_RESPONSE_SENT"
    assert session._startup_state == "READY_FOR_USER_TURN"


def test_session_created_can_be_the_first_initialization_trigger() -> None:
    class InitialBridge(FakeBridge):
        async def claim_initial_reply(self, voice_session_id: str):
            return type(
                "Claim",
                (),
                {
                    "claimed": True,
                    "initial_reply_text": "これから開始します。最初の質問です。",
                    "initial_question_id": "q-1",
                    "reason": None,
                },
            )()

    async def run() -> tuple[OpenAIRealtimeSession, list[dict]]:
        session = make_session(
            InitialBridge(),
            initial_reply_text="これから開始します。最初の質問です。",
        )
        websocket = FakeWebSocket()
        session._websocket = websocket

        await session._handle_event({"type": "session.created"})
        assert [event["type"] for event in websocket.sent] == ["session.update"]
        assert session._initial_task is None

        await session._handle_event({"type": "session.updated"})
        assert session._initial_task is not None
        await session._initial_task
        return session, websocket.sent

    session, events = asyncio.run(run())

    assert [event["type"] for event in events] == ["session.update", "response.create"]
    assert session._session_created_received is True
    assert session._session_updated_received is True
    assert session._startup_state == "READY_FOR_USER_TURN"


def test_sideband_open_sends_session_update_before_reading_events(monkeypatch) -> None:
    class InitialBridge(FakeBridge):
        async def claim_initial_reply(self, voice_session_id: str):
            return type(
                "Claim",
                (),
                {
                    "claimed": True,
                    "initial_reply_text": "これから開始します。最初の質問です。",
                    "initial_question_id": "q-1",
                    "reason": None,
                },
            )()

    class SidebandWebSocket(FakeWebSocket):
        def __init__(self) -> None:
            super().__init__()
            self._event_consumed = False
            self._wait_forever = asyncio.Event()

        def __aiter__(self):
            return self

        async def __anext__(self) -> str:
            if not self._event_consumed:
                self._event_consumed = True
                return json.dumps({"type": "session.updated"})
            await self._wait_forever.wait()
            raise StopAsyncIteration

    class SidebandContext:
        def __init__(self, websocket: SidebandWebSocket) -> None:
            self.websocket = websocket

        async def __aenter__(self) -> SidebandWebSocket:
            return self.websocket

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

    async def run() -> tuple[OpenAIRealtimeSession, SidebandWebSocket]:
        websocket = SidebandWebSocket()
        monkeypatch.setattr(
            coordinator_module,
            "websocket_connect",
            lambda *args, **kwargs: SidebandContext(websocket),
        )
        session = make_session(
            InitialBridge(),
            initial_reply_text="これから開始します。最初の質問です。",
        )
        await session.start()
        for _ in range(10):
            if session._initial_task is not None:
                break
            await asyncio.sleep(0)
        assert session._initial_task is not None
        await session._initial_task
        await session.close(reason="test_cleanup")
        return session, websocket

    session, websocket = asyncio.run(run())

    assert [event["type"] for event in websocket.sent] == [
        "session.update",
        "response.create",
    ]
    assert session._session_updated_received is True
    assert session._startup_state == "READY_FOR_USER_TURN"


def test_initialization_is_idempotent_when_session_created_arrives_after_open() -> None:
    class InitialBridge(FakeBridge):
        async def claim_initial_reply(self, voice_session_id: str):
            return type(
                "Claim",
                (),
                {
                    "claimed": True,
                    "initial_reply_text": "これから開始します。最初の質問です。",
                    "initial_question_id": "q-1",
                    "reason": None,
                },
            )()

    async def run() -> tuple[OpenAIRealtimeSession, list[dict]]:
        session = make_session(
            InitialBridge(),
            initial_reply_text="これから開始します。最初の質問です。",
        )
        websocket = FakeWebSocket()
        session._websocket = websocket

        await asyncio.gather(session._initialize_once(), session._initialize_once())
        await session._handle_event({"type": "session.updated"})
        await session._handle_event({"type": "session.created"})
        await session._handle_event({"type": "session.updated"})
        assert session._initial_task is not None
        await session._initial_task
        return session, websocket.sent

    session, events = asyncio.run(run())

    assert len([event for event in events if event["type"] == "session.update"]) == 1
    assert len([event for event in events if event["type"] == "response.create"]) == 1
    assert session._session_created_received is True
    assert session._session_updated_received is True


def test_initialization_timeout_logs_explicit_reason(caplog) -> None:
    async def run() -> OpenAIRealtimeSession:
        session = make_session(
            initial_reply_text="最初の質問です。",
            config=OpenAIRealtimeConfig(
                api_key="sk-project-test-key",
                enabled=True,
                sideband_connect_timeout_seconds=0.01,
            ),
        )
        session._transport_ready.set()
        session._session_update_sent = True
        await session._wait_for_conversation_start()
        return session

    session = asyncio.run(run())

    assert session._startup_error is not None
    assert session._startup_error.code == "openai_realtime_initialization_timeout"
    assert "failure_reason=INITIALIZATION_TIMEOUT" in caplog.text
    assert "session_update_sent=True" in caplog.text
    assert "session_updated_received=False" in caplog.text


def test_dependency_error_is_explicit() -> None:
    error = OpenAIRealtimeProviderError("openai_realtime_dependency_missing")

    assert error.code == "openai_realtime_dependency_missing"
