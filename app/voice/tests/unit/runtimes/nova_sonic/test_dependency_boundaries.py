import asyncio
from typing import Any, cast

from ai_interviewer_voice.runtimes.nova_sonic.completion_lifecycle import CompletionLifecycle
from ai_interviewer_voice.runtimes.nova_sonic.completion_registry import CompletionRegistry
from ai_interviewer_voice.runtimes.nova_sonic.config import NovaSonicRuntimeConfig
from ai_interviewer_voice.runtimes.nova_sonic.input_gate import InputGateController
from ai_interviewer_voice.runtimes.nova_sonic.pending_turn_store import PendingTurnStore
from ai_interviewer_voice.runtimes.nova_sonic.protocol.events import ErrorEvent
from ai_interviewer_voice.runtimes.nova_sonic.protocol_dispatcher import ProtocolEventDispatcher
from ai_interviewer_voice.runtimes.nova_sonic.response_controller import ResponseController
from ai_interviewer_voice.runtimes.nova_sonic.runtime_ports import (
    ApprovedResponseStore,
    NovaObservability,
    RuntimeSessionContext,
)
from ai_interviewer_voice.runtimes.nova_sonic.tool_turn_coordinator import ToolTurnCoordinator


class FakeEventSink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def emit(self, event: Any) -> None:
        self.events.append(event)


class FakeRecorder:
    def __init__(self) -> None:
        self.event_types: list[str] = []

    async def record(self, event_type: str, **kwargs: Any) -> None:
        self.event_types.append(event_type)


class FailingTurnCoordinator:
    async def maybe_process_pending_turn(self, completion_id: str) -> None:
        raise AssertionError("error event must not invoke turn coordinator")


class FakeEvaluationCoordinator:
    async def start_local_preface(self, pending: Any) -> None:
        return None

    async def evaluation_ready(self, pending: Any, result: Any) -> None:
        return None


class FakeToolResultSender:
    async def send_tool_result(self, **kwargs: Any) -> None:
        return None


def _input_gate(session: RuntimeSessionContext, response: ResponseController) -> InputGateController:
    gate = InputGateController(
        voice_session_id_getter=lambda: session.voice_session_id,
        has_pending_turn_cycle=lambda: False,
        authorization_state_getter=lambda: response.authorization_state,
        emit_event=lambda event: None,
    )
    session.attach_input_gate(gate)
    return gate


def test_dispatcher_can_be_constructed_without_runtime_and_calls_only_error_ports() -> None:
    async def run() -> tuple[int, list[str]]:
        response = ResponseController()
        session = RuntimeSessionContext()
        sink = FakeEventSink()
        recorder = FakeRecorder()
        dispatcher = ProtocolEventDispatcher(
            config=NovaSonicRuntimeConfig(),
            completion_registry=CompletionRegistry(),
            completion_lifecycle=cast(CompletionLifecycle, object()),
            response_controller=response,
            turn_coordinator=FailingTurnCoordinator(),
            input_gate=_input_gate(session, response),
            event_sink=sink,
            session_context=session,
            assistant_event_recorder=recorder,
            observability=NovaObservability(),
            pending_turn_store=PendingTurnStore(),
            approved_responses=ApprovedResponseStore(),
            evaluation_reply_metrics={},
        )

        await dispatcher.handle_protocol_event(ErrorEvent(code="test_error", message="failed"))
        return len(sink.events), recorder.event_types

    event_count, recorded = asyncio.run(run())
    assert event_count == 1
    assert recorded == ["assistant_error"]


def test_tool_turn_coordinator_can_be_constructed_without_runtime() -> None:
    response = ResponseController()
    session = RuntimeSessionContext()
    coordinator = ToolTurnCoordinator(
        config=NovaSonicRuntimeConfig(),
        interview_bridge=None,
        session_state=session,
        evaluation_coordinator=FakeEvaluationCoordinator(),
        tool_result_sender=FakeToolResultSender(),
        pending_turn_store=PendingTurnStore(),
        input_gate=_input_gate(session, response),
        response_controller=response,
        observability=NovaObservability(),
    )

    assert not hasattr(coordinator, "_runtime")
