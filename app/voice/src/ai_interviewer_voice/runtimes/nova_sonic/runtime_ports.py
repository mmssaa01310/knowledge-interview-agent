"""
Role:
    Nova Sonic Runtimeコンポーネント間の明示的なPortと共有状態を提供する。

Summary:
    セッション文脈、イベント送出、観測値、承認済み応答を小さな責務へ分け、
    DispatcherやCoordinatorがRuntime本体へ依存しない構成を支える。

Relations:
    Uses voice schemas and session_state models. Used by Runtime components.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Protocol
from uuid import uuid4

from ai_interviewer_voice.runtimes.nova_sonic.session_state import (
    ApprovedToolResponse,
    InputState,
    NovaSonicObservedOutput,
    PendingToolCall,
    VoiceSessionRuntimeState,
    VoiceTurnTrace,
)
from ai_interviewer_voice.runtimes.nova_sonic.input_gate import InputGateController
from ai_interviewer_voice.schemas.events import VoiceRuntimeEvent
from ai_interviewer_voice.schemas.sessions import VoiceRuntimeContext
from ai_interviewer_voice.services.interview_bridge import InterviewBridgeResult


class RuntimeEventSink(Protocol):
    async def emit(self, event: VoiceRuntimeEvent) -> None: ...


class ProtocolSessionContext(Protocol):
    @property
    def voice_session_id(self) -> str | None: ...

    @property
    def current_question_id(self) -> str | None: ...

    @property
    def state_version(self) -> int: ...

    @property
    def input_state(self) -> InputState: ...


class AssistantEventRecorderPort(Protocol):
    async def record(
        self,
        event_type: str,
        *,
        response_id: str | None,
        generation: int | None,
        transcript: str | None,
        detail: dict[str, Any],
    ) -> None: ...


class QueueRuntimeEventSink:
    def __init__(self, queue: asyncio.Queue[VoiceRuntimeEvent | None]) -> None:
        self._queue = queue

    async def emit(self, event: VoiceRuntimeEvent) -> None:
        await self._queue.put(event)

    def emit_nowait(self, event: VoiceRuntimeEvent) -> None:
        self._queue.put_nowait(event)


@dataclass
class RuntimeSessionContext:
    context: VoiceRuntimeContext | None = None
    voice_state: VoiceSessionRuntimeState | None = None
    turn_index: int = 0
    last_user_speech_started_at: int | None = None
    last_user_speech_ended_at: int | None = None
    pending_initial_reply_text: str | None = None
    pending_initial_question_id: str | None = None
    initial_tool_completion_id: str | None = None
    runtime_open: bool = False
    audio_input_open: bool = False
    close_after_current_completion: bool = False
    processed_tool_use_keys: set[str] = field(default_factory=set)
    _tool_response_counter: int = 0
    _input_gate: InputGateController | None = field(default=None, repr=False)

    @property
    def voice_session_id(self) -> str | None:
        return self.context.voice_session_id if self.context is not None else None

    @property
    def current_question_id(self) -> str | None:
        return self.voice_state.current_question_id if self.voice_state is not None else None

    @property
    def state_version(self) -> int:
        return self.voice_state.state_version if self.voice_state is not None else 0

    @property
    def interview_status(self) -> str:
        return self.voice_state.interview_status if self.voice_state is not None else "active"

    @property
    def input_state(self) -> InputState:
        if self._input_gate is None:
            return InputState.ANSWER_LISTENING
        return self._input_gate.input_state

    def attach_input_gate(self, input_gate: InputGateController) -> None:
        self._input_gate = input_gate

    def reset(self, context: VoiceRuntimeContext) -> None:
        self.context = context
        self.voice_state = None
        self.turn_index = 0
        self.last_user_speech_started_at = None
        self.last_user_speech_ended_at = None
        self.pending_initial_reply_text = None
        self.pending_initial_question_id = None
        self.initial_tool_completion_id = None
        self.runtime_open = False
        self.audio_input_open = False
        self.close_after_current_completion = False
        self.processed_tool_use_keys.clear()
        self._tool_response_counter = 0

    def next_turn(self) -> int:
        self.turn_index += 1
        return self.turn_index

    def next_tool_response_id(self) -> str:
        self._tool_response_counter += 1
        return f"tool-response-{self._tool_response_counter}"

    def apply_bridge_result(self, result: InterviewBridgeResult) -> None:
        if self.voice_state is None:
            return
        self.voice_state.current_question_id = result.question_id
        self.voice_state.state_version = result.state_version
        self.voice_state.interview_status = result.interview_status
        self.close_after_current_completion = result.interview_status == "completed"


class NovaObservability:
    def __init__(self) -> None:
        self.output = NovaSonicObservedOutput()
        self._started_at: float | None = None
        self._audio_sequence = 0

    def reset(self) -> None:
        self.output = NovaSonicObservedOutput()
        self._started_at = monotonic()
        self._audio_sequence = 0

    def elapsed_ms(self) -> int | None:
        if self._started_at is None:
            return None
        return int((monotonic() - self._started_at) * 1000)

    def now_ms(self) -> int:
        return self.elapsed_ms() or 0

    def next_audio_sequence(self) -> int:
        self._audio_sequence += 1
        return self._audio_sequence

    def set_failed_stage(self, stage: str) -> None:
        if self.output.failed_stage == "none":
            self.output.failed_stage = stage

    def ensure_trace(self, pending: PendingToolCall, *, turn_index: int) -> VoiceTurnTrace:
        if pending.trace is None:
            pending.trace = VoiceTurnTrace(
                trace_id=f"voice-turn-trace-{uuid4().hex[:12]}",
                turn_index=max(turn_index, 1),
                completion_id=pending.completion_id,
                tool_use_id=pending.tool_use_id,
            )
        return pending.trace


class ApprovedResponseStore:
    def __init__(self) -> None:
        self._responses: dict[str, ApprovedToolResponse] = {}

    def get(self, completion_id: str) -> ApprovedToolResponse | None:
        return self._responses.get(completion_id)

    def put(self, response: ApprovedToolResponse) -> None:
        self._responses[response.completion_id] = response

    def remove(self, completion_id: str) -> None:
        self._responses.pop(completion_id, None)

    def clear(self) -> None:
        self._responses.clear()

    def detail(self, completion_id: str | None) -> dict[str, Any]:
        approved = self.get(completion_id or "")
        if approved is None:
            return {}
        return {
            "turnId": approved.turn_id,
            "completionId": approved.completion_id,
            "toolUseId": approved.tool_use_id,
            "plannedReplyText": approved.planned_reply_text,
            "action": approved.action,
            "questionId": approved.question_id,
            "stateVersion": approved.state_version,
        }
