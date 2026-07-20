"""
Role:
    Nova Sonicプロトコルイベントの分配とRuntimeイベント変換。

Summary:
    Bedrockストリーム出力をNovaイベントへデコードし、completion/content状態の更新、
    承認済み音声の通過制御、follow-up reply完了処理を担当する。

Relations:
    Uses protocol events, event_mapper, session_state, response_controller state.
    Used by nova_sonic.runtime as the protocol event dispatch path.
"""

from __future__ import annotations

import logging
from time import monotonic
from typing import Any, Protocol

from aws_sdk_bedrock_runtime.models import (
    InvokeModelWithBidirectionalStreamOutputChunk,
    InvokeModelWithBidirectionalStreamOutputModelStreamErrorException,
    InvokeModelWithBidirectionalStreamOutputModelTimeoutException,
    InvokeModelWithBidirectionalStreamOutputServiceUnavailableException,
    InvokeModelWithBidirectionalStreamOutputThrottlingException,
    InvokeModelWithBidirectionalStreamOutputValidationException,
)

from ai_interviewer_voice.runtimes.nova_sonic.event_mapper import map_protocol_event
from ai_interviewer_voice.runtimes.nova_sonic.completion_lifecycle import CompletionLifecycle
from ai_interviewer_voice.runtimes.nova_sonic.completion_registry import CompletionRegistry
from ai_interviewer_voice.runtimes.nova_sonic.config import NovaSonicRuntimeConfig
from ai_interviewer_voice.runtimes.nova_sonic.input_gate import InputGateController
from ai_interviewer_voice.runtimes.nova_sonic.pending_turn_store import PendingTurnStore
from ai_interviewer_voice.runtimes.nova_sonic.response_controller import ResponseAuthorizationState
from ai_interviewer_voice.runtimes.nova_sonic.runtime_ports import (
    ApprovedResponseStore,
    AssistantEventRecorderPort,
    NovaObservability,
    ProtocolSessionContext,
    RuntimeEventSink,
)
from ai_interviewer_voice.runtimes.nova_sonic.protocol.events import (
    AudioOutputEvent,
    CompletionEndEvent,
    CompletionStartEvent,
    ContentEndEvent,
    ContentStartEvent,
    ErrorEvent,
    TextOutputEvent,
    ToolResultEvent,
    ToolUseEvent,
    UnknownEvent,
    UsageEvent,
    UserSpeechEndEvent,
    UserSpeechStartEvent,
    decode_output_bytes,
)
from ai_interviewer_voice.runtimes.nova_sonic.session_state import CompletionStatus, ContentState, InputState, InterviewTurnKind, PendingToolCall
from ai_interviewer_voice.schemas.events import (
    AssistantAudioChunk,
    AssistantSpeechEnded,
    AssistantSpeechStarted,
    AssistantTranscriptFinal,
    UserSpeechEnded,
    UserSpeechStarted,
    UserTranscriptFinal,
    VoiceRuntimeEvent,
    RuntimeError as RuntimeErrorEvent,
)


logger = logging.getLogger(__name__)


class AssistantResponsePort(Protocol):
    @property
    def generation(self) -> int: ...

    @property
    def authorization_state(self) -> ResponseAuthorizationState: ...

    @property
    def active_response_id(self) -> str | None: ...

    @property
    def active_completion_id(self) -> str | None: ...

    @property
    def pending_reply(self) -> Any: ...

    def bind_completion(self, *, completion_id: str, completion_started_at_ms: int) -> bool: ...

    def on_user_speech_started(self) -> None: ...

    def on_user_transcript_final(self) -> None: ...

    def accepts_audio_chunk(self, event: AssistantAudioChunk) -> bool: ...

    def accepts_transcript(
        self,
        event: AssistantTranscriptFinal,
        *,
        completion_id: str | None,
    ) -> bool: ...

    def accepts_speech_started(
        self,
        event: AssistantSpeechStarted,
        *,
        completion_id: str | None,
    ) -> bool: ...

    def accepts_speech_ended(
        self,
        event: AssistantSpeechEnded,
        *,
        completion_id: str | None,
    ) -> bool: ...


class ProtocolTurnSessionContext(ProtocolSessionContext, Protocol):
    turn_index: int
    last_user_speech_started_at: int | None
    last_user_speech_ended_at: int | None
    pending_initial_reply_text: str | None
    pending_initial_question_id: str | None
    initial_tool_completion_id: str | None
    runtime_open: bool
    audio_input_open: bool

    def next_turn(self) -> int: ...


class TurnCoordinatorPort(Protocol):
    async def maybe_process_pending_turn(self, completion_id: str) -> None: ...


