import asyncio
from dataclasses import dataclass
from typing import Any

from ai_interviewer_voice.runtimes.nova_sonic.evaluation_turn_coordinator import (
    EvaluationTurnCoordinator,
)
from ai_interviewer_voice.runtimes.nova_sonic.local_preface import LocalPrefaceSegment
from ai_interviewer_voice.runtimes.nova_sonic.pending_turn_store import PendingTurnStore
from ai_interviewer_voice.runtimes.nova_sonic.session_state import PendingToolCall
from ai_interviewer_voice.services.interview_bridge import InterviewBridgeResult


def _result(*, response_id: str = "bridge-response-1") -> InterviewBridgeResult:
    return InterviewBridgeResult(
        turn_id="turn-1",
        response_id=response_id,
        reply_text="評価後の返答です。",
        action="ask_followup",
        question_id="q-1",
        state_version=2,
        interview_status="active",
    )


@dataclass
class FakeLocalPrefacePlayer:
    segment: LocalPrefaceSegment
    enqueue_count: int = 0

    async def enqueue(self, pending: PendingToolCall) -> LocalPrefaceSegment:
        self.enqueue_count += 1
        return self.segment


class FakeToolResultSender:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.fail_once = fail_once
        self.calls: list[dict[str, Any]] = []

    async def send_tool_result(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("send failed")
        kwargs["pending"].result_sent = True


def _coordinator(
    *,
    sender: FakeToolResultSender,
    player: FakeLocalPrefacePlayer,
    store: PendingTurnStore,
) -> EvaluationTurnCoordinator:
    return EvaluationTurnCoordinator(
        local_preface_player=player,
        tool_result_sender=sender,
        pending_turn_store=store,
        voice_session_id_getter=lambda: "vs-1",
        input_state_getter=lambda: "ANSWER_PROCESSING",
        set_next_listening_action=lambda action: None,
        metrics_by_response_id={},
    )


def _pending(store: PendingTurnStore) -> PendingToolCall:
    pending = PendingToolCall(
        completion_id="completion-1",
        processing_mode="answer_evaluation",
        tool_use_id="original-tool-use-1",
    )
    store.put(pending)
    return pending


def test_evaluation_finishes_before_local_preface_drain() -> None:
    async def run() -> tuple[FakeToolResultSender, PendingToolCall]:
        store = PendingTurnStore()
        pending = _pending(store)
        sender = FakeToolResultSender()
        segment = LocalPrefaceSegment(
            response_id="local-preface-response:turn-1",
            generation=11,
            sample_rate_hz=24000,
            pcm_bytes=50670,
        )
        coordinator = _coordinator(
            sender=sender,
            player=FakeLocalPrefacePlayer(segment),
            store=store,
        )

        await coordinator.start_local_preface(pending)
        await coordinator.evaluation_ready(pending, _result())
        assert sender.calls == []

        await coordinator.notify_local_preface_playback_drained(
            response_id=segment.response_id,
            generation=segment.generation,
        )
        return sender, pending

    sender, pending = asyncio.run(run())
    assert len(sender.calls) == 1
    assert sender.calls[0]["completion_id"] == "completion-1"
    assert sender.calls[0]["tool_use_id"] == "original-tool-use-1"
    assert sender.calls[0]["result"] == {"reply_text": "評価後の返答です。"}
    assert pending.result_sent is True


def test_local_preface_drain_finishes_before_evaluation() -> None:
    async def run() -> int:
        store = PendingTurnStore()
        pending = _pending(store)
        sender = FakeToolResultSender()
        segment = LocalPrefaceSegment(
            response_id="local-preface-response:turn-1",
            generation=21,
            sample_rate_hz=24000,
            pcm_bytes=50670,
        )
        coordinator = _coordinator(
            sender=sender,
            player=FakeLocalPrefacePlayer(segment),
            store=store,
        )

        await coordinator.start_local_preface(pending)
        await coordinator.notify_local_preface_playback_drained(
            response_id=segment.response_id,
            generation=segment.generation,
        )
        assert sender.calls == []

        await coordinator.evaluation_ready(pending, _result())
        await coordinator.evaluation_ready(pending, _result())
        await coordinator.notify_local_preface_playback_drained(
            response_id=segment.response_id,
            generation=segment.generation,
        )
        return len(sender.calls)

    assert asyncio.run(run()) == 1


def test_send_failure_keeps_result_and_retries_once() -> None:
    async def run() -> tuple[FakeToolResultSender, PendingToolCall]:
        store = PendingTurnStore()
        pending = _pending(store)
        sender = FakeToolResultSender(fail_once=True)
        segment = LocalPrefaceSegment(
            response_id="local-preface-response:turn-1",
            generation=31,
            sample_rate_hz=24000,
            pcm_bytes=50670,
        )
        coordinator = _coordinator(
            sender=sender,
            player=FakeLocalPrefacePlayer(segment),
            store=store,
        )

        await coordinator.start_local_preface(pending)
        await coordinator.evaluation_ready(pending, _result())
        await coordinator.notify_local_preface_playback_drained(
            response_id=segment.response_id,
            generation=segment.generation,
        )
        await asyncio.sleep(0.25)
        return sender, pending

    sender, pending = asyncio.run(run())
    assert len(sender.calls) == 2
    assert pending.evaluation_result is not None
    assert pending.result_sent is True


def test_local_preface_segment_uses_explicit_24khz_pcm_contract() -> None:
    segment = LocalPrefaceSegment(
        response_id="local-preface-response:turn-1",
        generation=41,
        sample_rate_hz=24000,
        pcm_bytes=50670,
    )

    assert segment.response_id.startswith("local-preface-response:")
    assert segment.sample_rate_hz == 24000
    assert segment.pcm_bytes % 2 == 0
    assert round(segment.audio_duration_ms) == 1056
