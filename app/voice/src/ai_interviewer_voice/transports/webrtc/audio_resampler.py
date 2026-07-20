from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from time import monotonic

import av


SAMPLE_WIDTH_BYTES = 2


def frame_to_pcm_bytes(frame: av.AudioFrame) -> bytes:
    """s16/monoフレームから有効なPCM領域だけを取得する。"""
    if not frame.planes:
        return b""

    if frame.format.name not in {"s16", "s16p"}:
        raise ValueError(f"Unsupported audio format: {frame.format.name}")

    if len(frame.layout.channels) != 1:
        raise ValueError(f"Unsupported channel layout: {frame.layout.name}")

    valid_bytes = frame.samples * SAMPLE_WIDTH_BYTES

    # planeにはアラインメント用paddingが含まれる場合があるため、
    # frame.samplesに対応する有効領域だけを使用する。
    return bytes(frame.planes[0])[:valid_bytes]


@dataclass(frozen=True)
class ResamplerStats:
    audio_resampler_processing_ms: float
    frames_produced: int


class InputAudioResampler:
    def __init__(self, *, output_rate_hz: int = 16000) -> None:
        self._resampler = av.AudioResampler(
            format="s16",
            layout="mono",
            rate=output_rate_hz,
        )
        self.output_rate_hz = output_rate_hz

    def resample(
        self,
        frame: av.AudioFrame,
    ) -> tuple[list[bytes], ResamplerStats]:
        started_at = monotonic()

        resampled = self._resampler.resample(frame)
        frames = resampled if isinstance(resampled, list) else [resampled]

        chunks = [
            frame_to_pcm_bytes(item)
            for item in frames
            if item is not None
        ]

        return chunks, ResamplerStats(
            audio_resampler_processing_ms=(monotonic() - started_at) * 1000,
            frames_produced=len(chunks),
        )

    def flush(self) -> tuple[list[bytes], ResamplerStats]:
        started_at = monotonic()

        try:
            resampled = self._resampler.resample(None)
        except TypeError:
            resampled = []

        frames = resampled if isinstance(resampled, list) else [resampled]

        chunks = [
            frame_to_pcm_bytes(item)
            for item in frames
            if item is not None
        ]

        return chunks, ResamplerStats(
            audio_resampler_processing_ms=(monotonic() - started_at) * 1000,
            frames_produced=len(chunks),
        )


def build_pcm_frame(
    pcm: bytes,
    *,
    sample_rate_hz: int,
    samples: int,
    pts: int | None = None,
) -> av.AudioFrame:
    expected_bytes = samples * SAMPLE_WIDTH_BYTES

    if len(pcm) != expected_bytes:
        raise ValueError(
            "PCM length does not match sample count: "
            f"expected={expected_bytes}, actual={len(pcm)}, samples={samples}"
        )

    frame = av.AudioFrame(
        format="s16",
        layout="mono",
        samples=samples,
    )
    frame.sample_rate = sample_rate_hz
    frame.planes[0].update(pcm)

    if pts is not None:
        frame.pts = pts
        frame.time_base = Fraction(1, sample_rate_hz)

    return frame


class OutputAudioResampler:
    def __init__(
        self,
        *,
        input_rate_hz: int = 24000,
        output_rate_hz: int = 48000,
        output_samples_per_frame: int = 960,
    ) -> None:
        self.input_rate_hz = input_rate_hz
        self.output_rate_hz = output_rate_hz
        self.output_samples_per_frame = output_samples_per_frame

        self._output_frame_bytes = output_samples_per_frame * SAMPLE_WIDTH_BYTES
        self._output_pcm_buffer = bytearray()

        self._resampler = av.AudioResampler(
            format="s16",
            layout="mono",
            rate=output_rate_hz,
        )

    def _append_resampled_frames(
        self,
        frames: Sequence[av.AudioFrame | None],
    ) -> None:
        for frame in frames:
            if frame is None:
                continue

            pcm = frame_to_pcm_bytes(frame)
            if pcm:
                self._output_pcm_buffer.extend(pcm)

    def _take_fixed_output_pcm(self) -> bytes:
        available_bytes = min(
            len(self._output_pcm_buffer),
            self._output_frame_bytes,
        )

        output_pcm = bytes(self._output_pcm_buffer[:available_bytes])
        del self._output_pcm_buffer[:available_bytes]

        if available_bytes < self._output_frame_bytes:
            # 出力時間を変化させないため、常に960 samplesへ固定する。
            # 直前サンプルの反復はDC成分やノイズの原因になるため使用しない。
            output_pcm += bytes(self._output_frame_bytes - available_bytes)

        return output_pcm

    def resample(
        self,
        pcm: bytes,
        *,
        pts: int,
    ) -> tuple[av.AudioFrame, ResamplerStats]:
        started_at = monotonic()

        if len(pcm) % SAMPLE_WIDTH_BYTES != 0:
            raise ValueError(
                f"PCM byte length must be even: actual={len(pcm)}"
            )

        if pcm:
            input_samples = len(pcm) // SAMPLE_WIDTH_BYTES

            input_frame = build_pcm_frame(
                pcm,
                sample_rate_hz=self.input_rate_hz,
                samples=input_samples,
            )

            resampled = self._resampler.resample(input_frame)
            frames = resampled if isinstance(resampled, list) else [resampled]
            self._append_resampled_frames(frames)

        output_pcm = self._take_fixed_output_pcm()

        output_frame = build_pcm_frame(
            output_pcm,
            sample_rate_hz=self.output_rate_hz,
            samples=self.output_samples_per_frame,
            pts=pts,
        )

        return output_frame, ResamplerStats(
            audio_resampler_processing_ms=(monotonic() - started_at) * 1000,
            frames_produced=1,
        )

    def flush(self) -> tuple[list[av.AudioFrame], ResamplerStats]:
        started_at = monotonic()

        try:
            resampled = self._resampler.resample(None)
        except TypeError:
            resampled = []

        frames = resampled if isinstance(resampled, Sequence) else [resampled]
        self._append_resampled_frames(frames)

        output_frames: list[av.AudioFrame] = []

        while self._output_pcm_buffer:
            output_pcm = self._take_fixed_output_pcm()

            output_frames.append(
                build_pcm_frame(
                    output_pcm,
                    sample_rate_hz=self.output_rate_hz,
                    samples=self.output_samples_per_frame,
                )
            )

        return output_frames, ResamplerStats(
            audio_resampler_processing_ms=(monotonic() - started_at) * 1000,
            frames_produced=len(output_frames),
        )
