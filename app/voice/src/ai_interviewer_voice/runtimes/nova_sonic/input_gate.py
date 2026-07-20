"""
Role:
    Nova Sonic Runtimeの入力ゲート制御。

Summary:
    assistant再生中とlistening再開の状態遷移を管理し、
    gate open/closeログと InputStateChanged イベント送出を一箇所へ集約する。

Relations:
    Uses session_state.InputState and voice event schemas.
    Used by nova_sonic.runtime as the gate state authority.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from time import monotonic

from ai_interviewer_voice.runtimes.nova_sonic.response_controller import ResponseAuthorizationState
from ai_interviewer_voice.runtimes.nova_sonic.session_state import InputState
from ai_interviewer_voice.schemas.events import InputStateChanged


logger = logging.getLogger(__name__)


class InputGateController:
    def __init__(
        self,
        *,
        voice_session_id_getter: Callable[[], str | None],
        has_pending_turn_cycle: Callable[[], bool],
        authorization_state_getter: Callable[[], ResponseAuthorizationState],
        emit_event: Callable[[InputStateChanged], None],
        guard_seconds: float = 0.2,
    ) -> None:
        self._voice_session_id_getter = voice_session_id_getter
        self._has_pending_turn_cycle = has_pending_turn_cycle
        self._authorization_state_getter = authorization_state_getter
        self._emit_event = emit_event
        self._guard_seconds = guard_seconds
        self._input_state = InputState.ANSWER_LISTENING
        self._next_listening_state = InputState.ANSWER_LISTENING
        self._gate_reopen_task: asyncio.Task[None] | None = None

    @property
    def input_state(self) -> InputState:
        return self._input_state

    @property
    def next_listening_state(self) -> InputState:
        return self._next_listening_state

    @next_listening_state.setter
    def next_listening_state(self, state: InputState) -> None:
        self._next_listening_state = state

    def reset(self) -> None:
        self._input_state = InputState.ANSWER_LISTENING
        self._next_listening_state = InputState.ANSWER_LISTENING
        self._gate_reopen_task = None

    def listening_state_for_action(self, action: str | None) -> InputState:
        if action == "ask_confirmation":
            return InputState.CONFIRMATION_LISTENING
        return InputState.ANSWER_LISTENING

    def set_state(
        self,
        state: InputState,
        *,
        turn_id: str | None = None,
        playback_generation_id: int | None = None,
        reason: str,
    ) -> None:
        if self._input_state == state:
            return
        self._input_state = state
        log_name = (
            "voice_input_gate_opened"
            if state in {InputState.ANSWER_LISTENING, InputState.CONFIRMATION_LISTENING}
            else "voice_input_gate_closed"
        )
        logger.info(
            "%s voice_session_id=%s turn_id=%s playback_generation_id=%s input_state=%s monotonic_timestamp_ms=%s reason=%s",
            log_name,
            self._voice_session_id_getter(),
            turn_id,
            playback_generation_id,
            state.value,
            int(monotonic() * 1000),
            reason,
        )
        self._emit_event(
            InputStateChanged(
                input_state=state.value,
                generation=playback_generation_id,
            )
        )

    def schedule_reopen(self, *, generation: int | None) -> None:
        if self._gate_reopen_task is not None and not self._gate_reopen_task.done():
            self._gate_reopen_task.cancel()
        self._gate_reopen_task = asyncio.create_task(
            self._reopen_after_guard(generation=generation)
        )

    async def close(self) -> None:
        if self._gate_reopen_task is not None:
            self._gate_reopen_task.cancel()
            self._gate_reopen_task = None

    async def _reopen_after_guard(self, *, generation: int | None) -> None:
        try:
            await asyncio.sleep(self._guard_seconds)
            if self._authorization_state_getter() != ResponseAuthorizationState.BLOCKED:
                return
            if self._has_pending_turn_cycle():
                return
            self.set_state(
                self._next_listening_state,
                playback_generation_id=generation,
                reason="assistant_playback_drained",
            )
        except asyncio.CancelledError:
            raise
