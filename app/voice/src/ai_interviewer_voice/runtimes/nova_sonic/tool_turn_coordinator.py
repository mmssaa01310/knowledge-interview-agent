"""
Role:
    forced tool turn と Interview API 連携の調停。

Summary:
    toolUse受信後の pending turn 判定、interview bridge 呼び出し、
    confirmation preface / follow-up / tool result 送信をまとめて扱う。

Relations:
    Uses session_state models and InterviewBridge contracts.
    Used by nova_sonic.runtime as the tool turn execution coordinator.
"""

from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import Protocol

from ai_interviewer_voice.runtimes.nova_sonic.config import NovaSonicRuntimeConfig
from ai_interviewer_voice.runtimes.nova_sonic.input_gate import InputGateController
from ai_interviewer_voice.runtimes.nova_sonic.pending_turn_store import PendingTurnStore
from ai_interviewer_voice.runtimes.nova_sonic.runtime_ports import NovaObservability
from ai_interviewer_voice.runtimes.nova_sonic.session_state import InterviewTurnKind, PendingToolCall
from ai_interviewer_voice.services.interview_bridge import InterviewApiError, InterviewBridgeResult, InvalidInterviewResponseError
from ai_interviewer_voice.services.interview_bridge import InterviewBridge
from ai_interviewer_voice.runtimes.nova_sonic.tool_result_sender import ToolResultSenderPort


logger = logging.getLogger(__name__)


class EvaluationCoordinatorPort(Protocol):
    async def start_local_preface(self, pending: PendingToolCall) -> None: ...

    async def evaluation_ready(
        self,
        pending: PendingToolCall,
        result: InterviewBridgeResult,
    ) -> None: ...


class GenerationPort(Protocol):
    @property
    def generation(self) -> int: ...


class VoiceSessionStatePort(Protocol):
    @property
    def voice_session_id(self) -> str | None: ...

    @property
    def current_question_id(self) -> str | None: ...

    @property
    def state_version(self) -> int: ...

    @property
    def interview_status(self) -> str: ...

    turn_index: int
    close_after_current_completion: bool
    processed_tool_use_keys: set[str]

    def next_tool_response_id(self) -> str: ...

    def apply_bridge_result(self, result: InterviewBridgeResult) -> None: ...