class ProtocolEventDispatcher:
    def __init__(
        self,
        *,
        config: NovaSonicRuntimeConfig,
        completion_registry: CompletionRegistry,
        completion_lifecycle: CompletionLifecycle,
        response_controller: AssistantResponsePort,
        turn_coordinator: TurnCoordinatorPort,
        input_gate: InputGateController,
        event_sink: RuntimeEventSink,
        session_context: ProtocolTurnSessionContext,
        assistant_event_recorder: AssistantEventRecorderPort,
        observability: NovaObservability,
        pending_turn_store: PendingTurnStore,
        approved_responses: ApprovedResponseStore,
        evaluation_reply_metrics: dict[str, PendingToolCall],
    ) -> None:
        self._config = config
        self._completion_registry = completion_registry
        self._completion_lifecycle = completion_lifecycle
        self._response_controller = response_controller
        self._turn_coordinator = turn_coordinator
        self._input_gate = input_gate
        self._event_sink = event_sink
        self._session = session_context
        self._assistant_event_recorder = assistant_event_recorder
        self._observability = observability
        self._pending_turn_store = pending_turn_store
        self._approved_responses = approved_responses
        self._evaluation_reply_metrics = evaluation_reply_metrics

    def configure(self, config: NovaSonicRuntimeConfig) -> None:
        self._config = config

    async def handle_stream_event(self, event: Any) -> None:
        if isinstance(event, InvokeModelWithBidirectionalStreamOutputChunk):
            protocol_event = decode_output_bytes(
                event.value.bytes_ or b"",
                active_content_id=self._completion_registry.active_output_content_id,
            )
            await self.handle_protocol_event(protocol_event)
            return

        if isinstance(event, InvokeModelWithBidirectionalStreamOutputValidationException):
            await self.emit_runtime_error("nova_sonic_validation_error", str(event.value.message or "validation error"))
            return
        if isinstance(event, InvokeModelWithBidirectionalStreamOutputThrottlingException):
            await self.emit_runtime_error("nova_sonic_throttling", str(event.value.message or "throttled"))
            return
        if isinstance(event, InvokeModelWithBidirectionalStreamOutputModelTimeoutException):
            await self.emit_runtime_error("nova_sonic_timeout", str(event.value.message or "model timeout"))
            return
        if isinstance(event, InvokeModelWithBidirectionalStreamOutputServiceUnavailableException):
            await self.emit_runtime_error("nova_sonic_service_unavailable", str(event.value.message or "service unavailable"))
            return
        if isinstance(event, InvokeModelWithBidirectionalStreamOutputModelStreamErrorException):
            await self.emit_runtime_error("nova_sonic_model_stream_error", str(event.value.message or "stream error"))
            return

    async def handle_protocol_event(self, event: Any) -> None:
        self._observability.output.last_event_type = event.event_type
        self._observability.output.last_received_event = event.event_type
        self._observability.output.last_content_id = getattr(event, "content_id", None)
        self._observability.output.received_event_types.append(event.event_type)
        logger.info(
            "output_event_received event_type=%s content_id=%s",
            event.event_type,
            getattr(event, "content_id", None),
        )
        if isinstance(event, AudioOutputEvent):
            audio_bytes = event.audio_bytes
            if not audio_bytes or len(audio_bytes) % 2 != 0:
                logger.warning(
                    "assistant_audio_payload_invalid voice_session_id=%s completion_id=%s content_id=%s payload_bytes=%s",
                    self._session.voice_session_id,
                    event.completion_id,
                    event.content_id,
                    len(audio_bytes),
                )
                return

        if isinstance(event, CompletionStartEvent):
            logger.info(
                "nova_completion_started completion_id=%s elapsed_ms=%s",
                event.completion_id,
                self._observability.elapsed_ms(),
            )
            self._observability.output.completion_start_received = True
            self._observability.output.completion_start_ms = self._observability.elapsed_ms()
            if event.completion_id is not None:
                self._completion_registry.set_active_completion_id(event.completion_id)
                completion_state = self._completion_registry.resolve_completion_state(event.completion_id)
                assert completion_state is not None
                completion_state.started_at_ms = completion_state.started_at_ms or self._observability.elapsed_ms()
                completion_state.started_at_ms = completion_state.started_at_ms or self._observability.elapsed_ms()
                if self._response_controller.bind_completion(
                    completion_id=event.completion_id,
                    completion_started_at_ms=self._observability.elapsed_ms() or 0,
                ):
                    pending = self._response_controller.pending_reply
                    next_response_id = self._response_controller.active_response_id
                    next_generation = self._response_controller.generation
                    completion_state = self._completion_registry.bind_completion_response(
                        completion_id=event.completion_id,
                        response_id=next_response_id,
                        generation=next_generation,
                        started_at_ms=completion_state.started_at_ms,
                    )
                    completion_state.authorized = True
                    completion_state.planned_reply_text = pending.text if pending is not None else None
                    self._completion_lifecycle.cancel_completion_start_watchdog(next_response_id)
                    self._observability.output.response_authorization_state = self._response_controller.authorization_state.value
                    self._observability.output.approved_completion_started = True
                    self._observability.output.approved_completion_id = event.completion_id
                else:
                    completion_state.authorized = False
                    completion_state.response_id = None
                    completion_state.generation = None
                    self._observability.output.spontaneous_completion_started = True
                    self._observability.output.unauthorized_completion_count += 1
        elif isinstance(event, ContentStartEvent):
            self._completion_registry.set_active_output_content_id(
                event.content_id or self._completion_registry.active_output_content_id
            )
            completion_id = event.completion_id or self._completion_registry.active_completion_id
            if event.content_id is not None:
                self._completion_registry.bind_content(
                    content_id=event.content_id,
                    completion_id=completion_id,
                    role=event.role,
                    content_type=event.modality,
                    generation_stage=event.generation_stage,
                )
            if event.role == "ASSISTANT":
                self._observability.output.assistant_content_start_received = True
                if event.modality == "AUDIO" and self._observability.output.assistant_audio_start_ms is None:
                    self._observability.output.assistant_audio_start_ms = self._observability.elapsed_ms()
                if (
                    event.modality == "TEXT"
                    and event.generation_stage == "FINAL"
                    and self._observability.output.assistant_final_text_start_ms is None
                ):
                    self._observability.output.assistant_final_text_start_ms = self._observability.elapsed_ms()
        elif isinstance(event, TextOutputEvent):
            self._observability.output.text_output_received = True
            content_id = (
                event.content_id
                or self._completion_registry.active_output_content_id
                or "unknown"
            )
            content_state = self._completion_registry.resolve_content_state(content_id)
            role = (
                content_state.role
                if content_state is not None
                else self._completion_registry.get_content_role(content_id)
            )
            completion_state = self._completion_registry.resolve_completion_state(content_state.completion_id if content_state is not None else event.completion_id)
            if role == "USER":
                if self._session.pending_initial_reply_text is not None and self._session.initial_tool_completion_id is None:
                    logger.info(
                        "initial_control_user_text_suppressed completion_id=%s content_id=%s text_length=%s elapsed_ms=%s",
                        completion_state.completion_id if completion_state is not None else event.completion_id,
                        content_id,
                        len(event.text),
                        self._observability.elapsed_ms(),
                    )
                    return
                logger.info(
                    "nova_user_transcript_final completion_id=%s content_id=%s text_length=%s elapsed_ms=%s",
                    completion_state.completion_id if completion_state is not None else event.completion_id,
                    content_id,
                    len(event.text),
                    self._observability.elapsed_ms(),
                )
                self._observability.output.user_transcript_received = True
                self._observability.output.user_transcript_text_length += len(event.text)
                if completion_state is not None:
                    completion_state.user_transcript_received = True
                if self._input_gate.input_state not in {
                    InputState.ANSWER_LISTENING,
                    InputState.CONFIRMATION_LISTENING,
                }:
                    logger.info(
                        "transcript_ignored_while_gate_closed voice_session_id=%s turn_id=%s playback_generation_id=%s input_state=%s monotonic_timestamp_ms=%s text_length=%s completion_id=%s",
                        self._session.voice_session_id,
                        None,
                        self._response_controller.generation,
                        self._input_gate.input_state.value,
                        int(monotonic() * 1000),
                        len(event.text),
                        completion_state.completion_id if completion_state is not None else event.completion_id,
                    )
                    if self._input_gate.input_state is InputState.ASSISTANT_SPEAKING:
                        logger.info(
                            "transcript_overlap_with_assistant_audio voice_session_id=%s turn_id=%s playback_generation_id=%s input_state=%s monotonic_timestamp_ms=%s text_length=%s completion_id=%s",
                            self._session.voice_session_id,
                            None,
                            self._response_controller.generation,
                            self._input_gate.input_state.value,
                            int(monotonic() * 1000),
                            len(event.text),
                            completion_state.completion_id if completion_state is not None else event.completion_id,
                        )
                    return
                self._session.next_turn()
                logger.info(
                    "voice_user_transcript_final voice_session_id=%s turn_index=%s current_question_id=%s state_version=%s completion_id=%s text_length=%s authorization_state=%s retrieval_policy=%s retrieval_executed=%s",
                    self._session.voice_session_id,
                    self._session.turn_index,
                    self._session.current_question_id,
                    self._session.state_version,
                    completion_state.completion_id if completion_state is not None else event.completion_id,
                    len(event.text),
                    self._response_controller.authorization_state.value,
                    "unknown",
                    False,
                )
                self._input_gate.set_state(
                    InputState.ANSWER_PROCESSING,
                    playback_generation_id=self._response_controller.generation,
                    reason="user_transcript_final_accepted",
                )
                await self._event_sink.emit(UserTranscriptFinal(text=event.text))
                self._response_controller.on_user_transcript_final()
                self._observability.output.response_authorization_state = self._response_controller.authorization_state.value
                if completion_state is not None:
                    pending_key = completion_state.completion_id or "unknown"
                    pending_turn = self._pending_turn_store.get(pending_key)
                    if pending_turn is None or pending_turn.result_sent:
                        pending_turn = PendingToolCall(completion_id=pending_key)
                        self._pending_turn_store.put(pending_turn)
                    trace = self._observability.ensure_trace(pending_turn, turn_index=self._session.turn_index)
                    trace.turn_index = self._session.turn_index
                    trace.user_speech_started_at = self._session.last_user_speech_started_at
                    trace.user_speech_ended_at = self._session.last_user_speech_ended_at
                    trace.user_transcript_final_at = self._observability.now_ms()
                    pending_turn.user_transcript = event.text
                    logger.info(
                        "user_turn_created voice_session_id=%s turn_id=%s playback_generation_id=%s input_state=%s monotonic_timestamp_ms=%s completion_id=%s text_length=%s",
                        self._session.voice_session_id,
                        trace.turn_id,
                        self._response_controller.generation,
                        self._input_gate.input_state.value,
                        int(monotonic() * 1000),
                        pending_turn.completion_id,
                        len(event.text),
                    )
                    await self._turn_coordinator.maybe_process_pending_turn(pending_turn.completion_id)
                return
            elif role == "ASSISTANT":
                if completion_state is not None and completion_state.authorized:
                    pending_turn = self._pending_turn_store.get(completion_state.completion_id)
                    if pending_turn is not None:
                        trace = self._observability.ensure_trace(pending_turn, turn_index=self._session.turn_index)
                        if trace.assistant_text_first_received_at is None:
                            trace.assistant_text_first_received_at = self._observability.now_ms()
                        if pending_turn.kind == InterviewTurnKind.INITIAL:
                            logger.info(
                                "voice_initial_assistant_text_received voice_session_id=%s initial_question_id=%s initial_reply_status=%s completion_id=%s tool_use_id=%s response_id=%s generation=%s text_length=%s",
                                self._session.voice_session_id,
                                pending_turn.initial_question_id,
                                "sending",
                                pending_turn.completion_id,
                                pending_turn.tool_use_id,
                                f"initial-response-{self._session.voice_session_id}" if self._session.voice_session_id else None,
                                self._response_controller.generation,
                                len(event.text),
                            )
                    self._observability.output.assistant_text_output_received = True
                    self._observability.output.post_tool_assistant_text_received = True
                    completion_state.spoken_transcript += event.text
                    if event.generation_stage == "SPECULATIVE":
                        completion_state.assistant_speculative_text_received = True
                    else:
                        self._observability.output.assistant_final_text_received = True
                        completion_state.assistant_final_text_received = True
                else:
                    self._observability.output.unauthorized_text_received = True
                    self._observability.output.pre_tool_assistant_text_count += 1
            self._completion_registry.append_transcript(content_id, event.text)
        elif isinstance(event, AudioOutputEvent):
            content_state = self._completion_registry.resolve_content_state(event.content_id)
            completion_state = self._completion_registry.resolve_completion_state(
                content_state.completion_id if content_state is not None else event.completion_id
            )
            if completion_state is not None:
                if completion_state.authorized:
                    pending_turn = self._pending_turn_store.get(completion_state.completion_id)
                    if pending_turn is not None:
                        trace = self._observability.ensure_trace(pending_turn, turn_index=self._session.turn_index)
                        approved_response = self._approved_responses.get(pending_turn.completion_id)
                        if trace.assistant_audio_first_received_at is None:
                            trace.assistant_audio_first_received_at = self._observability.now_ms()
                        logger.info(
                            "voice_assistant_audio_received voice_session_id=%s turn_index=%s question_id=%s state_version=%s completion_id=%s tool_use_id=%s response_id=%s generation=%s bytes=%s retrieval_policy=%s retrieval_executed=%s",
                            self._session.voice_session_id,
                            trace.turn_index,
                            self._session.current_question_id,
                            self._session.state_version,
                            pending_turn.completion_id,
                            pending_turn.tool_use_id,
                            completion_state.response_id,
                            self._response_controller.generation,
                            len(event.audio_bytes),
                            approved_response.retrieval_policy if approved_response is not None else None,
                            approved_response.retrieval_executed if approved_response is not None else False,
                        )
                        if pending_turn.kind == InterviewTurnKind.INITIAL:
                            logger.info(
                                "voice_initial_assistant_audio_received voice_session_id=%s initial_question_id=%s initial_reply_status=%s completion_id=%s tool_use_id=%s response_id=%s generation=%s bytes=%s",
                                self._session.voice_session_id,
                                pending_turn.initial_question_id,
                                "sending",
                                pending_turn.completion_id,
                                pending_turn.tool_use_id,
                                f"initial-response-{self._session.voice_session_id}" if self._session.voice_session_id else None,
                                self._response_controller.generation,
                                len(event.audio_bytes),
                            )
                    evaluation_pending = (
                        self._evaluation_reply_metrics.get(completion_state.response_id or "")
                        if completion_state is not None and completion_state.response_id is not None
                        else None
                    )
                    if (
                        evaluation_pending is not None
                        and not evaluation_pending.evaluation_audio_first_chunk_logged
                    ):
                        evaluation_pending.evaluation_audio_first_chunk_logged = True
                        logger.info(
                            "evaluation_audio_first_chunk_received voice_session_id=%s turn_id=%s question_id=%s playback_generation_id=%s input_state=%s monotonic_timestamp_ms=%s response_id=%s completion_id=%s bytes=%s",
                            self._session.voice_session_id,
                            evaluation_pending.trace.turn_id if evaluation_pending.trace is not None else None,
                            self._session.current_question_id,
                            completion_state.generation,
                            self._input_gate.input_state.value,
                            int(monotonic() * 1000),
                            completion_state.response_id,
                            completion_state.completion_id,
                            len(event.audio_bytes),
                        )
                    self._observability.output.audio_output_chunks += 1
                    self._observability.output.assistant_audio_bytes += len(event.audio_bytes)
                    self._observability.output.post_tool_audio_chunks += 1
                    completion_state.assistant_audio_chunks += 1
                else:
                    self._observability.output.unauthorized_audio_chunks += 1
                    self._observability.output.unauthorized_audio_bytes += len(event.audio_bytes)
                    self._observability.output.pre_tool_audio_chunks += 1
                    self._observability.output.pre_tool_audio_bytes += len(event.audio_bytes)
        elif isinstance(event, ContentEndEvent):
            self._observability.output.content_end_received = True
            content_state = self._completion_registry.resolve_content_state(
                event.content_id or self._completion_registry.active_output_content_id
            )
            completion_state = self._completion_registry.resolve_completion_state(
                content_state.completion_id
                if content_state is not None
                else event.completion_id or self._completion_registry.active_completion_id
            )
            if content_state is not None:
                if content_state.role == "USER" and content_state.content_type == "TEXT":
                    if self._session.pending_initial_reply_text is not None and self._session.initial_tool_completion_id is None:
                        logger.info(
                            "initial_control_user_text_end_suppressed completion_id=%s content_id=%s stop_reason=%s elapsed_ms=%s",
                            content_state.completion_id,
                            content_state.content_id,
                            event.stop_reason,
                            self._observability.elapsed_ms(),
                        )
                        return
                    logger.info(
                        "nova_user_text_content_end completion_id=%s content_id=%s stop_reason=%s elapsed_ms=%s",
                        content_state.completion_id,
                        content_state.content_id,
                        event.stop_reason,
                        self._observability.elapsed_ms(),
                    )
                    self._observability.output.user_final_text_end_ms = self._observability.elapsed_ms()
                    pending_turn = self._pending_turn_store.get_or_create(
                        content_state.completion_id or "unknown"
                    )
                    await self._turn_coordinator.maybe_process_pending_turn(pending_turn.completion_id)
                    return
                elif content_state.content_type == "TOOL":
                    pending_turn = self._pending_turn_store.get_or_create(
                        content_state.completion_id or "unknown"
                    )
                    if pending_turn.tool_content_id == content_state.content_id:
                        pending_turn.tool_content_end_received = True
                        pending_turn.tool_content_stop_reason = event.stop_reason
                        logger.info(
                            "nova_tool_content_end completion_id=%s content_id=%s stop_reason=%s ready_transcript=%s elapsed_ms=%s",
                            pending_turn.completion_id,
                            content_state.content_id,
                            event.stop_reason,
                            pending_turn.user_transcript is not None,
                            self._observability.elapsed_ms(),
                        )
                        self._observability.output.tool_output_content_end_received = True
                        self._observability.output.tool_output_stop_reason = event.stop_reason
                        self._observability.output.tool_content_end_received_at_ms = self._observability.elapsed_ms()
                        trace = self._observability.ensure_trace(pending_turn, turn_index=self._session.turn_index)
                        trace.tool_content_end_received_at = self._observability.now_ms()
                        if pending_turn.kind == InterviewTurnKind.INITIAL:
                            logger.info(
                                "voice_initial_tool_content_end_received voice_session_id=%s initial_question_id=%s initial_reply_status=%s completion_id=%s tool_use_id=%s response_id=%s generation=%s",
                                self._session.voice_session_id,
                                pending_turn.initial_question_id,
                                "sending",
                                pending_turn.completion_id,
                                pending_turn.tool_use_id,
                                f"initial-response-{self._session.voice_session_id}" if self._session.voice_session_id else None,
                                self._response_controller.generation,
                            )
                        await self._turn_coordinator.maybe_process_pending_turn(pending_turn.completion_id)
                    return
                elif content_state.role == "ASSISTANT" and content_state.content_type == "TEXT":
                    if content_state.generation_stage == "SPECULATIVE":
                        self._observability.output.assistant_speculative_text_end_ms = self._observability.elapsed_ms()
                    else:
                        self._observability.output.assistant_final_text_end_ms = self._observability.elapsed_ms()
                        if completion_state is not None:
                            completion_state.assistant_final_text_end_received = True
                elif content_state.role == "ASSISTANT" and content_state.content_type == "AUDIO":
                    self._observability.output.assistant_audio_end_ms = self._observability.elapsed_ms()
                    if completion_state is not None:
                        completion_state.assistant_audio_end_received = True
                elif content_state.role != "ASSISTANT":
                    return
                self._completion_lifecycle.update_completion_status(completion_state)
                await self._completion_lifecycle.maybe_complete_session_after_authorized_output(completion_state)
                if completion_state is not None and completion_state.status == CompletionStatus.OUTPUT_COMPLETE:
                    await self._completion_lifecycle.finalize_authorized_completion_once(
                        completion_state.completion_id,
                        reason="assistant_output_complete",
                    )
        elif isinstance(event, CompletionEndEvent):
            self._observability.output.completion_end_received = True
            self._observability.output.completion_stop_reason = event.stop_reason
            self._observability.output.completion_end_ms = self._observability.elapsed_ms()
            self._observability.output.completion_end_received_at_ms = self._observability.elapsed_ms()
            if (
                self._observability.output.session_end_sent_at_ms is not None
                and self._observability.output.completion_end_received_at_ms is not None
                and self._observability.output.completion_end_received_at_ms >= self._observability.output.session_end_sent_at_ms
            ):
                self._observability.output.completion_end_after_session_end = True
            completion_state = self._completion_registry.resolve_completion_state(
                event.completion_id or self._completion_registry.active_completion_id
            )
            if completion_state is not None:
                completion_state.assistant_audio_end_received = (
                    completion_state.assistant_audio_end_received or completion_state.assistant_audio_chunks > 0
                )
                completion_state.assistant_final_text_end_received = (
                    completion_state.assistant_final_text_end_received or completion_state.assistant_final_text_received
                )
                completion_state.completion_end_received = True
                completion_state.stop_reason = event.stop_reason
                completion_state.status = CompletionStatus.PROTOCOL_COMPLETE
                self._observability.output.completion_status = CompletionStatus.PROTOCOL_COMPLETE.value
                if completion_state.authorized:
                    self._observability.output.approved_output_complete = True
                    self._observability.output.approved_protocol_complete = True
                    self._completion_lifecycle.finalize_planned_vs_spoken(completion_state)
                    await self._completion_lifecycle.maybe_complete_session_after_authorized_output(completion_state)
                    await self._completion_lifecycle.finalize_authorized_completion_once(
                        completion_state.completion_id,
                        reason="completion_end",
                    )
        elif isinstance(event, UsageEvent):
            pass
        elif isinstance(event, UserSpeechStartEvent):
            logger.info("nova_user_speech_start elapsed_ms=%s", self._observability.elapsed_ms())
            logger.info(
                "voice_user_speech_started voice_session_id=%s turn_index=%s current_question_id=%s state_version=%s runtime_open=%s audio_input_open=%s authorization_state=%s",
                self._session.voice_session_id,
                self._session.turn_index + 1,
                self._session.current_question_id,
                self._session.state_version,
                self._session.runtime_open,
                self._session.audio_input_open,
                self._response_controller.authorization_state.value,
            )
            self._observability.output.user_speech_start_ms = self._observability.elapsed_ms()
            self._session.last_user_speech_started_at = self._observability.now_ms()
            if self._config.enable_forced_tool_use and any(
                pending.tool_use_received and not pending.result_sent for pending in self._pending_turn_store.active_turns()
            ):
                self._observability.output.ignored_user_speech_during_tool_wait_count += 1
            if self._input_gate.input_state not in {
                InputState.ANSWER_LISTENING,
                InputState.CONFIRMATION_LISTENING,
            }:
                return
            self._response_controller.on_user_speech_started()
            self._observability.output.response_authorization_state = self._response_controller.authorization_state.value
        elif isinstance(event, UserSpeechEndEvent):
            logger.info("nova_user_speech_end elapsed_ms=%s", self._observability.elapsed_ms())
            self._observability.output.user_speech_end_ms = self._observability.elapsed_ms()
            self._session.last_user_speech_ended_at = self._observability.now_ms()
        elif isinstance(event, ToolUseEvent):
            logger.info(
                "nova_tool_use completion_id=%s content_id=%s tool_name=%s tool_use_id_present=%s elapsed_ms=%s",
                event.completion_id or self._completion_registry.active_completion_id,
                event.content_id,
                event.tool_name,
                event.tool_use_id is not None,
                self._observability.elapsed_ms(),
            )
            logger.info(
                "voice_tool_use_received voice_session_id=%s turn_index=%s current_question_id=%s state_version=%s completion_id=%s tool_use_id=%s authorization_state=%s",
                self._session.voice_session_id,
                self._session.turn_index + 1,
                self._session.current_question_id,
                self._session.state_version,
                event.completion_id or self._completion_registry.active_completion_id,
                event.tool_use_id,
                self._response_controller.authorization_state.value,
            )
            self._observability.output.tool_use_received = True
            self._observability.output.tool_name = event.tool_name
            self._observability.output.tool_use_id_present = event.tool_use_id is not None
            self._observability.output.tool_use_received_at_ms = self._observability.elapsed_ms()
            completion_id = event.completion_id or self._completion_registry.active_completion_id
            pending_key = completion_id or "unknown"
            pending_turn = self._pending_turn_store.get(pending_key)
            if (
                pending_turn is None
                or pending_turn.result_sent
                or (
                    pending_turn.tool_use_id is not None
                    and event.tool_use_id is not None
                    and pending_turn.tool_use_id != event.tool_use_id
                )
            ):
                previous_user_transcript = pending_turn.user_transcript if pending_turn is not None else None
                previous_trace = pending_turn.trace if pending_turn is not None and not pending_turn.result_sent else None
                pending_turn = PendingToolCall(completion_id=pending_key)
                pending_turn.user_transcript = previous_user_transcript
                pending_turn.trace = previous_trace
                self._pending_turn_store.put(pending_turn)
            pending_turn.tool_use_received = True
            pending_turn.tool_content_id = event.content_id
            pending_turn.tool_use_id = event.tool_use_id
            pending_turn.tool_name = event.tool_name
            if self._session.pending_initial_reply_text is not None and self._session.initial_tool_completion_id is None:
                pending_turn.kind = InterviewTurnKind.INITIAL
                pending_turn.initial_reply_text = self._session.pending_initial_reply_text
                pending_turn.initial_question_id = self._session.pending_initial_question_id
                self._session.initial_tool_completion_id = pending_turn.completion_id
                logger.info(
                    "voice_initial_tool_use_received voice_session_id=%s initial_question_id=%s initial_reply_status=%s completion_id=%s tool_use_id=%s response_id=%s generation=%s",
                    self._session.voice_session_id,
                    pending_turn.initial_question_id,
                    "sending",
                    pending_turn.completion_id,
                    pending_turn.tool_use_id,
                    f"initial-response-{self._session.voice_session_id}" if self._session.voice_session_id else None,
                    self._response_controller.generation,
                )
            trace = self._observability.ensure_trace(pending_turn, turn_index=self._session.turn_index)
            trace.tool_use_received_at = self._observability.now_ms()
            trace.tool_use_id = event.tool_use_id
            self._observability.output.tool_use_completion_matches = (
                completion_id is not None
                and event.content_id is not None
            )
            await self._turn_coordinator.maybe_process_pending_turn(pending_turn.completion_id)
        elif isinstance(event, ToolResultEvent):
            pass
        elif isinstance(event, ErrorEvent):
            await self.emit_runtime_error(event.code, event.message)
            return
        elif isinstance(event, UnknownEvent):
            self._observability.output.unknown_event_count += 1
            for key in event.event_keys or (() if event.raw_event_type is None else (event.raw_event_type,)):
                if key not in self._observability.output.unknown_event_keys:
                    self._observability.output.unknown_event_keys.append(key)
            logger.warning(
                "nova_unknown_event top_level_keys=%s event_keys=%s role=%s type=%s stop_reason=%s generation_stage=%s session_id_present=%s prompt_name_present=%s completion_id_present=%s content_id_present=%s content_length=%s audio_content_length=%s",
                event.safe_shape.get("top_level_keys") if event.safe_shape else [],
                event.safe_shape.get("event_keys") if event.safe_shape else [],
                event.safe_shape.get("role") if event.safe_shape else None,
                event.safe_shape.get("type") if event.safe_shape else None,
                event.safe_shape.get("stopReason") if event.safe_shape else None,
                event.safe_shape.get("generationStage") if event.safe_shape else "unknown",
                event.safe_shape.get("sessionId_present") if event.safe_shape else False,
                event.safe_shape.get("promptName_present") if event.safe_shape else False,
                event.safe_shape.get("completionId_present") if event.safe_shape else False,
                event.safe_shape.get("contentId_present") if event.safe_shape else False,
                event.safe_shape.get("content_length") if event.safe_shape else 0,
                event.safe_shape.get("audio_content_length") if event.safe_shape else 0,
            )
            mapped = map_protocol_event(
                event,
                response_id=self._response_controller.active_response_id,
                completion_id=getattr(event, "completion_id", None),
                generation=self._response_controller.generation if self._response_controller.active_response_id is not None else None,
                sequence=0,
                authorized=False,
                transcript_text=None,
            )
            if mapped is not None:
                await self._event_sink.emit(mapped)
            return

        if isinstance(event, ContentStartEvent) and event.role != "ASSISTANT":
            return

        event_completion_id = getattr(event, "completion_id", None)
        approved_tool_response = self._approved_responses.get(event_completion_id or "")
        mapped_completion_state = self._completion_registry.resolve_completion_state(event_completion_id)
        response_id = (
            approved_tool_response.response_id
            if approved_tool_response is not None
            else mapped_completion_state.response_id if mapped_completion_state is not None else self._response_controller.active_response_id
        )
        generation = (
            mapped_completion_state.generation
            if mapped_completion_state is not None and mapped_completion_state.response_id == response_id
            else self._response_controller.generation if response_id is not None else None
        )
        transcript_text = None
        if isinstance(event, (ContentEndEvent, CompletionEndEvent)):
            content_id = (
                getattr(event, "content_id", None)
                or self._completion_registry.active_output_content_id
            )
            transcript_text = self._completion_registry.pop_transcript(content_id)

        mapped = map_protocol_event(
            event,
            response_id=response_id,
            completion_id=event_completion_id,
            generation=generation,
            sequence=self._observability.next_audio_sequence() if isinstance(event, AudioOutputEvent) else 0,
            authorized=self._is_authorized_event(event),
            transcript_text=transcript_text,
        )
        if mapped is None:
            return
        if self._is_user_event(mapped):
            await self._event_sink.emit(mapped)
            return
        if isinstance(mapped, AssistantAudioChunk):
            if approved_tool_response is not None:
                if mapped.authorized and mapped_completion_state is not None and mapped.generation == mapped_completion_state.generation:
                    await self._event_sink.emit(mapped)
            elif self._response_controller.accepts_audio_chunk(mapped):
                await self._event_sink.emit(mapped)
            return
        if isinstance(mapped, AssistantTranscriptFinal):
            if approved_tool_response is not None:
                if self._is_authorized_event(event):
                    await self._event_sink.emit(mapped)
                    await self._assistant_event_recorder.record(
                        "assistant_transcript_final",
                        response_id=mapped.response_id,
                        generation=mapped.generation,
                        transcript=mapped.text,
                        detail=self._approved_responses.detail(event_completion_id),
                    )
            elif self._response_controller.accepts_transcript(
                mapped,
                completion_id=event_completion_id,
            ):
                await self._event_sink.emit(mapped)
            return
        if isinstance(mapped, AssistantSpeechStarted):
            if approved_tool_response is not None:
                if self._is_authorized_event(event):
                    await self._event_sink.emit(mapped)
                    await self._assistant_event_recorder.record(
                        "assistant_speech_started",
                        response_id=mapped.response_id,
                        generation=mapped.generation,
                        transcript=None,
                        detail=self._approved_responses.detail(event_completion_id),
                    )
            elif self._response_controller.accepts_speech_started(
                mapped,
                completion_id=event_completion_id,
            ):
                await self._event_sink.emit(mapped)
            return
        if isinstance(mapped, AssistantSpeechEnded):
            completion_state = self._completion_registry.lookup_completion_state(event_completion_id)
            if completion_state is None or completion_state.status not in {
                CompletionStatus.OUTPUT_COMPLETE,
                CompletionStatus.PROTOCOL_COMPLETE,
            }:
                return
            if approved_tool_response is not None:
                if self._is_authorized_event(event):
                    await self._event_sink.emit(mapped)
                    await self._assistant_event_recorder.record(
                        "assistant_speech_ended",
                        response_id=mapped.response_id,
                        generation=mapped.generation,
                        transcript=None,
                        detail=self._approved_responses.detail(event_completion_id),
                    )
                    await self._completion_lifecycle.finalize_authorized_completion_once(
                        event_completion_id,
                        reason="assistant_speech_ended",
                    )
            elif (
                self._response_controller.accepts_speech_ended(
                    mapped,
                    completion_id=event_completion_id,
                )
                or (
                    completion_state.authorized
                    and mapped.response_id == completion_state.response_id
                    and mapped.generation == completion_state.generation
                )
            ):
                await self._event_sink.emit(mapped)
                await self._completion_lifecycle.finalize_authorized_completion_once(
                    event_completion_id,
                    reason="assistant_speech_ended",
                )
            return

        if isinstance(event, (ContentEndEvent, CompletionEndEvent)):
            self._completion_registry.clear_active_output_content_id()
            if isinstance(event, CompletionEndEvent):
                self._completion_registry.clear_active_completion_id()

    def _is_authorized_event(self, event: Any) -> bool:
        completion_id = getattr(event, "completion_id", None)
        completion_state = self._completion_registry.resolve_completion_state(completion_id)
        return bool(completion_state is not None and completion_state.authorized)

    def _is_user_event(self, event: VoiceRuntimeEvent) -> bool:
        return isinstance(event, (UserSpeechStarted, UserSpeechEnded, UserTranscriptFinal))

    async def emit_runtime_error(self, code: str, message: str) -> None:
        observed = self._observability.output
        observed.explicit_stream_error = True
        observed.model_stream_error = True
        observed.explicit_stream_error_type = code
        observed.explicit_stream_error_message = message
        self._observability.set_failed_stage(code)
        if code == "nova_sonic_model_stream_error":
            observed.received_event_types.append("model_stream_error")
        await self._event_sink.emit(
            RuntimeErrorEvent(
                message=message,
                detail={
                    "code": code,
                    "stage": observed.last_input_event,
                    "received_event_types": list(observed.received_event_types),
                    "message": message,
                },
            )
        )
        active_response_id = self._response_controller.active_response_id
        await self._assistant_event_recorder.record(
            "assistant_error",
            response_id=active_response_id,
            generation=(
                self._response_controller.generation
                if active_response_id is not None
                else None
            ),
            transcript=None,
            detail={"code": code},
        )
