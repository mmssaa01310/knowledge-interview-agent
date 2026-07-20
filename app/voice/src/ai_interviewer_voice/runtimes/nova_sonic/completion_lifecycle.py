"""
Role:
    Nova Sonic authorized completion の状態遷移と後始末を管理する。

Summary:
    completion / content の完了判定、planned と spoken の比較、
    finalize-once と follow-up 解放、completion 後の状態破棄を担当する。

Relations:
    Uses CompletionRegistry, ResponseController, session_state models.
    Used by nova_sonic.runtime and ProtocolEventDispatcher.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from time import monotonic

from ai_interviewer_voice.runtimes.nova_sonic.completion_registry import CompletionRegistry
from ai_interviewer_voice.runtimes.nova_sonic.pending_turn_store import PendingTurnStore
from ai_interviewer_voice.runtimes.nova_sonic.response_controller import ResponseController
from ai_interviewer_voice.runtimes.nova_sonic.session_state import (
    CompletionState,
    CompletionStatus,
    InputState,
    InterviewTurnKind,
    NovaSonicObservedOutput,
    PendingToolCall,
    VoiceSessionRuntimeState,
)


logger = logging.getLogger(__name__)


class CompletionLifecycle:
    def __init__(
        self,
        *,
        registry: CompletionRegistry,
        response_controller: ResponseController,
        observed_output_getter: Callable[[], NovaSonicObservedOutput],
        voice_session_state_getter: Callable[[], VoiceSessionRuntimeState | None],
        voice_session_id_getter: Callable[[], str | None],
        turn_index_getter: Callable[[], int],
        input_state_getter: Callable[[], InputState],
        generation_getter: Callable[[], int],
        pending_turn_store: PendingTurnStore,
        remove_approved_tool_response: Callable[[str], None],
        cancel_reply_completion_start_watchdog: Callable[[str | None], None],
        maybe_mark_initial_reply_sent: Callable[[str], Awaitable[None]],
        end_audio_input: Callable[[], Awaitable[None]],
        close_after_current_completion_getter: Callable[[], bool],
        close_after_current_completion_setter: Callable[[bool], None],
        now_ms: Callable[[], int],
    ) -> None:
        self._registry = registry
        self._response_controller = response_controller
        self._observed_output_getter = observed_output_getter
        self._voice_session_state_getter = voice_session_state_getter
        self._voice_session_id_getter = voice_session_id_getter
        self._turn_index_getter = turn_index_getter
        self._input_state_getter = input_state_getter
        self._generation_getter = generation_getter
        self._pending_turn_store = pending_turn_store
        self._remove_approved_tool_response = remove_approved_tool_response
        self._cancel_reply_completion_start_watchdog = cancel_reply_completion_start_watchdog
        self._maybe_mark_initial_reply_sent = maybe_mark_initial_reply_sent
        self._end_audio_input = end_audio_input
        self._close_after_current_completion_getter = close_after_current_completion_getter
        self._close_after_current_completion_setter = close_after_current_completion_setter
        self._now_ms = now_ms

    def cancel_completion_start_watchdog(self, response_id: str | None) -> None:
        self._cancel_reply_completion_start_watchdog(response_id)

    def update_completion_status(self, completion_state: CompletionState | None) -> None:
        if completion_state is None:
            return
        observed_output = self._observed_output_getter()
        assistant_text_complete = (
            completion_state.assistant_final_text_end_received
            or completion_state.assistant_final_text_received
        )
        if completion_state.completion_end_received:
            completion_state.status = CompletionStatus.PROTOCOL_COMPLETE
        elif completion_state.assistant_audio_end_received and assistant_text_complete:
            completion_state.status = CompletionStatus.OUTPUT_COMPLETE
        else:
            completion_state.status = CompletionStatus.GENERATING
        observed_output.completion_status = completion_state.status.value
        if completion_state.authorized and completion_state.status == CompletionStatus.OUTPUT_COMPLETE:
            observed_output.approved_output_complete = True
            self.finalize_planned_vs_spoken(completion_state)
            voice_session_state = self._voice_session_state_getter()
            logger.info(
                "voice_assistant_output_complete voice_session_id=%s turn_index=%s current_question_id=%s state_version=%s completion_id=%s response_id=%s generation=%s authorization_state=%s interview_status=%s",
                self._voice_session_id_getter(),
                self._turn_index_getter(),
                voice_session_state.current_question_id if voice_session_state else None,
                voice_session_state.state_version if voice_session_state else None,
                completion_state.completion_id,
                completion_state.response_id,
                self._generation_getter(),
                self._response_controller.authorization_state.value,
                voice_session_state.interview_status if voice_session_state else None,
            )

    async def maybe_complete_session_after_authorized_output(
        self,
        completion_state: CompletionState | None,
    ) -> None:
        if completion_state is None or not completion_state.authorized:
            return
        if completion_state.status not in {
            CompletionStatus.OUTPUT_COMPLETE,
            CompletionStatus.PROTOCOL_COMPLETE,
        }:
            return
        await self._maybe_mark_initial_reply_sent(completion_state.completion_id)
        if not self._close_after_current_completion_getter():
            return
        self._close_after_current_completion_setter(False)
        voice_session_state = self._voice_session_state_getter()
        logger.info(
            "voice_runtime_shutdown_started voice_session_id=%s turn_index=%s current_question_id=%s state_version=%s completion_id=%s response_id=%s generation=%s reason=interview_completed",
            self._voice_session_id_getter(),
            self._turn_index_getter(),
            voice_session_state.current_question_id if voice_session_state else None,
            voice_session_state.state_version if voice_session_state else None,
            completion_state.completion_id,
            completion_state.response_id,
            self._generation_getter(),
        )
        await self._end_audio_input()

    async def finalize_authorized_completion_once(
        self,
        completion_id: str | None,
        *,
        reason: str,
    ) -> None:
        completion_state = self._registry.lookup_completion_state(completion_id)
        if completion_state is None or not completion_state.authorized:
            return
        if completion_state.finalized:
            return
        if completion_state.status not in {
            CompletionStatus.OUTPUT_COMPLETE,
            CompletionStatus.PROTOCOL_COMPLETE,
        }:
            return
        completion_state.finalized = True
        pending = self._lookup_pending_turn(completion_state.completion_id)
        self._cancel_reply_completion_start_watchdog(completion_state.response_id)
        logger.info(
            "assistant_authorized_completion_finished voice_session_id=%s turn_index=%s current_question_id=%s state_version=%s completion_id=%s response_id=%s generation=%s output_audio_content_end_received=%s output_text_content_end_received=%s completion_end_received=%s completion_fallback_reason=%s input_state=%s",
            self._voice_session_id_getter(),
            self._turn_index_getter() + 1,
            self._current_question_id(),
            self._current_state_version(),
            completion_state.completion_id,
            completion_state.response_id,
            completion_state.generation,
            completion_state.assistant_audio_end_received,
            completion_state.assistant_final_text_end_received,
            completion_state.completion_end_received,
            self.completion_fallback_reason(completion_state),
            self._input_state_getter().value,
        )
        logger.info(
            "authorized_completion_finalized completion_id=%s response_id=%s generation=%s reason=%s",
            completion_state.completion_id,
            completion_state.response_id,
            completion_state.generation,
            reason,
        )
        self._response_controller.on_completion_finished(completion_state.completion_id)
        self._response_controller.reset_to_blocked_if_idle()
        self._observed_output_getter().response_authorization_state = (
            self._response_controller.authorization_state.value
        )
        self.reset_turn_state_after_assistant_ended(completion_state.completion_id)

    def reset_turn_state_after_assistant_ended(self, completion_id: str | None) -> None:
        if completion_id is None:
            return
        completion_state = self._registry.lookup_completion_state(completion_id)
        if completion_state is None or not completion_state.authorized:
            return
        if completion_state.status not in {
            CompletionStatus.OUTPUT_COMPLETE,
            CompletionStatus.PROTOCOL_COMPLETE,
        }:
            return
        pending_turn = self._pending_turn_store.get(completion_id)
        trace = pending_turn.trace if pending_turn is not None else None
        if trace is not None:
            trace.assistant_speech_ended_at = self._now_ms()
        voice_session_state = self._voice_session_state_getter()
        if self._close_after_current_completion_getter() or (
            voice_session_state is not None and voice_session_state.interview_status == "completed"
        ):
            return
        self._registry.remove_completion_content(completion_id)
        self._pending_turn_store.remove(completion_id)
        self._remove_approved_tool_response(completion_id)

    def finalize_planned_vs_spoken(self, completion_state: CompletionState) -> None:
        observed_output = self._observed_output_getter()
        planned = completion_state.planned_reply_text or observed_output.planned_reply_text or ""
        spoken = completion_state.spoken_transcript
        observed_output.planned_reply_text = planned
        observed_output.planned_reply_length = len(planned)
        observed_output.spoken_transcript = spoken
        observed_output.spoken_transcript_length = len(spoken)
        observed_output.spoken_matches_exactly = bool(planned) and planned == spoken
        observed_output.spoken_contains_planned_reply = bool(planned) and planned in spoken

    @staticmethod
    def completion_fallback_reason(completion_state: CompletionState) -> str | None:
        if not completion_state.completion_end_received:
            return None
        if (
            completion_state.assistant_audio_end_received
            and completion_state.assistant_final_text_end_received
        ):
            return None
        if completion_state.assistant_audio_end_received:
            return "completion_end_without_final_text_end"
        if completion_state.assistant_final_text_end_received:
            return "completion_end_without_audio_end"
        return "completion_end_without_output_content_end"

    async def apply_output_complete_grace_period(self, grace_seconds: float) -> None:
        observed_output = self._observed_output_getter()
        if observed_output.completion_end_received or observed_output.explicit_stream_error:
            return
        if observed_output.completion_status != CompletionStatus.OUTPUT_COMPLETE.value:
            return
        await asyncio.sleep(grace_seconds)
        if observed_output.completion_end_received or observed_output.explicit_stream_error:
            return
        observed_output.completion_protocol_degraded = True
        observed_output.failed_stage = "output_complete_without_completion_end"

    def _lookup_pending_turn(self, completion_id: str) -> PendingToolCall | None:
        return self._pending_turn_store.get_any(completion_id)

    def _current_question_id(self) -> str | None:
        voice_session_state = self._voice_session_state_getter()
        return voice_session_state.current_question_id if voice_session_state else None

    def _current_state_version(self) -> int | None:
        voice_session_state = self._voice_session_state_getter()
        return voice_session_state.state_version if voice_session_state else None