class ToolTurnCoordinator:
    def __init__(
        self,
        *,
        config: NovaSonicRuntimeConfig,
        interview_bridge: InterviewBridge | None,
        session_state: VoiceSessionStatePort,
        evaluation_coordinator: EvaluationCoordinatorPort,
        tool_result_sender: ToolResultSenderPort,
        pending_turn_store: PendingTurnStore,
        input_gate: InputGateController,
        response_controller: GenerationPort,
        observability: NovaObservability,
    ) -> None:
        self._config = config
        self._interview_bridge = interview_bridge
        self._session = session_state
        self._evaluation_coordinator = evaluation_coordinator
        self._tool_result_sender = tool_result_sender
        self._pending_turn_store = pending_turn_store
        self._input_gate = input_gate
        self._response_controller = response_controller
        self._observability = observability

    def configure(self, config: NovaSonicRuntimeConfig) -> None:
        self._config = config

    async def maybe_process_pending_turn(self, completion_id: str) -> None:
        if not self._config.enable_forced_tool_use:
            return
        pending = self._pending_turn_store.get(completion_id)
        if pending is None:
            return
        can_start_interview = (
            pending.kind == InterviewTurnKind.USER_ANSWER
            and pending.user_transcript is not None
            and pending.tool_use_received
            and not pending.result_sent
            and pending.interview_task is None
            and pending.tool_name == self._config.forced_tool_name
            and self._interview_bridge is not None
            and self._session.voice_session_id is not None
        )
        if can_start_interview:
            trace = self._observability.ensure_trace(pending, turn_index=self._session.turn_index)
            logger.info(
                "voice_interview_process_async_started voice_session_id=%s voice_turn_trace_id=%s turn_index=%s current_question_id=%s state_version=%s completion_id=%s tool_use_id=%s retrieval_policy=%s retrieval_executed=%s",
                self._session.voice_session_id,
                trace.trace_id,
                trace.turn_index,
                self._session.current_question_id,
                self._session.state_version,
                completion_id,
                pending.tool_use_id,
                "unknown",
                False,
            )
            pending.interview_task = asyncio.create_task(self.process_interview_bridge_turn(pending))

        if pending.kind == InterviewTurnKind.INITIAL:
            ready = (
                pending.tool_use_received
                and pending.tool_content_end_received
                and pending.tool_content_stop_reason == "TOOL_USE"
                and not pending.result_sent
            )
        else:
            ready = (
                pending.user_transcript is not None
                and pending.tool_use_received
                and pending.tool_content_end_received
                and pending.tool_content_stop_reason == "TOOL_USE"
                and not pending.result_sent
            )
        if not ready:
            logger.info(
                "interview_tool_waiting completion_id=%s kind=%s transcript=%s tool_use=%s tool_content_end=%s stop_reason=%s result_sent=%s",
                completion_id,
                pending.kind.value,
                pending.user_transcript is not None,
                pending.tool_use_received,
                pending.tool_content_end_received,
                pending.tool_content_stop_reason,
                pending.result_sent,
            )
            return
        task_key = pending.tool_use_id or completion_id
        active_task = self._pending_turn_store.get_task(task_key)
        if active_task is not None and not active_task.done():
            return
        logger.info("interview_tool_ready completion_id=%s", completion_id)
        task = asyncio.create_task(
            self.process_forced_tool_turn(completion_id, pending, task_key)
        )
        self._pending_turn_store.put_task(task_key, task)
        await task

    async def process_forced_tool_turn(
        self,
        completion_id: str,
        pending: PendingToolCall,
        task_key: str,
    ) -> None:
        if pending is None or pending.result_sent:
            return
        await asyncio.sleep(self._config.forced_tool_result_delay_ms / 1000)
        if (
            pending.kind == InterviewTurnKind.USER_ANSWER
            and pending.processing_mode == "unknown"
            and pending.interview_task is not None
            and not pending.interview_task.done()
        ):
            deadline = monotonic() + 2.0
            while (
                pending.processing_mode == "unknown"
                and pending.interview_task is not None
                and not pending.interview_task.done()
                and monotonic() < deadline
            ):
                await asyncio.sleep(0.005)
        try:
            if pending.tool_name != self._config.forced_tool_name:
                bridge_result = InterviewBridgeResult(
                    turn_id=f"invalid-tool-{pending.tool_use_id or 'unknown'}",
                    response_id=self._session.next_tool_response_id(),
                    reply_text=self._config.interview_error_reply_text,
                    action="error",
                    question_id=self._session.current_question_id,
                    state_version=self._session.state_version,
                    interview_status="active",
                    retrieval_policy=None,
                    retrieval_executed=False,
                )
            elif pending.kind == InterviewTurnKind.INITIAL:
                bridge_result = self.build_initial_bridge_result(pending)
            elif pending.processing_mode == "answer_evaluation":
                await self._evaluation_coordinator.start_local_preface(pending)
                pending.preface_sent = True
                if pending.interview_task is not None:
                    asyncio.create_task(self._queue_followup_reply_after_preface(pending))
                return
            elif pending.interview_task is not None:
                bridge_result = await pending.interview_task
            elif self._interview_bridge is not None and self._session.voice_session_id is not None:
                bridge_result = await self.process_interview_bridge_turn(pending)
            else:
                bridge_result = InterviewBridgeResult(
                    turn_id=f"tool-turn-{pending.tool_use_id or 'unknown'}",
                    response_id=self._session.next_tool_response_id(),
                    reply_text=self._config.forced_tool_result_reply_text,
                    action="ask_followup",
                    question_id=self._session.current_question_id,
                    state_version=self._session.state_version,
                    interview_status="active",
                    retrieval_policy=None,
                    retrieval_executed=False,
                )
            await self._tool_result_sender.send_tool_result(
                completion_id=completion_id,
                tool_use_id=pending.tool_use_id or "missing-tool-use-id",
                result={"reply_text": bridge_result.reply_text},
                bridge_result=bridge_result,
                pending=pending,
            )
        except InvalidInterviewResponseError:
            self._observability.set_failed_stage("invalid_interview_response")
            bridge_result = self.build_error_bridge_result(
                InterviewApiError("invalid_interview_response", "invalid interview response")
            )
            await self._tool_result_sender.send_tool_result(
                completion_id=completion_id,
                tool_use_id=pending.tool_use_id or "missing-tool-use-id",
                result={"reply_text": bridge_result.reply_text},
                bridge_result=bridge_result,
                pending=pending,
            )
        finally:
            self._pending_turn_store.remove_task(task_key)

    def build_initial_bridge_result(self, pending: PendingToolCall) -> InterviewBridgeResult:
        reply_text = (pending.initial_reply_text or "").strip()
        if not reply_text:
            return self.build_error_bridge_result(
                InterviewApiError("initial_reply_missing", "initial reply missing")
            )
        voice_session_id = self._session.voice_session_id or "unknown"
        return InterviewBridgeResult(
            turn_id=f"initial-{voice_session_id}",
            response_id=f"initial-response-{voice_session_id}",
            reply_text=reply_text,
            action="ask_initial_question",
            question_id=pending.initial_question_id or (
                self._session.current_question_id
            ),
            state_version=self._session.state_version,
            interview_status=self._session.interview_status,
            retrieval_policy="never",
            retrieval_executed=False,
        )

    async def process_interview_bridge_turn(self, pending: PendingToolCall) -> InterviewBridgeResult:
        assert self._interview_bridge is not None
        voice_session_id = self._session.voice_session_id
        assert voice_session_id is not None
        tool_use_key = f"{voice_session_id}:{pending.tool_use_id or 'missing'}"
        if tool_use_key in self._session.processed_tool_use_keys:
            raise InvalidInterviewResponseError("duplicate tool use")
        self._session.processed_tool_use_keys.add(tool_use_key)
        trace = self._observability.ensure_trace(pending, turn_index=self._session.turn_index)
        try:
            trace.turn_save_started_at = self._observability.now_ms()
            logger.info(
                "interview_bridge_save_turn_started voice_session_id=%s tool_use_id=%s answer_to_question_id=%s",
                voice_session_id,
                pending.tool_use_id,
                self._session.current_question_id,
            )
            save_result = await self._interview_bridge.save_turn(
                voice_session_id,
                transcript=pending.user_transcript or "",
                answer_to_question_id=self._session.current_question_id,
            )
            logger.info(
                "voice_user_message_saved voice_session_id=%s turn_id=%s question_id=%s state_version=%s retrieval_policy=%s retrieval_executed=%s tool_use_id=%s",
                voice_session_id,
                save_result.turn_id,
                self._session.current_question_id,
                self._session.state_version,
                "unknown",
                False,
                pending.tool_use_id,
            )
            trace.turn_saved_at = self._observability.now_ms()
            trace.turn_id = save_result.turn_id
            pending.processing_mode = getattr(save_result, "processing_mode", "confirmation_reply")
            observed = self._observability.output
            observed.turn_saved = True
            observed.turn_id_present = bool(save_result.turn_id)
            observed.interview_process_called = True
            trace.interview_process_started_at = self._observability.now_ms()
            logger.info(
                "voice_interview_process_started voice_session_id=%s turn_id=%s question_id=%s state_version=%s tool_use_id=%s retrieval_policy=%s retrieval_executed=%s",
                voice_session_id,
                save_result.turn_id,
                self._session.current_question_id,
                self._session.state_version,
                pending.tool_use_id,
                "unknown",
                False,
            )
            bridge_result = await self._interview_bridge.process_saved_turn(
                voice_session_id=voice_session_id,
                turn_id=save_result.turn_id,
            )
            logger.info(
                "interview_bridge_process_turn_completed voice_session_id=%s turn_id=%s response_id=%s retrieval_policy=%s retrieval_executed=%s",
                voice_session_id,
                bridge_result.turn_id,
                bridge_result.response_id,
                bridge_result.retrieval_policy,
                bridge_result.retrieval_executed,
            )
            trace.interview_process_completed_at = self._observability.now_ms()
            trace.response_id = bridge_result.response_id
            logger.info(
                "voice_interview_process_completed voice_session_id=%s turn_index=%s current_question_id=%s state_version=%s turn_id=%s response_id=%s result_question_id=%s result_state_version=%s interview_status=%s",
                voice_session_id,
                self._session.turn_index,
                self._session.current_question_id,
                self._session.state_version,
                bridge_result.turn_id,
                bridge_result.response_id,
                bridge_result.question_id,
                bridge_result.state_version,
                bridge_result.interview_status,
            )
            logger.info(
                "voice_retrieval_decision_completed voice_session_id=%s turn_id=%s question_id=%s state_version=%s retrieval_policy=%s retrieval_executed=%s response_id=%s tool_use_id=%s",
                voice_session_id,
                bridge_result.turn_id,
                bridge_result.question_id,
                bridge_result.state_version,
                bridge_result.retrieval_policy,
                bridge_result.retrieval_executed,
                bridge_result.response_id,
                pending.tool_use_id,
            )
            self._session.apply_bridge_result(bridge_result)
            observed.interview_process_completed = True
            observed.reply_text_present = bool(bridge_result.reply_text)
            return bridge_result
        except asyncio.CancelledError:
            logger.warning(
                "interview_bridge_turn_cancelled voice_session_id=%s tool_use_id=%s",
                voice_session_id,
                pending.tool_use_id,
            )
            raise
        except InterviewApiError as exc:
            return self.build_error_bridge_result(exc)
        except InvalidInterviewResponseError:
            raise
        except Exception as exc:
            return self.build_error_bridge_result(
                InterviewApiError("turn_process_failed", str(exc) or "turn_process_failed")
            )

    def build_error_bridge_result(self, exc: InterviewApiError) -> InterviewBridgeResult:
        reply_text = self._config.interview_error_reply_text
        if exc.code.endswith("_timeout"):
            reply_text = self._config.interview_timeout_reply_text
        elif exc.code == "unauthorized":
            reply_text = self._config.interview_unauthorized_reply_text
            self._session.close_after_current_completion = True
        elif exc.code == "voice_session_closed":
            self._session.close_after_current_completion = True
        return InterviewBridgeResult(
            turn_id=f"error-turn-{self._session.next_tool_response_id()}",
            response_id=f"error-response-{self._session.next_tool_response_id()}",
            reply_text=reply_text,
            action="error",
            question_id=self._session.current_question_id,
            state_version=self._session.state_version,
            interview_status=self._session.interview_status,
            retrieval_policy=None,
            retrieval_executed=False,
        )

    async def _queue_followup_reply_after_preface(self, pending: PendingToolCall) -> None:
        assert pending.interview_task is not None
        bridge_result = await pending.interview_task
        await self._evaluation_coordinator.evaluation_ready(pending, bridge_result)
