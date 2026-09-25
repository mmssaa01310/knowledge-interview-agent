"""
Role:
    Transcribe Streaming + Polly方式の実運用RealtimeVoiceRuntime。

Summary:
    16kHz PCMをVADとTranscribeへ渡してturnを確定し、Interview APIが
    条件付き更新した正式応答をPollyで逐次音声化する。全音声出力は
    AudioOutputCoordinatorで優先制御し、generationで古い出力を破棄する。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from enum import StrEnum
from hashlib import sha256
from time import monotonic, time
from typing import Any, Literal
from uuid import uuid4

from ai_interviewer_voice.clients.interview_api import VoiceSessionSnapshot
from ai_interviewer_voice.runtimes.transcribe_polly.config import (
    TranscribePollyRuntimeConfig,
)
from ai_interviewer_voice.runtimes.transcribe_polly.output_coordinator import (
    AudioOutputCoordinator,
    AudioOutputRequest,
    OutputKind,
)
from ai_interviewer_voice.runtimes.transcribe_polly.polly_synthesizer import (
    PollySynthesisError,
    PollySynthesisPort,
    PollySynthesizer,
)
from ai_interviewer_voice.runtimes.transcribe_polly.text_chunker import (
    PollyTextChunker,
    PollyTextChunkerConfig,
    split_text_for_polly,
)
from ai_interviewer_voice.runtimes.transcribe_polly.transcribe_stream import (
    AwsTranscribeStreamingPort,
    TranscribeResult,
    TranscribeStreamingPort,
)
from ai_interviewer_voice.runtimes.transcribe_polly.vad import PcmEnergyVad
from ai_interviewer_voice.schemas.audio import AudioFrame
from ai_interviewer_voice.schemas.events import (
    AssistantAudioChunk,
    AssistantBackchannel,
    AssistantInterrupted,
    AssistantResponsePreparing,
    AssistantSpeechEnded,
    AssistantSpeechStarted,
    AssistantTranscriptFinal,
    InputStateChanged,
    RuntimeClosed,
    RuntimeError,
    RuntimeReady,
    RuntimeReconnecting,
    UserSpeechEnded,
    UserSpeechStarted,
    UserTranscriptFinal,
    UserTranscriptPartial,
    VoiceRuntimeEvent,
)
from ai_interviewer_voice.schemas.sessions import AssistantReply, VoiceRuntimeContext
from ai_interviewer_voice.services.interview_bridge import (
    InterviewApiError,
    InterviewBridge,
    InterviewBridgeResult,
    InterviewBridgeStreamCompleted,
    InterviewBridgeStreamStarted,
    InterviewBridgeTextDelta,
)
from ai_interviewer_voice.startup_timing import log_voice_startup_stage

logger = logging.getLogger(__name__)


def _transcript_digest(text: str | None) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    return sha256(value.encode("utf-8")).hexdigest()[:16]

LISTEN_ACK_TEXT = "なるほど。"
PROCESSING_ACK_TEXT = "回答を確認しています。"
LONG_PROCESSING_TEXT = "確認に少し時間がかかっています。"
class InterviewAction(StrEnum):
    ASK_FOLLOW_UP = "ASK_FOLLOW_UP"
    ASK_CONFIRMATION = "ASK_CONFIRMATION"
    NEXT_QUESTION = "NEXT_QUESTION"
    END_INTERVIEW = "END_INTERVIEW"
    RETRY = "RETRY"

    @classmethod
    def from_api(cls, value: str) -> InterviewAction:
        normalized = value.strip().upper()
        aliases = {
            "ASK_CONFIGURED_FIELD": cls.NEXT_QUESTION,
            "ASK_STRUCTURED": cls.NEXT_QUESTION,
            "ASK_INITIAL_QUESTION": cls.NEXT_QUESTION,
            "FINISH": cls.END_INTERVIEW,
        }
        if normalized in aliases:
            return aliases[normalized]
        return cls(normalized)


class AssistantResponseState(StrEnum):
    PLANNED = "PLANNED"
    SYNTHESIZING = "SYNTHESIZING"
    PLAYING = "PLAYING"
    PLAYED = "PLAYED"
    INTERRUPTED = "INTERRUPTED"


def _normalize_ending(text: str) -> str:
    return text.strip().rstrip("。！？!?、, ")


def _transcript_fingerprint(text: str) -> str:
    return "".join(text.casefold().split())


def _merge_transcript_parts(left: str, right: str) -> str:
    """Concatenate STT segments without duplicating ResultId revisions."""

    left = left.strip()
    right = right.strip()
    if not left:
        return right
    if not right or left == right:
        return left
    if right.startswith(left):
        return right
    if left.endswith(right):
        return left
    max_overlap = min(len(left), len(right))
    for overlap in range(max_overlap, 0, -1):
        if left[-overlap:] == right[:overlap]:
            return f"{left}{right[overlap:]}".strip()
    return f"{left}{right}".strip()


class TranscribePollyRuntime:
    def __init__(
        self,
        *,
        config: TranscribePollyRuntimeConfig | None = None,
        interview_bridge: InterviewBridge | None = None,
        transcribe: TranscribeStreamingPort | None = None,
        polly: PollySynthesisPort | None = None,
    ) -> None:
        self._config = config or TranscribePollyRuntimeConfig()
        self._interview_bridge = interview_bridge
        self._transcribe = transcribe or AwsTranscribeStreamingPort(self._config)
        self._polly = polly or PollySynthesizer(self._config)
        self._vad = PcmEnergyVad(
            sample_rate_hz=self._config.input_sample_rate_hz,
            rms_threshold=self._config.vad_rms_threshold,
        )
        self._events: asyncio.Queue[VoiceRuntimeEvent] = asyncio.Queue()
        self._context: VoiceRuntimeContext | None = None
        self._started = False
        self._closed = False
        self._input_available = True
        self._input_gate_reason: str | None = None
        self._silence_keepalive_logged = False
        self._listening_ready_at: float | None = None
        self._first_input_after_resume = False
        self._transcribe_unavailable = False
        self._generation = 0
        self._audio_sequence = 0
        self._current_question_id: str | None = None
        self._state_version = 0
        self._interview_status = "active"
        self._audio_batch = bytearray()
        self._turn_active = False
        self._speech_active = False
        self._assistant_speaking = False
        self._voiced_duration_ms = 0
        self._barge_in_voiced_ms = 0
        self._silence_started_at: float | None = None
        self._last_user_speech_end_at_ms: int | None = None
        self._last_transcribe_result_at: float | None = None
        self._last_transcribe_final_at_ms: int | None = None
        self._has_final_transcript = False
        self._stable_text = ""
        self._latest_partial_text = ""
        self._latest_stt_confidence: float | None = None
        self._final_segments: dict[str, str] = {}
        self._final_segment_results: dict[str, TranscribeResult] = {}
        self._partial_segments: dict[str, TranscribeResult] = {}
        self._segment_sequence: dict[str, int] = {}
        self._next_segment_sequence = 0
        self._final_result_ids: list[str] = []
        self._final_segment_fingerprints: set[str] = set()
        self._anonymous_final_index = 0
        self._soft_endpoint_triggered = False
        self._soft_endpoint_at: float | None = None
        self._settle_started_at: float | None = None
        self._turn_started_at: float | None = None
        self._hard_timeout_count = 0
        self._speculative_id: str | None = None
        self._speculative_transcript = ""
        self._speculative_task: asyncio.Task[None] | None = None
        self._listen_ack_played = False
        self._processing_ack_played = False
        self._long_notice_played = False
        self._last_backchannel_at: float | None = None
        self._endpoint_task: asyncio.Task[None] | None = None
        self._processing_task: asyncio.Task[None] | None = None
        self._state_sync_task: asyncio.Task[object] | None = None
        self._active_client_turn_id: str | None = None
        self._active_expected_state_version: int | None = None
        self._reply_task: asyncio.Task[None] | None = None
        self._reply_lock = asyncio.Lock()
        self._notice_tasks: set[asyncio.Task[None]] = set()
        self._background_tasks: set[asyncio.Task[object]] = set()
        self._prepared_polly_tasks: dict[str, asyncio.Task[bytes]] = {}
        self._formal_response_id: str | None = None
        self._formal_generation: int | None = None
        self._formal_playback_drained_event: asyncio.Event | None = None
        self._assistant_response_states: dict[str, AssistantResponseState] = {}
        self._interrupt_emitted_for: set[str] = set()
        self._backchannel_metadata: dict[str, tuple[OutputKind, str]] = {}
        self._response_turn_ids: dict[str, str] = {}
        self._turn_timings: dict[str, dict[str, Any]] = {}
        self._pipeline_timings: dict[str, dict[str, Any]] = {}
        self._startup_first_tts_chunk_logged = False
        self._pending_listening_state: Literal[
            "ANSWER_LISTENING",
            "CONFIRMATION_LISTENING",
            "INTERVIEW_COMPLETED",
        ] | None = None
        self._output = AudioOutputCoordinator(
            sample_rate_hz=self._config.polly_sample_rate_hz,
            emit_frame=self._emit_output_frame,
            on_started=self._on_output_started,
            on_completed=self._on_output_completed,
            on_interrupted=self._on_output_interrupted,
            is_generation_current=lambda generation: (
                not self._closed and generation == self._generation
            ),
        )

    @property
    def provider_name(self) -> str:
        return self._config.provider_name

    @property
    def output_sample_rate_hz(self) -> int:
        return self._config.polly_sample_rate_hz

    async def start(self, context: VoiceRuntimeContext) -> None:
        if self._started and not self._closed:
            return
        started_at = monotonic()
        self._context = context
        self._startup_first_tts_chunk_logged = False
        self._closed = False
        self._input_available = True
        self._input_gate_reason = None
        self._silence_keepalive_logged = False
        self._transcribe_unavailable = False
        self._startup_stage(
            "runtime_session_initialization_started",
            state_load_parallel=self._interview_bridge is not None,
        )
        self._startup_stage("external_service_connect_started", service="transcribe")
        transcribe_start_task = asyncio.create_task(self._transcribe.start(
            on_result=self._on_transcribe_result,
            on_reconnecting=self._on_transcribe_reconnecting,
            on_fatal_error=self._on_transcribe_fatal_error,
        ))
        state_load_task = (
            asyncio.create_task(
                self._interview_bridge.load_voice_session(context.voice_session_id)
            )
            if self._interview_bridge is not None
            else None
        )
        try:
            await transcribe_start_task
            self._startup_stage("external_service_ready", service="transcribe")
            if state_load_task is not None:
                snapshot = await state_load_task
                self._apply_voice_session_snapshot(snapshot)
        except BaseException:
            if not transcribe_start_task.done():
                transcribe_start_task.cancel()
            if state_load_task is not None and not state_load_task.done():
                state_load_task.cancel()
            cleanup_tasks = [transcribe_start_task]
            if state_load_task is not None:
                cleanup_tasks.append(state_load_task)
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)
            raise
        self._started = True
        self._endpoint_task = asyncio.create_task(self._endpoint_loop())
        if self._config.backchannel_enabled:
            self._spawn_background(
                self._polly.warm(
                    (
                        self._config.listen_ack_text,
                        self._config.processing_ack_text,
                        self._config.long_processing_text,
                    )
                )
            )
        logger.info(
            "transcribe_polly_runtime_ready voice_session_id=%s startup_ms=%s",
            context.voice_session_id,
            round((monotonic() - started_at) * 1000),
        )
        self._startup_stage(
            "runtime_ready_emitted",
            startup_ms=round((monotonic() - started_at) * 1000, 1),
        )
        await self._emit(RuntimeReady())
        await self._emit_input_state("ANSWER_LISTENING")

    async def prepare_initial_reply(self, reply_text: str) -> None:
        """Start Polly synthesis before the first reply is sent to the output queue.

        The WebRTC offer is processed before the remote track starts the runtime.  The
        initial greeting and question are already known at that point, so synthesizing
        the first output chunks in parallel with ICE negotiation removes the initial
        Polly request latency from the user's visible ``preparing_audio`` state.
        """
        await self._prepare_polly_chunks(
            reply_text,
            log_event="voice_initial_reply_audio_preload_scheduled",
        )

    async def _prepare_polly_chunks(self, text: str, *, log_event: str) -> None:
        """Schedule cached-or-Polly chunks without waiting for synthesis."""

        if self._closed:
            return
        chunks = split_text_for_polly(
            text,
            PollyTextChunkerConfig(
                first_min_chars=self._config.first_chunk_min_chars,
                first_max_chars=self._config.first_chunk_max_chars,
                following_min_chars=self._config.following_chunk_min_chars,
                following_max_chars=self._config.following_chunk_max_chars,
            ),
        )
        if not chunks:
            return

        prepared_count = 0
        parallel = max(1, self._config.polly_max_parallel_requests)
        for chunk in chunks[:parallel]:
            if chunk in self._prepared_polly_tasks:
                continue
            if await self._polly.get_cached(chunk) is not None:
                continue
            task = asyncio.create_task(self._polly.synthesize(chunk))
            task.add_done_callback(self._consume_prepared_task_exception)
            self._prepared_polly_tasks[chunk] = task
            prepared_count += 1
        if prepared_count:
            logger.info(
                "%s voice_session_id=%s prepared_chunks=%s",
                log_event,
                self._context.voice_session_id if self._context is not None else None,
                prepared_count,
            )

    async def push_audio(self, frame: AudioFrame) -> None:
        if (
            not self._started
            or self._closed
            or self._transcribe_unavailable
            or self._interview_status in {"completed", "stopped"}
        ):
            return
        if (
            frame.sample_rate_hz != self._config.input_sample_rate_hz
            or frame.channels != 1
        ):
            raise ValueError("TranscribePollyRuntime requires 16kHz mono PCM")
        monitor_barge_in = (
            self._assistant_speaking
            and self._interview_status not in {"completed", "stopped"}
        )
        if self._input_available or monitor_barge_in:
            vad = self._vad.inspect(frame.pcm)
            await self._handle_vad(vad.voiced, vad.duration_ms)

        # Keep the AWS streaming request alive while the application input
        # gate is closed, without forwarding microphone audio or allowing VAD
        # and transcript callbacks to start another user turn.  _handle_vad
        # can close the gate while processing this very frame, so check the
        # gate again after it returns.
        gated = not self._input_available
        self._audio_batch.extend(bytes(len(frame.pcm)) if gated else frame.pcm)
        target_bytes = int(
            self._config.input_sample_rate_hz
            * 2
            * (self._config.transcribe_chunk_ms / 1000)
        )
        while len(self._audio_batch) >= target_bytes:
            chunk = bytes(self._audio_batch[:target_bytes])
            del self._audio_batch[:target_bytes]
            await self._transcribe.send_audio(chunk)
            if gated and not self._silence_keepalive_logged:
                self._silence_keepalive_logged = True
                logger.info(
                    "transcribe_polly_silence_keepalive_started voice_session_id=%s generation=%s "
                    "chunk_ms=%s gate_reason=%s",
                    self._context.voice_session_id if self._context else None,
                    self._generation,
                    self._config.transcribe_chunk_ms,
                    self._input_gate_reason,
                )
            if not gated and self._first_input_after_resume:
                self._first_input_after_resume = False
                logger.info(
                    "voice_handoff event=first_user_audio_chunk voice_session_id=%s generation=%s "
                    "monotonic_ms=%.3f listening_ready_to_chunk_ms=%.3f chunk_ms=%s",
                    self._context.voice_session_id if self._context else None,
                    self._generation, monotonic() * 1000,
                    (monotonic() - (self._listening_ready_at or monotonic())) * 1000,
                    self._config.transcribe_chunk_ms,
                )

    async def send_reply(self, reply: AssistantReply) -> None:
        if not self._started or self._closed:
            return
        async with self._reply_lock:
            if not await self._wait_for_formal_reply_to_finish():
                return
            if self._closed:
                return
            task = asyncio.create_task(self._play_formal_reply(reply))
            self._reply_task = task
            try:
                await task
            finally:
                if self._reply_task is task:
                    self._reply_task = None

    async def interrupt(self) -> None:
        if self._closed:
            return
        await self._interrupt_output(reason="explicit_interrupt")

    async def notify_assistant_playback_started(
        self,
        *,
        response_id: str | None,
        generation: int | None,
    ) -> None:
        if (
            response_id == self._formal_response_id
            and generation == self._formal_generation
        ):
            self._assistant_speaking = True

    async def notify_assistant_playback_drained(
        self,
        *,
        response_id: str | None,
        generation: int | None,
    ) -> None:
        if (
            response_id != self._formal_response_id
            or generation != self._formal_generation
        ):
            logger.debug(
                "assistant_playback_drain_ignored response_id=%s generation=%s expected_response_id=%s expected_generation=%s",
                response_id,
                generation,
                self._formal_response_id,
                self._formal_generation,
            )
            return
        logger.info(
            "assistant_playback_drained response_id=%s generation=%s next_input_state=%s",
            response_id,
            generation,
            self._pending_listening_state,
        )
        self._assistant_speaking = False
        next_state = self._pending_listening_state
        self._formal_response_id = None
        self._formal_generation = None
        self._release_formal_playback_waiter()
        self._pending_listening_state = None
        if response_id is not None:
            self._assistant_response_states[response_id] = AssistantResponseState.PLAYED
        if next_state is not None:
            await self._resume_input_after_formal_reply(
                next_state,
                reason="assistant_playback_drained",
            )

    async def _wait_for_formal_reply_to_finish(self) -> bool:
        """Keep formal replies serialized until the browser has drained audio."""
        while self._formal_response_id is not None:
            response_id = self._formal_response_id
            generation = self._formal_generation
            drained_event = self._formal_playback_drained_event
            if drained_event is None:
                return True
            logger.info(
                "assistant_reply_waiting_for_playback_drain response_id=%s generation=%s",
                response_id,
                generation,
            )
            await drained_event.wait()
            if self._closed:
                return False
            if self._assistant_response_states.get(response_id) is AssistantResponseState.INTERRUPTED:
                logger.info(
                    "assistant_reply_dropped_after_previous_interrupt response_id=%s generation=%s",
                    response_id,
                    generation,
                )
                return False
        return True

    def events(self) -> AsyncIterator[VoiceRuntimeEvent]:
        return self._event_generator()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._input_available = False
        self._release_formal_playback_waiter()
        await self._output.close()
        prepared_tasks = tuple(self._prepared_polly_tasks.values())
        self._prepared_polly_tasks.clear()
        tasks = [
            self._endpoint_task,
            self._processing_task,
            self._state_sync_task,
            self._reply_task,
            self._speculative_task,
            *self._notice_tasks,
            *self._background_tasks,
            *prepared_tasks,
        ]
        current = asyncio.current_task()
        for task in tasks:
            if task is not None and task is not current and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in tasks if task is not None and task is not current),
            return_exceptions=True,
        )
        self._notice_tasks.clear()
        self._response_turn_ids.clear()
        self._turn_timings.clear()
        self._pipeline_timings.clear()
        self._background_tasks.clear()
        await self._transcribe.close()
        await self._emit(RuntimeClosed())

    @staticmethod
    def _consume_prepared_task_exception(task: asyncio.Task[bytes]) -> None:
        if task.cancelled():
            return
        task.exception()

    async def _event_generator(self) -> AsyncIterator[VoiceRuntimeEvent]:
        while True:
            event = await self._events.get()
            yield event
            if isinstance(event, RuntimeClosed):
                break

    async def _emit(self, event: VoiceRuntimeEvent) -> None:
        await self._events.put(event)

    async def _emit_input_state(
        self,
        state: Literal[
            "ANSWER_LISTENING",
            "ANSWER_PROCESSING",
            "CONFIRMATION_LISTENING",
            "INTERVIEW_COMPLETED",
            "INPUT_UNAVAILABLE",
        ],
    ) -> None:
        await self._emit(InputStateChanged(input_state=state, generation=self._generation))

    def _close_input_gate(self, *, reason: str) -> None:
        if self._input_available:
            self._silence_keepalive_logged = False
        self._input_available = False
        self._input_gate_reason = reason
        self._turn_active = False
        self._speech_active = False
        self._silence_started_at = None
        self._audio_batch.clear()
        self._barge_in_voiced_ms = 0
        logger.info(
            "transcribe_polly_input_gate_closed voice_session_id=%s generation=%s reason=%s",
            self._context.voice_session_id if self._context is not None else None,
            self._generation,
            reason,
        )

    async def _handle_vad(self, voiced: bool, duration_ms: int) -> None:
        now = monotonic()
        if voiced:
            if (
                (self._assistant_speaking or self._formal_response_id is not None)
                and not self._turn_active
            ):
                self._barge_in_voiced_ms += duration_ms
                if self._barge_in_voiced_ms < self._config.barge_in_voice_ms:
                    return
                initial_ms = self._barge_in_voiced_ms
                self._barge_in_voiced_ms = 0
                await self._interrupt_output(reason="barge_in")
                await self._cancel_pending_turn()
                await self._start_user_turn(
                    increment_generation=False,
                    initial_voiced_ms=initial_ms,
                    allow_when_gated=True,
                )
                return
            if not self._turn_active:
                if self._active_client_turn_id is not None:
                    await self._interrupt_output(
                        reason="new_speech_during_pending_turn"
                    )
                    await self._cancel_pending_turn()
                    await self._start_user_turn(
                        increment_generation=False,
                        initial_voiced_ms=duration_ms,
                    )
                else:
                    await self._start_user_turn(
                        increment_generation=True,
                        initial_voiced_ms=duration_ms,
                    )
            else:
                self._voiced_duration_ms += duration_ms
            if self._soft_endpoint_triggered:
                if self._settle_started_at is not None:
                    self._log_transcript_event(
                        "transcript_settle_reset",
                        detail="speech_resumed",
                    )
                await self._discard_speculative_preview(reason="speech_resumed")
                self._soft_endpoint_triggered = False
                self._soft_endpoint_at = None
                self._settle_started_at = None
                self._log_transcript_event("speech_resumed")
            self._speech_active = True
            self._silence_started_at = None
            return
        self._barge_in_voiced_ms = 0
        if self._turn_active and self._speech_active:
            self._speech_active = False
            self._silence_started_at = now
            self._last_user_speech_end_at_ms = int(time() * 1000)

    async def _start_user_turn(
        self,
        *,
        increment_generation: bool,
        initial_voiced_ms: int,
        allow_when_gated: bool = False,
    ) -> None:
        if self._interview_status in {"completed", "stopped"}:
            return
        if not self._input_available:
            if not allow_when_gated:
                return
            # A VAD-confirmed barge-in may interrupt the fixed processing
            # acknowledgement while the previous turn is still pending. Do
            # not reopen the gate for ordinary gated audio.
            self._input_available = True
            self._input_gate_reason = None
            self._silence_keepalive_logged = False
            self._listening_ready_at = monotonic()
            self._first_input_after_resume = True
            self._audio_batch.clear()
            await self._emit_input_state("ANSWER_LISTENING")
        if increment_generation:
            self._generation += 1
        await self._cancel_notice_tasks()
        self._turn_active = True
        self._speech_active = True
        self._reset_transcription_turn_state()
        self._turn_started_at = monotonic()
        self._voiced_duration_ms = initial_voiced_ms
        await self._emit(UserSpeechStarted())
        self._log_transcript_event("speech_start")

    def _reset_transcription_turn_state(self) -> None:
        """Discard transcript/timing data so it cannot leak into another turn."""
        self._last_transcribe_result_at = None
        self._last_transcribe_final_at_ms = None
        self._last_user_speech_end_at_ms = None
        self._has_final_transcript = False
        self._stable_text = ""
        self._latest_partial_text = ""
        self._latest_stt_confidence = None
        self._final_segments.clear()
        self._final_segment_results.clear()
        self._partial_segments.clear()
        self._segment_sequence.clear()
        self._next_segment_sequence = 0
        self._final_result_ids.clear()
        self._final_segment_fingerprints.clear()
        self._listen_ack_played = False
        self._processing_ack_played = False
        self._long_notice_played = False
        self._soft_endpoint_triggered = False
        self._soft_endpoint_at = None
        self._settle_started_at = None
        self._turn_started_at = monotonic()
        self._speculative_id = None
        self._speculative_transcript = ""
        if self._speculative_task is not None and not self._speculative_task.done():
            self._speculative_task.cancel()
        self._speculative_task = None

    async def _on_transcribe_result(self, result: TranscribeResult) -> None:
        if self._closed or not self._turn_active or not self._input_available:
            return
        now = monotonic()
        if self._soft_endpoint_triggered and self._settle_started_at is not None:
            self._log_transcript_event(
                "transcript_settle_reset",
                result=result,
                detail="transcript_updated",
            )
            self._settle_started_at = None
        self._last_transcribe_result_at = now
        key = self._transcript_result_key(result)
        self._remember_segment_sequence(key)
        if result.is_partial:
            self._partial_segments[key] = result
            latest_partial = self._latest_partial_result()
            self._latest_partial_text = latest_partial.text if latest_partial else ""
            self._stable_text = latest_partial.stable_text if latest_partial else ""
            if result.confidence is not None:
                self._latest_stt_confidence = result.confidence
            self._log_transcript_event("transcript_partial_updated", result=result)
            if result.stable_text:
                self._log_transcript_event(
                    "transcript_stable_prefix",
                    result=result,
                    stable_text=result.stable_text,
                )
        else:
            self._has_final_transcript = True
            self._last_transcribe_final_at_ms = int(time() * 1000)
            if result.confidence is not None:
                self._latest_stt_confidence = result.confidence
            fingerprint = _transcript_fingerprint(result.text)
            if not result.result_id and fingerprint in self._final_segment_fingerprints:
                logger.debug("transcribe_final_duplicate_ignored result_id=%s", result.result_id)
                return
            previous_partial = self._partial_segments.pop(key, None)
            if previous_partial is not None and (
                result.start_time is None or result.end_time is None
            ):
                result = replace(
                    result,
                    start_time=(
                        result.start_time
                        if result.start_time is not None
                        else previous_partial.start_time
                    ),
                    end_time=(
                        result.end_time
                        if result.end_time is not None
                        else previous_partial.end_time
                    ),
                )
            self._final_segments[key] = result.text
            self._final_segment_results[key] = result
            if result.result_id and result.result_id not in self._final_result_ids:
                self._final_result_ids.append(result.result_id)
            self._final_segment_fingerprints.add(fingerprint)
            latest_partial = self._latest_partial_result()
            self._stable_text = (
                latest_partial.stable_text
                if latest_partial is not None
                else self._combined_final_text()
            )
            self._latest_partial_text = latest_partial.text if latest_partial else ""
            self._log_transcript_event("transcribe_segment_final", result=result)
            # Keep the historical event name for existing log consumers while
            # exposing the turn-level name used by robustness telemetry.
            self._log_transcript_event("transcript_segment_final", result=result)
            logger.info(
                "voice_turn_transcribe_final transcribe_final=%s result_id=%s",
                self._last_transcribe_final_at_ms,
                result.result_id,
            )
        visible = self._combined_transcript()
        if visible:
            self._log_transcript_event(
                "pending_transcript_updated",
                result=result,
                transcript=visible,
            )
            await self._emit(UserTranscriptPartial(text=visible))

    async def _on_transcribe_reconnecting(self, attempt: int) -> None:
        logger.warning("transcribe_reconnecting attempt=%s", attempt)
        await self._emit(RuntimeReconnecting())

    async def _on_transcribe_fatal_error(self, exc: Exception) -> None:
        if self._closed or self._transcribe_unavailable:
            return
        self._transcribe_unavailable = True
        self._input_available = False
        self._turn_active = False
        self._speech_active = False
        self._audio_batch.clear()
        if self._endpoint_task is not None and self._endpoint_task is not asyncio.current_task():
            self._endpoint_task.cancel()
        await self._cancel_notice_tasks()
        await self._emit_input_state("INPUT_UNAVAILABLE")
        await self._emit(
            RuntimeError(
                message="transcribe_stream_failed",
                detail={"errorType": exc.__class__.__name__, "fallback": "text"},
                fatal=False,
            )
        )

    async def _start_speculative_llm(self, transcript: str) -> None:
        """Start a non-committing interpretation while STT keeps listening."""

        if self._interview_bridge is None or self._context is None:
            return
        start_method = getattr(self._interview_bridge, "start_speculative_turn", None)
        if not callable(start_method):
            return
        if self._speculative_task is not None and not self._speculative_task.done():
            return
        speculative_id = f"speculative-{uuid4().hex[:20]}"
        self._speculative_id = speculative_id
        self._speculative_transcript = transcript
        self._log_transcript_event(
            "speculative_llm_started",
            result_id=speculative_id,
            transcript=transcript,
        )

        async def run() -> None:
            try:
                await start_method(
                    voice_session_id=self._context.voice_session_id,
                    transcript=transcript,
                    answer_to_question_id=self._current_question_id,
                    expected_state_version=self._state_version,
                    speculative_id=speculative_id,
                    stt_confidence=self._latest_stt_confidence,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - speculative work is optional
                logger.warning(
                    "speculative_llm_failed voice_session_id=%s turn_id=%s error_type=%s",
                    self._context.voice_session_id if self._context else None,
                    speculative_id,
                    exc.__class__.__name__,
                )

        self._speculative_task = asyncio.create_task(run())

    async def _discard_speculative_preview(self, *, reason: str) -> None:
        task = self._speculative_task
        speculative_id = self._speculative_id
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._speculative_task = None
        if speculative_id:
            self._log_transcript_event(
                "speculative_llm_discarded",
                result_id=speculative_id,
                transcript=self._speculative_transcript,
                detail=reason,
            )
        self._speculative_id = None
        self._speculative_transcript = ""

    def _log_transcript_event(
        self,
        event: str,
        *,
        result: TranscribeResult | None = None,
        result_id: str | None = None,
        stable_text: str | None = None,
        transcript: str | None = None,
        detail: str | None = None,
    ) -> None:
        session_id = self._context.voice_session_id if self._context else None
        turn_id = self._speculative_id or f"pending-{self._generation}"
        if result is not None:
            result_id = result.result_id or result_id
            stable_text = result.stable_text if stable_text is None else stable_text
        if transcript is None:
            transcript = self._combined_transcript()
        elapsed_ms = (
            round((monotonic() - self._turn_started_at) * 1000, 1)
            if self._turn_started_at is not None
            else 0.0
        )
        logger.info(
            "transcribe_turn_event event=%s session_id=%s turn_id=%s result_id=%s "
            "is_partial=%s stable_text_len=%s stable_text_sha256=%s "
            "transcript_len=%s transcript_sha256=%s elapsed_ms=%s detail=%s",
            event,
            session_id,
            turn_id,
            result_id,
            result.is_partial if result is not None else None,
            len(stable_text or ""),
            _transcript_digest(stable_text),
            len(transcript or ""),
            _transcript_digest(transcript),
            elapsed_ms,
            detail or "",
        )

    def _log_turn_telemetry(
        self,
        event: str,
        client_turn_id: str | None,
        **details: object,
    ) -> None:
        """Emit bounded turn telemetry without sending it through Interview API."""

        session_id = self._context.voice_session_id if self._context else None
        logger.info(
            "transcribe_turn_telemetry event=%s session_id=%s client_turn_id=%s details=%s",
            event,
            session_id,
            client_turn_id,
            details,
        )

    @staticmethod
    def _turn_latency_metrics(timing: dict[str, Any]) -> dict[str, int | None]:
        commit_at = timing.get("turn_finalize_at_ms")
        final_at = timing.get("transcript_final_at_ms")
        ack_started_at = timing.get("ack_started_at_ms")
        ack_ended_at = timing.get("ack_ended_at_ms")
        main_started_at = timing.get("main_started_at_ms")

        def delta(end: object, start: object) -> int | None:
            if not isinstance(end, (int, float)) or not isinstance(start, (int, float)):
                return None
            return max(0, round(end - start))

        return {
            "segmentFinalToTurnCommitMs": delta(commit_at, final_at),
            "turnCommitToAckMs": delta(ack_started_at, commit_at),
            "turnCommitToMainResponseMs": delta(main_started_at, commit_at),
            "ackToMainResponseMs": delta(main_started_at, ack_ended_at),
        }

    async def _endpoint_loop(self) -> None:
        # Keep the endpoint watcher alive while the input gate is closed.  The
        # gate is reopened only after formal assistant playback has drained;
        # recreating this task at every turn would race with the first frame of
        # the next answer.
        while not self._closed:
            await asyncio.sleep(0.025)
            if not self._turn_active or self._silence_started_at is None:
                continue
            silence_ms = int((monotonic() - self._silence_started_at) * 1000)
            stable = self._combined_stable_text()
            final = self._combined_final_text()
            normalized_stable = _normalize_ending(stable)
            if silence_ms >= self._config.listen_ack_silence_ms:
                await self._maybe_play_listen_ack(normalized_stable)
            if not self._soft_endpoint_triggered and silence_ms >= self._soft_endpoint_ms():
                await self._trigger_soft_endpoint()
            if not self._soft_endpoint_triggered:
                continue
            if self._has_final_transcript and _normalize_ending(final):
                if self._settle_started_at is None:
                    self._settle_started_at = max(
                        self._soft_endpoint_at or monotonic(),
                        self._last_transcribe_result_at or monotonic(),
                    )
                    self._log_transcript_event(
                        "transcript_settle_started",
                        stable_text=self._combined_stable_text(),
                    )
                # A Transcribe ``IsPartial=false`` result closes only an STT
                # segment. It is not a conversation-turn boundary. Keep the
                # pending transcript open until the hard endpoint so a later
                # segment (including one after a natural pause) can be merged
                # into the same user turn.
            if (
                self._soft_endpoint_at is not None
                and int((monotonic() - self._soft_endpoint_at) * 1000)
                >= self._hard_timeout_after_soft_ms()
            ):
                if self._has_final_transcript and _normalize_ending(final):
                    await self._finalize_user_turn(final)
                else:
                    await self._recover_from_missing_final_transcript()

    async def _maybe_play_listen_ack(self, stable: str) -> None:
        if not self._config.backchannel_enabled:
            return
        if self._listen_ack_played or self._assistant_speaking:
            return
        # Once Transcribe has produced a final segment, the user has finished
        # an answer rather than pausing mid-sentence.  Do not insert a
        # listen-time backchannel immediately before the formal response.
        if self._has_final_transcript:
            return
        if self._voiced_duration_ms < self._config.listen_ack_min_speech_ms:
            return
        if len(stable) < self._config.listen_ack_min_stable_chars:
            return
        if self._last_backchannel_at is not None:
            elapsed_ms = int((monotonic() - self._last_backchannel_at) * 1000)
            if elapsed_ms < self._config.backchannel_cooldown_ms:
                return
        pcm = await self._polly.get_cached(self._config.listen_ack_text)
        if not pcm:
            return
        accepted = await self._play_backchannel(
            kind=OutputKind.LISTEN_ACK,
            text=self._config.listen_ack_text,
            pcm=pcm,
        )
        if accepted:
            self._listen_ack_played = True

    async def _trigger_soft_endpoint(self) -> None:
        if not self._turn_active or self._soft_endpoint_triggered:
            return
        self._soft_endpoint_triggered = True
        self._soft_endpoint_at = monotonic()
        self._settle_started_at = None
        transcript = self._combined_stable_text().strip()
        self._log_transcript_event(
            "soft_endpoint",
            stable_text=self._combined_stable_text(),
            transcript=transcript,
        )
        self._log_transcript_event(
            "soft_endpoint_triggered",
            stable_text=self._combined_stable_text(),
            transcript=transcript,
        )
        if transcript:
            await self._start_speculative_llm(transcript)

    async def _finalize_user_turn(self, transcript: str, *, forced: bool = False) -> None:
        normalized = transcript.strip()
        if not normalized or not self._turn_active:
            return
        # A final answer closes the audio input immediately.  Frames that are
        # already queued by the browser are discarded here, so trailing noise
        # cannot start a second Transcribe turn while the API evaluates this
        # answer or while Polly plays the next question.
        self._close_input_gate(reason="final_transcript")
        turn_finalize_at_ms = int(time() * 1000)
        client_turn_id = self._final_client_turn_id()
        speculative_id = self._speculative_id
        self._turn_timings[client_turn_id] = {
            "client_turn_id": client_turn_id,
            "transcript_final_at_ms": self._last_transcribe_final_at_ms,
            "turn_finalize_at_ms": turn_finalize_at_ms,
            "user_speech_end_at_ms": self._last_user_speech_end_at_ms,
        }
        self._log_turn_telemetry(
            "user_turn_committed",
            client_turn_id,
            segmentFinalToTurnCommitMs=(
                max(0, turn_finalize_at_ms - self._last_transcribe_final_at_ms)
                if self._last_transcribe_final_at_ms is not None
                else None
            ),
            forced=forced,
        )
        self._log_transcript_event(
            "transcript_finalized",
            result_id=(self._final_result_ids[-1] if self._final_result_ids else client_turn_id),
            transcript=normalized,
            detail="hard_timeout" if forced else "hard_endpoint",
        )
        logger.info(
            "voice_turn_timing_event event=turn_finalize user_speech_end=%s transcribe_final=%s turn_finalize=%s forced=%s",
            self._last_user_speech_end_at_ms,
            self._last_transcribe_final_at_ms,
            turn_finalize_at_ms,
            forced,
        )
        await self._emit(UserSpeechEnded())
        await self._emit_input_state("ANSWER_PROCESSING")
        self._processing_task = asyncio.create_task(
            self._process_interview_turn(
                transcript=normalized,
                generation=self._generation,
                expected_state_version=self._state_version,
                client_turn_id=client_turn_id,
                transcript_final_at_ms=self._last_transcribe_final_at_ms,
                turn_finalize_at_ms=turn_finalize_at_ms,
                stt_confidence=self._latest_stt_confidence,
                speculative_id=speculative_id,
            )
        )

    async def _recover_from_missing_final_transcript(self) -> None:
        """Force the best available transcript after the hard safety timeout."""

        if not self._turn_active:
            return
        self._hard_timeout_count += 1
        best_transcript = self._combined_transcript().strip()
        self._log_transcript_event(
            "hard_timeout_triggered",
            result_id=(self._final_result_ids[-1] if self._final_result_ids else None),
            stable_text=self._combined_stable_text(),
            transcript=best_transcript,
            detail=f"count={self._hard_timeout_count}",
        )
        logger.warning(
            "hard_timeout_triggered voice_session_id=%s silence_ms=%s partial_chars=%s stable_chars=%s hard_timeout_count=%s",
            self._context.voice_session_id if self._context else None,
            int((monotonic() - (self._silence_started_at or monotonic())) * 1000),
            len(self._latest_partial_text),
            len(self._combined_stable_text()),
            self._hard_timeout_count,
        )
        if best_transcript:
            await self._finalize_user_turn(best_transcript, forced=True)
            return
        self._close_input_gate(reason="transcribe_final_timeout")
        await self._emit(UserSpeechEnded())
        await self._emit(
            RuntimeError(
                message="transcribe_final_timeout",
                detail={
                    "fallback": "retry_input",
                    "runtime": "transcribe_polly",
                    "answerEvaluation": "NOT_HEARD",
                    "nextAction": "ASK_REPEAT",
                    "recognitionEvidence": ["no_final_or_partial_transcript"],
                },
                fatal=False,
            )
        )
        self._reset_transcription_turn_state()
        await self._resume_input_after_formal_reply(
            "ANSWER_LISTENING",
            reason="transcribe_final_timeout",
        )

    async def _process_interview_turn(
        self,
        *,
        transcript: str,
        generation: int,
        expected_state_version: int,
        client_turn_id: str,
        transcript_final_at_ms: int | None = None,
        turn_finalize_at_ms: int | None = None,
        stt_confidence: float | None = None,
        speculative_id: str | None = None,
        _conflict_retry_attempt: int = 0,
        _emit_final_transcript: bool = True,
        _schedule_processing_notices: bool = True,
    ) -> None:
        if self._interview_bridge is None or self._context is None:
            await self._emit(RuntimeError(message="interview_bridge_unavailable", fatal=True))
            return
        if self._state_sync_task is not None:
            await asyncio.gather(self._state_sync_task, return_exceptions=True)
            expected_state_version = self._state_version
        answer_question_id = self._current_question_id
        self._active_client_turn_id = client_turn_id
        self._active_expected_state_version = expected_state_version
        logger.info(
            "voice_turn_api_request voice_session_id=%s client_turn_id=%s "
            "answer_to_question_id=%s expected_state_version=%s",
            self._context.voice_session_id,
            client_turn_id,
            answer_question_id,
            expected_state_version,
        )
        if self._config.backchannel_enabled and _schedule_processing_notices:
            self._schedule_notice(
                delay_ms=self._config.processing_ack_delay_ms,
                kind=OutputKind.PROCESSING_ACK,
                text=self._config.processing_ack_text,
                generation=generation,
            )
            self._schedule_notice(
                delay_ms=self._config.long_processing_notice_ms,
                kind=OutputKind.LONG_PROCESSING,
                text=self._config.long_processing_text,
                generation=generation,
            )
        result_received = False
        processing_may_continue = False
        api_started_at = monotonic()
        stream_queue: asyncio.Queue[tuple[str, Any]] | None = None
        stream_play_task: asyncio.Task[None] | None = None
        streamed_response_id: str | None = None
        result: InterviewBridgeResult | None = None
        cancel_stream_play_task = False
        # Show the final STT text immediately.  Its content and target are
        # already known at this point; waiting for answer evaluation made the
        # UI appear frozen for the entire API/LLM pipeline.
        if _emit_final_transcript:
            await self._emit(
                UserTranscriptFinal(
                    text=transcript,
                    turn_type="ANSWER",
                    question_id=answer_question_id,
                    client_turn_id=client_turn_id,
                )
            )
        try:
            process_stream = getattr(self._interview_bridge, "process_turn_stream", None)
            if callable(process_stream):
                stream_kwargs = {
                    "voice_session_id": self._context.voice_session_id,
                    "transcript": transcript,
                    "answer_to_question_id": answer_question_id,
                    "turn_type": "ANSWER",
                    "expected_state_version": expected_state_version,
                    "client_turn_id": client_turn_id,
                    "stt_confidence": stt_confidence,
                }
                if speculative_id:
                    stream_kwargs["speculative_id"] = speculative_id
                async for stream_event in process_stream(**stream_kwargs):
                    if isinstance(stream_event, InterviewBridgeStreamStarted):
                        if stream_queue is not None:
                            continue
                        stream_queue = asyncio.Queue()
                        streamed_response_id = stream_event.response_id
                        self._response_turn_ids[streamed_response_id] = client_turn_id
                        self._pipeline_timings[streamed_response_id] = {
                            "turn_id": client_turn_id,
                            "client_turn_id": client_turn_id,
                            "transcript_final_at_ms": transcript_final_at_ms
                            or int(time() * 1000),
                            "turn_finalize_at_ms": turn_finalize_at_ms
                            or int(time() * 1000),
                            "user_speech_end_at_ms": self._last_user_speech_end_at_ms,
                            "api_started_at": api_started_at,
                            "question_stream_started_at_ms": int(time() * 1000),
                        }
                        stream_play_task = asyncio.create_task(
                            self._play_streaming_formal_reply(
                                response_id=streamed_response_id,
                                generation=generation,
                                stream_queue=stream_queue,
                            )
                        )
                    elif isinstance(stream_event, InterviewBridgeTextDelta):
                        if stream_queue is not None:
                            await stream_queue.put(("delta", stream_event.text))
                    elif isinstance(stream_event, InterviewBridgeStreamCompleted):
                        result = stream_event.result
                        result_received = True
                        if stream_queue is not None:
                            timing = self._pipeline_timings.get(result.response_id)
                            if timing is not None:
                                timing.update(result.latency_metrics or {})
                                timing["turn_id"] = result.turn_id
                                timing["api_completed_at_ms"] = int(time() * 1000)
                                timing["api_completed_at"] = monotonic()
                            await stream_queue.put(("complete", result))
                        break
                if not result_received:
                    raise InterviewApiError(
                        "turn_stream_incomplete",
                        "interview turn stream ended before completion",
                        category="NETWORK_ERROR",
                    )
            else:
                process_kwargs = {
                    "voice_session_id": self._context.voice_session_id,
                    "transcript": transcript,
                    "answer_to_question_id": answer_question_id,
                    "turn_type": "ANSWER",
                    "expected_state_version": expected_state_version,
                    "client_turn_id": client_turn_id,
                    "stt_confidence": stt_confidence,
                }
                if speculative_id:
                    process_kwargs["speculative_id"] = speculative_id
                result = await self._interview_bridge.process_turn(**process_kwargs)
                result_received = True
        except asyncio.CancelledError:
            cancel_stream_play_task = True
            raise
        except InterviewApiError as exc:
            if exc.code in {
                "turn_state_conflict",
                "turn_duplicate_conflict",
                "turn_question_conflict",
            }:
                retry_allowed = (
                    exc.code == "turn_state_conflict"
                    and _conflict_retry_attempt == 0
                    and stream_queue is None
                )
                retry = await self._resync_after_turn_conflict(
                    client_turn_id=client_turn_id,
                    conflict_code=exc.code,
                    expected_state_version=expected_state_version,
                    answer_question_id=answer_question_id,
                    retry_allowed=retry_allowed,
                )
                if retry and not self._closed:
                    # Reuse the durable source identity and transcript. The
                    # backend only rebases a still-RECEIVED turn whose original
                    # question remains current; committed turns stay idempotent.
                    await self._process_interview_turn(
                        transcript=transcript,
                        generation=generation,
                        expected_state_version=self._state_version,
                        client_turn_id=client_turn_id,
                        transcript_final_at_ms=transcript_final_at_ms,
                        turn_finalize_at_ms=turn_finalize_at_ms,
                        stt_confidence=stt_confidence,
                        speculative_id=speculative_id,
                        _conflict_retry_attempt=_conflict_retry_attempt + 1,
                        _emit_final_transcript=False,
                        _schedule_processing_notices=False,
                    )
                return
            processing_may_continue = await self._handle_interview_failure(exc)
            return
        except Exception as exc:  # noqa: BLE001 - InterviewBridge boundary
            await self._handle_interview_failure(exc)
            return
        finally:
            if stream_queue is not None and not result_received:
                await stream_queue.put(("error", None))
            if stream_play_task is not None and not result_received:
                if cancel_stream_play_task and not stream_play_task.done():
                    stream_play_task.cancel()
                stream_results = await asyncio.gather(
                    stream_play_task,
                    return_exceptions=True,
                )
                stream_error = stream_results[0]
                if isinstance(stream_error, BaseException) and not isinstance(
                    stream_error, asyncio.CancelledError
                ):
                    logger.warning(
                        "transcribe_polly_stream_playback_cleanup_failed response_id=%s error_type=%s",
                        streamed_response_id,
                        stream_error.__class__.__name__,
                    )
            await self._cancel_notice_tasks()
            if not result_received and not processing_may_continue:
                self._clear_active_client_turn(client_turn_id)
        if generation != self._generation or self._closed:
            if stream_play_task is not None and not stream_play_task.done():
                stream_play_task.cancel()
                await asyncio.gather(stream_play_task, return_exceptions=True)
            self._clear_active_client_turn(client_turn_id)
            if not self._closed and self._formal_response_id is None:
                await self._resume_input_after_formal_reply(
                    "ANSWER_LISTENING",
                    reason="stale_turn_generation",
                )
            return
        if result is None:
            self._clear_active_client_turn(client_turn_id)
            return
        self._apply_bridge_result(result)
        api_completed_at = monotonic()
        pipeline_timing: dict[str, Any] = {
            "turn_id": result.turn_id,
            "client_turn_id": client_turn_id,
            "user_speech_end_at_ms": self._last_user_speech_end_at_ms,
            "transcribe_final_at_ms": transcript_final_at_ms or int(time() * 1000),
            "transcript_final_at_ms": transcript_final_at_ms or int(time() * 1000),
            "turn_finalize_at_ms": turn_finalize_at_ms or int(time() * 1000),
            "api_started_at": api_started_at,
            "api_completed_at": api_completed_at,
            "api_completed_at_ms": int(time() * 1000),
        }
        pipeline_timing.update(result.latency_metrics or {})
        existing_timing = self._pipeline_timings.get(result.response_id)
        if existing_timing is not None:
            existing_timing.update(pipeline_timing)
            pipeline_timing = existing_timing
        self._pipeline_timings[result.response_id] = pipeline_timing
        self._response_turn_ids[result.response_id] = client_turn_id
        logger.info(
            "voice_turn_api_completed voice_session_id=%s turn_id=%s response_id=%s question_id=%s api_processing_ms=%s transcribe_to_api_completed_ms=%s api_metrics=%s",
            self._context.voice_session_id,
            result.turn_id,
            result.response_id,
            result.question_id,
            round((api_completed_at - api_started_at) * 1000, 1),
            max(0, pipeline_timing["api_completed_at_ms"] - pipeline_timing["transcript_final_at_ms"]),
            result.latency_metrics or {},
        )
        self._clear_active_client_turn(client_turn_id)
        if stream_play_task is not None and stream_queue is not None:
            stream_results = await asyncio.gather(
                stream_play_task,
                return_exceptions=True,
            )
            stream_error = stream_results[0]
            if isinstance(stream_error, BaseException) and not isinstance(
                stream_error, asyncio.CancelledError
            ):
                logger.warning(
                    "transcribe_polly_stream_playback_failed response_id=%s error_type=%s",
                    result.response_id,
                    stream_error.__class__.__name__,
                )
        else:
            await self.send_reply(
                AssistantReply(
                    turn_id=result.turn_id,
                    response_id=result.response_id,
                    text=result.reply_text,
                    action=result.action,
                    question_id=result.question_id,
                    state_version=result.state_version,
                    latency_metrics=result.latency_metrics or {},
                )
            )

    async def _handle_interview_failure(self, exc: Exception) -> bool:
        category = exc.category if isinstance(exc, InterviewApiError) else "API_ERROR"
        message = {
            "PROCESS_TIMEOUT": "interview_process_timeout",
            "NETWORK_ERROR": "interview_process_network_error",
            "API_ERROR": "interview_process_api_error",
        }[category]
        await self._emit(
            RuntimeError(
                message=message,
                detail={
                    "code": category,
                    "errorCode": exc.code if isinstance(exc, InterviewApiError) else None,
                    "errorType": exc.__class__.__name__,
                },
                fatal=False,
            )
        )
        if category == "PROCESS_TIMEOUT":
            # The API may still commit this turn after the client-side timeout.
            # Keep its clientTurnId active so a later utterance cancels that exact
            # backend turn before any replacement turn starts, but reopen the
            # microphone. Leaving the gate closed here permanently deadlocks the
            # runtime when the backend has actually failed rather than completed
            # in the background.
            await self._resume_input_after_formal_reply(
                "ANSWER_LISTENING",
                reason="interview_process_timeout",
            )
            return True
        if self._interview_status == "active" and self._formal_response_id is None:
            await self._resume_input_after_formal_reply(
                "ANSWER_LISTENING",
                reason=f"interview_failure:{category.lower()}",
            )
        return False

    def _schedule_notice(
        self,
        *,
        delay_ms: int,
        kind: Literal[OutputKind.PROCESSING_ACK, OutputKind.LONG_PROCESSING],
        text: str,
        generation: int,
    ) -> None:
        task = asyncio.create_task(
            self._play_delayed_notice(
                delay_ms=delay_ms,
                kind=kind,
                text=text,
                generation=generation,
            )
        )
        self._notice_tasks.add(task)
        task.add_done_callback(self._notice_task_done)

    async def _play_delayed_notice(
        self,
        *,
        delay_ms: int,
        kind: Literal[OutputKind.PROCESSING_ACK, OutputKind.LONG_PROCESSING],
        text: str,
        generation: int,
    ) -> None:
        await asyncio.sleep(delay_ms / 1000)
        if (
            not self._config.backchannel_enabled
            or generation != self._generation
            or self._closed
        ):
            return
        if kind is OutputKind.PROCESSING_ACK:
            if self._listen_ack_played or self._processing_ack_played:
                return
        elif self._long_notice_played:
            return
        pcm = await self._polly.get_cached(text)
        if not pcm:
            return
        accepted = await self._play_backchannel(kind=kind, text=text, pcm=pcm)
        if not accepted:
            return
        if kind is OutputKind.PROCESSING_ACK:
            self._processing_ack_played = True
        else:
            self._long_notice_played = True

    async def _play_backchannel(
        self,
        *,
        kind: OutputKind,
        text: str,
        pcm: bytes,
    ) -> bool:
        if not self._config.backchannel_enabled:
            return False
        response_id = f"backchannel-{kind.value}-{uuid4().hex[:10]}"
        request = AudioOutputRequest(
            response_id=response_id,
            generation=self._generation,
            kind=kind,
            pcm_chunks=_single_pcm(pcm),
        )
        self._assistant_response_states[response_id] = AssistantResponseState.PLANNED
        self._backchannel_metadata[response_id] = (kind, text)
        if kind is OutputKind.PROCESSING_ACK and self._active_client_turn_id is not None:
            self._response_turn_ids[response_id] = self._active_client_turn_id
        result = await self._output.play(request)
        if not result.accepted:
            self._backchannel_metadata.pop(response_id, None)
            self._assistant_response_states[response_id] = (
                AssistantResponseState.INTERRUPTED
            )
        else:
            self._last_backchannel_at = monotonic()
        return result.accepted

    async def _play_formal_reply(self, reply: AssistantReply) -> None:
        generation = self._generation or 1
        if self._generation == 0:
            self._generation = generation
        action = InterviewAction.from_api(reply.action)
        next_input_state = self._next_input_state(action)
        # Keep the gate closed for the entire formal reply lifecycle.  This
        # covers initial questions as well as replies produced after an
        # evaluated answer, and prevents barge-in/noise from reaching VAD or
        # Transcribe before browser playback has drained.
        self._close_input_gate(reason="formal_reply_started")
        self._assistant_response_states[reply.response_id] = AssistantResponseState.PLANNED
        await self._cancel_notice_tasks()
        await self._output.cancel_notices()
        timing = self._pipeline_timings.get(reply.response_id)
        if timing is not None and "polly_started_at" not in timing:
            timing["polly_started_at"] = monotonic()
            timing["polly_started_at_ms"] = int(time() * 1000)
            timing["polly_first_start_ms"] = timing["polly_started_at_ms"]
            logger.info(
                "voice_turn_polly_started response_id=%s polly_started_at_ms=%s",
                reply.response_id,
                timing["polly_started_at_ms"],
            )
        # Polly calls are now scheduled before data-channel transcript
        # delivery. They run while the browser renders the text and while the
        # output coordinator is being prepared, without changing the formal
        # reply that the API has already committed.
        await self._prepare_polly_chunks(
            reply.text,
            log_event="voice_formal_reply_audio_preload_scheduled",
        )
        await self._emit(
            AssistantResponsePreparing(
                response_id=reply.response_id,
                generation=generation,
            )
        )
        await self._emit(
            AssistantTranscriptFinal(
                text=reply.text,
                response_id=reply.response_id,
                generation=generation,
            )
        )
        self._record_assistant_event_background(
            "assistant_transcript_final",
            response_id=reply.response_id,
            transcript=reply.text,
            detail={
                "plannedReplyText": reply.text,
                "spokenTranscript": reply.text,
                "turnId": reply.turn_id,
                "action": reply.action,
                "questionId": reply.question_id,
            },
        )
        self._formal_response_id = reply.response_id
        self._formal_generation = generation
        self._formal_playback_drained_event = asyncio.Event()
        self._pending_listening_state = next_input_state
        request = AudioOutputRequest(
            response_id=reply.response_id,
            generation=generation,
            kind=OutputKind.FORMAL_REPLY,
            pcm_chunks=self._synthesize_chunks(
                reply.text,
                generation,
                response_id=reply.response_id,
            ),
        )
        self._assistant_response_states[reply.response_id] = (
            AssistantResponseState.SYNTHESIZING
        )
        try:
            result = await self._output.play(request)
        except PollySynthesisError as exc:
            self._pipeline_timings.pop(reply.response_id, None)
            self._assistant_speaking = False
            self._formal_response_id = None
            self._formal_generation = None
            self._release_formal_playback_waiter()
            self._pending_listening_state = None
            self._assistant_response_states[reply.response_id] = (
                AssistantResponseState.INTERRUPTED
            )
            await self._emit(
                RuntimeError(
                    message="polly_synthesis_failed",
                    detail={"errorType": exc.__class__.__name__},
                    fatal=False,
                )
            )
            await self._resume_input_after_formal_reply(
                next_input_state,
                reason="polly_synthesis_failed",
            )
            return
        if not result.accepted or result.cancelled:
            self._pipeline_timings.pop(reply.response_id, None)
            if self._formal_response_id == reply.response_id:
                self._formal_response_id = None
                self._formal_generation = None
                self._pending_listening_state = None
                self._release_formal_playback_waiter()
                await self._resume_input_after_formal_reply(
                    next_input_state,
                    reason="formal_reply_not_accepted",
                )
            return
        if action is InterviewAction.END_INTERVIEW:
            self._interview_status = "completed"
            self._input_available = False
        if result.audio_duration_ms <= 0:
            logger.warning(
                "formal_reply_audio_empty response_id=%s generation=%s action=%s; reopening input",
                reply.response_id,
                generation,
                action.value,
            )
            await self.notify_assistant_playback_drained(
                response_id=reply.response_id,
                generation=generation,
            )

    async def _play_streaming_formal_reply(
        self,
        *,
        response_id: str,
        generation: int,
        stream_queue: asyncio.Queue[tuple[str, Any]],
    ) -> None:
        """Play Polly chunks while the API is still receiving LLM deltas."""

        self._close_input_gate(reason="formal_reply_stream_started")
        self._assistant_response_states[response_id] = AssistantResponseState.PLANNED
        await self._cancel_notice_tasks()
        await self._output.cancel_notices()
        self._formal_response_id = response_id
        self._formal_generation = generation
        self._formal_playback_drained_event = asyncio.Event()
        self._pending_listening_state = "ANSWER_LISTENING"
        final_result: InterviewBridgeResult | None = None

        async def on_complete(result: InterviewBridgeResult) -> None:
            nonlocal final_result
            if result.response_id != response_id:
                raise PollySynthesisError("stream response id changed before completion")
            final_result = result
            self._pending_listening_state = self._next_input_state(
                InterviewAction.from_api(result.action)
            )
            await self._emit(
                AssistantTranscriptFinal(
                    text=result.reply_text,
                    response_id=response_id,
                    generation=generation,
                )
            )
            self._record_assistant_event_background(
                "assistant_transcript_final",
                response_id=response_id,
                transcript=result.reply_text,
                detail={
                    "plannedReplyText": result.reply_text,
                    "spokenTranscript": result.reply_text,
                    "turnId": result.turn_id,
                    "action": result.action,
                    "questionId": result.question_id,
                },
            )

        await self._emit(
            AssistantResponsePreparing(
                response_id=response_id,
                generation=generation,
            )
        )
        request = AudioOutputRequest(
            response_id=response_id,
            generation=generation,
            kind=OutputKind.FORMAL_REPLY,
            pcm_chunks=self._synthesize_streaming_chunks(
                stream_queue,
                generation,
                response_id=response_id,
                on_complete=on_complete,
            ),
        )
        self._assistant_response_states[response_id] = AssistantResponseState.SYNTHESIZING
        try:
            result = await self._output.play(request)
        except Exception as exc:  # noqa: BLE001 - stream playback must recover the input gate
            self._pipeline_timings.pop(response_id, None)
            self._assistant_speaking = False
            self._formal_response_id = None
            self._formal_generation = None
            self._release_formal_playback_waiter()
            self._pending_listening_state = None
            self._assistant_response_states[response_id] = AssistantResponseState.INTERRUPTED
            await self._emit(
                RuntimeError(
                    message="polly_synthesis_failed",
                    detail={"errorType": exc.__class__.__name__},
                    fatal=False,
                )
            )
            await self._resume_input_after_formal_reply(
                "ANSWER_LISTENING",
                reason="polly_synthesis_failed",
            )
            return
        if not result.accepted or result.cancelled:
            self._pipeline_timings.pop(response_id, None)
            return
        if final_result is not None and final_result.action == "finish":
            self._interview_status = "completed"
            self._input_available = False
        if result.audio_duration_ms <= 0:
            await self.notify_assistant_playback_drained(
                response_id=response_id,
                generation=generation,
            )

    async def _synthesize_streaming_chunks(
        self,
        stream_queue: asyncio.Queue[tuple[str, Any]],
        generation: int,
        *,
        response_id: str,
        on_complete: Callable[[InterviewBridgeResult], Awaitable[None]],
    ) -> AsyncIterator[bytes]:
        """Bounded Polly worker pool fed by incremental Question Generator text."""

        timing = self._pipeline_timings.get(response_id)
        if timing is not None and "polly_started_at" not in timing:
            timing["polly_started_at"] = monotonic()
            timing["polly_started_at_ms"] = int(time() * 1000)
            timing["polly_first_start_ms"] = timing["polly_started_at_ms"]
            logger.info(
                "voice_turn_polly_first_start response_id=%s polly_first_start=%s",
                response_id,
                timing["polly_started_at_ms"],
            )
        config = PollyTextChunkerConfig(
            first_min_chars=self._config.first_chunk_min_chars,
            first_max_chars=self._config.first_chunk_max_chars,
            following_min_chars=self._config.following_chunk_min_chars,
            following_max_chars=self._config.following_chunk_max_chars,
        )
        text_queue: asyncio.Queue[tuple[int, str] | None] = asyncio.Queue(
            maxsize=max(2, self._config.polly_max_parallel_requests * 2)
        )
        pcm_streams: asyncio.Queue[
            tuple[int, asyncio.Queue[tuple[bytes | None, BaseException | None]]] | None
        ] = asyncio.Queue()
        chunker = PollyTextChunker(config)
        producer_error: BaseException | None = None
        worker_error: BaseException | None = None

        async def produce() -> None:
            nonlocal producer_error
            index = 0
            streamed_text = ""
            try:
                while True:
                    kind, payload = await stream_queue.get()
                    if kind == "delta":
                        delta = str(payload or "")
                        streamed_text += delta
                        for chunk in chunker.push(delta, final=False):
                            await text_queue.put((index, chunk))
                            index += 1
                        continue
                    if kind == "complete":
                        result = payload
                        if not isinstance(result, InterviewBridgeResult):
                            raise PollySynthesisError("missing streamed interview result")
                        await on_complete(result)
                        if streamed_text.strip() != result.reply_text.strip():
                            logger.warning(
                                "voice_turn_stream_text_mismatch response_id=%s streamed_chars=%s final_chars=%s",
                                response_id,
                                len(streamed_text),
                                len(result.reply_text),
                            )
                        if not streamed_text.strip():
                            final_text = result.reply_text
                        elif result.reply_text.startswith(streamed_text):
                            final_text = result.reply_text[len(streamed_text) :]
                        elif result.reply_text.startswith(streamed_text.rstrip()):
                            final_text = result.reply_text[len(streamed_text.rstrip()) :]
                        else:
                            # The already-spoken stream cannot be replaced. Do
                            # not append a rewritten full answer and duplicate
                            # content when the final API text diverges.
                            logger.warning(
                                "voice_turn_stream_reply_rewrite_ignored response_id=%s",
                                response_id,
                            )
                            final_text = ""
                        for chunk in chunker.push(final_text, final=True):
                            await text_queue.put((index, chunk))
                            index += 1
                        break
                    if kind == "error":
                        raise PollySynthesisError("question stream ended before commit")
                    raise PollySynthesisError("unknown question stream signal")
            except BaseException as exc:
                producer_error = exc
            finally:
                for _ in range(max(1, self._config.polly_max_parallel_requests)):
                    await text_queue.put(None)

        async def synthesize() -> None:
            nonlocal worker_error
            while True:
                item = await text_queue.get()
                if item is None:
                    await pcm_streams.put(None)
                    return
                index, text = item
                output_queue: asyncio.Queue[
                    tuple[bytes | None, BaseException | None]
                ] = asyncio.Queue(maxsize=8)
                await pcm_streams.put((index, output_queue))
                stream_error: BaseException | None = None
                try:
                    async for pcm in self._synthesize_polly_chunk(text):
                        if pcm:
                            await output_queue.put((pcm, None))
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:
                    worker_error = exc
                    stream_error = exc
                finally:
                    await output_queue.put((None, stream_error))
                if stream_error is not None:
                    await pcm_streams.put(None)
                    return

        producer_task = asyncio.create_task(produce())
        workers = [
            asyncio.create_task(synthesize())
            for _ in range(max(1, self._config.polly_max_parallel_requests))
        ]
        pending: dict[
            int,
            asyncio.Queue[tuple[bytes | None, BaseException | None]],
        ] = {}
        next_index = 0
        completed_workers = 0
        try:
            while completed_workers < len(workers):
                item = await pcm_streams.get()
                if item is None:
                    completed_workers += 1
                    continue
                index, output_queue = item
                pending[index] = output_queue
                while next_index in pending:
                    current_queue = pending[next_index]
                    while True:
                        pcm, stream_error = await current_queue.get()
                        if pcm is None:
                            pending.pop(next_index, None)
                            if stream_error is not None:
                                if isinstance(stream_error, PollySynthesisError):
                                    raise stream_error
                                raise PollySynthesisError(
                                    "polly synthesis failed"
                                ) from stream_error
                            next_index += 1
                            break
                        if generation != self._generation or self._closed:
                            return
                        if timing is not None and next_index == 0 and "polly_first_chunk_ready_at" not in timing:
                            self._mark_first_polly_chunk(timing, response_id)
                        yield pcm
            await producer_task
            if producer_error is not None:
                if isinstance(producer_error, PollySynthesisError):
                    raise producer_error
                raise PollySynthesisError("question stream failed") from producer_error
            if worker_error is not None:
                if isinstance(worker_error, PollySynthesisError):
                    raise worker_error
                raise PollySynthesisError("polly synthesis failed") from worker_error
        finally:
            if not producer_task.done():
                producer_task.cancel()
            for task in workers:
                if not task.done():
                    task.cancel()
            await asyncio.gather(producer_task, *workers, return_exceptions=True)

    async def _synthesize_polly_chunk(self, text: str) -> AsyncIterator[bytes]:
        prepared = self._prepared_polly_tasks.pop(text, None)
        stream_method = getattr(self._polly, "synthesize_stream", None)
        if callable(stream_method):
            async for pcm in stream_method(text):
                yield pcm
            return
        if prepared is not None:
            yield await prepared
            return
        yield await self._polly.synthesize(text)

    async def _resume_input_after_formal_reply(
        self,
        next_state: Literal[
            "ANSWER_LISTENING",
            "CONFIRMATION_LISTENING",
            "INTERVIEW_COMPLETED",
        ],
        *,
        reason: str,
    ) -> None:
        """Reopen input only after a formal reply has finished or failed.

        The browser may still have buffered audio when the runtime emits
        ``assistant_speech_ended``.  Callers therefore use this helper from
        the playback-drained callback (or a terminal error path), not from the
        output-completed callback.
        """
        if self._closed or self._transcribe_unavailable:
            return
        resume_started_at = monotonic()
        logger.info(
            "voice_handoff event=listening_resume_requested voice_session_id=%s generation=%s monotonic_ms=%.3f reason=%s",
            self._context.voice_session_id if self._context else None,
            self._generation, resume_started_at * 1000, reason,
        )
        if (
            next_state == "INTERVIEW_COMPLETED"
            or self._interview_status in {"completed", "stopped"}
        ):
            self._input_available = False
        else:
            self._input_available = True
            self._input_gate_reason = None
            self._silence_keepalive_logged = False
            self._listening_ready_at = monotonic()
            self._first_input_after_resume = True
            self._turn_active = False
            self._speech_active = False
            self._silence_started_at = None
            self._audio_batch.clear()
            self._barge_in_voiced_ms = 0
        logger.info(
            "transcribe_polly_input_gate_%s voice_session_id=%s generation=%s next_input_state=%s reason=%s",
            "opened" if self._input_available else "closed",
            self._context.voice_session_id if self._context is not None else None,
            self._generation,
            next_state,
            reason,
        )
        await self._emit_input_state(next_state)
        logger.info(
            "voice_handoff event=listening_ready voice_session_id=%s generation=%s monotonic_ms=%.3f "
            "resume_ms=%.3f input_available=%s",
            self._context.voice_session_id if self._context else None,
            self._generation, monotonic() * 1000,
            (monotonic() - resume_started_at) * 1000, self._input_available,
        )

    async def _synthesize_chunks(
        self,
        text: str,
        generation: int,
        response_id: str | None = None,
    ) -> AsyncIterator[bytes]:
        timing = self._pipeline_timings.get(response_id) if response_id else None
        is_initial_response = bool(response_id and response_id.startswith("initial-response-"))
        if is_initial_response and not self._startup_first_tts_chunk_logged:
            self._startup_stage("tts_generation_started", response_id=response_id)
        if timing is not None and "polly_started_at" not in timing:
            timing["polly_started_at"] = monotonic()
            timing["polly_started_at_ms"] = int(time() * 1000)
            logger.info(
                "voice_turn_polly_started response_id=%s polly_started_at_ms=%s",
                response_id,
                timing["polly_started_at_ms"],
            )
        chunks = split_text_for_polly(
            text,
            PollyTextChunkerConfig(
                first_min_chars=self._config.first_chunk_min_chars,
                first_max_chars=self._config.first_chunk_max_chars,
                following_min_chars=self._config.following_chunk_min_chars,
                following_max_chars=self._config.following_chunk_max_chars,
            ),
        )
        parallel = max(1, self._config.polly_max_parallel_requests)
        tasks: dict[int, asyncio.Task[bytes]] = {}
        if not chunks:
            return
        next_to_schedule = 1
        try:
            while next_to_schedule < min(parallel, len(chunks)):
                tasks[next_to_schedule] = self._take_or_schedule_polly_task(
                    chunks[next_to_schedule]
                )
                next_to_schedule += 1
            first_chunk_started = False
            async for pcm in self._synthesize_polly_chunk(chunks[0]):
                if generation != self._generation or self._closed:
                    return
                if not first_chunk_started:
                    first_chunk_started = True
                    if timing is not None:
                        self._mark_first_polly_chunk(timing, response_id)
                    if is_initial_response and not self._startup_first_tts_chunk_logged:
                        self._startup_first_tts_chunk_logged = True
                        self._startup_stage(
                            "first_tts_chunk_ready",
                            response_id=response_id,
                            bytes=len(pcm),
                        )
                yield pcm
            for index in range(1, len(chunks)):
                if index not in tasks:
                    tasks[index] = self._take_or_schedule_polly_task(chunks[index])
                pcm = await tasks.pop(index)
                if generation != self._generation or self._closed:
                    return
                yield pcm
                if next_to_schedule < len(chunks):
                    chunk = chunks[next_to_schedule]
                    tasks[next_to_schedule] = self._take_or_schedule_polly_task(chunk)
                    next_to_schedule += 1
        finally:
            for task in tasks.values():
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks.values(), return_exceptions=True)

    def _mark_first_polly_chunk(
        self,
        timing: dict[str, Any],
        response_id: str | None,
    ) -> None:
        first_at = monotonic()
        timing["polly_first_chunk_ready_at"] = first_at
        timing["polly_first_chunk_ready_at_ms"] = int(time() * 1000)
        timing["polly_first_end_ms"] = timing["polly_first_chunk_ready_at_ms"]
        logger.info(
            "voice_turn_polly_first_chunk_ready response_id=%s polly_first_chunk_ms=%s",
            response_id,
            round((first_at - timing.get("polly_started_at", first_at)) * 1000, 1),
        )

    def _take_or_schedule_polly_task(self, chunk: str) -> asyncio.Task[bytes]:
        prepared = self._prepared_polly_tasks.pop(chunk, None)
        if prepared is not None:
            return prepared
        return asyncio.create_task(self._polly.synthesize(chunk))

    def _startup_stage(self, stage: str, **details: object) -> None:
        log_voice_startup_stage(
            logger,
            voice_session_id=self._context.voice_session_id if self._context else None,
            provider=self._config.provider_name,
            stage=stage,
            **details,
        )

    async def _emit_output_frame(
        self,
        request: AudioOutputRequest,
        pcm: bytes,
    ) -> None:
        self._audio_sequence += 1
        await self._emit(
            AssistantAudioChunk(
                response_id=request.response_id,
                completion_id=f"polly-{request.response_id}",
                generation=request.generation,
                sequence=self._audio_sequence,
                pcm=pcm,
                authorized=True,
                sample_rate_hz=self._config.polly_sample_rate_hz,
            )
        )

    async def _on_output_started(self, request: AudioOutputRequest) -> None:
        self._assistant_speaking = True
        self._assistant_response_states[request.response_id] = (
            AssistantResponseState.PLAYING
        )
        timing = self._pipeline_timings.get(request.response_id)
        if timing is not None:
            output_started_at = monotonic()
            timing["output_started_at"] = output_started_at
            timing["output_started_at_ms"] = int(time() * 1000)
            timing["playback_first_audio_at_ms"] = timing["output_started_at_ms"]
            timing["speech_end_to_first_audio_ms"] = (
                timing["output_started_at_ms"] - timing["user_speech_end_at_ms"]
                if timing.get("user_speech_end_at_ms")
                else None
            )
            timing["polly_first_chunk_ms"] = round(
                (
                    timing.get("polly_first_chunk_ready_at", output_started_at)
                    - timing.get("polly_started_at", output_started_at)
                )
                * 1000,
                1,
            )
            timing["total_turn_latency_ms"] = max(
                0,
                timing["output_started_at_ms"] - timing["transcript_final_at_ms"],
            )
            logger.info(
                "voice_turn_pipeline_latency turn_id=%s response_id=%s user_speech_end=%s transcribe_final=%s turn_finalize=%s interpreter_start=%s interpreter_end=%s rag_start=%s rag_end=%s question_llm_start=%s question_first_token=%s question_first_sentence=%s question_llm_end=%s polly_first_start=%s polly_first_end=%s playback_first_audio=%s speech_end_to_first_audio_ms=%s interpreter_ms=%s medium_retry_ms=%s patch_repair_ms=%s state_transition_ms=%s retrieval_ms=%s question_generation_ms=%s api_total_ms=%s polly_first_chunk_ms=%s total_turn_latency_ms=%s",
                timing.get("turn_id"),
                request.response_id,
                timing.get("user_speech_end_at_ms"),
                timing.get("transcribe_final_at_ms"),
                timing.get("turn_finalize_at_ms"),
                timing.get("interpreter_start_ms"),
                timing.get("interpreter_end_ms"),
                timing.get("rag_start_ms"),
                timing.get("rag_end_ms"),
                timing.get("question_llm_start_ms"),
                timing.get("question_first_token_ms"),
                timing.get("question_first_sentence_ms"),
                timing.get("question_llm_end_ms"),
                timing.get("polly_first_start_ms"),
                timing.get("polly_first_end_ms"),
                timing.get("playback_first_audio_at_ms"),
                timing.get("speech_end_to_first_audio_ms"),
                timing.get("interpreter_ms", 0),
                timing.get("medium_retry_ms", 0),
                timing.get("patch_repair_ms", 0),
                timing.get("state_transition_ms", 0),
                timing.get("retrieval_ms", 0),
                timing.get("question_generation_ms", 0),
                timing.get("api_total_ms", 0),
                timing["polly_first_chunk_ms"],
                timing["total_turn_latency_ms"],
            )
        client_turn_id = self._response_turn_ids.get(request.response_id)
        if timing is not None:
            client_turn_id = timing.get("client_turn_id") or client_turn_id
        turn_timing = (
            self._turn_timings.get(client_turn_id)
            if client_turn_id is not None
            else None
        )
        now_ms = int(time() * 1000)
        if request.kind is OutputKind.FORMAL_REPLY:
            if turn_timing is not None:
                turn_timing["main_started_at_ms"] = now_ms
            self._log_turn_telemetry(
                "assistant_main_started",
                client_turn_id,
                **(
                    self._turn_latency_metrics(turn_timing)
                    if turn_timing is not None
                    else {}
                ),
            )
        elif request.kind is OutputKind.PROCESSING_ACK:
            if turn_timing is not None:
                turn_timing["ack_started_at_ms"] = now_ms
            self._log_turn_telemetry(
                "ack_started",
                client_turn_id,
                **(
                    self._turn_latency_metrics(turn_timing)
                    if turn_timing is not None
                    else {}
                ),
            )

        backchannel = self._backchannel_metadata.get(request.response_id)
        if backchannel is not None:
            kind, text = backchannel
            await self._emit(
                AssistantBackchannel(
                    kind=_backchannel_event_kind(kind),
                    response_id=request.response_id,
                    generation=request.generation,
                    text=text,
                )
            )
            self._record_assistant_event_background(
                "assistant_backchannel",
                response_id=request.response_id,
                transcript=None,
                detail={"kind": kind.value, "text": text},
            )
        await self._emit(
            AssistantSpeechStarted(
                response_id=request.response_id,
                generation=request.generation,
            )
        )
        self._record_assistant_event_background(
            "assistant_speech_started",
            response_id=request.response_id,
            transcript=None,
            detail={
                "kind": request.kind.value,
                **(
                    {
                        "turnId": timing.get("turn_id"),
                        "latencyMetrics": {
                            name: timing.get(name, 0)
                            for name in (
                                "user_speech_end_at_ms",
                                "transcribe_final_at_ms",
                                "turn_finalize_at_ms",
                                "interpreter_start_ms",
                                "interpreter_end_ms",
                                "rag_start_ms",
                                "rag_end_ms",
                                "question_llm_start_ms",
                                "question_first_token_ms",
                                "question_first_sentence_ms",
                                "question_llm_end_ms",
                                "polly_first_start_ms",
                                "polly_first_end_ms",
                                "playback_first_audio_at_ms",
                                "speech_end_to_first_audio_ms",
                                "interpreter_ms",
                                "medium_retry_ms",
                                "patch_repair_ms",
                                "state_transition_ms",
                                "retrieval_ms",
                                "question_generation_ms",
                                "api_total_ms",
                                "polly_first_chunk_ms",
                                "total_turn_latency_ms",
                            )
                        },
                    }
                    if timing is not None
                    else {}
                ),
            },
        )

    async def _on_output_completed(
        self,
        request: AudioOutputRequest,
        duration_ms: int,
    ) -> None:
        await self._emit(
            AssistantSpeechEnded(
                response_id=request.response_id,
                generation=request.generation,
                audio_duration_ms=duration_ms,
            )
        )
        self._record_assistant_event_background(
            "assistant_speech_ended",
            response_id=request.response_id,
            transcript=None,
            detail={"kind": request.kind.value, "audioDurationMs": duration_ms},
        )
        pipeline_timing = self._pipeline_timings.get(request.response_id)
        client_turn_id = self._response_turn_ids.get(request.response_id)
        if pipeline_timing is not None:
            client_turn_id = pipeline_timing.get("client_turn_id") or client_turn_id
        turn_timing = (
            self._turn_timings.get(client_turn_id)
            if client_turn_id is not None
            else None
        )
        if request.kind is OutputKind.PROCESSING_ACK:
            if turn_timing is not None:
                turn_timing["ack_ended_at_ms"] = int(time() * 1000)
            self._log_turn_telemetry(
                "ack_ended",
                client_turn_id,
                interrupted=False,
                **(
                    self._turn_latency_metrics(turn_timing)
                    if turn_timing is not None
                    else {}
                ),
            )
        elif request.kind is OutputKind.FORMAL_REPLY:
            if turn_timing is not None:
                turn_timing["main_ended_at_ms"] = int(time() * 1000)
            self._log_turn_telemetry(
                "assistant_main_ended",
                client_turn_id,
                interrupted=False,
                **(
                    self._turn_latency_metrics(turn_timing)
                    if turn_timing is not None
                    else {}
                ),
            )
        if request.kind is not OutputKind.FORMAL_REPLY:
            self._assistant_speaking = False
            self._backchannel_metadata.pop(request.response_id, None)
            self._assistant_response_states[request.response_id] = (
                AssistantResponseState.PLAYED
            )
        else:
            self._pipeline_timings.pop(request.response_id, None)
            if client_turn_id is not None:
                self._turn_timings.pop(client_turn_id, None)
        self._response_turn_ids.pop(request.response_id, None)

    async def _on_output_interrupted(self, request: AudioOutputRequest) -> None:
        interrupted_formal = self._formal_response_id == request.response_id
        pipeline_timing = self._pipeline_timings.get(request.response_id)
        client_turn_id = self._response_turn_ids.get(request.response_id)
        if pipeline_timing is not None:
            client_turn_id = pipeline_timing.get("client_turn_id") or client_turn_id
        turn_timing = (
            self._turn_timings.get(client_turn_id)
            if client_turn_id is not None
            else None
        )
        next_input_state = (
            self._pending_listening_state if interrupted_formal else None
        )
        if self._formal_response_id == request.response_id:
            self._formal_response_id = None
            self._formal_generation = None
            self._release_formal_playback_waiter()
            self._pending_listening_state = None
        self._backchannel_metadata.pop(request.response_id, None)
        self._assistant_response_states[request.response_id] = (
            AssistantResponseState.INTERRUPTED
        )
        if request.kind is OutputKind.FORMAL_REPLY:
            self._pipeline_timings.pop(request.response_id, None)
            if turn_timing is not None:
                turn_timing["main_ended_at_ms"] = int(time() * 1000)
            self._log_turn_telemetry(
                "assistant_main_ended",
                client_turn_id,
                interrupted=True,
                **(
                    self._turn_latency_metrics(turn_timing)
                    if turn_timing is not None
                    else {}
                ),
            )
            if client_turn_id is not None:
                self._turn_timings.pop(client_turn_id, None)
        elif request.kind is OutputKind.PROCESSING_ACK:
            if turn_timing is not None:
                turn_timing["ack_ended_at_ms"] = int(time() * 1000)
            self._log_turn_telemetry(
                "ack_ended",
                client_turn_id,
                interrupted=True,
                **(
                    self._turn_latency_metrics(turn_timing)
                    if turn_timing is not None
                    else {}
                ),
            )
        if self._output.active_response_id in {None, request.response_id}:
            self._assistant_speaking = False
        if request.response_id in self._interrupt_emitted_for:
            self._interrupt_emitted_for.discard(request.response_id)
        else:
            await self._emit(
                AssistantInterrupted(
                    response_id=request.response_id,
                    generation=self._generation,
                )
            )
        if interrupted_formal and next_input_state is not None:
            await self._resume_input_after_formal_reply(
                next_input_state,
                reason="formal_reply_interrupted",
            )
        self._response_turn_ids.pop(request.response_id, None)

    def _release_formal_playback_waiter(self) -> None:
        drained_event = self._formal_playback_drained_event
        self._formal_playback_drained_event = None
        if drained_event is not None:
            drained_event.set()

    async def _interrupt_output(self, *, reason: str) -> None:
        """Stop Assistant output without changing any committed Interview Turn."""
        old_generation = self._generation
        self._generation += 1
        await self._cancel_notice_tasks()
        response_id = self._output.active_response_id or self._formal_response_id
        if response_id is not None:
            self._interrupt_emitted_for.add(response_id)
        cancelled_response_id = await self._output.cancel_current()
        if cancelled_response_id is not None:
            response_id = cancelled_response_id
        if response_id is not None or self._assistant_speaking:
            await self._emit(
                AssistantInterrupted(
                    response_id=response_id,
                    generation=self._generation,
                )
            )
        if response_id == self._formal_response_id:
            next_input_state = self._pending_listening_state
            self._formal_response_id = None
            self._formal_generation = None
            self._release_formal_playback_waiter()
            self._pending_listening_state = None
            self._assistant_response_states[response_id] = AssistantResponseState.INTERRUPTED
            if (
                next_input_state is not None
                and next_input_state != "INTERVIEW_COMPLETED"
            ):
                await self._resume_input_after_formal_reply(
                    next_input_state,
                    reason="formal_reply_interrupted",
                )
        if (
            self._active_client_turn_id is None
            and self._processing_task is not None
            and not self._processing_task.done()
        ):
            self._processing_task.cancel()
        self._assistant_speaking = False
        logger.info(
            "transcribe_polly_output_interrupted old_generation=%s generation=%s response_id=%s reason=%s",
            old_generation,
            self._generation,
            response_id,
            reason,
        )

    async def _cancel_pending_turn(self) -> None:
        """Cancel only a RECEIVED/EVALUATING Turn and reject its delayed commit."""
        client_turn_id = self._active_client_turn_id
        expected_state_version = self._active_expected_state_version
        if (
            client_turn_id is not None
            and expected_state_version is not None
            and self._interview_bridge is not None
            and self._context is not None
        ):
            self._state_sync_task = self._spawn_background(
                self._cancel_backend_turn(
                    client_turn_id=client_turn_id,
                    expected_state_version=expected_state_version,
                )
            )
        if self._processing_task is not None and not self._processing_task.done():
            self._processing_task.cancel()

    async def _cancel_notice_tasks(self) -> None:
        tasks = tuple(self._notice_tasks)
        self._notice_tasks.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            self._spawn_background(_gather_tasks(tasks))

    def _record_assistant_event_background(
        self,
        event_type: str,
        *,
        response_id: str | None,
        transcript: str | None,
        detail: dict,
    ) -> None:
        if self._interview_bridge is None or self._context is None:
            return
        self._spawn_background(
            self._record_assistant_event(
                event_type,
                response_id=response_id,
                generation=self._generation,
                transcript=transcript,
                detail=detail,
            )
        )

    async def _record_assistant_event(
        self,
        event_type: str,
        *,
        response_id: str | None,
        generation: int,
        transcript: str | None,
        detail: dict,
    ) -> None:
        assert self._interview_bridge is not None
        assert self._context is not None
        try:
            await self._interview_bridge.create_assistant_event(
                voice_session_id=self._context.voice_session_id,
                event_type=event_type,
                response_id=response_id,
                generation=generation,
                transcript=transcript,
                detail=detail,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - telemetry failure is non-fatal
            logger.debug("transcribe_polly_assistant_event_failed: %s", exc)

    async def _cancel_backend_turn(
        self,
        *,
        client_turn_id: str,
        expected_state_version: int,
    ) -> None:
        assert self._interview_bridge is not None
        assert self._context is not None
        try:
            await self._interview_bridge.cancel_turn(
                voice_session_id=self._context.voice_session_id,
                client_turn_id=client_turn_id,
                expected_state_version=expected_state_version,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - cancellation is optimistic concurrency
            logger.warning(
                "voice_turn_cancel_failed client_turn_id=%s error=%s",
                client_turn_id,
                exc,
            )
        finally:
            try:
                snapshot = await self._interview_bridge.load_voice_session(
                    self._context.voice_session_id
                )
                self._apply_voice_session_snapshot(snapshot)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - refresh failure is retried next turn
                logger.warning(
                    "voice_state_refresh_after_cancel_failed client_turn_id=%s error=%s",
                    client_turn_id,
                    exc,
                )
            self._clear_active_client_turn(client_turn_id)

    async def _resync_after_turn_conflict(
        self,
        *,
        client_turn_id: str,
        conflict_code: str,
        expected_state_version: int,
        answer_question_id: str | None,
        retry_allowed: bool,
    ) -> bool:
        """Reload canonical state and retry once only for the unchanged question."""
        if self._interview_bridge is None or self._context is None:
            return False

        voice_session_id = self._context.voice_session_id
        state_version_before = self._state_version
        question_id_before = self._current_question_id
        interview_status_before = self._interview_status
        logger.info(
            "turn_state_conflict voice_session_id=%s client_turn_id=%s code=%s "
            "expected_state_version=%s runtime_state_version=%s answer_question_id=%s",
            voice_session_id,
            client_turn_id,
            conflict_code,
            expected_state_version,
            state_version_before,
            answer_question_id,
        )
        if self._closed:
            self._clear_active_client_turn(client_turn_id)
            return False

        logger.info(
            "voice_state_resync_started voice_session_id=%s client_turn_id=%s "
            "state_version=%s current_question_id=%s",
            voice_session_id,
            client_turn_id,
            state_version_before,
            question_id_before,
        )
        try:
            snapshot = await self._interview_bridge.load_voice_session(voice_session_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - do not resume against stale state
            self._close_input_gate(reason="state_resync_failed")
            self._reset_transcription_turn_state()
            self._clear_active_client_turn(client_turn_id)
            error_code = exc.code if isinstance(exc, InterviewApiError) else None
            status_code = exc.status_code if isinstance(exc, InterviewApiError) else None
            logger.error(
                "voice_state_resync_failed voice_session_id=%s client_turn_id=%s "
                "conflict_code=%s error_code=%s status_code=%s error_type=%s",
                voice_session_id,
                client_turn_id,
                conflict_code,
                error_code,
                status_code,
                exc.__class__.__name__,
            )
            await self._emit_input_state("INPUT_UNAVAILABLE")
            await self._emit(
                RuntimeError(
                    message="interview_state_resync_failed",
                    detail={
                        "code": conflict_code,
                        "errorCode": error_code,
                        "errorType": exc.__class__.__name__,
                    },
                    fatal=True,
                )
            )
            return False

        self._apply_voice_session_snapshot(snapshot)
        self._reset_transcription_turn_state()
        self._clear_active_client_turn(client_turn_id)
        question_unchanged = (
            answer_question_id is not None
            and snapshot.current_question_id == answer_question_id
        )
        can_retry = (
            retry_allowed
            and conflict_code == "turn_state_conflict"
            and bool(client_turn_id)
            and question_unchanged
            and snapshot.interview_status not in {"completed", "stopped"}
        )
        decision = (
            "retry_same_question_once"
            if can_retry
            else "discard_question_changed"
            if not question_unchanged
            else "discard_retry_limit"
            if conflict_code == "turn_state_conflict" and not retry_allowed
            else "discard_non_retryable_conflict"
        )
        logger.info(
            "voice_state_resync voice_session_id=%s client_turn_id=%s "
            "state_version_before=%s state_version_after=%s "
            "current_question_id_before=%s current_question_id_after=%s "
            "answer_question_id=%s question_id_changed=%s "
            "interview_status_before=%s interview_status_after=%s decision=%s retry_attempt=%s",
            voice_session_id,
            client_turn_id,
            state_version_before,
            snapshot.state_version,
            question_id_before,
            snapshot.current_question_id,
            answer_question_id,
            answer_question_id != snapshot.current_question_id,
            interview_status_before,
            snapshot.interview_status,
            decision,
            1 if can_retry else 0,
        )
        if self._closed or self._formal_response_id is not None:
            return False

        if can_retry:
            logger.info(
                "voice_turn_conflict_retry voice_session_id=%s client_turn_id=%s "
                "answer_question_id=%s expected_state_version=%s retry_attempt=1",
                voice_session_id,
                client_turn_id,
                answer_question_id,
                snapshot.state_version,
            )
            return True

        logger.info(
            "discarded_stale_turn voice_session_id=%s client_turn_id=%s code=%s decision=%s",
            voice_session_id,
            client_turn_id,
            conflict_code,
            decision,
        )

        next_state: Literal[
            "ANSWER_LISTENING",
            "CONFIRMATION_LISTENING",
            "INTERVIEW_COMPLETED",
        ] = (
            "INTERVIEW_COMPLETED"
            if snapshot.interview_status in {"completed", "stopped"}
            else "ANSWER_LISTENING"
        )
        await self._resume_input_after_formal_reply(
            next_state,
            reason="state_resynced_after_turn_conflict",
        )
        return False

    def _spawn_background(self, awaitable: object) -> asyncio.Task[object]:
        task = asyncio.create_task(awaitable)  # type: ignore[arg-type]
        self._background_tasks.add(task)
        task.add_done_callback(self._background_task_done)
        return task

    def _notice_task_done(self, task: asyncio.Task[None]) -> None:
        self._notice_tasks.discard(task)
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            logger.warning("transcribe_polly_notice_failed: %s", exception)

    def _background_task_done(self, task: asyncio.Task[object]) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            logger.warning("transcribe_polly_background_task_failed: %s", exception)

    def _apply_bridge_result(self, result: InterviewBridgeResult) -> None:
        self._current_question_id = result.question_id
        self._state_version = result.state_version
        self._interview_status = result.interview_status

    def _apply_voice_session_snapshot(self, snapshot: VoiceSessionSnapshot) -> None:
        self._current_question_id = snapshot.current_question_id
        self._state_version = snapshot.state_version
        self._interview_status = snapshot.interview_status

    def _clear_active_client_turn(self, client_turn_id: str) -> None:
        if self._active_client_turn_id == client_turn_id:
            self._active_client_turn_id = None
            self._active_expected_state_version = None

    def _transcript_result_key(self, result: TranscribeResult) -> str:
        if result.result_id:
            return result.result_id
        if result.is_partial:
            return "anonymous-partial"
        self._anonymous_final_index += 1
        return f"anonymous-{self._anonymous_final_index}"

    def _remember_segment_sequence(self, key: str) -> None:
        if key not in self._segment_sequence:
            self._next_segment_sequence += 1
            self._segment_sequence[key] = self._next_segment_sequence

    def _latest_partial_result(self) -> TranscribeResult | None:
        if not self._partial_segments:
            return None
        return max(
            self._partial_segments.values(),
            key=lambda result: (
                getattr(result, "start_time", None)
                if getattr(result, "start_time", None) is not None
                else float("inf"),
                self._segment_sequence.get(result.result_id or "anonymous-partial", 0),
            ),
        )

    def _combined_final_text(self) -> str:
        keys = self._ordered_final_segment_keys()
        text = ""
        for key in keys:
            text = _merge_transcript_parts(text, self._final_segments[key])
        return text.strip()

    def _ordered_final_segment_keys(self) -> list[str]:
        return sorted(
            self._final_segments,
            key=lambda key: (
                getattr(self._final_segment_results.get(key), "start_time", None)
                if getattr(self._final_segment_results.get(key), "start_time", None) is not None
                else float("inf"),
                self._segment_sequence.get(key, 0),
            ),
        )

    def _final_client_turn_id(self) -> str:
        """Derive a reconnect-stable identity from accepted Transcribe finals."""

        ordered_result_ids = [
            key
            for key in self._ordered_final_segment_keys()
            if key in self._final_result_ids
        ]
        if ordered_result_ids and len(ordered_result_ids) == len(self._final_segments):
            if len(ordered_result_ids) == 1:
                return f"transcribe-{ordered_result_ids[0]}"
            digest = sha256("\x1f".join(ordered_result_ids).encode("utf-8")).hexdigest()[:24]
            return f"transcribe-{digest}"
        # Older/alternate Transcribe streams may omit ResultId.  Do not derive
        # a durable identity from transcript text because identical answers in
        # different turns are valid; the API still provides its idempotency
        # boundary for callers that can supply a source id.
        return f"transcribe-{uuid4().hex}"

    def _combined_stable_text(self) -> str:
        final = self._combined_final_text()
        latest_partial = self._latest_partial_result()
        stable = latest_partial.stable_text if latest_partial else self._stable_text
        return _merge_transcript_parts(final, stable)

    def _combined_transcript(self) -> str:
        final = self._combined_final_text()
        latest_partial = self._latest_partial_result()
        partial = latest_partial.text if latest_partial else self._latest_partial_text
        return _merge_transcript_parts(final, partial or self._stable_text)

    def _soft_endpoint_ms(self) -> int:
        # Keep the historical long-form tuning only for callers that have not
        # opted into the new explicit soft/settle/hard configuration. The
        # production factory always supplies ``hard_timeout_ms`` and therefore
        # uses the fixed 600ms soft endpoint.
        if self._config.hard_timeout_ms is not None:
            return max(0, self._config.normal_endpoint_ms)
        if self._voiced_duration_ms < self._config.long_form_speech_ms:
            return self._config.normal_endpoint_ms
        return max(
            self._config.normal_endpoint_ms,
            min(
                self._config.long_form_endpoint_ms,
                max(
                    self._config.normal_endpoint_ms,
                    self._config.hard_endpoint_ms - self._config.final_result_wait_ms,
                ),
            ),
        )

    def _transcript_settle_ms(self) -> int:
        if self._config.transcript_settle_ms is not None:
            return max(0, self._config.transcript_settle_ms)
        return max(0, self._config.final_result_settle_ms)

    def _hard_timeout_after_soft_ms(self) -> int:
        if self._config.hard_timeout_ms is not None:
            return max(0, self._config.hard_timeout_ms)
        return max(
            0,
            self._config.hard_endpoint_ms
            - self._soft_endpoint_ms(),
        )

    @staticmethod
    def _next_input_state(
        action: InterviewAction,
    ) -> Literal[
        "ANSWER_LISTENING",
        "CONFIRMATION_LISTENING",
        "INTERVIEW_COMPLETED",
    ]:
        if action is InterviewAction.ASK_CONFIRMATION:
            return "CONFIRMATION_LISTENING"
        if action is InterviewAction.END_INTERVIEW:
            return "INTERVIEW_COMPLETED"
        return "ANSWER_LISTENING"


async def _single_pcm(pcm: bytes) -> AsyncIterator[bytes]:
    yield pcm


async def _gather_tasks(tasks: tuple[asyncio.Task[None], ...]) -> None:
    await asyncio.gather(*tasks, return_exceptions=True)


def _backchannel_event_kind(
    kind: OutputKind,
) -> Literal["listen_ack", "processing_ack", "long_processing_notice"]:
    if kind is OutputKind.LISTEN_ACK:
        return "listen_ack"
    if kind is OutputKind.PROCESSING_ACK:
        return "processing_ack"
    return "long_processing_notice"
