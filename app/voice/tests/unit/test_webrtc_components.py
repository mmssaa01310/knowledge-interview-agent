from __future__ import annotations

import asyncio
from fractions import Fraction
import logging

import av
import httpx
import pytest
from fastapi import HTTPException
from aiortc import MediaStreamTrack

from ai_interviewer_voice.schemas.events import AssistantAudioChunk, AssistantSpeechEnded, RuntimeError, UserTranscriptFinal
from ai_interviewer_voice.schemas.events import InputStateChanged
from ai_interviewer_voice.services.voice_session_service import AuthorizedVoiceSession, InitialReplyClaim
from ai_interviewer_voice.services.voice_session_service import VoiceSessionService
from ai_interviewer_voice.transports.webrtc.audio_input_track import AudioInputTrackConsumer
from ai_interviewer_voice.transports.webrtc.data_channel import VoiceEventContext, _serialize_runtime_event
from ai_interviewer_voice.transports.webrtc.playback_buffer import PlaybackBuffer
from ai_interviewer_voice.transports.webrtc.playback_buffer import PlaybackBufferCapacityExceeded
from ai_interviewer_voice.transports.webrtc.peer_connection import INITIAL_VOICE_GREETING, VoicePeerConnection
from ai_interviewer_voice.transports.webrtc.registry import DuplicatePeerConnectionError, PeerConnectionRegistry


class StubDataChannel:
    def __init__(self, label: str = "voice-events", ready_state: str = "open") -> None:
        self.label = label
        self.readyState = ready_state
        self.messages: list[str] = []

    def send(self, payload: str) -> None:
        self.messages.append(payload)


class StubRuntime:
    def __init__(self, delay_seconds: float = 0.0) -> None:
        self.delay_seconds = delay_seconds
        self.frames: list[bytes] = []
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.playback_drained: list[tuple[str | None, int | None]] = []

    async def push_audio(self, frame) -> None:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        self.frames.append(frame.pcm)

    async def start_initial_reply(self, *, reply_text: str, question_id: str | None) -> None:
        self.calls.append(("start_initial_reply", {"reply_text": reply_text, "question_id": question_id}))

    async def queue_initial_followup_reply(self, *, reply_text: str, question_id: str | None) -> None:
        self.calls.append(("queue_initial_followup_reply", {"reply_text": reply_text, "question_id": question_id}))

    async def send_reply(self, reply) -> None:
        self.calls.append(("send_reply", {"text": reply.text, "question_id": reply.question_id}))

    async def start(self, context) -> None:
        return None

    async def interrupt(self) -> None:
        return None

    async def notify_assistant_playback_drained(
        self,
        *,
        response_id: str | None,
        generation: int | None,
    ) -> None:
        self.playback_drained.append((response_id, generation))

    async def close(self) -> None:
        return None

    async def start_audio_input(self) -> None:
        return None

    async def end_audio_input(self) -> None:
        return None

    async def events(self):
        for event in ():
            yield event


class BurstAudioTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self, frame_count: int) -> None:
        super().__init__()
        self._remaining = frame_count
        self._pts = 0

    async def recv(self):
        if self._remaining <= 0:
            raise asyncio.CancelledError
        self._remaining -= 1
        frame = av.AudioFrame(format="s16", layout="mono", samples=960)
        frame.sample_rate = 48000
        frame.planes[0].update(b"\x01\x02" * 960)
        frame.pts = self._pts
        frame.time_base = Fraction(1, 48000)
        self._pts += 960
        return frame


@pytest.mark.anyio
async def test_voice_session_service_rejects_invalid_token() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(401, json={"detail": "invalid_token"})
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://voice.test") as client:
        service = VoiceSessionService(
            api_base_url="http://voice.test",
            internal_api_token="internal-token",
            http_client=client,
        )
        with pytest.raises(HTTPException) as exc_info:
            await service.authorize_session("vs-1", bearer_token="bad-token")
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_voice_session_service_loads_authorized_session() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "id": "vs-1",
                "recordId": "record-1",
                "ownerUserId": "user-1",
                "provider": "nova_sonic",
                "status": "active",
                "currentQuestionId": "q-001",
                "initialReplyText": "どのような現象が起きていますか？",
                "initialQuestionId": "q-001",
                "initialReplyStatus": "pending",
                "stateVersion": 2,
            },
        )
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://voice.test") as client:
        service = VoiceSessionService(
            api_base_url="http://voice.test",
            internal_api_token="internal-token",
            http_client=client,
        )
        session = await service.authorize_session("vs-1", bearer_token="dev-manager")
    assert session.voice_session_id == "vs-1"
    assert session.current_question_id == "q-001"
    assert session.initial_reply_text == "どのような現象が起きていますか？"
    assert session.initial_question_id == "q-001"
    assert session.initial_reply_status == "pending"
    assert session.state_version == 2


