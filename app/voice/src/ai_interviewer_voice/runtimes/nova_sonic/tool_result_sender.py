"""
Role:
    Nova Sonic toolResult列の構築・送信と承認済み応答の登録。

Summary:
    tool resultを一度だけ送信し、completion bindingと観測値を更新して
    AssistantResponsePreparingをRuntimeイベントとして公開する。

Relations:
    Uses CompletionRegistry, stream writer, response stores. Used by ToolTurnCoordinator.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from ai_interviewer_voice.runtimes.nova_sonic.completion_registry import CompletionRegistry
from ai_interviewer_voice.runtimes.nova_sonic.config import NovaSonicRuntimeConfig
from ai_interviewer_voice.runtimes.nova_sonic.pending_turn_store import PendingTurnStore
from ai_interviewer_voice.runtimes.nova_sonic.protocol.payloads import build_tool_result_sequence
from ai_interviewer_voice.runtimes.nova_sonic.runtime_ports import (
    ApprovedResponseStore,
    NovaObservability,
    RuntimeEventSink,
    RuntimeSessionContext,
)
from ai_interviewer_voice.runtimes.nova_sonic.session_state import (
    ApprovedToolResponse,
    InterviewTurnKind,
    PendingToolCall,
)
from ai_interviewer_voice.runtimes.nova_sonic.stream_writer import ToolResultOutputPort
from ai_interviewer_voice.schemas.events import AssistantResponsePreparing
from ai_interviewer_voice.services.interview_bridge import InterviewBridgeResult


logger = logging.getLogger(__name__)


class GenerationPort(Protocol):
    @property
    def generation(self) -> int: ...

    def reserve_segment_generation(self) -> int: ...


class ToolResultSenderPort(Protocol):
    async def send_tool_result(
        self,
        *,
        completion_id: str,
        tool_use_id: str,
        result: dict[str, Any],
        bridge_result: InterviewBridgeResult,
        pending: PendingToolCall,
    ) -> None: ...


class ToolResultSender:
    def __init__(
        self,
        *,
        config: NovaSonicRuntimeConfig,
        completion_registry: CompletionRegistry,
        pending_turn_store: PendingTurnStore,
        session_context: RuntimeSessionContext,
        response_controller: GenerationPort,
        output: ToolResultOutputPort,
        event_sink: RuntimeEventSink,
        observability: NovaObservability,
        approved_responses: ApprovedResponseStore,
    ) -> None:
        self._config = config
        self._completion_registry = completion_registry
        self._pending_turn_store = pending_turn_store
        self._session = session_context
        self._response_controller = response_controller
        self._output = output
        self._event_sink = event_sink
        self._observability = observability
        self._approved_responses = approved_responses

    def configure(self, config: NovaSonicRuntimeConfig) -> None:
        self._config = config

    async def send_tool_result(
        self,
        *,
        completion_id: str,
        tool_use_id: str,
        result: dict[str, Any],
        bridge_result: InterviewBridgeResult,
        pending: PendingToolCall,
    ) -> None:
        if not self._output.is_open or pending.result_sent:
            return
        generation = self._response_controller.reserve_segment_generation()
        content_name = self._output.next_content_name("tool-result")
        sequence = build_tool_result_sequence(
            prompt_name=self._output.prompt_name,
            content_name=content_name,
            tool_use_id=tool_use_id,
            result=result,
        )
        self._log_payload_shape(sequence, content_name)
        trace = self._observability.ensure_trace(pending, turn_index=self._session.turn_index)
        observed = self._observability.output
        approved = ApprovedToolResponse(
            response_id=bridge_result.response_id,
            completion_id=completion_id,
            tool_use_id=tool_use_id,
            turn_id=bridge_result.turn_id,
            planned_reply_text=str(result.get("reply_text") or ""),
            action=bridge_result.action,
            question_id=bridge_result.question_id,
            state_version=bridge_result.state_version,
            tool_result_sent_at_ms=self._observability.elapsed_ms() or 0,
            retrieval_policy=bridge_result.retrieval_policy,
            retrieval_executed=bridge_result.retrieval_executed,
        )
        self._approved_responses.put(approved)
        completion_state = self._completion_registry.resolve_completion_state(completion_id)
        if completion_state is not None:
            self._completion_registry.reset_completion_output_state(completion_state)
            completion_state.authorized = True
            completion_state.response_id = bridge_result.response_id
            completion_state.generation = generation
            completion_state.planned_reply_text = approved.planned_reply_text
        await self._event_sink.emit(
            AssistantResponsePreparing(
                response_id=bridge_result.response_id,
                generation=generation,
            )
        )
        try:
            await self._output.send_sequence(sequence)
        except Exception:
            self._approved_responses.remove(completion_id)
            if completion_state is not None:
                completion_state.authorized = False
                completion_state.response_id = None
                completion_state.generation = None
            raise
        pending.result_sent = True
        trace.tool_result_content_start_sent_at = observed.tool_result_content_start_sent_at_ms
        trace.tool_result_sent_at = observed.tool_result_sent_at_ms
        trace.tool_result_content_end_sent_at = observed.tool_result_content_end_sent_at_ms
        logger.info(
            "voice_tool_result_sent voice_session_id=%s turn_index=%s current_question_id=%s state_version=%s completion_id=%s tool_use_id=%s response_id=%s generation=%s",
            self._session.voice_session_id,
            self._session.turn_index,
            self._session.current_question_id,
            self._session.state_version,
            completion_id,
            tool_use_id,
            bridge_result.response_id,
            generation,
        )
        self._log_turn_budget(pending, bridge_result, trace)
        observed.tool_result_sent_after_tool_content_end = pending.tool_content_end_received
        observed.tool_result_sent = True
        observed.tool_result_delay_ms = self._config.forced_tool_result_delay_ms
        observed.approved_reply_sent = True
        observed.planned_reply_text = approved.planned_reply_text
        observed.planned_reply_length = len(approved.planned_reply_text)
        self._session.apply_bridge_result(bridge_result)

    def _log_turn_budget(self, pending: PendingToolCall, result: InterviewBridgeResult, trace: Any) -> None:
        if pending.kind != InterviewTurnKind.USER_ANSWER:
            return
        latency = (
            max(0, trace.tool_result_sent_at - trace.user_transcript_final_at)
            if trace.user_transcript_final_at is not None and trace.tool_result_sent_at is not None
            else None
        )
        logger.info(
            "voice_turn_tool_result_budget voice_session_id=%s turn_index=%s turn_id=%s response_id=%s completion_id=%s tool_use_id=%s transcript_to_tool_result_ms=%s target_ms=%s budget_ms=%s over_budget=%s retrieval_policy=%s retrieval_executed=%s",
            self._session.voice_session_id,
            self._session.turn_index,
            result.turn_id,
            result.response_id,
            pending.completion_id,
            pending.tool_use_id,
            latency,
            self._config.normal_turn_tool_result_target_ms,
            self._config.normal_turn_tool_result_budget_ms,
            latency is not None and latency > self._config.normal_turn_tool_result_budget_ms,
            result.retrieval_policy,
            result.retrieval_executed,
        )

    @staticmethod
    def _log_payload_shape(sequence: list[tuple[str, dict[str, Any]]], content_name: str) -> None:
        start_payload, tool_payload, end_payload = (item[1] for item in sequence)
        tool_content = tool_payload["event"]["toolResult"]["content"]
        logger.info(
            "tool_result_shape content_name=%s content_is_string=%s content_json_parseable=%s content_name_matches=%s",
            content_name,
            isinstance(tool_content, str),
            _is_json_string(tool_content),
            end_payload["event"]["contentEnd"].get("contentName") == content_name,
        )


def _is_json_string(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        json.loads(value)
    except Exception:
        return False
    return True
