from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from time import monotonic

from aiortc import MediaStreamTrack

from ai_interviewer_voice.runtimes.base import RealtimeVoiceRuntime
from ai_interviewer_voice.schemas.audio import AudioFrame
from ai_interviewer_voice.transports.webrtc.audio_resampler import InputAudioResampler


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _QueuedAudioFrame:
    frame: AudioFrame
    enqueued_at_ms: int


class AudioInputTrackConsumer:
    def __init__(
        self,
        runtime: RealtimeVoiceRuntime,
        *,
        voice_session_id: str | None = None,
        max_queue_frames: int = 12,
    ) -> None:
        self._runtime = runtime
        self._voice_session_id = voice_session_id
        self._max_queue_frames = max_queue_frames
        self._recv_task: asyncio.Task[None] | None = None
        self._sender_task: asyncio.Task[None] | None = None
        self._source_track: MediaStreamTrack | None = None
        self._queue: asyncio.Queue[_QueuedAudioFrame | None] = asyncio.Queue(maxsize=max_queue_frames)
        self._input_resampler = InputAudioResampler(output_rate_hz=16000)
        self.frame_count = 0
        self.drop_count = 0
        self.sender_error_count = 0
        self._last_flow_log_at = 0.0
        self._last_frame_at_ms: int | None = None
        self._frames_sent = 0
        self._sender_lag_total_ms = 0.0
        self._sender_lag_max_ms = 0.0
        self._resampler_processing_total_ms = 0.0
        self._resampler_processing_samples = 0

    @property
    def is_running(self) -> bool:
        return self._recv_task is not None and not self._recv_task.done()

    @property
    def source_track_state(self) -> str | None:
        if self._source_track is None:
            return None
        return self._source_track.readyState

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    async def start(self, track: MediaStreamTrack) -> None:
        await self.stop()
        self._source_track = track
        self._queue = asyncio.Queue(maxsize=self._max_queue_frames)
        self._last_flow_log_at = monotonic()
        self._last_frame_at_ms = None
        self._frames_sent = 0
        self._sender_lag_total_ms = 0.0
        self._sender_lag_max_ms = 0.0
        self._resampler_processing_total_ms = 0.0
        self._resampler_processing_samples = 0
        logger.info(
            "runtime_audio_input_task_started voice_session_id=%s track_ready_state=%s queue_max_frames=%s",
            self._voice_session_id,
            self.source_track_state,
            self._max_queue_frames,
        )
        self._recv_task = asyncio.create_task(self._run())
        self._sender_task = asyncio.create_task(self._sender_loop())

    async def stop(self) -> None:
        if self._recv_task is None and self._sender_task is None:
            return
        for task in (self._recv_task, self._sender_task):
            if task is not None:
                task.cancel()
        for task in (self._recv_task, self._sender_task):
            if task is not None:
                with suppress(asyncio.CancelledError):
                    await task
        self._drain_queue()
        _, flush_stats = self._input_resampler.flush()
        self._resampler_processing_total_ms += flush_stats.audio_resampler_processing_ms
        self._resampler_processing_samples += 1
        logger.info(
            "runtime_audio_input_task_stopped voice_session_id=%s frames_received=%s frames_forwarded=%s drops=%s sender_errors=%s audio_input_queue_depth=%s audio_input_frames_dropped=%s audio_input_sender_lag_ms=%s audio_resampler_processing_ms=%s last_frame_at=%s track_ready_state=%s",
            self._voice_session_id,
            self.frame_count + self.drop_count,
            self._frames_sent,
            self.drop_count,
            self.sender_error_count,
            self.queue_depth,
            self.drop_count,
            round(self._sender_lag_max_ms, 1),
            round(self._average_resampler_processing_ms(), 3),
            self._last_frame_at_ms,
            self.source_track_state,
        )
        self._recv_task = None
        self._sender_task = None
        self._source_track = None

    async def _run(self) -> None:
        assert self._source_track is not None
        while True:
            try:
                frame = await self._source_track.recv()
            except asyncio.CancelledError:
                logger.info(
                    "audio_input_track_cancelled voice_session_id=%s frames_received=%s frames_forwarded=%s last_frame_at=%s track_ready_state=%s",
                    self._voice_session_id,
                    self.frame_count + self.drop_count,
                    self._frames_sent,
                    self._last_frame_at_ms,
                    self.source_track_state,
                )
                raise
            except Exception as exc:
                logger.exception(
                    "runtime_audio_input_task_failed voice_session_id=%s error_type=%s frames_received=%s frames_forwarded=%s last_frame_at=%s track_ready_state=%s",
                    self._voice_session_id,
                    exc.__class__.__name__,
                    self.frame_count + self.drop_count,
                    self._frames_sent,
                    self._last_frame_at_ms,
                    self.source_track_state,
                )
                return

            timestamp_ms = int(monotonic() * 1000)
            self._last_frame_at_ms = timestamp_ms
            chunks, stats = self._input_resampler.resample(frame)
            self._resampler_processing_total_ms += stats.audio_resampler_processing_ms
            self._resampler_processing_samples += 1
            if not chunks:
                self.drop_count += 1
                self._log_flow_if_due()
                continue
            for chunk in chunks:
                self.frame_count += 1
                queued = self._enqueue_latest(
                    _QueuedAudioFrame(
                        frame=AudioFrame(
                            pcm=chunk,
                            sample_rate_hz=16000,
                            channels=1,
                            timestamp_ms=timestamp_ms,
                        ),
                        enqueued_at_ms=timestamp_ms,
                    )
                )
                if not queued:
                    self.drop_count += 1
            self._log_flow_if_due()

    async def _sender_loop(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            lag_ms = max(0, int(monotonic() * 1000) - item.enqueued_at_ms)
            self._sender_lag_total_ms += lag_ms
            self._sender_lag_max_ms = max(self._sender_lag_max_ms, float(lag_ms))
            try:
                await self._runtime.push_audio(item.frame)
                self._frames_sent += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.sender_error_count += 1
                logger.exception(
                    "audio_input_sender_failed voice_session_id=%s error_type=%s audio_input_queue_depth=%s audio_input_frames_dropped=%s audio_input_sender_lag_ms=%s",
                    self._voice_session_id,
                    exc.__class__.__name__,
                    self.queue_depth,
                    self.drop_count,
                    lag_ms,
                )
                return
            finally:
                self._queue.task_done()
            self._log_flow_if_due()

    def _enqueue_latest(self, item: _QueuedAudioFrame) -> bool:
        try:
            self._queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            dropped = 0
            while self._queue.full():
                try:
                    stale = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if stale is not None:
                    dropped += 1
                self._queue.task_done()
            self.drop_count += dropped
            try:
                self._queue.put_nowait(item)
                return True
            except asyncio.QueueFull:
                return False

    def _drain_queue(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is not None:
                self.drop_count += 1
            self._queue.task_done()

    def _average_resampler_processing_ms(self) -> float:
        if self._resampler_processing_samples <= 0:
            return 0.0
        return self._resampler_processing_total_ms / self._resampler_processing_samples

    def _log_flow_if_due(self) -> None:
        now = monotonic()
        if now - self._last_flow_log_at < 1.0:
            return
        self._last_flow_log_at = now
        average_lag_ms = self._sender_lag_total_ms / self._frames_sent if self._frames_sent else 0.0
        logger.info(
            "audio_input_flow voice_session_id=%s frames_received=%s frames_forwarded=%s frames_dropped=%s audio_input_queue_depth=%s audio_input_frames_dropped=%s audio_input_sender_lag_ms=%s sender_avg_lag_ms=%s sender_errors=%s audio_resampler_processing_ms=%s last_frame_at=%s track_ready_state=%s",
            self._voice_session_id,
            self.frame_count + self.drop_count,
            self._frames_sent,
            self.drop_count,
            self.queue_depth,
            self.drop_count,
            round(self._sender_lag_max_ms, 1),
            round(average_lag_ms, 1),
            self.sender_error_count,
            round(self._average_resampler_processing_ms(), 3),
            self._last_frame_at_ms,
            self.source_track_state,
        )
