"""
Role:
    Nova Sonicの処理中turnと関連taskを保持するストア。

Summary:
    通常turnから評価待ちturnへの移動、重複処理防止task、終了時のcancelを
    一箇所で管理し、Runtimeコンポーネント間の辞書共有を隠蔽する。

Relations:
    Uses session_state.PendingToolCall. Used by Runtime lifecycle components.
"""

from __future__ import annotations

import asyncio

from ai_interviewer_voice.runtimes.nova_sonic.session_state import PendingToolCall


class PendingTurnStore:
    def __init__(self) -> None:
        self._turns: dict[str, PendingToolCall] = {}
        self._tool_tasks: dict[str, asyncio.Task[None]] = {}

    def get(self, completion_id: str) -> PendingToolCall | None:
        return self._turns.get(completion_id)

    def get_any(self, completion_id: str) -> PendingToolCall | None:
        return self.get(completion_id)

    def put(self, pending: PendingToolCall) -> None:
        self._turns[pending.completion_id] = pending

    def get_or_create(self, completion_id: str) -> PendingToolCall:
        pending = self.get(completion_id)
        if pending is None:
            pending = PendingToolCall(completion_id=completion_id)
            self.put(pending)
        return pending

    def remove(self, completion_id: str) -> PendingToolCall | None:
        return self._turns.pop(completion_id, None)

    def has_active_cycle(self) -> bool:
        return bool(self._turns)

    def active_turns(self) -> list[PendingToolCall]:
        unique: dict[int, PendingToolCall] = {}
        for pending in self._turns.values():
            unique[id(pending)] = pending
        return list(unique.values())

    @property
    def evaluation_count(self) -> int:
        return sum(
            1
            for pending in self._turns.values()
            if pending.processing_mode == "answer_evaluation" and not pending.result_sent
        )

    @property
    def pending_reply_count(self) -> int:
        return sum(
            1
            for pending in self.active_turns()
            if pending.evaluation_result is not None and not pending.result_sent
        )

    def clear(self) -> None:
        self._turns.clear()
        self._tool_tasks.clear()

    def get_task(self, key: str) -> asyncio.Task[None] | None:
        return self._tool_tasks.get(key)

    def put_task(self, key: str, task: asyncio.Task[None]) -> None:
        self._tool_tasks[key] = task

    def remove_task(self, key: str) -> asyncio.Task[None] | None:
        return self._tool_tasks.pop(key, None)

    def cancel_tasks(self) -> None:
        for task in self._tool_tasks.values():
            if not task.done():
                task.cancel()
        self._tool_tasks.clear()
