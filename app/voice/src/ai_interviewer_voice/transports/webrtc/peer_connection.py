"""
Role:
    voice session単位のWebRTC接続オーケストレーション。

Summary:
    RTCPeerConnection、audio入出力、data channel、runtimeイベント消費を束ね、
    browser再生状態とbackendの入力ゲート制御を接続する。

Relations:
    Uses RealtimeVoiceRuntime, PlaybackBuffer, AudioOutputTrack, VoiceEventsDataChannel.
    Used by the WebRTC router and voice session service layer.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import replace
from dataclasses import dataclass
import json
from time import monotonic
from typing import Awaitable, Callable

from aiortc import (
    RTCConfiguration,
    RTCDataChannel,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
)

from ai_interviewer_voice.runtimes.base import RealtimeVoiceRuntime
from ai_interviewer_voice.runtimes.nova_sonic.protocol.payloads import AUDIO_OUTPUT_CHANNELS
from ai_interviewer_voice.runtimes.nova_sonic.protocol.payloads import AUDIO_OUTPUT_SAMPLE_SIZE_BITS
from ai_interviewer_voice.schemas.events import (
    AssistantAudioChunk,
    AssistantInterrupted,
    AssistantSpeechEnded,
    AssistantSpeechStarted,
    InputStateChanged,
    RuntimeClosed,
    RuntimeError,
    RuntimeReady,
    VoiceRuntimeEvent,
)
from ai_interviewer_voice.schemas.sessions import AssistantReply, VoiceRuntimeContext
from ai_interviewer_voice.services.ice_server_service import IceServer
from ai_interviewer_voice.services.voice_session_service import AuthorizedVoiceSession, VoiceSessionService
from ai_interviewer_voice.transports.webrtc.audio_input_track import AudioInputTrackConsumer
from ai_interviewer_voice.transports.webrtc.audio_output_track import AudioOutputTrack
from ai_interviewer_voice.transports.webrtc.audio_output_track import OutputFrameMetrics
from ai_interviewer_voice.transports.webrtc.data_channel import VoiceEventContext, VoiceEventsDataChannel
from ai_interviewer_voice.transports.webrtc.playback_buffer import PlaybackBuffer
from ai_interviewer_voice.transports.webrtc.playback_buffer import PlaybackBufferCapacityExceeded


logger = logging.getLogger(__name__)

INITIAL_VOICE_GREETING = "これからインタビューを開始します。"


@dataclass
class AssistantGenerationMetrics:
    generation: int
    response_id: str | None = None
    completion_id: str | None = None
    received_pcm_bytes: int = 0
    playback_old_audio_dropped_ms: float = 0.0
    playback_buffer_peak_depth_ms: float = 0.0
    output_frame_count: int = 0
    assistant_interrupted: bool = False
    first_output_emitted_at: float | None = None
    last_output_emitted_at: float | None = None
    speech_ended_received: bool = False


class VoicePeerConnection:
    def __init__(
        self,
        *,
        session: AuthorizedVoiceSession,
        bearer_token: str,
        runtime_factory: Callable[..., RealtimeVoiceRuntime],
        ice_servers: tuple[IceServer, ...],
        voice_session_service: VoiceSessionService,
        on_closed: Callable[[str], Awaitable[None]],
        ice_gathering_timeout_seconds: float,
        peer_disconnected_grace_seconds: float,
        audio_input_queue_max_frames: int = 12,
        playback_buffer_target_ms: float = 100.0,
        playback_buffer_retention_max_ms: float = 60000.0,
        playback_preroll_ms: float = 80.0,
        playback_short_underrun_ms: float = 40.0,
        playback_drain_timeout_seconds: float = 10.0,
    ) -> None:
        self.voice_session_id = session.voice_session_id
        self.owner_user_id = session.owner_user_id
        self._bearer_token = bearer_token
        self._runtime_factory = runtime_factory
        self._voice_session_service = voice_session_service
        self._on_closed = on_closed
        self._ice_gathering_timeout_seconds = ice_gathering_timeout_seconds
        self._peer_disconnected_grace_seconds = peer_disconnected_grace_seconds
        self._playback_drain_timeout_seconds = playback_drain_timeout_seconds
        self._session_state = session
        self._runtime = (
            runtime_factory(session.provider, session.interview_locale)
            if session.provider == "transcribe_polly"
            else runtime_factory(session.provider)
        )
        runtime_output_rate_hz = int(getattr(self._runtime, "output_sample_rate_hz", 24000))
        self._playback_buffer = PlaybackBuffer(
            sample_rate_hz=runtime_output_rate_hz,
            target_depth_ms=playback_buffer_target_ms,
            retention_max_ms=playback_buffer_retention_max_ms,
        )
        self._output_track = AudioOutputTrack(
            self._playback_buffer,
            voice_session_id=self.voice_session_id,
            input_rate_hz=runtime_output_rate_hz,
            preroll_ms=playback_preroll_ms,
            short_underrun_ms=playback_short_underrun_ms,
            on_frame_emitted=self._handle_output_frame_emitted,
        )
        self._data_channel = VoiceEventsDataChannel()
        self._input_consumer = AudioInputTrackConsumer(
            self._runtime,
            voice_session_id=self.voice_session_id,
            max_queue_frames=audio_input_queue_max_frames,
        )
        self._runtime_events_task: asyncio.Task[None] | None = None
        self._disconnect_task: asyncio.Task[None] | None = None
        self._runtime_started = False
        self._audio_input_started = False
        self._runtime_closed = False
        self._initial_reply_sent = False
        self._initial_reply_preload_task: asyncio.Task[None] | None = None
        self._closed = False
        self._start_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._current_generation: int | None = None
        self._generation_metrics: dict[int, AssistantGenerationMetrics] = {}
        self._generation_finalize_tasks: dict[int, asyncio.Task[None]] = {}
        self._playback_drain_tasks: dict[
            tuple[str | None, int | None], asyncio.Task[None]
        ] = {}
        self._first_enqueued_responses: set[str] = set()
        self._last_playback_log_at = monotonic()
        self._assistant_output_sample_rate_hz = runtime_output_rate_hz
        self._assistant_output_channels = AUDIO_OUTPUT_CHANNELS
        self._assistant_output_bytes_per_sample = AUDIO_OUTPUT_SAMPLE_SIZE_BITS // 8
        configuration = RTCConfiguration(
            iceServers=[
                RTCIceServer(
                    urls=list(server.urls),
                    username=server.username,
                    credential=server.credential,
                )
                for server in ice_servers
            ]
        )
        self._pc = RTCPeerConnection(configuration=configuration)
        self._remote_audio_track = None
        self._register_handlers()

    @property
    def connection_state(self) -> str:
        return self._normalize_state(self._pc.connectionState)

    async def apply_offer(self, offer_sdp: str, offer_type: str) -> str:
        started_at = monotonic()
        self._schedule_initial_reply_audio_preload()
        self._pc.addTrack(self._output_track)
        await self._pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type=offer_type))
        answer = await self._pc.createAnswer()
        await self._pc.setLocalDescription(answer)
        await self._wait_for_ice_gathering_complete()
        assert self._pc.localDescription is not None
        logger.info(
            "webrtc_offer_answer_ready voice_session_id=%s latency_ms=%s ice_gathering_state=%s",
            self.voice_session_id,
            round((monotonic() - started_at) * 1000),
            self._pc.iceGatheringState,
        )
        return self._pc.localDescription.sdp

    def _schedule_initial_reply_audio_preload(self) -> None:
        if self._initial_reply_preload_task is not None:
            return
        initial_reply_text = (self._session_state.initial_reply_text or "").strip()
        if (
            not initial_reply_text
            or self._session_state.initial_reply_status == "sent"
            or self._session_state.initial_question_id != self._session_state.current_question_id
        ):
            return
        prepare = getattr(self._runtime, "prepare_initial_reply", None)
        if not callable(prepare):
            return
        question_text = _extract_initial_question_text(initial_reply_text)
        spoken_text = f"{INITIAL_VOICE_GREETING}{question_text}"
        self._initial_reply_preload_task = asyncio.create_task(prepare(spoken_text))
        self._initial_reply_preload_task.add_done_callback(
            self._handle_initial_reply_preload_done
        )

    def _handle_initial_reply_preload_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            logger.warning(
                "voice_initial_reply_audio_preload_failed voice_session_id=%s error_type=%s",
                self.voice_session_id,
                error.__class__.__name__,
            )

    async def close(
        self,
        *,
        reason: str = "external_close",
        source: str = "voice_peer_connection.close",
    ) -> None:
        await self._close(reason=reason, source=source)

    def _register_handlers(self) -> None:
        @self._pc.on("track")
        async def on_track(track) -> None:
            if track.kind != "audio":
                return
            self._remote_audio_track = track
            if self._runtime_started and self._audio_input_started:
                await self._input_consumer.start(track)
                return
            await self._start_runtime_if_ready()

        @self._pc.on("datachannel")
        def on_datachannel(channel: RTCDataChannel) -> None:
            self._data_channel.bind(channel)

            def send_current_state() -> None:
                self._data_channel.send_connection_state(
                    voice_session_id=self.voice_session_id,
                    state=self.connection_state,
                )
                if self._runtime_started:
                    self._data_channel.send_runtime_ready(context=self._event_context())
                self._data_channel.send_interview_state(context=self._event_context())

            @channel.on("open")
            def on_open() -> None:
                send_current_state()

            @channel.on("message")
            def on_message(message: str | bytes) -> None:
                if not isinstance(message, str):
                    return
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    return
                event_type = payload.get("type")
                if event_type == "assistant_playback_started" and hasattr(self._runtime, "notify_assistant_playback_started"):
                    asyncio.create_task(
                        getattr(self._runtime, "notify_assistant_playback_started")(
                            response_id=payload.get("responseId"),
                            generation=payload.get("generation"),
                        )
                    )
                elif event_type == "assistant_playback_drained" and hasattr(self._runtime, "notify_assistant_playback_drained"):
                    response_id = payload.get("responseId")
                    generation = payload.get("generation")
                    self._cancel_playback_drain_watchdog(
                        response_id=response_id,
                        generation=generation,
                    )
                    logger.info(
                        "assistant_playback_drain_received voice_session_id=%s response_id=%s generation=%s",
                        self.voice_session_id,
                        response_id,
                        generation,
                    )
                    asyncio.create_task(
                        getattr(self._runtime, "notify_assistant_playback_drained")(
                            response_id=response_id,
                            generation=generation,
                        )
                    )

            send_current_state()

        @self._pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            state = self.connection_state
            logger.info(
                "voice_peer_connection_state_changed voice_session_id=%s state=%s ice_state=%s signaling_state=%s",
                self.voice_session_id,
                state,
                self._pc.iceConnectionState,
                self._pc.signalingState,
            )
            self._data_channel.send_connection_state(
                voice_session_id=self.voice_session_id,
                state=state,
            )
            await self._voice_session_service.create_connection_event(
                self.voice_session_id,
                event_type="peer_connection_state_changed",
                connection_status=state,
                detail={},
            )
            if state == "connected":
                if self._disconnect_task is not None:
                    self._disconnect_task.cancel()
                    self._disconnect_task = None
                await self._start_runtime_if_ready()
            elif state == "disconnected":
                if self._disconnect_task is None or self._disconnect_task.done():
                    self._disconnect_task = asyncio.create_task(self._disconnect_after_grace())
            elif state in {"failed", "closed"}:
                await self._close(reason=f"peer_{state}", source="connection_state_change")

    async def _start_runtime_if_ready(self) -> None:
        async with self._start_lock:
            if self._runtime_started or self._closed:
                return
            if self.connection_state != "connected":
                return
            if self._remote_audio_track is None:
                return
            try:
                await self._runtime.start(
                    VoiceRuntimeContext(
                        voice_session_id=self._session_state.voice_session_id,
                        record_id=self._session_state.record_id,
                        provider=self._session_state.provider,
                        interview_locale=self._session_state.interview_locale,
                    )
                )
                self._runtime_events_task = asyncio.create_task(self._consume_runtime_events())
                if hasattr(self._runtime, "start_audio_input"):
                    await getattr(self._runtime, "start_audio_input")()
                await self._input_consumer.start(self._remote_audio_track)
                self._runtime_started = True
                self._audio_input_started = True
                await self._send_initial_reply_if_pending()
            except Exception as exc:
                logger.exception(
                    "voice_runtime_start_failed voice_session_id=%s error_type=%s",
                    self.voice_session_id,
                    exc.__class__.__name__,
                )
                self._data_channel.send_event(
                    RuntimeError(message=f"runtime_start_failed:{exc.__class__.__name__}"),
                    context=self._event_context(),
                )
                await self._voice_session_service.create_connection_event(
                    self.voice_session_id,
                    event_type="runtime_start_failed",
                    connection_status=self.connection_state,
                    detail={"errorType": exc.__class__.__name__, "message": str(exc)},
                )
                await self._close(reason="runtime_start_failed", source="runtime_start")
                raise

    async def _send_initial_reply_if_pending(self) -> None:
        if self._initial_reply_sent:
            return
        initial_reply_text = (self._session_state.initial_reply_text or "").strip()
        if not initial_reply_text:
            return
        if self._session_state.initial_reply_status == "sent":
            return
        if self._session_state.initial_question_id != self._session_state.current_question_id:
            await self._voice_session_service.create_connection_event(
                self.voice_session_id,
                event_type="initial_reply_skipped",
                connection_status=self.connection_state,
                detail={"reason": "question_mismatch"},
            )
            return
        logger.info(
            "voice_initial_reply_claim_started voice_session_id=%s initial_question_id=%s initial_reply_status=%s",
            self.voice_session_id,
            self._session_state.initial_question_id,
            self._session_state.initial_reply_status,
        )
        claim_task = asyncio.create_task(
            self._voice_session_service.claim_initial_reply(self.voice_session_id)
        )
        self._initial_reply_sent = True
        try:
            greeting_text = _extract_initial_greeting_text(initial_reply_text)
            if hasattr(self._runtime, "start_initial_reply"):
                await getattr(self._runtime, "start_initial_reply")(
                    reply_text=greeting_text,
                    question_id=None,
                )
                logger.info(
                    "voice_initial_control_text_sent voice_session_id=%s initial_question_id=%s initial_reply_status=%s",
                    self.voice_session_id,
                    None,
                    "sending",
                )
            else:
                await self._runtime.send_reply(
                    AssistantReply(
                        turn_id=f"initial-{self.voice_session_id}",
                        response_id=f"initial-response-{self.voice_session_id}",
                        text=greeting_text,
                        action="ask_initial_question",
                        question_id=None,
                        state_version=self._session_state.state_version,
                    )
                )
            claim = await claim_task
            if not claim.claimed:
                await self._voice_session_service.create_connection_event(
                    self.voice_session_id,
                    event_type="initial_reply_skipped",
                    connection_status=self.connection_state,
                    detail={"reason": claim.reason or "not_claimed"},
                )
                return
            logger.info(
                "voice_initial_reply_claimed voice_session_id=%s initial_question_id=%s initial_reply_status=%s",
                self.voice_session_id,
                claim.initial_question_id,
                "sending",
            )
            question_text = _extract_initial_question_text(claim.initial_reply_text)
            if question_text:
                if hasattr(self._runtime, "queue_initial_followup_reply"):
                    await getattr(self._runtime, "queue_initial_followup_reply")(
                        reply_text=question_text,
                        question_id=claim.initial_question_id,
                    )
                else:
                    await self._runtime.send_reply(
                        AssistantReply(
                            turn_id=f"initial-{self.voice_session_id}-question",
                            response_id=f"initial-response-{self.voice_session_id}-question",
                            text=question_text,
                            action="ask_initial_question",
                            question_id=claim.initial_question_id,
                            state_version=self._session_state.state_version,
                        )
                    )
                    await self._voice_session_service.mark_initial_reply_sent(self.voice_session_id)
        except Exception:
            self._initial_reply_sent = False
            claim_task.cancel()
            await self._voice_session_service.mark_initial_reply_failed(self.voice_session_id)
            raise
        self._data_channel.send_initial_reply_sent(
            context=self._event_context(),
            response_id=f"initial-response-{self.voice_session_id}",
        )
        await self._voice_session_service.create_connection_event(
            self.voice_session_id,
            event_type="initial_reply_sent",
            connection_status=self.connection_state,
            detail={
                "questionId": self._session_state.current_question_id,
                "stateVersion": self._session_state.state_version,
            },
        )

    async def _consume_runtime_events(self) -> None:
        try:
            async for event in self._runtime.events():
                await self._handle_runtime_event(event)
                if isinstance(event, RuntimeClosed):
                    self._runtime_closed = True
                    break
            if not self._closed:
                await self._close(reason="runtime_closed", source="runtime_event_consumer")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._voice_session_service.create_connection_event(
                self.voice_session_id,
                event_type="runtime_event_consumer_failed",
                connection_status=self.connection_state,
                detail={"message": str(exc)},
            )
            await self._close(reason="runtime_event_consumer_failed", source="runtime_event_consumer")

    async def _handle_runtime_event(self, event: VoiceRuntimeEvent) -> None:
        event_to_send = event
        if isinstance(event, RuntimeReady):
            self._data_channel.send_runtime_ready(context=self._event_context())
            self._data_channel.send_interview_state(context=self._event_context())
            logger.info(
                "voice_turn_listening_started voice_session_id=%s current_question_id=%s state_version=%s peer_connection_state=%s runtime_open=%s audio_input_open=%s browser_track_state=%s",
                self.voice_session_id,
                self._session_state.current_question_id,
                self._session_state.state_version,
                self.connection_state,
                self._runtime_started and not self._runtime_closed,
                self._audio_input_started and self._input_consumer.is_running,
                self._input_consumer.source_track_state,
            )
            return

        if isinstance(event, AssistantSpeechStarted) and event.generation is not None:
            self._current_generation = event.generation
            metrics = self._generation_metrics.setdefault(
                event.generation,
                AssistantGenerationMetrics(
                    generation=event.generation,
                    response_id=event.response_id,
                ),
            )
            if event.response_id is not None:
                metrics.response_id = event.response_id
            logger.info(
                "voice_assistant_speech_started voice_session_id=%s current_question_id=%s state_version=%s response_id=%s generation=%s peer_connection_state=%s runtime_open=%s audio_input_open=%s browser_track_state=%s",
                self.voice_session_id,
                self._session_state.current_question_id,
                self._session_state.state_version,
                event.response_id,
                event.generation,
                self.connection_state,
                self._runtime_started and not self._runtime_closed,
                self._audio_input_started and self._input_consumer.is_running,
                self._input_consumer.source_track_state,
            )
        elif isinstance(event, AssistantAudioChunk):
            if self._closed:
                return
            if self._current_generation is None:
                self._current_generation = event.generation
            completion_matches = bool(event.completion_id)
            try:
                enqueued = await self._playback_buffer.enqueue(
                    event,
                    current_generation=self._current_generation,
                )
            except PlaybackBufferCapacityExceeded:
                logger.exception(
                    "voice_playback_buffer_capacity_exceeded voice_session_id=%s response_id=%s completion_id=%s generation=%s retention_max_ms=%s",
                    self.voice_session_id,
                    event.response_id,
                    event.completion_id,
                    event.generation,
                    self._playback_buffer.retention_max_ms,
                )
                self._data_channel.send_event(
                    RuntimeError(message="assistant_playback_buffer_capacity_exceeded"),
                    context=self._event_context(),
                )
                await self._close(reason="assistant_playback_buffer_capacity_exceeded", source="runtime_event_handler")
                return
            stats = await self._playback_buffer.snapshot_stats()
            metrics = self._generation_metrics.setdefault(
                event.generation,
                AssistantGenerationMetrics(
                    generation=event.generation,
                    response_id=event.response_id,
                    completion_id=event.completion_id,
                ),
            )
            metrics.received_pcm_bytes += len(event.pcm)
            metrics.completion_id = event.completion_id
            metrics.playback_buffer_peak_depth_ms = max(
                metrics.playback_buffer_peak_depth_ms,
                stats.playback_buffer_peak_depth_ms,
            )
            should_log_playback = (
                not enqueued
                or (monotonic() - self._last_playback_log_at) >= 1.0
            )
            if should_log_playback:
                self._last_playback_log_at = monotonic()
                logger.info(
                    "voice_playback_audio_chunk voice_session_id=%s response_id=%s completion_id=%s event_generation=%s current_generation=%s event_authorized=%s completion_matches=%s bytes=%s enqueued=%s enqueue_result=%s drop_count=%s playback_buffer_enqueue_count=%s playback_buffer_depth_ms=%s playback_buffer_peak_depth_ms=%s playback_old_audio_dropped_ms=%s playback_underrun_count=%s playback_silence_inserted_ms=%s assistant_pcm_received_bytes=%s assistant_pcm_received_duration_ms=%s assistant_generation_id=%s assistant_interrupted=%s",
                    self.voice_session_id,
                    event.response_id,
                    event.completion_id,
                    event.generation,
                    self._current_generation,
                    event.authorized,
                    completion_matches,
                    len(event.pcm),
                    enqueued,
                    "enqueued" if enqueued else "dropped",
                    self._playback_buffer.drop_count,
                    self._playback_buffer.enqueue_count,
                    round(stats.playback_buffer_depth_ms, 1),
                    round(stats.playback_buffer_peak_depth_ms, 1),
                    round(stats.playback_old_audio_dropped_ms, 1),
                    stats.playback_underrun_count,
                    round(stats.playback_silence_inserted_ms, 1),
                    metrics.received_pcm_bytes,
                    round(self._assistant_audio_duration_ms(metrics.received_pcm_bytes), 1),
                    event.generation,
                    metrics.assistant_interrupted,
                )
            if event.response_id.startswith("initial-response-"):
                logger.info(
                    "voice_initial_playback_enqueued voice_session_id=%s response_id=%s generation=%s completion_id=%s enqueued=%s playback_buffer_enqueue_count=%s",
                    self.voice_session_id,
                    event.response_id,
                    event.generation,
                    event.completion_id,
                    enqueued,
                    self._playback_buffer.enqueue_count,
                )
            if enqueued and event.response_id not in self._first_enqueued_responses:
                self._first_enqueued_responses.add(event.response_id)
                logger.info(
                    "voice_playback_first_enqueue voice_session_id=%s response_id=%s generation=%s completion_id=%s monotonic_ms=%s peer_connection_state=%s",
                    self.voice_session_id,
                    event.response_id,
                    event.generation,
                    event.completion_id,
                    int(monotonic() * 1000),
                    self.connection_state,
                )
            return
        elif isinstance(event, AssistantInterrupted):
            self._cancel_playback_drain_watchdog(
                response_id=event.response_id,
                generation=event.generation,
            )
            if event.generation is not None:
                self._current_generation = event.generation
                metrics = self._generation_metrics.setdefault(
                    event.generation,
                    AssistantGenerationMetrics(
                        generation=event.generation,
                        response_id=event.response_id,
                    ),
                )
                metrics.assistant_interrupted = True
            await self._output_track.prepare_interrupt()
            dropped_ms = await self._playback_buffer.clear(count_as_dropped=True)
            if event.generation is not None:
                metrics = self._generation_metrics.setdefault(
                    event.generation,
                    AssistantGenerationMetrics(
                        generation=event.generation,
                        response_id=event.response_id,
                    ),
                )
                metrics.playback_old_audio_dropped_ms += dropped_ms
                await self._finalize_generation_metrics(event.generation)
            logger.info(
                "assistant_audio_buffer_cleared voice_session_id=%s response_id=%s generation=%s reason=%s",
                self.voice_session_id,
                event.response_id,
                event.generation,
                "assistant_interrupted",
            )
        elif isinstance(event, AssistantSpeechEnded):
            await self._refresh_session_state()
            if event.generation is not None:
                metrics = self._generation_metrics.setdefault(
                    event.generation,
                    AssistantGenerationMetrics(
                        generation=event.generation,
                        response_id=event.response_id,
                    ),
                )
                metrics.speech_ended_received = True
                metrics.response_id = event.response_id
                self._schedule_generation_finalize_after_drain(event.generation)
                event_to_send = replace(
                    event,
                    audio_duration_ms=self._assistant_audio_duration_ms(
                        metrics.received_pcm_bytes
                    ),
                )
            logger.info(
                "voice_assistant_speech_ended voice_session_id=%s current_question_id=%s state_version=%s response_id=%s generation=%s interview_status=%s peer_connection_state=%s runtime_open=%s audio_input_open=%s browser_track_state=%s",
                self.voice_session_id,
                self._session_state.current_question_id,
                self._session_state.state_version,
                event.response_id,
                event.generation,
                self._session_state.interview_status,
                self.connection_state,
                self._runtime_started and not self._runtime_closed,
                self._audio_input_started and self._input_consumer.is_running,
                self._input_consumer.source_track_state,
            )
        elif isinstance(event, RuntimeError):
            await self._voice_session_service.create_connection_event(
                self.voice_session_id,
                event_type="runtime_error",
                connection_status=self.connection_state,
                detail={"message": event.message},
            )

        self._data_channel.send_event(event_to_send, context=self._event_context())

        if isinstance(event_to_send, AssistantSpeechEnded):
            self._data_channel.send_interview_state(context=self._event_context())
            if self._session_state.interview_status == "completed":
                await self._wait_for_playback_drain()
                self._data_channel.send_event(
                    InputStateChanged(
                        input_state="INTERVIEW_COMPLETED",
                        generation=event_to_send.generation,
                    ),
                    context=self._event_context(),
                )
                self._data_channel.send_interview_completed(context=self._event_context())
                await self._close(reason="interview_completed", source="assistant_speech_ended")
            else:
                self._schedule_playback_drain_watchdog(event_to_send)
                logger.info(
                    "voice_next_turn_ready voice_session_id=%s current_question_id=%s state_version=%s peer_connection_state=%s runtime_open=%s audio_input_open=%s browser_track_state=%s",
                    self.voice_session_id,
                    self._session_state.current_question_id,
                    self._session_state.state_version,
                    self.connection_state,
                    self._runtime_started and not self._runtime_closed,
                    self._audio_input_started and self._input_consumer.is_running,
                    self._input_consumer.source_track_state,
                )
        elif isinstance(event, RuntimeError) and event.fatal:
            await self._close(reason="runtime_error", source="runtime_event_handler")

    async def _refresh_session_state(self) -> None:
        try:
            self._session_state = await self._voice_session_service.get_session(
                self.voice_session_id,
                bearer_token=self._bearer_token,
            )
        except Exception:
            return

    async def _wait_for_playback_drain(self) -> None:
        deadline = asyncio.get_running_loop().time() + self._playback_drain_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if await self._playback_buffer.is_empty():
                return
            await asyncio.sleep(0.05)
        logger.info(
            "assistant_audio_buffer_cleared voice_session_id=%s response_id=%s generation=%s reason=%s",
            self.voice_session_id,
            None,
            self._current_generation,
            "playback_drain_timeout",
        )
        await self._playback_buffer.clear()
        await self._voice_session_service.create_connection_event(
            self.voice_session_id,
            event_type="playback_drain_timeout",
            connection_status=self.connection_state,
            detail={"timeoutSeconds": self._playback_drain_timeout_seconds},
        )

    def _schedule_playback_drain_watchdog(self, event: AssistantSpeechEnded) -> None:
        if event.response_id is None or event.generation is None or self._closed:
            return
        key = (event.response_id, event.generation)
        self._cancel_playback_drain_watchdog(
            response_id=event.response_id,
            generation=event.generation,
        )
        delay_seconds = self._playback_drain_delay_seconds(event.audio_duration_ms)
        task = asyncio.create_task(
            self._playback_drain_watchdog(
                response_id=event.response_id,
                generation=event.generation,
                delay_seconds=delay_seconds,
            )
        )
        self._playback_drain_tasks[key] = task
        task.add_done_callback(
            lambda completed: self._playback_drain_task_done(key, completed)
        )
        logger.info(
            "assistant_playback_drain_watchdog_scheduled voice_session_id=%s response_id=%s generation=%s delay_seconds=%s",
            self.voice_session_id,
            event.response_id,
            event.generation,
            round(delay_seconds, 2),
        )

    def _cancel_playback_drain_watchdog(
        self,
        *,
        response_id: str | None,
        generation: int | None,
    ) -> None:
        key = (response_id, generation)
        task = self._playback_drain_tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()

    async def _playback_drain_watchdog(
        self,
        *,
        response_id: str,
        generation: int,
        delay_seconds: float,
    ) -> None:
        try:
            await asyncio.sleep(delay_seconds)
        except asyncio.CancelledError:
            return
        if self._closed or not hasattr(self._runtime, "notify_assistant_playback_drained"):
            return
        logger.warning(
            "assistant_playback_drain_fallback voice_session_id=%s response_id=%s generation=%s delay_seconds=%s",
            self.voice_session_id,
            response_id,
            generation,
            round(delay_seconds, 2),
        )
        try:
            await getattr(self._runtime, "notify_assistant_playback_drained")(
                response_id=response_id,
                generation=generation,
            )
        except Exception:  # noqa: BLE001 - recovery must not close the peer
            logger.exception(
                "assistant_playback_drain_fallback_failed voice_session_id=%s response_id=%s generation=%s",
                self.voice_session_id,
                response_id,
                generation,
            )
        try:
            await self._voice_session_service.create_connection_event(
                self.voice_session_id,
                event_type="playback_drain_fallback",
                connection_status=self.connection_state,
                detail={
                    "responseId": response_id,
                    "generation": generation,
                    "delaySeconds": delay_seconds,
                },
            )
        except Exception:  # noqa: BLE001 - telemetry must not block recovery
            logger.exception(
                "assistant_playback_drain_fallback_event_failed voice_session_id=%s response_id=%s generation=%s",
                self.voice_session_id,
                response_id,
                generation,
            )

    def _playback_drain_task_done(
        self,
        key: tuple[str | None, int | None],
        task: asyncio.Task[None],
    ) -> None:
        if self._playback_drain_tasks.get(key) is task:
            self._playback_drain_tasks.pop(key, None)
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            logger.warning(
                "assistant_playback_drain_watchdog_failed voice_session_id=%s error_type=%s",
                self.voice_session_id,
                exception.__class__.__name__,
            )

    def _playback_drain_delay_seconds(self, audio_duration_ms: int | float | None) -> float:
        configured = max(0.5, self._playback_drain_timeout_seconds)
        if audio_duration_ms is None or audio_duration_ms <= 0:
            return configured
        # The configured timeout is the fallback for an unknown duration. A
        # known duration must never be capped by it; otherwise a long reply
        # can reopen the input gate while its audio is still playing.
        return max(1.5, (float(audio_duration_ms) / 1000.0) + 1.0)

    def _handle_output_frame_emitted(self, metrics: OutputFrameMetrics) -> None:
        generation = self._current_generation
        if generation is None:
            return
        generation_metrics = self._generation_metrics.setdefault(
            generation,
            AssistantGenerationMetrics(generation=generation),
        )
        if (
            generation_metrics.received_pcm_bytes == 0
            and generation_metrics.output_frame_count == 0
            and metrics.consumed_source_bytes == 0
        ):
            return
        generation_metrics.output_frame_count += 1
        if generation_metrics.first_output_emitted_at is None:
            generation_metrics.first_output_emitted_at = metrics.emitted_at
        generation_metrics.last_output_emitted_at = metrics.emitted_at

    def _schedule_generation_finalize_after_drain(self, generation: int) -> None:
        existing = self._generation_finalize_tasks.get(generation)
        if existing is not None and not existing.done():
            return
        self._generation_finalize_tasks[generation] = asyncio.create_task(
            self._finalize_generation_after_drain(generation)
        )

    async def _finalize_generation_after_drain(self, generation: int) -> None:
        deadline = asyncio.get_running_loop().time() + self._playback_drain_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if await self._playback_buffer.is_empty():
                await self._finalize_generation_metrics(generation)
                return
            await asyncio.sleep(0.05)

    async def _finalize_generation_metrics(self, generation: int) -> None:
        metrics = self._generation_metrics.pop(generation, None)
        if metrics is None:
            return
        task = self._generation_finalize_tasks.pop(generation, None)
        current_task = asyncio.current_task()
        if task is not None and task is not current_task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        output_duration_ms = metrics.output_frame_count * 20.0
        received_duration_ms = self._assistant_audio_duration_ms(metrics.received_pcm_bytes)
        elapsed_ms = output_duration_ms
        if (
            metrics.first_output_emitted_at is not None
            and metrics.last_output_emitted_at is not None
        ):
            elapsed_ms = (
                (metrics.last_output_emitted_at - metrics.first_output_emitted_at) * 1000.0
            ) + 20.0
        logger.info(
            "assistant_generation_audio_summary voice_session_id=%s assistant_generation_id=%s response_id=%s completion_id=%s assistant_pcm_received_bytes=%s assistant_pcm_received_duration_ms=%s playback_old_audio_dropped_ms=%s playback_buffer_peak_depth_ms=%s output_frame_count=%s output_duration_ms=%s playback_elapsed_ms=%s assistant_interrupted=%s",
            self.voice_session_id,
            generation,
            metrics.response_id,
            metrics.completion_id,
            metrics.received_pcm_bytes,
            round(received_duration_ms, 1),
            round(metrics.playback_old_audio_dropped_ms, 1),
            round(metrics.playback_buffer_peak_depth_ms, 1),
            metrics.output_frame_count,
            round(output_duration_ms, 1),
            round(elapsed_ms, 1),
            metrics.assistant_interrupted,
        )

    def _assistant_audio_duration_ms(self, pcm_bytes: int) -> float:
        return (
            pcm_bytes
            / (
                self._assistant_output_sample_rate_hz
                * self._assistant_output_channels
                * self._assistant_output_bytes_per_sample
            )
        ) * 1000.0

    async def _disconnect_after_grace(self) -> None:
        await asyncio.sleep(self._peer_disconnected_grace_seconds)
        if self.connection_state == "disconnected":
            await self._close(reason="peer_disconnected", source="disconnect_grace_timeout")

    async def _wait_for_ice_gathering_complete(self) -> None:
        started_at = monotonic()
        deadline = asyncio.get_running_loop().time() + self._ice_gathering_timeout_seconds
        while self._pc.iceGatheringState != "complete":
            if asyncio.get_running_loop().time() >= deadline:
                logger.info(
                    "webrtc_ice_gathering_soft_timeout voice_session_id=%s timeout_seconds=%s elapsed_ms=%s ice_gathering_state=%s",
                    self.voice_session_id,
                    self._ice_gathering_timeout_seconds,
                    round((monotonic() - started_at) * 1000),
                    self._pc.iceGatheringState,
                )
                return
            await asyncio.sleep(0.05)
        logger.info(
            "webrtc_ice_gathering_complete voice_session_id=%s elapsed_ms=%s",
            self.voice_session_id,
            round((monotonic() - started_at) * 1000),
        )

    async def _close(self, *, reason: str, source: str) -> None:
        async with self._close_lock:
            if self._closed:
                return
            close_context = self._close_log_context(reason=reason, source=source)
            logger.info("voice_session_close_requested %s", close_context)
            logger.info("peer_connection_close_requested %s", close_context)
            logger.info(
                "voice_session_close_started %s",
                close_context,
            )
            logger.info(
                "voice_peer_cleanup_started voice_session_id=%s reason=%s source=%s current_question_id=%s state_version=%s peer_connection_state=%s runtime_open=%s audio_input_open=%s browser_track_state=%s",
                self.voice_session_id,
                reason,
                source,
                self._session_state.current_question_id,
                self._session_state.state_version,
                self.connection_state,
                self._runtime_started and not self._runtime_closed,
                self._audio_input_started and self._input_consumer.is_running,
                self._input_consumer.source_track_state,
            )
            self._closed = True
            current_task = asyncio.current_task()

            initial_reply_preload_task = self._initial_reply_preload_task
            self._initial_reply_preload_task = None
            if (
                initial_reply_preload_task is not None
                and initial_reply_preload_task is not current_task
                and not initial_reply_preload_task.done()
            ):
                initial_reply_preload_task.cancel()
                await asyncio.gather(initial_reply_preload_task, return_exceptions=True)

            if self._initial_reply_sent:
                await self._voice_session_service.mark_initial_reply_failed(self.voice_session_id)
            playback_drain_tasks = tuple(self._playback_drain_tasks.values())
            self._playback_drain_tasks.clear()
            for task in playback_drain_tasks:
                if task is not current_task and not task.done():
                    task.cancel()
            if playback_drain_tasks:
                await asyncio.gather(*playback_drain_tasks, return_exceptions=True)
            await self._playback_buffer.clear()
            await self._input_consumer.stop()
            if self._disconnect_task is not None:
                self._disconnect_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._disconnect_task
                self._disconnect_task = None
            if self._runtime_events_task is not None:
                if self._runtime_events_task is not current_task:
                    self._runtime_events_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self._runtime_events_task
                self._runtime_events_task = None
            if (
                (self._runtime_started or initial_reply_preload_task is not None)
                and not self._runtime_closed
            ):
                logger.info("runtime_close_requested %s", self._close_log_context(reason=reason, source=source))
                with suppress(Exception):
                    await self._runtime.close()
            self._runtime_closed = True
            with suppress(Exception):
                self._data_channel.close()
            await self._pc.close()
            await self._voice_session_service.create_connection_event(
                self.voice_session_id,
                event_type=reason,
                connection_status="closed",
                detail={"state": self.connection_state, "reason": reason, "source": source},
            )
            await self._on_closed(self.voice_session_id)
            logger.info("voice_session_close_completed %s", self._close_log_context(reason=reason, source=source))

    def _close_log_context(self, *, reason: str, source: str) -> str:
        return (
            f"voice_session_id={self.voice_session_id} reason={reason} source={source} "
            f"input_state={getattr(self._runtime, 'input_state', 'unknown')} "
            f"pending_evaluation_count={getattr(self._runtime, 'pending_evaluation_count', 0)} "
            f"pending_reply_count={getattr(self._runtime, 'pending_reply_count', 0)} "
            f"connection_state={self.connection_state} "
            f"ice_connection_state={self._pc.iceConnectionState} "
            f"runtime_started={self._runtime_started} runtime_closed={self._runtime_closed} "
            f"monotonic_timestamp_ms={round(monotonic() * 1000)}"
        )

    def _event_context(self) -> VoiceEventContext:
        return VoiceEventContext(
            voice_session_id=self.voice_session_id,
            question_id=self._session_state.current_question_id,
            state_version=self._session_state.state_version,
            interview_status=self._session_state.interview_status,
        )

    @staticmethod
    def _normalize_state(state: str) -> str:
        if state in {"new", "connecting", "connected", "disconnected", "failed", "closed"}:
            return state
        return "new"


def _extract_initial_greeting_text(initial_reply_text: str) -> str:
    return INITIAL_VOICE_GREETING


def _extract_initial_question_text(initial_reply_text: str | None) -> str:
    text = str(initial_reply_text or "").strip()
    if not text:
        return ""
    if text.startswith(INITIAL_VOICE_GREETING):
        return text[len(INITIAL_VOICE_GREETING) :].strip()
    return text
