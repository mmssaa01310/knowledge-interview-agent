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
import re
from collections.abc import AsyncIterator
from enum import StrEnum
from time import monotonic, time
from typing import Literal
from uuid import uuid4

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
)

logger = logging.getLogger(__name__)

LISTEN_ACK_TEXT = "はい。"
PROCESSING_ACK_TEXT = "回答を確認しています。"
LONG_PROCESSING_TEXT = "確認に少し時間がかかっています。"
_SHORT_DIRECT_ANSWERS = frozenset({"はい", "いいえ"})


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


def _looks_like_continuation(text: str) -> bool:
    """Only suppress a listening backchannel for obvious unfinished speech.

    This is deliberately not the turn-completion decision. Completion is
    decided by the Structured Interpreter after a Transcribe final result.
    """

    value = _normalize_ending(text)
    if len(value) < 2:
        return False
    if re.search(
        r"(?:し|っ|ですが|なので|けど|けれど|例えば|まず|それから|というか|担当しているのは|私の場合は)$",
        value,
    ):
        return True
    return bool(re.search(r"(?:and|or|but|because|to|which|that|with|for)$", value.casefold()))


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
        self._last_transcribe_result_at: float | None = None
        self._has_final_transcript = False
        self._stable_text = ""
        self._latest_partial_text = ""
        self._latest_stt_confidence: float | None = None
        self._final_segments: dict[str, str] = {}
        self._final_segment_fingerprints: set[str] = set()
        self._anonymous_final_index = 0
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
        self._pipeline_timings: dict[str, dict[str, float | int]] = {}
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
        self._closed = False
        self._input_available = True
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
            if state_load_task is not None:
                snapshot = await state_load_task
                self._current_question_id = snapshot.current_question_id
                self._state_version = snapshot.state_version
                self._interview_status = snapshot.interview_status
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
        await self._emit(RuntimeReady())
        await self._emit_input_state("ANSWER_LISTENING")

    async def prepare_initial_reply(self, reply_text: str) -> None:
        """Start Polly synthesis before the first reply is sent to the output queue.

        The WebRTC offer is processed before the remote track starts the runtime.  The
        initial greeting and question are already known at that point, so synthesizing
        the first output chunks in parallel with ICE negotiation removes the initial
        Polly request latency from the user's visible ``preparing_audio`` state.
        """
        if self._closed:
            return
        chunks = split_text_for_polly(
            reply_text,
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
                "voice_initial_reply_audio_preload_scheduled voice_session_id=%s prepared_chunks=%s",
                self._context.voice_session_id if self._context is not None else None,
                prepared_count,
            )

    async def push_audio(self, frame: AudioFrame) -> None:
        if (
            not self._started
            or self._closed
            or not self._input_available
            or self._interview_status in {"completed", "stopped"}
        ):
            return
        if (
            frame.sample_rate_hz != self._config.input_sample_rate_hz
            or frame.channels != 1
        ):
            raise ValueError("TranscribePollyRuntime requires 16kHz mono PCM")
        vad = self._vad.inspect(frame.pcm)
        await self._handle_vad(vad.voiced, vad.duration_ms)
        self._audio_batch.extend(frame.pcm)
        target_bytes = int(
            self._config.input_sample_rate_hz
            * 2
            * (self._config.transcribe_chunk_ms / 1000)
        )
        while len(self._audio_batch) >= target_bytes:
            chunk = bytes(self._audio_batch[:target_bytes])
            del self._audio_batch[:target_bytes]
            await self._transcribe.send_audio(chunk)

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
            await self._emit_input_state(next_state)

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

    async def _handle_vad(self, voiced: bool, duration_ms: int) -> None:
        now = monotonic()
        if voiced:
            if self._assistant_speaking and not self._turn_active:
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
            self._speech_active = True
            self._silence_started_at = None
            return
        self._barge_in_voiced_ms = 0
        if self._turn_active and self._speech_active:
            self._speech_active = False
            self._silence_started_at = now

    async def _start_user_turn(
        self,
        *,
        increment_generation: bool,
        initial_voiced_ms: int,
    ) -> None:
        if self._interview_status in {"completed", "stopped"} or not self._input_available:
            return
        if increment_generation:
            self._generation += 1
        await self._cancel_notice_tasks()
        self._turn_active = True
        self._speech_active = True
        self._last_transcribe_result_at = None
        self._has_final_transcript = False
        self._voiced_duration_ms = initial_voiced_ms
        self._stable_text = ""
        self._latest_partial_text = ""
        self._latest_stt_confidence = None
        self._final_segments.clear()
        self._final_segment_fingerprints.clear()
        self._listen_ack_played = False
        self._processing_ack_played = False
        self._long_notice_played = False
        await self._emit(UserSpeechStarted())

    async def _on_transcribe_result(self, result: TranscribeResult) -> None:
        if self._closed or not self._turn_active or not self._input_available:
            return
        self._last_transcribe_result_at = monotonic()
        if result.is_partial:
            self._latest_partial_text = result.text
            if result.stable_text:
                self._stable_text = result.stable_text
        else:
            self._has_final_transcript = True
            self._latest_stt_confidence = result.confidence
            key = result.result_id
            if not key:
                self._anonymous_final_index += 1
                key = f"anonymous-{self._anonymous_final_index}"
            fingerprint = _transcript_fingerprint(result.text)
            if key in self._final_segments or fingerprint in self._final_segment_fingerprints:
                logger.debug("transcribe_final_duplicate_ignored result_id=%s", result.result_id)
                return
            self._final_segments[key] = result.text
            self._final_segment_fingerprints.add(fingerprint)
            self._stable_text = self._combined_final_text()
            self._latest_partial_text = ""
        visible = self._combined_transcript()
        if visible:
            await self._emit(UserTranscriptPartial(text=visible))

    async def _on_transcribe_reconnecting(self, attempt: int) -> None:
        logger.warning("transcribe_reconnecting attempt=%s", attempt)
        await self._emit(RuntimeReconnecting())

    async def _on_transcribe_fatal_error(self, exc: Exception) -> None:
        if self._closed or not self._input_available:
            return
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

    async def _endpoint_loop(self) -> None:
        while not self._closed and self._input_available:
            await asyncio.sleep(0.025)
            if not self._turn_active or self._silence_started_at is None:
                continue
            silence_ms = int((monotonic() - self._silence_started_at) * 1000)
            stable = self._combined_stable_text()
            final = self._combined_final_text()
            normalized_stable = _normalize_ending(stable)
            if silence_ms >= self._config.listen_ack_silence_ms:
                await self._maybe_play_listen_ack(normalized_stable)
            if (
                self._has_final_transcript
                and silence_ms >= self._endpoint_silence_ms()
                and self._transcript_quiet_ms() >= self._config.final_result_settle_ms
                and _normalize_ending(final)
            ):
                await self._finalize_user_turn(final)
                continue

    async def _maybe_play_listen_ack(self, stable: str) -> None:
        if not self._config.backchannel_enabled:
            return
        if self._listen_ack_played or self._assistant_speaking:
            return
        if self._voiced_duration_ms < self._config.listen_ack_min_speech_ms:
            return
        if len(stable) < self._config.listen_ack_min_stable_chars:
            return
        if stable in _SHORT_DIRECT_ANSWERS or _looks_like_continuation(stable):
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

    async def _finalize_user_turn(self, transcript: str) -> None:
        normalized = transcript.strip()
        if not normalized or not self._turn_active:
            return
        self._turn_active = False
        self._speech_active = False
        self._silence_started_at = None
        transcript_final_at_ms = int(time() * 1000)
        await self._emit(UserSpeechEnded())
        await self._emit_input_state("ANSWER_PROCESSING")
        self._processing_task = asyncio.create_task(
            self._process_interview_turn(
                transcript=normalized,
                generation=self._generation,
                expected_state_version=self._state_version,
                client_turn_id=uuid4().hex,
                transcript_final_at_ms=transcript_final_at_ms,
                stt_confidence=self._latest_stt_confidence,
            )
        )

    async def _process_interview_turn(
        self,
        *,
        transcript: str,
        generation: int,
        expected_state_version: int,
        client_turn_id: str,
        transcript_final_at_ms: int | None = None,
        stt_confidence: float | None = None,
    ) -> None:
        if self._interview_bridge is None or self._context is None:
            await self._emit(RuntimeError(message="interview_bridge_unavailable", fatal=True))
            return
        if self._state_sync_task is not None:
            await asyncio.gather(self._state_sync_task, return_exceptions=True)
            expected_state_version = self._state_version
        self._active_client_turn_id = client_turn_id
        self._active_expected_state_version = expected_state_version
        if self._config.backchannel_enabled:
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
        try:
            result = await self._interview_bridge.process_turn(
                voice_session_id=self._context.voice_session_id,
                transcript=transcript,
                answer_to_question_id=self._current_question_id,
                turn_type="ANSWER",
                expected_state_version=expected_state_version,
                client_turn_id=client_turn_id,
                stt_confidence=stt_confidence,
            )
            is_answer = result.turn_type == "ANSWER"
            await self._emit(
                UserTranscriptFinal(
                    text=transcript,
                    turn_type=result.turn_type,
                    question_id=self._current_question_id if is_answer else None,
                )
            )
            result_received = True
        except asyncio.CancelledError:
            raise
        except InterviewApiError as exc:
            if exc.code in {"turn_state_conflict", "turn_duplicate_conflict"}:
                logger.info("discarded_stale_turn client_turn_id=%s code=%s", client_turn_id, exc.code)
                return
            processing_may_continue = await self._handle_interview_failure(exc)
            return
        except Exception as exc:  # noqa: BLE001 - InterviewBridge boundary
            await self._handle_interview_failure(exc)
            return
        finally:
            await self._cancel_notice_tasks()
            if not result_received and not processing_may_continue:
                self._clear_active_client_turn(client_turn_id)
        if generation != self._generation or self._closed:
            self._clear_active_client_turn(client_turn_id)
            return
        self._apply_bridge_result(result)
        api_completed_at = monotonic()
        pipeline_timing: dict[str, float | int] = {
            "transcript_final_at_ms": transcript_final_at_ms or int(time() * 1000),
            "api_started_at": api_started_at,
            "api_completed_at": api_completed_at,
            "api_completed_at_ms": int(time() * 1000),
        }
        self._pipeline_timings[result.response_id] = pipeline_timing
        logger.info(
            "voice_turn_api_completed voice_session_id=%s turn_id=%s response_id=%s question_id=%s api_processing_ms=%s transcribe_to_api_completed_ms=%s",
            self._context.voice_session_id,
            result.turn_id,
            result.response_id,
            result.question_id,
            round((api_completed_at - api_started_at) * 1000, 1),
            max(0, pipeline_timing["api_completed_at_ms"] - pipeline_timing["transcript_final_at_ms"]),
        )
        self._clear_active_client_turn(client_turn_id)
        await self.send_reply(
            AssistantReply(
                turn_id=result.turn_id,
                response_id=result.response_id,
                text=result.reply_text,
                action=result.action,
                question_id=result.question_id,
                state_version=result.state_version,
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
            # backend turn before any replacement turn starts.
            await self._emit_input_state("ANSWER_PROCESSING")
            return True
        if self._interview_status == "active":
            await self._emit_input_state("ANSWER_LISTENING")
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
        self._assistant_response_states[reply.response_id] = AssistantResponseState.PLANNED
        await self._cancel_notice_tasks()
        await self._output.cancel_notices()
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
        self._pending_listening_state = self._next_input_state(action)
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
            await self._emit_input_state(self._next_input_state(action))
            return
        if not result.accepted or result.cancelled:
            self._pipeline_timings.pop(reply.response_id, None)
            if self._formal_response_id == reply.response_id:
                self._formal_response_id = None
                self._formal_generation = None
                self._pending_listening_state = None
                self._release_formal_playback_waiter()
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

    async def _synthesize_chunks(
        self,
        text: str,
        generation: int,
        response_id: str | None = None,
    ) -> AsyncIterator[bytes]:
        timing = self._pipeline_timings.get(response_id) if response_id else None
        if timing is not None:
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
        next_to_schedule = 0
        try:
            while next_to_schedule < min(parallel, len(chunks)):
                chunk = chunks[next_to_schedule]
                tasks[next_to_schedule] = self._take_or_schedule_polly_task(chunk)
                next_to_schedule += 1
            for index in range(len(chunks)):
                pcm = await tasks.pop(index)
                if generation != self._generation or self._closed:
                    return
                if timing is not None and index == 0:
                    timing["polly_first_chunk_ready_at"] = monotonic()
                    timing["polly_first_chunk_ready_at_ms"] = int(time() * 1000)
                    logger.info(
                        "voice_turn_polly_first_chunk_ready response_id=%s polly_first_chunk_ms=%s",
                        response_id,
                        round((timing["polly_first_chunk_ready_at"] - timing["polly_started_at"]) * 1000, 1),
                    )
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

    def _take_or_schedule_polly_task(self, chunk: str) -> asyncio.Task[bytes]:
        prepared = self._prepared_polly_tasks.pop(chunk, None)
        if prepared is not None:
            return prepared
        return asyncio.create_task(self._polly.synthesize(chunk))

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
            logger.info(
                "voice_turn_pipeline_latency response_id=%s transcribe_to_playback_start_ms=%s api_to_playback_start_ms=%s polly_to_playback_start_ms=%s",
                request.response_id,
                max(0, timing["output_started_at_ms"] - timing["transcript_final_at_ms"]),
                round((output_started_at - timing["api_completed_at"]) * 1000, 1),
                round((output_started_at - timing.get("polly_started_at", output_started_at)) * 1000, 1),
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
            detail={"kind": request.kind.value},
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
        if request.kind is not OutputKind.FORMAL_REPLY:
            self._assistant_speaking = False
            self._backchannel_metadata.pop(request.response_id, None)
            self._assistant_response_states[request.response_id] = (
                AssistantResponseState.PLAYED
            )
        else:
            self._pipeline_timings.pop(request.response_id, None)

    async def _on_output_interrupted(self, request: AudioOutputRequest) -> None:
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
        if self._output.active_response_id in {None, request.response_id}:
            self._assistant_speaking = False
        if request.response_id in self._interrupt_emitted_for:
            self._interrupt_emitted_for.discard(request.response_id)
            return
        await self._emit(
            AssistantInterrupted(
                response_id=request.response_id,
                generation=self._generation,
            )
        )

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
        response_id = await self._output.cancel_current()
        if response_id is not None:
            self._interrupt_emitted_for.add(response_id)
        if response_id is not None or self._assistant_speaking:
            await self._emit(
                AssistantInterrupted(
                    response_id=response_id,
                    generation=self._generation,
                )
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
                self._state_version = snapshot.state_version
                self._current_question_id = snapshot.current_question_id
                self._interview_status = snapshot.interview_status
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - refresh failure is retried next turn
                logger.warning(
                    "voice_state_refresh_after_cancel_failed client_turn_id=%s error=%s",
                    client_turn_id,
                    exc,
                )
            self._clear_active_client_turn(client_turn_id)

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

    def _clear_active_client_turn(self, client_turn_id: str) -> None:
        if self._active_client_turn_id == client_turn_id:
            self._active_client_turn_id = None
            self._active_expected_state_version = None

    def _combined_final_text(self) -> str:
        return "".join(self._final_segments.values()).strip()

    def _combined_stable_text(self) -> str:
        final = self._combined_final_text()
        if self._stable_text and self._stable_text != final:
            return f"{final}{self._stable_text}".strip()
        return final or self._stable_text

    def _combined_transcript(self) -> str:
        final = self._combined_final_text()
        partial = self._latest_partial_text
        if final and partial and partial != final:
            return f"{final}{partial}".strip()
        return final or partial or self._stable_text

    def _endpoint_silence_ms(self) -> int:
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

    def _transcript_quiet_ms(self) -> int:
        if self._last_transcribe_result_at is None:
            return 0
        return int((monotonic() - self._last_transcribe_result_at) * 1000)

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
