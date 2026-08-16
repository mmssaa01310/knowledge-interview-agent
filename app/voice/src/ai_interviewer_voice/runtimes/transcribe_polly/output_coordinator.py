"""
Role:
    Transcribe + Polly Runtimeの音声出力を一列化し、優先度付きで中断する。

Summary:
    正式回答と固定相槌を単一の再生経路へ集約し、20ms PCM frameを
    monotonic deadlineで実時間送信する。高優先度出力は低優先度出力を
    待たずに無効化し、生成・再生taskを回収する。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from time import monotonic


class OutputKind(StrEnum):
    FORMAL_REPLY = "formal_reply"
    PROCESSING_ACK = "processing_ack"
    LISTEN_ACK = "listen_ack"
    LONG_PROCESSING = "long_processing"


OUTPUT_PRIORITY = {
    OutputKind.FORMAL_REPLY: 100,
    OutputKind.PROCESSING_ACK: 50,
    OutputKind.LISTEN_ACK: 40,
    OutputKind.LONG_PROCESSING: 30,
}


@dataclass
class AudioOutputRequest:
    response_id: str
    generation: int
    kind: OutputKind
    pcm_chunks: AsyncIterator[bytes]
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def priority(self) -> int:
        return OUTPUT_PRIORITY[self.kind]


@dataclass(frozen=True)
class AudioOutputResult:
    accepted: bool
    cancelled: bool
    audio_duration_ms: int


@dataclass
class _ActiveOutput:
    request: AudioOutputRequest
    task: asyncio.Task[AudioOutputResult] | None = None


class AudioOutputCoordinator:
    def __init__(
        self,
        *,
        sample_rate_hz: int,
        emit_frame: Callable[[AudioOutputRequest, bytes], Awaitable[None]],
        on_started: Callable[[AudioOutputRequest], Awaitable[None]],
        on_completed: Callable[[AudioOutputRequest, int], Awaitable[None]],
        on_interrupted: Callable[[AudioOutputRequest], Awaitable[None]],
        is_generation_current: Callable[[int], bool],
    ) -> None:
        self._sample_rate_hz = sample_rate_hz
        self._emit_frame = emit_frame
        self._on_started = on_started
        self._on_completed = on_completed
        self._on_interrupted = on_interrupted
        self._is_generation_current = is_generation_current
        self._active: _ActiveOutput | None = None
        self._lock = asyncio.Lock()
        self._collection_tasks: set[asyncio.Task[object]] = set()
        self._closed = False

    @property
    def active_response_id(self) -> str | None:
        active = self._active
        return active.request.response_id if active is not None else None

    @property
    def active_kind(self) -> OutputKind | None:
        active = self._active
        return active.request.kind if active is not None else None

    async def play(self, request: AudioOutputRequest) -> AudioOutputResult:
        async with self._lock:
            if self._closed:
                await request.pcm_chunks.aclose()
                return AudioOutputResult(False, True, 0)
            active = self._active
            if active is not None and not active.request.cancel_event.is_set():
                may_preempt = request.priority > active.request.priority or (
                    request.kind is OutputKind.FORMAL_REPLY
                    and active.request.kind is OutputKind.FORMAL_REPLY
                )
                if not may_preempt:
                    await request.pcm_chunks.aclose()
                    return AudioOutputResult(False, False, 0)
                self._cancel_active_locked(active)
            holder = _ActiveOutput(request=request)
            task = asyncio.create_task(self._run(holder))
            holder.task = task
            self._active = holder
        return await task

    async def cancel_current(self) -> str | None:
        async with self._lock:
            active = self._active
            if active is None:
                return None
            response_id = active.request.response_id
            self._cancel_active_locked(active)
            return response_id

    async def cancel_notices(self) -> None:
        async with self._lock:
            active = self._active
            if active is not None and active.request.kind is not OutputKind.FORMAL_REPLY:
                self._cancel_active_locked(active)

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            active = self._active
            if active is not None:
                self._cancel_active_locked(active)
        tasks = tuple(self._collection_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _cancel_active_locked(self, active: _ActiveOutput) -> None:
        active.request.cancel_event.set()
        if active.task is not None and not active.task.done():
            active.task.cancel()
            collector = asyncio.create_task(self._collect(active.task))
            self._collection_tasks.add(collector)
            collector.add_done_callback(self._collection_tasks.discard)

    async def _collect(self, task: asyncio.Task[object]) -> None:
        await asyncio.gather(task, return_exceptions=True)

    async def _run(self, holder: _ActiveOutput) -> AudioOutputResult:
        request = holder.request
        started = False
        duration_ms = 0
        interrupted = False
        frame_bytes = int(self._sample_rate_hz * 2 * 0.02)
        next_deadline = monotonic()
        pending = bytearray()
        try:
            async for pcm in request.pcm_chunks:
                if self._must_stop(holder):
                    interrupted = True
                    break
                pending.extend(pcm)
                while len(pending) >= frame_bytes:
                    if self._must_stop(holder):
                        interrupted = True
                        break
                    frame = bytes(pending[:frame_bytes])
                    del pending[:frame_bytes]
                    if not started:
                        started = True
                        await self._on_started(request)
                        next_deadline = monotonic()
                    await self._emit_frame(request, frame)
                    duration_ms += 20
                    next_deadline += 0.020
                    sleep_seconds = next_deadline - monotonic()
                    if sleep_seconds > 0:
                        await asyncio.sleep(sleep_seconds)
                if interrupted:
                    break
            if pending and not interrupted and not self._must_stop(holder):
                if not started:
                    started = True
                    await self._on_started(request)
                pending.extend(bytes(frame_bytes - len(pending)))
                await self._emit_frame(request, bytes(pending))
                duration_ms += 20
            interrupted = interrupted or self._must_stop(holder)
            if started and not interrupted:
                await self._on_completed(request, duration_ms)
            return AudioOutputResult(True, interrupted, duration_ms)
        except asyncio.CancelledError:
            interrupted = True
            raise
        except Exception:
            interrupted = started
            raise
        finally:
            await request.pcm_chunks.aclose()
            if interrupted or request.cancel_event.is_set():
                await self._on_interrupted(request)
            async with self._lock:
                if self._active is holder:
                    self._active = None

    def _must_stop(self, holder: _ActiveOutput) -> bool:
        request = holder.request
        return (
            self._closed
            or request.cancel_event.is_set()
            or self._active is not holder
            or not self._is_generation_current(request.generation)
        )
