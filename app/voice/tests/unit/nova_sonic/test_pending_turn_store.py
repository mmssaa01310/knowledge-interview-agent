import asyncio

from ai_interviewer_voice.runtimes.nova_sonic.pending_turn_store import PendingTurnStore
from ai_interviewer_voice.runtimes.nova_sonic.session_state import PendingToolCall


def test_pending_turn_store_keeps_evaluation_turn_until_completion_finishes() -> None:
    store = PendingTurnStore()
    pending = PendingToolCall(completion_id="completion-1")

    store.put(pending)
    pending.processing_mode = "answer_evaluation"

    assert store.get("completion-1") is pending
    assert store.get_any("completion-1") is pending
    assert store.evaluation_count == 1
    assert store.remove("completion-1") is pending
    assert store.has_active_cycle() is False


def test_pending_turn_store_cancels_owned_tasks() -> None:
    async def run() -> bool:
        store = PendingTurnStore()
        task = asyncio.create_task(asyncio.sleep(60))
        store.put_task("tool-1", task)
        store.cancel_tasks()
        await asyncio.sleep(0)
        return task.cancelled()

    assert asyncio.run(run()) is True
