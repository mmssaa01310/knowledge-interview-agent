"""
Role:
    Nova Sonic Runtimeの外部向けFacade。

Summary:
    双方向ストリームの開始・終了、completion管理、承認済みreply制御、
    音声イベントの整列をまとめて扱い、周辺コンポーネントへ処理を委譲する。

Relations:
    Uses response_controller, event_mapper, protocol payload/event helpers.
    Used by WebRTC transport through RealtimeVoiceRuntime.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import AsyncIterator
from time import monotonic
from typing import Any

from aws_sdk_bedrock_runtime.client import BedrockRuntimeClient

from ai_interviewer_voice.runtimes.nova_sonic.config import NovaSonicRuntimeConfig
from ai_interviewer_voice.runtimes.nova_sonic.completion_lifecycle import CompletionLifecycle
from ai_interviewer_voice.runtimes.nova_sonic.completion_registry import CompletionRegistry
from ai_interviewer_voice.runtimes.nova_sonic.input_gate import InputGateController
from ai_interviewer_voice.runtimes.nova_sonic.evaluation_turn_coordinator import (
    EvaluationTurnCoordinator,
)
from ai_interviewer_voice.runtimes.nova_sonic.local_preface import (
    LocalConfirmationPrefacePlayer,
)
from ai_interviewer_voice.runtimes.nova_sonic.pending_turn_store import PendingTurnStore
from ai_interviewer_voice.runtimes.nova_sonic.runtime_ports import (
    ApprovedResponseStore,
    NovaObservability,
    QueueRuntimeEventSink,
    RuntimeSessionContext,
)
from ai_interviewer_voice.runtimes.nova_sonic.assistant_event_recorder import AssistantEventRecorder
from ai_interviewer_voice.runtimes.nova_sonic.preflight import (
    NovaSonicPreflightResult,
    NovaSonicPreflightService,
)
from ai_interviewer_voice.runtimes.nova_sonic.protocol_dispatcher import ProtocolEventDispatcher
from ai_interviewer_voice.runtimes.nova_sonic.protocol.payloads import (
    DEFAULT_PROMPT_NAME,
    SYSTEM_CONTENT_NAME,
    build_audio_input_event,
    build_audio_end_sequence,
    build_audio_start_sequence,
    build_prompt_end_event,
    build_runtime_start_sequence,
    build_session_end_event,
    dumps_event_payload,
    build_user_text_sequence,
)
from ai_interviewer_voice.runtimes.nova_sonic.response_controller import ResponseController
from ai_interviewer_voice.runtimes.nova_sonic.response_controller import (
    ResponseAuthorizationState,
)
from ai_interviewer_voice.runtimes.nova_sonic.session_state import (
    InputState,
    NovaSonicObservedOutput,
    PendingToolCall,
    VoiceSessionRuntimeState,
)
from ai_interviewer_voice.runtimes.nova_sonic.sdk_client import (
    create_bedrock_runtime_client,
    open_bidirectional_stream,
    send_payload,
)
from ai_interviewer_voice.runtimes.nova_sonic.tool_turn_coordinator import ToolTurnCoordinator
from ai_interviewer_voice.runtimes.nova_sonic.tool_result_sender import ToolResultSender
from ai_interviewer_voice.runtimes.nova_sonic.stream_writer import NovaStreamWriter
from ai_interviewer_voice.schemas.audio import AudioFrame
from ai_interviewer_voice.schemas.events import (
    AssistantInterrupted,
    RuntimeClosed,
    RuntimeReady,
    VoiceRuntimeEvent,
)
from ai_interviewer_voice.schemas.sessions import AssistantReply, VoiceRuntimeContext
from ai_interviewer_voice.services.interview_bridge import InterviewBridge


logger = logging.getLogger(__name__)


class NovaSonicRuntime:
    def __init__(
        self,
        config: NovaSonicRuntimeConfig | None = None,
        *,
        preflight_service: NovaSonicPreflightService | None = None,
        sdk_client: BedrockRuntimeClient | None = None,
        interview_bridge: InterviewBridge | None = None,
    ) -> None:
        self._config = config or NovaSonicRuntimeConfig()
        self._event_queue: asyncio.Queue[VoiceRuntimeEvent | None] = asyncio.Queue()
        self._event_sink = QueueRuntimeEventSink(self._event_queue)
        self._session_context = RuntimeSessionContext()
        self._observability = NovaObservability()
        self._stream_writer = NovaStreamWriter(self._observability)
        self._response_controller = ResponseController()
        self._preflight_service = preflight_service
        self._sdk_client = sdk_client
        self._interview_bridge = interview_bridge
        self._preflight_result: NovaSonicPreflightResult | None = None
        self._stream: Any | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._audio_sequence = 0
        self._audio_input_frame_count = 0
        self._audio_input_bytes_sent = 0
        self._last_audio_input_flow_log_at = 0.0
        self._started = False
        self._closed = False
        self._prompt_name = DEFAULT_PROMPT_NAME
        self._system_content_name = SYSTEM_CONTENT_NAME
        self._content_counter = 0
        self._audio_content_name: str | None = None
        self._completion_registry = CompletionRegistry()
        self._pending_turn_store = PendingTurnStore()
        self._evaluation_reply_metrics_by_response_id: dict[str, PendingToolCall] = {}
        self._approved_response_store = ApprovedResponseStore()
        self._observed_output = self._observability.output
        self._started_at_monotonic: float | None = None
        self._grace_period_task: asyncio.Task[None] | None = None
        self._shutdown_events_sent = False
        self._reply_completion_start_watchdogs: dict[str, asyncio.Task[None]] = {}
        self._session_context.turn_index = 0
        self._pending_initial_mark_sent_on_completion = False
        self._initial_reply_marked_sent = False
        self._queued_initial_followup_reply_text: str | None = None
        self._queued_initial_followup_question_id: str | None = None
        self._initial_followup_ready = False
        self._initial_followup_task: asyncio.Task[None] | None = None
        self._input_gate = InputGateController(
            voice_session_id_getter=lambda: self._session_context.voice_session_id,
            has_pending_turn_cycle=self._has_pending_turn_cycle,
            authorization_state_getter=lambda: self._response_controller.authorization_state,
            emit_event=lambda event: self._event_queue.put_nowait(event),
        )
        self._session_context.attach_input_gate(self._input_gate)
        self._tool_result_sender = ToolResultSender(
            config=self._config,
            completion_registry=self._completion_registry,
            pending_turn_store=self._pending_turn_store,
            session_context=self._session_context,
            response_controller=self._response_controller,
            output=self._stream_writer,
            event_sink=self._event_sink,
            observability=self._observability,
            approved_responses=self._approved_response_store,
        )
        self._assistant_event_recorder = AssistantEventRecorder(
            interview_bridge=self._interview_bridge,
            session_context=self._session_context,
        )
        self._local_preface_player = LocalConfirmationPrefacePlayer(
            event_sink=self._event_sink,
            assistant_event_recorder=self._assistant_event_recorder,
            generation_port=self._response_controller,
            audio_sequence_port=self._observability,
        )
        self._evaluation_turn_coordinator = EvaluationTurnCoordinator(
            local_preface_player=self._local_preface_player,
            tool_result_sender=self._tool_result_sender,
            pending_turn_store=self._pending_turn_store,
            voice_session_id_getter=lambda: self._session_context.voice_session_id,
            input_state_getter=lambda: self._input_gate.input_state.value,
            set_next_listening_action=self._set_next_listening_action,
            metrics_by_response_id=self._evaluation_reply_metrics_by_response_id,
        )
        self._completion_lifecycle = CompletionLifecycle(
            registry=self._completion_registry,
            response_controller=self._response_controller,
            observed_output_getter=lambda: self._observability.output,
            voice_session_state_getter=lambda: self._session_context.voice_state,
            voice_session_id_getter=lambda: self._session_context.voice_session_id,
            turn_index_getter=lambda: self._session_context.turn_index,
            input_state_getter=lambda: self._input_gate.input_state,
            generation_getter=lambda: self.current_generation,
            pending_turn_store=self._pending_turn_store,
            remove_approved_tool_response=self._approved_response_store.remove,
            cancel_reply_completion_start_watchdog=self._cancel_reply_completion_start_watchdog,
            maybe_mark_initial_reply_sent=self._maybe_mark_initial_reply_sent,
            end_audio_input=self.end_audio_input,
            close_after_current_completion_getter=lambda: self._session_context.close_after_current_completion,
            close_after_current_completion_setter=lambda value: setattr(self._session_context, "close_after_current_completion", value),
            now_ms=self._observability.now_ms,
        )
        self._tool_turn_coordinator = ToolTurnCoordinator(
            config=self._config,
            interview_bridge=self._interview_bridge,
            session_state=self._session_context,
            evaluation_coordinator=self._evaluation_turn_coordinator,
            tool_result_sender=self._tool_result_sender,
            pending_turn_store=self._pending_turn_store,
            input_gate=self._input_gate,
            response_controller=self._response_controller,
            observability=self._observability,
        )
        self._protocol_dispatcher = ProtocolEventDispatcher(
            config=self._config,
            completion_registry=self._completion_registry,
            completion_lifecycle=self._completion_lifecycle,
            response_controller=self._response_controller,
            turn_coordinator=self._tool_turn_coordinator,
            input_gate=self._input_gate,
            event_sink=self._event_sink,
            session_context=self._session_context,
            assistant_event_recorder=self._assistant_event_recorder,
            observability=self._observability,
            pending_turn_store=self._pending_turn_store,
            approved_responses=self._approved_response_store,
            evaluation_reply_metrics=self._evaluation_reply_metrics_by_response_id,
        )

    @property
    def provider_name(self) -> str:
        return self._config.provider_name

    @property
    def output_sample_rate_hz(self) -> int:
        return 24000

    @property
    def _stream(self) -> Any | None:
        return self._stream_writer.stream

    @_stream.setter
    def _stream(self, stream: Any | None) -> None:
        self._stream_writer.stream = stream

    @property
    def _context(self) -> VoiceRuntimeContext | None:
        return self._session_context.context

    @_context.setter
    def _context(self, context: VoiceRuntimeContext | None) -> None:
        self._session_context.context = context

    @property
    def _voice_session_state(self) -> VoiceSessionRuntimeState | None:
        return self._session_context.voice_state

    @_voice_session_state.setter
    def _voice_session_state(self, state: VoiceSessionRuntimeState | None) -> None:
        self._session_context.voice_state = state

    @property
    def _turn_index(self) -> int:
        return self._session_context.turn_index

    @_turn_index.setter
    def _turn_index(self, value: int) -> None:
        self._session_context.turn_index = value

    @property
    def _last_user_speech_started_at(self) -> int | None:
        return self._session_context.last_user_speech_started_at

    @_last_user_speech_started_at.setter
    def _last_user_speech_started_at(self, value: int | None) -> None:
        self._session_context.last_user_speech_started_at = value

    @property
    def _last_user_speech_ended_at(self) -> int | None:
        return self._session_context.last_user_speech_ended_at

    @_last_user_speech_ended_at.setter
    def _last_user_speech_ended_at(self, value: int | None) -> None:
        self._session_context.last_user_speech_ended_at = value

    @property
    def _pending_initial_reply_text(self) -> str | None:
        return self._session_context.pending_initial_reply_text

    @_pending_initial_reply_text.setter
    def _pending_initial_reply_text(self, value: str | None) -> None:
        self._session_context.pending_initial_reply_text = value

    @property
    def _pending_initial_question_id(self) -> str | None:
        return self._session_context.pending_initial_question_id

    @_pending_initial_question_id.setter
    def _pending_initial_question_id(self, value: str | None) -> None:
        self._session_context.pending_initial_question_id = value

    @property
    def _initial_tool_completion_id(self) -> str | None:
        return self._session_context.initial_tool_completion_id

    @_initial_tool_completion_id.setter
    def _initial_tool_completion_id(self, value: str | None) -> None:
        self._session_context.initial_tool_completion_id = value

    @property
    def _close_after_current_completion(self) -> bool:
        return self._session_context.close_after_current_completion

    @_close_after_current_completion.setter
    def _close_after_current_completion(self, value: bool) -> None:
        self._session_context.close_after_current_completion = value

    @property
    def _processed_tool_use_keys(self) -> set[str]:
        return self._session_context.processed_tool_use_keys

    @property
    def current_generation(self) -> int:
        return self._response_controller.generation

    @property
    def preflight_result(self) -> NovaSonicPreflightResult | None:
        return self._preflight_result

    @property
    def observed_output(self) -> NovaSonicObservedOutput:
        return self._observed_output

    @property
    def input_state(self) -> str:
        return self._input_gate.input_state.value

    @property
    def started(self) -> bool:
        return self._started

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def can_send_reply(self) -> bool:
        return bool(
            self._started
            and not self._closed
            and self._stream is not None
            and self._session_context.runtime_open
        )

    @property
    def pending_evaluation_count(self) -> int:
        return self._pending_turn_store.evaluation_count

    @property
    def pending_reply_count(self) -> int:
        return self._pending_turn_store.pending_reply_count

    def _set_input_state(
        self,
        state: InputState,
        *,
        turn_id: str | None = None,
        playback_generation_id: int | None = None,
        reason: str,
    ) -> None:
        self._input_gate.set_state(
            state,
            turn_id=turn_id,
            playback_generation_id=playback_generation_id,
            reason=reason,
        )

    def _has_pending_turn_cycle(self) -> bool:
        return self._pending_turn_store.has_active_cycle()

    def _listening_state_for_action(self, action: str | None) -> InputState:
        return self._input_gate.listening_state_for_action(action)

    def _set_next_listening_action(self, action: str) -> None:
        self._input_gate.next_listening_state = self._listening_state_for_action(action)

    async def _reopen_input_gate_after_guard(self, *, generation: int | None) -> None:
        self._input_gate.schedule_reopen(generation=generation)

    async def start(self, context: VoiceRuntimeContext) -> None:
        if self._started:
            return

        self._tool_result_sender.configure(self._config)
        self._tool_turn_coordinator.configure(self._config)
        self._protocol_dispatcher.configure(self._config)
        self._reset_state(context)
        try:
            await self._load_voice_session_state()
            logger.info(
                "nova_runtime_starting nova_model_id=%s nova_voice_id=%s",
                self._config.model_id,
                self._config.voice_id,
            )
            client = self._sdk_client or create_bedrock_runtime_client(self._config.aws_region)
            self._sdk_client = client
            self._record_input_stage("invoke_model_with_bidirectional_stream")
            self._stream = await open_bidirectional_stream(
                client,
                model_id=self._config.model_id,
                timeout_seconds=self._config.invoke_timeout_seconds,
            )
            self._session_context.runtime_open = True
            await self._send_initial_events()
            self._receive_task = asyncio.create_task(self._receive_output_loop())
            self._record_input_stage("output_receiver_started")
            self._started = True
            await self._event_queue.put(RuntimeReady())
        except Exception as exc:
            await self._cleanup_after_failed_start()
            self._set_failed_stage(f"start:{exc.__class__.__name__}")
            raise

    async def start_audio_input(self) -> None:
        if not self._started or self._stream is None:
            raise RuntimeError("NovaSonicRuntime has not been started")
        self._audio_content_name = self._next_content_name("audio")
        self._session_context.audio_input_open = True
        self._audio_input_frame_count = 0
        self._audio_input_bytes_sent = 0
        self._last_audio_input_flow_log_at = 0.0
        await self._send_sequence(
            build_audio_start_sequence(
                prompt_name=self._prompt_name,
                content_name=self._audio_content_name,
            )
        )
        logger.info(
            "runtime_audio_input_opened voice_session_id=%s content_name=%s runtime_open=%s",
            self._context.voice_session_id if self._context else None,
            self._audio_content_name,
            self._stream is not None and not self._closed,
        )

    async def end_audio_input(self) -> None:
        if not self._started or self._stream is None or self._audio_content_name is None:
            return
        await self._send_sequence(
            build_audio_end_sequence(
                prompt_name=self._prompt_name,
                content_name=self._audio_content_name,
            )
        )
        logger.info(
            "runtime_audio_input_closed voice_session_id=%s content_name=%s frames_sent=%s bytes_sent=%s",
            self._context.voice_session_id if self._context else None,
            self._audio_content_name,
            self._audio_input_frame_count,
            self._audio_input_bytes_sent,
        )
        self._audio_content_name = None
        self._session_context.audio_input_open = False

    async def run_preflight(self) -> NovaSonicPreflightResult:
        service = self._preflight_service or NovaSonicPreflightService(
            region_name=self._config.aws_region,
        )
        result = service.run(self._config.model_id)
        self._preflight_result = result
        return result

    async def push_audio(self, frame: AudioFrame | bytes) -> None:
        if not self._started or self._stream is None or self._audio_content_name is None:
            raise RuntimeError("Audio input has not been started")

        if isinstance(frame, bytes):
            frame = AudioFrame(pcm=frame, sample_rate_hz=16000, channels=1)
        payload = build_audio_input_event(
            prompt_name=self._prompt_name,
            content_name=self._audio_content_name,
            pcm=frame.pcm,
        )
        encoded = payload["event"]["audioInput"]["content"]
        json_bytes = len(dumps_event_payload(payload))
        logger.debug(
            "audio_input_serialized prompt_name_matches=%s content_name_matches=%s raw_pcm_bytes=%s base64_characters=%s content_is_string=%s content_has_bytes_prefix=%s json_bytes=%s",
            payload["event"]["audioInput"]["promptName"] == self._prompt_name,
            payload["event"]["audioInput"]["contentName"] == self._audio_content_name,
            len(frame.pcm),
            len(encoded),
            isinstance(encoded, str),
            isinstance(encoded, str) and encoded.startswith("b'"),
            json_bytes,
        )
        try:
            base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise RuntimeError(f"invalid base64 audio payload: {exc}") from exc
        self._record_input_stage("audio_input_sent")
        await send_payload(self._stream, payload)
        self._audio_input_frame_count += 1
        self._audio_input_bytes_sent += len(frame.pcm)
        self._log_audio_input_flow_if_due()

    async def send_reply(self, reply: AssistantReply) -> None:
        if not self._started or self._stream is None:
            raise RuntimeError("NovaSonicRuntime has not been started")
        self._input_gate.next_listening_state = self._listening_state_for_action(reply.action)
        logger.info(
            "assistant_reply_authorize_started voice_session_id=%s turn_id=%s question_id=%s response_id=%s action=%s monotonic_timestamp_ms=%s",
            self._context.voice_session_id if self._context else None,
            reply.turn_id,
            reply.question_id,
            reply.response_id,
            reply.action,
            int(monotonic() * 1000),
        )
        authorized = self._response_controller.authorize(
            reply,
            sent_at_ms=self._elapsed_ms() or 0,
        )
        if authorized is None:
            raise RuntimeError("evaluation reply authorization failed")
        logger.info(
            "assistant_reply_authorize_succeeded voice_session_id=%s turn_id=%s question_id=%s response_id=%s generation=%s monotonic_timestamp_ms=%s",
            self._context.voice_session_id if self._context else None,
            reply.turn_id,
            reply.question_id,
            reply.response_id,
            authorized.generation,
            int(monotonic() * 1000),
        )
        self._set_input_state(
            InputState.ANSWER_PROCESSING,
            turn_id=reply.turn_id,
            playback_generation_id=authorized.generation,
            reason="assistant_reply_authorized",
        )
        self._observed_output.response_authorization_state = self._response_controller.authorization_state.value
        self._observed_output.approved_reply_sent = True
        self._observed_output.approved_reply_sent_at_ms = self._elapsed_ms()
        self._observed_output.planned_reply_text = reply.text
        self._observed_output.planned_reply_length = len(reply.text)
        content_name = self._next_content_name("user-text")
        sequence = build_user_text_sequence(
            prompt_name=self._prompt_name,
            content_name=content_name,
            text=reply.text,
        )
        logger.info(
            "assistant_reply_sequence_send_started voice_session_id=%s turn_id=%s question_id=%s response_id=%s monotonic_timestamp_ms=%s",
            self._context.voice_session_id if self._context else None,
            reply.turn_id,
            reply.question_id,
            reply.response_id,
            int(monotonic() * 1000),
        )
        try:
            await self._send_sequence(sequence)
        except Exception:
            logger.exception(
                "assistant_reply_sequence_send_failed voice_session_id=%s turn_id=%s question_id=%s response_id=%s",
                self._context.voice_session_id if self._context else None,
                reply.turn_id,
                reply.question_id,
                reply.response_id,
            )
            raise
        logger.info(
            "assistant_reply_sequence_send_completed voice_session_id=%s turn_id=%s question_id=%s response_id=%s monotonic_timestamp_ms=%s",
            self._context.voice_session_id if self._context else None,
            reply.turn_id,
            reply.question_id,
            reply.response_id,
            int(monotonic() * 1000),
        )
        self._schedule_reply_completion_start_watchdog(reply.response_id)

    async def start_initial_reply(
        self,
        *,
        reply_text: str,
        question_id: str | None,
    ) -> None:
        if not self._started or self._stream is None:
            raise RuntimeError("NovaSonicRuntime has not been started")
        self._pending_initial_reply_text = reply_text
        self._pending_initial_question_id = question_id
        self._pending_initial_mark_sent_on_completion = False
        self._input_gate.next_listening_state = InputState.ANSWER_LISTENING
        self._set_input_state(
            InputState.ANSWER_PROCESSING,
            playback_generation_id=self.current_generation,
            reason="initial_reply_started",
        )
        self._observed_output.planned_reply_text = reply_text
        self._observed_output.planned_reply_length = len(reply_text)
        await self._send_initial_control_sequence()

    async def queue_initial_followup_reply(
        self,
        *,
        reply_text: str,
        question_id: str | None,
    ) -> None:
        if not self._started or self._stream is None:
            raise RuntimeError("NovaSonicRuntime has not been started")
        self._queued_initial_followup_reply_text = reply_text
        self._queued_initial_followup_question_id = question_id
        if self._pending_initial_reply_text is None and self._initial_tool_completion_id is None:
            await self._start_queued_initial_followup_reply()

    async def _start_initial_followup_after_gap(self) -> None:
        await asyncio.sleep(self._config.initial_followup_gap_ms / 1000)
        self._initial_followup_ready = False
        await self._start_queued_initial_followup_reply()

    async def notify_assistant_playback_started(
        self,
        *,
        response_id: str | None,
        generation: int | None,
    ) -> None:
        self._set_input_state(
            InputState.ASSISTANT_SPEAKING,
            playback_generation_id=generation,
            reason="browser_assistant_playback_started",
        )
        logger.info(
            "assistant_playback_started voice_session_id=%s turn_id=%s playback_generation_id=%s input_state=%s monotonic_timestamp_ms=%s response_id=%s",
            self._context.voice_session_id if self._context else None,
            None,
            generation,
            self._input_gate.input_state.value,
            int(monotonic() * 1000),
            response_id,
        )
        if response_id is not None:
            pending = self._evaluation_reply_metrics_by_response_id.get(response_id)
            if pending is not None:
                playback_started_at_ms = int(monotonic() * 1000)
                preface_to_ready_ms = _delta_ms(
                    pending.confirmation_preface_output_complete_at_ms,
                    pending.evaluation_reply_ready_at_ms,
                )
                ready_to_send_started_ms = _delta_ms(
                    pending.evaluation_reply_ready_at_ms,
                    pending.evaluation_reply_send_started_at_ms,
                )
                send_started_to_playback_ms = _delta_ms(
                    pending.evaluation_reply_send_started_at_ms,
                    playback_started_at_ms,
                )
                preface_to_playback_ms = _delta_ms(
                    pending.confirmation_preface_output_complete_at_ms,
                    playback_started_at_ms,
                )
                logger.info(
                    "evaluation_reply_playback_started voice_session_id=%s turn_id=%s question_id=%s playback_generation_id=%s input_state=%s monotonic_timestamp_ms=%s response_id=%s preface_output_complete_to_evaluation_ready_ms=%s evaluation_ready_to_send_started_ms=%s send_started_to_playback_started_ms=%s preface_output_complete_to_playback_started_ms=%s sla_target_ms=%s sla_overrun=%s",
                    self._context.voice_session_id if self._context else None,
                    pending.trace.turn_id if pending.trace is not None else None,
                    self._voice_session_state.current_question_id if self._voice_session_state else None,
                    generation,
                    self._input_gate.input_state.value,
                    playback_started_at_ms,
                    response_id,
                    preface_to_ready_ms,
                    ready_to_send_started_ms,
                    send_started_to_playback_ms,
                    preface_to_playback_ms,
                    2000,
                    bool(preface_to_playback_ms is not None and preface_to_playback_ms > 2000),
                )

    async def notify_assistant_playback_drained(
        self,
        *,
        response_id: str | None,
        generation: int | None,
    ) -> None:
        logger.info(
            "assistant_playback_drained voice_session_id=%s turn_id=%s playback_generation_id=%s input_state=%s monotonic_timestamp_ms=%s response_id=%s",
            self._context.voice_session_id if self._context else None,
            None,
            generation,
            self._input_gate.input_state.value,
            int(monotonic() * 1000),
            response_id,
        )
        if await self._evaluation_turn_coordinator.notify_local_preface_playback_drained(
            response_id=response_id,
            generation=generation,
        ):
            return
        if self._initial_followup_ready and self._queued_initial_followup_reply_text:
            if self._initial_followup_task is not None and not self._initial_followup_task.done():
                self._initial_followup_task.cancel()
            self._initial_followup_task = asyncio.create_task(
                self._start_initial_followup_after_gap()
            )
            return
        if self._response_controller.authorization_state != ResponseAuthorizationState.BLOCKED:
            return
        if self._has_pending_turn_cycle():
            return
        await self._reopen_input_gate_after_guard(generation=generation)

    async def interrupt(self) -> None:
        if not self._started:
            raise RuntimeError("NovaSonicRuntime has not been started")
        interrupted = self._response_controller.interrupt()
        if interrupted is not None:
            event = AssistantInterrupted(
                response_id=interrupted.response_id,
                generation=interrupted.generation,
            )
            await self._event_queue.put(event)
            await self._assistant_event_recorder.record(
                "assistant_interrupted",
                response_id=event.response_id,
                generation=event.generation,
                transcript=None,
                detail={},
            )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._session_context.runtime_open = False
        self._session_context.audio_input_open = False
        self._started = False

        if self._grace_period_task is not None:
            self._grace_period_task.cancel()
            self._grace_period_task = None
        await self._input_gate.close()
        if self._initial_followup_task is not None:
            self._initial_followup_task.cancel()
            self._initial_followup_task = None
        for task in self._reply_completion_start_watchdogs.values():
            task.cancel()
        self._reply_completion_start_watchdogs.clear()
        pending_turns_to_cancel = self._pending_turn_store.active_turns()
        await self._evaluation_turn_coordinator.close(pending_turns_to_cancel)
        self._pending_turn_store.cancel_tasks()

        if self._stream is not None:
            try:
                await self._send_shutdown_events()
            except Exception as exc:
                logger.debug("shutdown_event_send_failed: %s", exc)
            try:
                await self._wait_for_session_protocol_complete(timeout_seconds=5.0)
            except Exception as exc:
                logger.debug("session_protocol_wait_failed: %s", exc)

        if self._receive_task is not None:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug("receive_task_close_failed: %s", exc)
            self._receive_task = None

        if self._stream is not None:
            try:
                await self._stream.input_stream.close()
            except Exception as exc:
                logger.debug("input_stream_close_failed: %s", exc)
            try:
                if getattr(self._stream, "output_stream", None) is not None:
                    await self._stream.output_stream.close()
            except Exception as exc:
                logger.debug("output_stream_close_failed: %s", exc)
            try:
                await self._stream.close()
            except asyncio.CancelledError:
                logger.debug("sdk_stream_close_cancelled")
            except Exception as exc:
                logger.debug("sdk_stream_close_failed: %s", exc)
            self._stream = None

        await self._event_queue.put(RuntimeClosed())
        await self._event_queue.put(None)

    async def _send_initial_events(self) -> None:
        await self._send_sequence(
            build_runtime_start_sequence(
                prompt_name=self._prompt_name,
                system_content_name=self._system_content_name,
                system_prompt=self._config.system_prompt,
                endpointing_sensitivity=self._config.endpointing_sensitivity,
                voice_id=self._config.voice_id,
                forced_tool_name=self._config.forced_tool_name if self._config.enable_forced_tool_use else None,
            )
        )

    async def _send_initial_control_sequence(self) -> None:
        content_name = self._next_content_name("initial-control")
        sequence = build_user_text_sequence(
            prompt_name=self._prompt_name,
            content_name=content_name,
            text=self._config.initial_tool_control_text,
        )
        await self._send_sequence(sequence)

    async def _start_queued_initial_followup_reply(self) -> None:
        reply_text = (self._queued_initial_followup_reply_text or "").strip()
        if not reply_text:
            return
        self._pending_initial_reply_text = reply_text
        self._pending_initial_question_id = self._queued_initial_followup_question_id
        self._pending_initial_mark_sent_on_completion = True
        self._queued_initial_followup_reply_text = None
        self._queued_initial_followup_question_id = None
        self._observed_output.planned_reply_text = reply_text
        self._observed_output.planned_reply_length = len(reply_text)
        await self._send_initial_control_sequence()

    async def _send_sequence(self, sequence: list[tuple[str, dict[str, Any]]]) -> None:
        await self._stream_writer.send_sequence(sequence)

    async def _cleanup_after_failed_start(self) -> None:
        self._started = False
        if self._receive_task is not None:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except Exception:
                pass
            self._receive_task = None
        if self._stream is not None:
            try:
                await self._stream.input_stream.close()
            except Exception as exc:
                logger.debug("failed_start_input_close_failed: %s", exc)
            try:
                if getattr(self._stream, "output_stream", None) is not None:
                    await self._stream.output_stream.close()
            except Exception as exc:
                logger.debug("failed_start_output_close_failed: %s", exc)
            try:
                await self._stream.close()
            except Exception as exc:
                logger.debug("failed_start_stream_close_failed: %s", exc)
            self._stream = None

    async def _send_shutdown_events(self) -> None:
        if self._stream is None:
            return
        if self._shutdown_events_sent:
            return
        if self._audio_content_name is not None:
            await self._send_sequence(
                build_audio_end_sequence(
                    prompt_name=self._prompt_name,
                    content_name=self._audio_content_name,
                )
            )
            if self._observed_output.audio_content_end_sent_at_ms is None:
                self._observed_output.audio_content_end_sent_at_ms = self._elapsed_ms()
            self._audio_content_name = None
        await send_payload(self._stream, build_prompt_end_event(self._prompt_name))
        self._record_input_stage("prompt_end_sent")
        if self._observed_output.prompt_end_sent_at_ms is None:
            self._observed_output.prompt_end_sent_at_ms = self._elapsed_ms()
        await send_payload(self._stream, build_session_end_event())
        self._record_input_stage("session_end_sent")
        if self._observed_output.session_end_sent_at_ms is None:
            self._observed_output.session_end_sent_at_ms = self._elapsed_ms()
        self._shutdown_events_sent = True

    async def send_shutdown_probe_events(self) -> None:
        await self._send_shutdown_events()

    async def wait_for_session_protocol_complete(self, timeout_seconds: float = 5.0) -> None:
        await self._wait_for_session_protocol_complete(timeout_seconds=timeout_seconds)

    async def _receive_output_loop(self) -> None:
        if self._stream is None:
            return
        try:
            await asyncio.wait_for(
                self._stream.await_output(),
                timeout=self._config.await_output_timeout_seconds,
            )
            output_stream = self._stream.output_stream
            if output_stream is None:
                await self._protocol_dispatcher.emit_runtime_error(
                    "nova_sonic_missing_output_stream",
                    "output stream was not available",
                )
                return
            async for event in output_stream:
                await self._protocol_dispatcher.handle_stream_event(event)
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            await self._protocol_dispatcher.emit_runtime_error(
                "nova_sonic_await_output_timeout",
                f"{exc.__class__.__name__}: {str(exc) or '<empty>'}",
            )
        except Exception as exc:
            await self._protocol_dispatcher.emit_runtime_error(
                "nova_sonic_stream_receive_failed",
                f"{exc.__class__.__name__}: {str(exc) or '<empty>'}",
            )

    def _reset_state(self, context: VoiceRuntimeContext) -> None:
        self._session_context.reset(context)
        self._closed = False
        self._stream_writer.reset(prompt_name=f"prompt-{context.voice_session_id}")
        self._receive_task = None
        self._completion_registry.reset()
        self._pending_turn_store.clear()
        self._evaluation_reply_metrics_by_response_id.clear()
        self._approved_response_store.clear()
        self._observability.reset()
        self._observed_output = self._observability.output
        self._started_at_monotonic = monotonic()
        self._prompt_name = f"prompt-{context.voice_session_id}"
        self._system_content_name = f"system-{context.voice_session_id}"
        self._content_counter = 0
        self._audio_content_name = None
        self._shutdown_events_sent = False
        self._tool_result_counter = 0
        self._processed_tool_use_keys.clear()
        self._close_after_current_completion = False
        self._reply_completion_start_watchdogs.clear()
        self._turn_index = 0
        self._last_user_speech_started_at = None
        self._last_user_speech_ended_at = None
        self._pending_initial_reply_text = None
        self._pending_initial_question_id = None
        self._pending_initial_mark_sent_on_completion = False
        self._initial_tool_completion_id = None
        self._initial_reply_marked_sent = False
        self._queued_initial_followup_reply_text = None
        self._queued_initial_followup_question_id = None
        self._initial_followup_ready = False
        self._input_gate.reset()
        self._voice_session_state = VoiceSessionRuntimeState(
            voice_session_id=context.voice_session_id,
            record_id=context.record_id,
        )

    def mark_completion_wait_silence_frame(self) -> None:
        self._observed_output.silence_continued_during_completion_wait = True
        self._observed_output.silence_frames_during_completion_wait += 1

    def _next_content_name(self, prefix: str) -> str:
        return self._stream_writer.next_content_name(prefix)

    def _record_input_stage(self, stage_name: str) -> None:
        self._observed_output.last_input_event = stage_name

    async def _load_voice_session_state(self) -> None:
        if self._interview_bridge is None or self._context is None:
            return
        snapshot = await self._interview_bridge.load_voice_session(self._context.voice_session_id)
        self._voice_session_state = VoiceSessionRuntimeState(
            voice_session_id=snapshot.voice_session_id,
            record_id=snapshot.record_id,
            owner_user_id=snapshot.owner_user_id,
            current_question_id=snapshot.current_question_id,
            state_version=snapshot.state_version,
            interview_status=snapshot.interview_status,
        )

    def _set_failed_stage(self, stage: str) -> None:
        self._observability.set_failed_stage(stage)

    def _log_audio_input_flow_if_due(self) -> None:
        now = monotonic()
        if now - self._last_audio_input_flow_log_at < 2.0:
            return
        self._last_audio_input_flow_log_at = now
        logger.info(
            "runtime_audio_input_sent voice_session_id=%s frames_sent=%s bytes_sent=%s audio_input_open=%s runtime_open=%s",
            self._context.voice_session_id if self._context else None,
            self._audio_input_frame_count,
            self._audio_input_bytes_sent,
            self._audio_content_name is not None,
            self._stream is not None and not self._closed,
        )

    def mark_completion_wait_timeout(self) -> None:
        self._observed_output.completion_wait_timeout = True

    async def apply_output_complete_grace_period(self, grace_seconds: float) -> None:
        await self._completion_lifecycle.apply_output_complete_grace_period(grace_seconds)

    def _schedule_reply_completion_start_watchdog(self, response_id: str) -> None:
        existing = self._reply_completion_start_watchdogs.pop(response_id, None)
        if existing is not None:
            existing.cancel()
        self._reply_completion_start_watchdogs[response_id] = asyncio.create_task(
            self._reply_completion_start_watchdog(response_id)
        )

    async def _reply_completion_start_watchdog(self, response_id: str) -> None:
        try:
            await asyncio.sleep(self._config.reply_completion_start_timeout_seconds)
            if self._response_controller.active_response_id != response_id:
                return
            if self._response_controller.active_completion_id is not None:
                return
            logger.warning(
                "assistant_reply_completion_start_timeout voice_session_id=%s response_id=%s generation=%s timeout_seconds=%s",
                self._context.voice_session_id if self._context else None,
                response_id,
                self._response_controller.generation,
                self._config.reply_completion_start_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        finally:
            self._reply_completion_start_watchdogs.pop(response_id, None)

    def _cancel_reply_completion_start_watchdog(self, response_id: str | None) -> None:
        if response_id is None:
            return
        task = self._reply_completion_start_watchdogs.pop(response_id, None)
        if task is not None:
            task.cancel()

    async def _maybe_mark_initial_reply_sent(self, completion_id: str) -> None:
        if completion_id != self._initial_tool_completion_id:
            return
        if self._initial_reply_marked_sent:
            return
        if not self._pending_initial_mark_sent_on_completion:
            self._pending_initial_reply_text = None
            self._pending_initial_question_id = None
            self._pending_initial_mark_sent_on_completion = False
            self._initial_tool_completion_id = None
            if self._queued_initial_followup_reply_text:
                self._initial_followup_ready = True
            return
        logger.info(
            "voice_initial_reply_output_complete voice_session_id=%s initial_question_id=%s initial_reply_status=%s completion_id=%s response_id=%s generation=%s",
            self._context.voice_session_id if self._context else None,
            self._pending_initial_question_id,
            "sending",
            completion_id,
            f"initial-response-{self._context.voice_session_id}" if self._context else None,
            self.current_generation,
        )
        if self._interview_bridge is None or self._context is None:
            return
        try:
            await self._interview_bridge.mark_initial_reply_sent(self._context.voice_session_id)
            self._initial_reply_marked_sent = True
            logger.info(
                "voice_initial_reply_marked_sent voice_session_id=%s initial_question_id=%s initial_reply_status=%s completion_id=%s response_id=%s generation=%s",
                self._context.voice_session_id,
                self._pending_initial_question_id,
                "sent",
                completion_id,
                f"initial-response-{self._context.voice_session_id}",
                self.current_generation,
            )
            self._pending_initial_reply_text = None
            self._pending_initial_question_id = None
            self._pending_initial_mark_sent_on_completion = False
            self._initial_tool_completion_id = None
        except Exception as exc:
            logger.debug("initial_reply_mark_sent_failed: %s", exc)


    def _elapsed_ms(self) -> int | None:
        return self._observability.elapsed_ms()

    def _now_ms(self) -> int:
        return self._observability.now_ms()


    async def _wait_for_session_protocol_complete(self, timeout_seconds: float) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if self._observed_output.completion_end_received or self._observed_output.explicit_stream_error:
                break
            await asyncio.sleep(0.1)
        self._observed_output.session_protocol_complete = self._observed_output.completion_end_received
        if not self._observed_output.completion_end_received and not self._observed_output.explicit_stream_error:
            self._observed_output.session_close_degraded = True
    async def _iter_events(self) -> AsyncIterator[VoiceRuntimeEvent]:
        while True:
            event = await self._event_queue.get()
            if event is None:
                break
            yield event

    def events(self) -> AsyncIterator[VoiceRuntimeEvent]:
        return self._iter_events()

def _delta_ms(start: int | None, end: int | None) -> int | None:
    if start is None or end is None:
        return None
    return max(0, end - start)
