from __future__ import annotations

from fractions import Fraction

import av
import pytest

from ai_interviewer_voice.transports.webrtc.audio_resampler import (
    OutputAudioResampler,
    build_pcm_frame,
    frame_to_pcm_bytes,
)


def test_frame_to_pcm_bytes_excludes_plane_padding() -> None:
    resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)
    input_frame = build_pcm_frame(
        b"\x01\x00" * 480,
        sample_rate_hz=24000,
        samples=480,
    )

    resampled = resampler.resample(input_frame)
    frame = next(item for item in (resampled if isinstance(resampled, list) else [resampled]) if item is not None)

    assert len(bytes(frame.planes[0])) >= frame.samples * 2
    assert len(frame_to_pcm_bytes(frame)) == frame.samples * 2


def test_output_audio_resampler_returns_fixed_960_sample_frames() -> None:
    resampler = OutputAudioResampler(
        input_rate_hz=24000,
        output_rate_hz=48000,
        output_samples_per_frame=960,
    )

    for index in range(5):
        frame, _ = resampler.resample(b"\x01\x00" * 480, pts=index * 960)
        assert frame.sample_rate == 48000
        assert frame.samples == 960
        assert frame.pts == index * 960
        assert frame.time_base == Fraction(1, 48000)
        assert len(frame_to_pcm_bytes(frame)) == 1920


def test_output_audio_resampler_preserves_one_second_duration() -> None:
    resampler = OutputAudioResampler(
        input_rate_hz=24000,
        output_rate_hz=48000,
        output_samples_per_frame=960,
    )

    total_output_samples = 0
    for index in range(50):
        frame, _ = resampler.resample(b"\x01\x00" * 480, pts=index * 960)
        total_output_samples += frame.samples

    assert total_output_samples == 48000