@pytest.mark.anyio
async def test_voice_session_service_rejects_completed_session_on_authorize() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "id": "vs-1",
                "recordId": "record-1",
                "ownerUserId": "user-1",
                "provider": "nova_sonic",
                "status": "completed",
                "currentQuestionId": None,
                "stateVersion": 2,
            },
        )
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://voice.test") as client:
        service = VoiceSessionService(
            api_base_url="http://voice.test",
            internal_api_token="internal-token",
            http_client=client,
        )
        with pytest.raises(HTTPException) as exc_info:
            await service.authorize_session("vs-1", bearer_token="dev-manager")
    assert exc_info.value.status_code == 409


@pytest.mark.anyio
async def test_playback_buffer_rejects_unauthorized_and_old_generation() -> None:
    buffer = PlaybackBuffer()
    unauthorized = AssistantAudioChunk(
        response_id="r1",
        completion_id="c1",
        generation=1,
        sequence=1,
        pcm=b"\x01\x02",
        authorized=False,
    )
    old_generation = AssistantAudioChunk(
        response_id="r1",
        completion_id="c1",
        generation=1,
        sequence=1,
        pcm=b"\x03\x04",
        authorized=True,
    )
    assert await buffer.enqueue(unauthorized, current_generation=1) is False
    assert await buffer.enqueue(old_generation, current_generation=2) is False
    assert await buffer.depth_ms() == 0.0


@pytest.mark.anyio
async def test_playback_buffer_accepts_transcribe_polly_16khz_pcm() -> None:
    buffer = PlaybackBuffer(sample_rate_hz=16000)
    chunk = AssistantAudioChunk(
        response_id="polly-response",
        completion_id="polly-completion",
        generation=1,
        sequence=1,
        pcm=bytes(640),
        authorized=True,
        sample_rate_hz=16000,
    )

    assert await buffer.enqueue(chunk, current_generation=1) is True
    assert await buffer.depth_ms() == pytest.approx(20.0)


@pytest.mark.anyio
async def test_playback_buffer_clears_on_interrupt() -> None:
    buffer = PlaybackBuffer()
    chunk = AssistantAudioChunk(
        response_id="r1",
        completion_id="c1",
        generation=2,
        sequence=1,
        pcm=b"\x01\x02" * 480,
        authorized=True,
    )
    assert await buffer.enqueue(chunk, current_generation=2) is True
    assert await buffer.depth_ms() > 0
    cleared_ms = await buffer.clear(count_as_dropped=True)
    stats = await buffer.snapshot_stats()
    assert cleared_ms > 0
    assert await buffer.depth_ms() == 0.0
    assert stats.playback_old_audio_dropped_ms == pytest.approx(cleared_ms, abs=0.1)


@pytest.mark.anyio
async def test_playback_buffer_normal_clear_does_not_count_as_drop() -> None:
    buffer = PlaybackBuffer()
    chunk = AssistantAudioChunk(
        response_id="r1",
        completion_id="c1",
        generation=2,
        sequence=1,
        pcm=b"\x01\x02" * 480,
        authorized=True,
    )
    assert await buffer.enqueue(chunk, current_generation=2) is True
    cleared_ms = await buffer.clear()
    stats = await buffer.snapshot_stats()
    assert cleared_ms > 0
    assert stats.playback_old_audio_dropped_ms == 0.0


@pytest.mark.anyio
async def test_playback_buffer_keeps_pcm_when_preroll_target_is_exceeded() -> None:
    buffer = PlaybackBuffer(target_depth_ms=80.0, retention_max_ms=5000.0)
    chunk = AssistantAudioChunk(
        response_id="r1",
        completion_id="c1",
        generation=2,
        sequence=1,
        pcm=b"\x01\x02" * 480,
        authorized=True,
    )
    for _ in range(7):
        assert await buffer.enqueue(chunk, current_generation=2) is True
    stats = await buffer.snapshot_stats()
    assert buffer.drop_count == 0
    assert stats.playback_old_audio_dropped_ms == 0.0
    assert stats.playback_buffer_depth_ms == pytest.approx(140.0, abs=0.1)
    assert stats.playback_buffer_peak_depth_ms == pytest.approx(140.0, abs=0.1)


