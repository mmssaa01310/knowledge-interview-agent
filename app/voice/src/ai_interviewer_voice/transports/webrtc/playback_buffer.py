from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ai_interviewer_voice.schemas.events import AssistantAudioChunk


@dataclass(frozen=True)
class PlaybackBufferStats:
    playback_buffer_depth_ms: float
    playback_buffer_peak_depth_ms: float
    playback_old_audio_dropped_ms: float
    playback_underrun_count: int
    playback_silence_inserted_ms: float


class PlaybackBufferCapacityExceeded(Exception):
    pass


class PlaybackBuffer:
    BYTES_PER_SAMPLE = 2

    def __init__(
        self,
        *,
        sample_rate_hz: int = 24000,
        target_depth_ms: float = 100.0,
        retention_max_ms: float = 60000.0,
    ) -> None:
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        self._buffer = bytearray()
        self._lock = asyncio.Lock()
        self._sample_rate_hz = sample_rate_hz
        self._target_depth_ms = target_depth_ms
        self._retention_max_ms = retention_max_ms
        self.drop_count = 0
        self.enqueue_count = 0
        self.old_audio_dropped_ms = 0.0
        self.underrun_count = 0
        self.silence_inserted_ms = 0.0
        self.peak_depth_ms = 0.0

    @property
    def target_depth_ms(self) -> float:
        return self._target_depth_ms

    @property
    def retention_max_ms(self) -> float:
        return self._retention_max_ms

    async def enqueue(self, chunk: AssistantAudioChunk, *, current_generation: int) -> bool:
        if not chunk.authorized or chunk.generation != current_generation:
            return False
        if chunk.sample_rate_hz != self._sample_rate_hz:
            return False
        if not self._is_valid_pcm(chunk.pcm):
            return False
        async with self._lock:
            next_size = len(self._buffer) + len(chunk.pcm)
            if self._buffer_depth_ms(next_size) > self._retention_max_ms:
                raise PlaybackBufferCapacityExceeded(
                    f"assistant playback buffer exceeded retention cap: {self._retention_max_ms}ms"
                )
            self._buffer.extend(chunk.pcm)
            self.enqueue_count += 1
            self.peak_depth_ms = max(
                self.peak_depth_ms,
                self._buffer_depth_ms(len(self._buffer)),
            )
        return True

    async def pop_bytes(
        self,
        size: int | None = None,
        *,
        expected_bytes: int | None = None,
    ) -> bytes:
        requested_size = expected_bytes if expected_bytes is not None else size
        if requested_size is None:
            raise ValueError("pop_bytes requires size or expected_bytes")
        async with self._lock:
            if len(self._buffer) < requested_size:
                data = bytes(self._buffer)
                self._buffer.clear()
                if data:
                    self.underrun_count += 1
                return data
            data = bytes(self._buffer[:requested_size])
            del self._buffer[:requested_size]
            return data

    async def clear(self, *, count_as_dropped: bool = False) -> float:
        async with self._lock:
            cleared_ms = self._buffer_depth_ms(len(self._buffer))
            if count_as_dropped and self._buffer:
                self.drop_count += 1
                self.old_audio_dropped_ms += cleared_ms
            self._buffer.clear()
            return cleared_ms

    async def trim_to_generation_boundary(self) -> None:
        await self.clear(count_as_dropped=True)

    async def depth_ms(self) -> float:
        async with self._lock:
            return self._buffer_depth_ms(len(self._buffer))

    async def depth_bytes(self) -> int:
        async with self._lock:
            return len(self._buffer)

    async def is_empty(self) -> bool:
        async with self._lock:
            return not self._buffer

    async def has_preroll(self, preroll_ms: float) -> bool:
        async with self._lock:
            return self._buffer_depth_ms(len(self._buffer)) >= preroll_ms

    async def record_silence_inserted(self, duration_ms: float) -> None:
        async with self._lock:
            self.silence_inserted_ms += duration_ms

    async def snapshot_stats(self) -> PlaybackBufferStats:
        async with self._lock:
            return PlaybackBufferStats(
                playback_buffer_depth_ms=self._buffer_depth_ms(len(self._buffer)),
                playback_buffer_peak_depth_ms=self.peak_depth_ms,
                playback_old_audio_dropped_ms=self.old_audio_dropped_ms,
                playback_underrun_count=self.underrun_count,
                playback_silence_inserted_ms=self.silence_inserted_ms,
            )

    def _buffer_depth_ms(self, size_bytes: int) -> float:
        return (size_bytes / self.BYTES_PER_SAMPLE / self._sample_rate_hz) * 1000.0

    def _depth_bytes_for_ms(self, depth_ms: float) -> int:
        samples = int((depth_ms / 1000.0) * self._sample_rate_hz)
        return samples * self.BYTES_PER_SAMPLE

    @classmethod
    def _is_valid_pcm(cls, pcm: bytes) -> bool:
        return bool(pcm) and len(pcm) % cls.BYTES_PER_SAMPLE == 0
