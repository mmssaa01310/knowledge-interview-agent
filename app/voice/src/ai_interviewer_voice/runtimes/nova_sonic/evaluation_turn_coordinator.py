"""
Role:
    回答評価とローカルconfirmation prefaceの合流を制御する。

Summary:
    評価結果とBrowser playback drainedの両方がそろった場合だけ、
    元のtoolUseIdへ本来のtoolResultを一度送信し、失敗時の再試行も管理する。

Relations:
    Uses LocalConfirmationPrefacePlayer and ToolResultSenderPort. Used by ToolTurnCoordinator.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from time import monotonic
from typing import Protocol

from ai_interviewer_voice.runtimes.nova_sonic.local_preface import LocalPrefaceSegment
from ai_interviewer_voice.runtimes.nova_sonic.pending_turn_store import PendingTurnStore
from ai_interviewer_voice.runtimes.nova_sonic.session_state import PendingToolCall
from ai_interviewer_voice.runtimes.nova_sonic.tool_result_sender import ToolResultSenderPort
from ai_interviewer_voice.services.interview_bridge import InterviewBridgeResult


logger = logging.getLogger(__name__)


class LocalPrefacePlayerPort(Protocol):
    async def enqueue(self, pending: PendingToolCall) -> LocalPrefaceSegment: ...


class EvaluationTurnCoordinator:
    def __init__(
        self,
        *,
        local_preface_player: LocalPrefacePlayerPort,
        tool_result_sender: ToolResultSenderPort,
        pending_turn_store: PendingTurnStore,
        voice_session_id_getter: Callable[[], str | None],
        input_state_getter: Callable[[], str],
        set_next_listening_action: Callable[[str], None],
        metrics_by_response_id: dict[str, PendingToolCall],
    ) -> None:
        self._local_preface_player = local_preface_player
        self._tool_result_sender = tool_result_sender
        self._pending_turn_store = pending_turn_store
        self._voice_session_id_getter = voice_session_id_getter
        self._input_state_getter = input_state_getter
        self._set_next_listening_action = set_next_listening_action
        self._metrics_by_response_id = metrics_by_response_id

    async def start_local_preface(self, pending: PendingToolCall) -> None:
        if pending.local_preface_response_id is not None:
            return
        pending.confirmation_preface_enqueued_at_ms = int(monotonic() * 1000)
        segment = await self._local_preface_player.enqueue(pending)
        pending.local_preface_response_id = segment.response_id
        pending.local_preface_generation = segment.generation
        logger.info(
            "confirmation_preface_enqueued voice_session_id=%s turn_id=%s completion_id=%s tool_use_id=%s response_id=%s generation=%s sample_rate_hz=%s audio_duration_ms=%s input_state=%s monotonic_timestamp_ms=%s source=local_fixed_preface",
            self._voice_session_id_getter(),
            pending.trace.turn_id if pending.trace is not None else None,
            pending.completion_id,
            pending.tool_use_id,
            segment.response_id,
            segment.generation,
            segment.sample_rate_hz,
            round(segment.audio_duration_ms),
            self._input_state_getter(),
            pending.confirmation_preface_enqueued_at_ms,
        )

    async def evaluation_ready(
        self,
        pending: PendingToolCall,
        result: InterviewBridgeResult,
    ) -> None:
        if pending.result_sent:
            return
        pending.evaluation_result = result
        pending.evaluation_reply_ready_at_ms = int(monotonic() * 1000)
        logger.info(
            "evaluation_reply_ready voice_session_id=%s turn_id=%s question_id=%s completion_id=%s response_id=%s action=%s input_state=%s monotonic_timestamp_ms=%s",
            self._voice_session_id_getter(),
            result.turn_id,
            result.question_id,
            pending.completion_id,
            result.response_id,
            result.action,
            self._input_state_getter(),
            pending.evaluation_reply_ready_at_ms,
        )
        await self._try_send_original_tool_result(pending)

    async def notify_local_preface_playback_drained(
        self,
        *,
        response_id: str | None,
        generation: int | None,
    ) -> bool:
        pending = next(
            (
                item
                for item in self._pending_turn_store.active_turns()
                if item.local_preface_response_id == response_id
                and item.local_preface_generation == generation
            ),
            None,
        )
        if pending is None:
            return False
        pending.local_preface_playback_drained = True
        pending.confirmation_preface_output_complete_at_ms = int(monotonic() * 1000)
        logger.info(
            "confirmation_preface_playback_drained voice_session_id=%s turn_id=%s completion_id=%s response_id=%s generation=%s input_state=%s monotonic_timestamp_ms=%s",
            self._voice_session_id_getter(),
            pending.trace.turn_id if pending.trace is not None else None,
            pending.completion_id,
            response_id,
            generation,
            self._input_state_getter(),
            pending.confirmation_preface_output_complete_at_ms,
        )
        await self._try_send_original_tool_result(pending)
        return True

    async def close(self, pending_turns: list[PendingToolCall]) -> None:
        for pending in pending_turns:
            if pending.evaluation_retry_task is not None and not pending.evaluation_retry_task.done():
                pending.evaluation_retry_task.cancel()

    async def _try_send_original_tool_result(self, pending: PendingToolCall) -> None:
        if pending.result_sent or pending.evaluation_tool_result_dispatching:
            return
        if pending.evaluation_result is None or not pending.local_preface_playback_drained:
            return
        if pending.tool_use_id is None:
            logger.error(
                "evaluation_tool_result_blocked voice_session_id=%s completion_id=%s reason=tool_use_id_missing",
                self._voice_session_id_getter(),
                pending.completion_id,
            )
            return

        result = pending.evaluation_result
        pending.evaluation_tool_result_dispatching = True
        pending.evaluation_reply_send_attempts += 1
        pending.evaluation_reply_send_started_at_ms = int(monotonic() * 1000)
        pending.evaluation_reply_response_id = result.response_id
        self._metrics_by_response_id[result.response_id] = pending
        self._set_next_listening_action(result.action)
        logger.info(
            "evaluation_tool_result_send_started voice_session_id=%s turn_id=%s completion_id=%s tool_use_id=%s response_id=%s attempt=%s input_state=%s monotonic_timestamp_ms=%s",
            self._voice_session_id_getter(),
            result.turn_id,
            pending.completion_id,
            pending.tool_use_id,
            result.response_id,
            pending.evaluation_reply_send_attempts,
            self._input_state_getter(),
            pending.evaluation_reply_send_started_at_ms,
        )
        try:
            await self._tool_result_sender.send_tool_result(
                completion_id=pending.completion_id,
                tool_use_id=pending.tool_use_id,
                result={"reply_text": result.reply_text},
                bridge_result=result,
                pending=pending,
            )
        except Exception:
            pending.evaluation_tool_result_dispatching = False
            self._metrics_by_response_id.pop(result.response_id, None)
            logger.exception(
                "evaluation_tool_result_send_failed voice_session_id=%s completion_id=%s tool_use_id=%s response_id=%s",
                self._voice_session_id_getter(),
                pending.completion_id,
                pending.tool_use_id,
                result.response_id,
            )
            if pending.evaluation_reply_send_attempts < 2:
                pending.evaluation_retry_task = asyncio.create_task(
                    self._retry_after_delay(pending, delay_seconds=0.2)
                )
            return

        pending.evaluation_tool_result_dispatching = False
        pending.evaluation_reply_sent = True
        pending.evaluation_reply_send_completed_at_ms = int(monotonic() * 1000)
        logger.info(
            "evaluation_tool_result_send_completed voice_session_id=%s turn_id=%s completion_id=%s tool_use_id=%s response_id=%s input_state=%s monotonic_timestamp_ms=%s",
            self._voice_session_id_getter(),
            result.turn_id,
            pending.completion_id,
            pending.tool_use_id,
            result.response_id,
            self._input_state_getter(),
            pending.evaluation_reply_send_completed_at_ms,
        )

    async def _retry_after_delay(
        self,
        pending: PendingToolCall,
        *,
        delay_seconds: float,
    ) -> None:
        try:
            await asyncio.sleep(delay_seconds)
            await self._try_send_original_tool_result(pending)
        finally:
            pending.evaluation_retry_task = None
