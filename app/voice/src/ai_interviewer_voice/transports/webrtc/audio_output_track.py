from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic

from aiortc import MediaStreamTrack

from ai_interviewer_voice.transports.webrtc.audio_resampler import OutputAudioResampler
from ai_interviewer_voice.transports.webrtc.playback_buffer import PlaybackBuffer

logger = logging.getLogger(__name__)

OUTPUT_RATE_HZ = 48000
OUTPUT_SAMPLES_PER_FRAME = 960
OUTPUT_FRAME_DURATION_SECONDS = 0.02
@dataclass(frozen=True)
class OutputFrameMetrics:
    pts: int
    consumed_source_bytes: int
    frame_samples: int
    emitted_at: float
    silence_frame_returned: bool


class AudioOutputTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(
        self,
        playback_buffer: PlaybackBuffer,
        *,
        voice_session_id: str | None = None,
        input_rate_hz: int = 24000,
        preroll_ms: float = 80.0,
        short_underrun_ms: float = 40.0,
        on_frame_emitted: Callable[[OutputFrameMetrics], None] | None = None,
    ) -> None:
        super().__init__()
        self._playback_buffer = playback_buffer
        self._voice_session_id = voice_session_id
        self._preroll_ms = preroll_ms
        self._next_pts = 0
        self._short_underrun_ms = short_underrun_ms
        self._input_rate_hz = input_rate_hz
        self._input_samples_per_frame = int(input_rate_hz * OUTPUT_FRAME_DURATION_SECONDS)
        self._input_bytes_per_frame = self._input_samples_per_frame * 2
        self._frames_created = 0
        self._non_silence_frames = 0
        self._output_resampler = OutputAudioResampler(
            input_rate_hz=input_rate_hz,
            output_rate_hz=OUTPUT_RATE_HZ,
            output_samples_per_frame=OUTPUT_SAMPLES_PER_FRAME,
        )
        self._on_frame_emitted = on_frame_emitted
        self._playback_started_at: float | None = None
        self._last_log_at = monotonic()

    async def recv(self):
        if self._playback_started_at is None:
            self._playback_started_at = monotonic()

        target_time = self._playback_started_at + (self._next_pts / OUTPUT_RATE_HZ)
        wait_seconds = target_time - monotonic()
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)

        depth_before_bytes = await self._playback_buffer.depth_bytes()
        depth_before_ms = await self._playback_buffer.depth_ms()

        if self._preroll_ms > 0 and not await self._playback_buffer.has_preroll(self._preroll_ms):
            pcm = self._silence_pcm()
            consumed_bytes = 0
            silence_frame_returned = True
            await self._playback_buffer.record_silence_inserted(OUTPUT_FRAME_DURATION_SECONDS * 1000.0)
        else:
            pcm = await self._playback_buffer.pop_bytes(expected_bytes=self._input_bytes_per_frame)
            consumed_bytes = len(pcm)
            silence_frame_returned = consumed_bytes == 0

            if consumed_bytes == 0:
                pcm = self._silence_pcm()
                await self._playback_buffer.record_silence_inserted(OUTPUT_FRAME_DURATION_SECONDS * 1000.0)
            elif consumed_bytes < self._input_bytes_per_frame:
                missing_bytes = self._input_bytes_per_frame - consumed_bytes
                pcm = pcm + bytes(missing_bytes)
                await self._playback_buffer.record_silence_inserted(
                    (missing_bytes / 2 / self._input_rate_hz) * 1000.0
                )

        frame, stats = self._output_resampler.resample(
            pcm,
            pts=self._next_pts,
        )
        if frame.samples != OUTPUT_SAMPLES_PER_FRAME:
            raise RuntimeError(f"Invalid output samples: {frame.samples}")

        self._record_frame(
            depth_before_bytes=depth_before_bytes,
            depth_before_ms=depth_before_ms,
            consumed_bytes=consumed_bytes,
            silence_frame_returned=silence_frame_returned,
            resampler_processing_ms=stats.audio_resampler_processing_ms,
        )
        if self._on_frame_emitted is not None:
            self._on_frame_emitted(
                OutputFrameMetrics(
                    pts=self._next_pts,
                    consumed_source_bytes=consumed_bytes,
                    frame_samples=frame.samples,
                    emitted_at=monotonic(),
                    silence_frame_returned=silence_frame_returned,
                )
            )
        self._next_pts += OUTPUT_SAMPLES_PER_FRAME
        return frame

    async def prepare_interrupt(self) -> None:
        return None

    def _silence_pcm(self) -> bytes:
        return bytes(self._input_bytes_per_frame)

    def _record_frame(
        self,
        *,
        depth_before_bytes: int,
        depth_before_ms: float,
        consumed_bytes: int,
        silence_frame_returned: bool,
        resampler_processing_ms: float,
    ) -> None:
        self._frames_created += 1
        if not silence_frame_returned:
            self._non_silence_frames += 1
        log_now = (
            consumed_bytes not in {0, self._input_bytes_per_frame}
            or silence_frame_returned
            or (monotonic() - self._last_log_at) >= 1.0
        )
        if log_now:
            self._last_log_at = monotonic()
            logger.info(
                "voice_audio_output_frame_created voice_session_id=%s frame_count=%s non_silence_frame_count=%s playback_buffer_depth_bytes=%s playback_buffer_depth_ms=%s pcm_consumed_bytes=%s silence_frame_returned=%s audio_frame_pts=%s audio_resampler_processing_ms=%s",
                self._voice_session_id,
                self._frames_created,
                self._non_silence_frames,
                depth_before_bytes,
                round(depth_before_ms, 1),
                consumed_bytes,
                silence_frame_returned,
                self._next_pts,
                round(resampler_processing_ms, 3),
            )
