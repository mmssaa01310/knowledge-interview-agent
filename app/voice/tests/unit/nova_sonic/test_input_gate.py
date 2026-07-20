import asyncio

from ai_interviewer_voice.runtimes.nova_sonic.input_gate import InputGateController
from ai_interviewer_voice.runtimes.nova_sonic.response_controller import ResponseAuthorizationState
from ai_interviewer_voice.runtimes.nova_sonic.session_state import InputState


def test_input_gate_sets_confirmation_listening_for_confirmation_action() -> None:
    emitted: list[str] = []
    controller = InputGateController(
        voice_session_id_getter=lambda: "vs-1",
        has_pending_turn_cycle=lambda: False,
        authorization_state_getter=lambda: ResponseAuthorizationState.BLOCKED,
        emit_event=lambda event: emitted.append(event.input_state),
    )

    controller.set_state(
        InputState.ANSWER_PROCESSING,
        playback_generation_id=1,
        reason="processing",
    )
    controller.next_listening_state = controller.listening_state_for_action("ask_confirmation")
    controller.set_state(
        controller.next_listening_state,
        playback_generation_id=1,
        reason="done",
    )

    assert emitted == ["ANSWER_PROCESSING", "CONFIRMATION_LISTENING"]


def test_input_gate_reopens_only_after_guard_when_authorization_is_blocked() -> None:
    async def run() -> list[str]:
        emitted: list[str] = []
        controller = InputGateController(
            voice_session_id_getter=lambda: "vs-1",
            has_pending_turn_cycle=lambda: False,
            authorization_state_getter=lambda: ResponseAuthorizationState.BLOCKED,
            emit_event=lambda event: emitted.append(event.input_state),
            guard_seconds=0.01,
        )
        controller.set_state(
            InputState.ASSISTANT_SPEAKING,
            playback_generation_id=1,
            reason="playing",
        )
        controller.next_listening_state = InputState.ANSWER_LISTENING
        controller.schedule_reopen(generation=1)
        await asyncio.sleep(0.03)
        await controller.close()
        return emitted

    emitted = asyncio.run(run())
    assert emitted == ["ASSISTANT_SPEAKING", "ANSWER_LISTENING"]
