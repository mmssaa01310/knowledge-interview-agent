from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fractions import Fraction

import av
import httpx
import pytest
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc import MediaStreamTrack
from httpx import ASGITransport

from ai_interviewer_voice.main import app
from ai_interviewer_voice.routers import webrtc as webrtc_router
from ai_interviewer_voice.services.ice_server_service import IceServerConfig
from ai_interviewer_voice.services.voice_session_service import AuthorizedVoiceSession, InitialReplyClaim
from ai_interviewer_voice.transports.webrtc.registry import PeerConnectionRegistry


class SilentAudioTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self) -> None:
        super().__init__()
        self._pts = 0

    async def recv(self):
        await asyncio.sleep(0.02)
        frame = av.AudioFrame(format="s16", layout="mono", samples=960)
        frame.sample_rate = 48000
        frame.planes[0].update(b"\x00" * 1920)
        frame.pts = self._pts
        frame.time_base = Fraction(1, 48000)
        self._pts += 960
        return frame


@dataclass
class StubVoiceSessionService:
    authorize_calls: int = 0
    connection_events: list[tuple[str, str | None]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.connection_events = []

    async def authorize_session(self, voice_session_id: str, *, bearer_token: str, timeout_seconds: float = 5.0):
        self.authorize_calls += 1
        assert bearer_token == "dev-manager"
        return AuthorizedVoiceSession(
            voice_session_id=voice_session_id,
            record_id="record-1",
            owner_user_id="user-manager",
            provider="fake",
            status="active",
            current_question_id="q-001",
            state_version=1,
            interview_status="active",
            initial_reply_text="どのような現象が起きていますか？",
            initial_question_id="q-001",
            initial_reply_status="pending",
        )

    async def get_session(self, voice_session_id: str, *, bearer_token: str, timeout_seconds: float = 5.0):
        return await self.authorize_session(
            voice_session_id,
            bearer_token=bearer_token,
            timeout_seconds=timeout_seconds,
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
        self.connection_events.append((event_type, connection_status))

    async def mark_initial_reply_sent(self, voice_session_id: str, *, timeout_seconds: float = 5.0) -> None:
        self.connection_events.append(("initial_reply_marked_sent", None))

    async def mark_initial_reply_failed(self, voice_session_id: str, *, timeout_seconds: float = 5.0) -> None:
        self.connection_events.append(("initial_reply_marked_failed", None))

    async def claim_initial_reply(self, voice_session_id: str, *, timeout_seconds: float = 5.0) -> InitialReplyClaim:
        self.connection_events.append(("initial_reply_claimed", None))
        return InitialReplyClaim(
            claimed=True,
            initial_reply_text="どのような現象が起きていますか？",
            initial_question_id="q-001",
        )


class StubIceServerService:
    async def get_ice_servers(self) -> IceServerConfig:
        return IceServerConfig(
            ice_servers=(),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )


@pytest.mark.anyio
async def test_offer_answer_connects_and_streams_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_voice_session_service = StubVoiceSessionService()
    monkeypatch.setattr(webrtc_router, "_voice_session_service", stub_voice_session_service)
    monkeypatch.setattr(webrtc_router, "_ice_server_service", StubIceServerService())
    monkeypatch.setattr(webrtc_router, "_registry", PeerConnectionRegistry())

    received_messages: list[dict] = []
    received_audio_frames = 0
    remote_track = None
    pc = RTCPeerConnection()
    channel = pc.createDataChannel("voice-events", ordered=True)
    pc.addTrack(SilentAudioTrack())

    @channel.on("message")
    def on_message(message: str) -> None:
        received_messages.append(json.loads(message))

    @pc.on("track")
    async def on_track(track) -> None:
        nonlocal received_audio_frames, remote_track
        remote_track = track

        async def consume() -> None:
            nonlocal received_audio_frames
            for _ in range(2):
                await track.recv()
                received_audio_frames += 1

        asyncio.create_task(consume())

    offer = await pc.createOffer()
    try:
        await pc.setLocalDescription(offer)
    except PermissionError as exc:
        await pc.close()
        pytest.skip(f"aiortc host candidate gathering is unavailable in this environment: {exc}")

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/voice/webrtc/vs-1/offer",
            headers={"Authorization": "Bearer dev-manager"},
            json={"type": "offer", "sdp": pc.localDescription.sdp},
        )
        assert response.status_code == 200
        payload = response.json()
        await pc.setRemoteDescription(RTCSessionDescription(sdp=payload["sdp"], type=payload["type"]))

        deadline = asyncio.get_running_loop().time() + 10.0
        while asyncio.get_running_loop().time() < deadline:
            if pc.connectionState == "connected" and received_audio_frames > 0:
                break
            await asyncio.sleep(0.1)

        assert pc.connectionState == "connected"
        assert received_audio_frames > 0
        message_deadline = asyncio.get_running_loop().time() + 5.0
        while asyncio.get_running_loop().time() < message_deadline:
            if received_messages:
                break
            await asyncio.sleep(0.1)
        assert any(
            message["type"] in {"assistant_speech_started", "initial_reply_sent", "input_state_changed"}
            for message in received_messages
        )
        assert ("initial_reply_claimed", None) in stub_voice_session_service.connection_events
        assert ("initial_reply_marked_sent", None) in stub_voice_session_service.connection_events

        delete_response = await client.delete(
            "/voice/webrtc/vs-1",
            headers={"Authorization": "Bearer dev-manager"},
        )
        assert delete_response.status_code == 204

    await pc.close()
    await asyncio.sleep(1.0)
    assert stub_voice_session_service.authorize_calls >= 2