@pytest.mark.anyio
async def test_initial_reply_greeting_is_sent_before_claim_completes() -> None:
    events: list[tuple[str, str | None]] = []
    claim_release = asyncio.Event()
    runtime = StubRuntime()

    class StubInitialReplyService:
        async def claim_initial_reply(self, voice_session_id: str, *, timeout_seconds: float = 5.0) -> InitialReplyClaim:
            events.append(("claim_started", voice_session_id))
            await claim_release.wait()
            events.append(("claim_completed", voice_session_id))
            return InitialReplyClaim(
                claimed=True,
                initial_reply_text=f"{INITIAL_VOICE_GREETING}あなたの名前は？",
                initial_question_id="q-001",
            )

        async def create_connection_event(
            self,
            voice_session_id: str,
            *,
            event_type: str,
            connection_status: str | None,
            detail: dict,
            timeout_seconds: float = 5.0,
        ) -> None:
            events.append((event_type, connection_status))

        async def mark_initial_reply_sent(self, voice_session_id: str, *, timeout_seconds: float = 5.0) -> None:
            events.append(("mark_initial_reply_sent", voice_session_id))

        async def mark_initial_reply_failed(self, voice_session_id: str, *, timeout_seconds: float = 5.0) -> None:
            events.append(("mark_initial_reply_failed", voice_session_id))

    session = AuthorizedVoiceSession(
        voice_session_id="vs-1",
        record_id="record-1",
        owner_user_id="user-1",
        provider="fake",
        status="active",
        current_question_id="q-001",
        state_version=1,
        interview_status="active",
        initial_reply_text=f"{INITIAL_VOICE_GREETING}あなたの名前は？",
        initial_question_id="q-001",
        initial_reply_status="pending",
    )
    peer = VoicePeerConnection(
        session=session,
        bearer_token="dev-manager",
        runtime_factory=lambda provider: runtime,
        ice_servers=(),
        voice_session_service=StubInitialReplyService(),  # type: ignore[arg-type]
        on_closed=lambda _: asyncio.sleep(0),
        ice_gathering_timeout_seconds=1.0,
        peer_disconnected_grace_seconds=1.0,
    )
    peer._runtime_started = True

    task = asyncio.create_task(peer._send_initial_reply_if_pending())
    await asyncio.sleep(0.01)

    assert runtime.calls == [
        ("start_initial_reply", {"reply_text": INITIAL_VOICE_GREETING, "question_id": None}),
    ]
    assert events == [("claim_started", "vs-1")]

    claim_release.set()
    await task

    assert events[:2] == [("claim_started", "vs-1"), ("claim_completed", "vs-1")]
    assert runtime.calls == [
        ("start_initial_reply", {"reply_text": INITIAL_VOICE_GREETING, "question_id": None}),
        ("queue_initial_followup_reply", {"reply_text": "あなたの名前は？", "question_id": "q-001"}),
    ]
    await peer.close()


@pytest.mark.anyio
async def test_peer_close_logs_explicit_reason_and_runtime_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime = StubRuntime()

    class StubVoiceSessionService:
        async def mark_initial_reply_failed(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            return None

        async def create_connection_event(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            return None

    peer = VoicePeerConnection(
        session=AuthorizedVoiceSession(
            voice_session_id="vs-close",
            record_id="record-1",
            owner_user_id="user-1",
            provider="fake",
            status="active",
            current_question_id="q-001",
            state_version=1,
            interview_status="active",
        ),
        bearer_token="dev-manager",
        runtime_factory=lambda provider: runtime,
        ice_servers=(),
        voice_session_service=StubVoiceSessionService(),  # type: ignore[arg-type]
        on_closed=lambda _: asyncio.sleep(0),
        ice_gathering_timeout_seconds=1.0,
        peer_disconnected_grace_seconds=1.0,
    )
    peer._runtime_started = True

    with caplog.at_level(logging.INFO):
        await peer.close(reason="client_requested", source="webrtc_delete_endpoint")

    assert "voice_session_close_requested" in caplog.text
    assert "voice_session_close_started" in caplog.text
    assert "voice_session_close_completed" in caplog.text
    assert "peer_connection_close_requested" in caplog.text
    assert "runtime_close_requested" in caplog.text
    assert "reason=client_requested" in caplog.text
    assert "source=webrtc_delete_endpoint" in caplog.text


@pytest.mark.anyio
async def test_peer_playback_watchdog_reopens_runtime_when_browser_drain_is_missing() -> None:
    runtime = StubRuntime()

    class StubVoiceSessionService:
        async def mark_initial_reply_failed(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            return None

        async def create_connection_event(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            return None

    peer = VoicePeerConnection(
        session=AuthorizedVoiceSession(
            voice_session_id="vs-watchdog",
            record_id="record-1",
            owner_user_id="user-1",
            provider="fake",
            status="active",
            current_question_id="q-001",
            state_version=1,
            interview_status="active",
        ),
        bearer_token="dev-manager",
        runtime_factory=lambda provider: runtime,
        ice_servers=(),
        voice_session_service=StubVoiceSessionService(),  # type: ignore[arg-type]
        on_closed=lambda _: asyncio.sleep(0),
        ice_gathering_timeout_seconds=1.0,
        peer_disconnected_grace_seconds=1.0,
        playback_drain_timeout_seconds=0.01,
    )
    peer._runtime_started = True

    await peer._handle_runtime_event(
        AssistantSpeechEnded(
            response_id="response-watchdog",
            generation=1,
            audio_duration_ms=None,
        )
    )
    await asyncio.sleep(0.6)

    assert runtime.playback_drained == [("response-watchdog", 1)]
    await peer.close()


@pytest.mark.anyio
async def test_playback_buffer_raises_when_retention_cap_is_exceeded() -> None:
    buffer = PlaybackBuffer(target_depth_ms=80.0, retention_max_ms=100.0)
    chunk = AssistantAudioChunk(
        response_id="r1",
        completion_id="c1",
        generation=2,
        sequence=1,
        pcm=b"\x01\x02" * 480,
        authorized=True,
    )
    for _ in range(5):
        assert await buffer.enqueue(chunk, current_generation=2) is True
    with pytest.raises(PlaybackBufferCapacityExceeded):
        await buffer.enqueue(chunk, current_generation=2)


@pytest.mark.anyio
async def test_audio_input_consumer_prefers_latest_audio_when_queue_is_saturated() -> None:
    runtime = StubRuntime(delay_seconds=0.02)
    consumer = AudioInputTrackConsumer(runtime, voice_session_id="vs-1", max_queue_frames=2)
    track = BurstAudioTrack(frame_count=12)

    await consumer.start(track)
    await asyncio.sleep(0.15)
    await consumer.stop()

    assert consumer.drop_count > 0
    assert consumer.queue_depth == 0
    assert len(runtime.frames) < consumer.frame_count


@pytest.mark.anyio
async def test_registry_rejects_duplicate_connection() -> None:
    registry = PeerConnectionRegistry()
    await registry.create("vs-1", object())
    with pytest.raises(DuplicatePeerConnectionError):
        await registry.create("vs-1", object())
    assert await registry.get("vs-1") is not None
    await registry.remove("vs-1")
    assert await registry.get("vs-1") is None


def test_runtime_event_serialization_emits_common_events_only() -> None:
    payload = _serialize_runtime_event(
        UserTranscriptFinal(text="hello"),
        context=VoiceEventContext(
            voice_session_id="vs-1",
            question_id="q-001",
            state_version=2,
        ),
    )
    assert payload == {
        "type": "user_transcript_final",
        "voiceSessionId": "vs-1",
        "text": "hello",
        "turnType": "ANSWER",
        "questionId": "q-001",
        "stateVersion": 2,
    }
    error_payload = _serialize_runtime_event(
        RuntimeError(message="boom"),
        context=VoiceEventContext(voice_session_id="vs-1"),
    )
    assert error_payload == {
        "type": "error",
        "voiceSessionId": "vs-1",
        "message": "boom",
        "fatal": True,
    }
    gate_payload = _serialize_runtime_event(
        InputStateChanged(input_state="CONFIRMATION_LISTENING", generation=3),
        context=VoiceEventContext(voice_session_id="vs-1"),
    )
    assert gate_payload == {
        "type": "input_state_changed",
        "voiceSessionId": "vs-1",
        "inputState": "CONFIRMATION_LISTENING",
        "generation": 3,
    }
