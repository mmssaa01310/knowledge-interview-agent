from __future__ import annotations

from time import monotonic

import pytest

from ai_interviewer_voice.schemas.events import AssistantAudioChunk
from ai_interviewer_voice.transports.webrtc.audio_output_track import AudioOutputTrack
from ai_interviewer_voice.transports.webrtc.playback_buffer import PlaybackBuffer
from ai_interviewer_voice.transports.webrtc.audio_resampler import frame_to_pcm_bytes


@pytest.mark.anyio
async def test_audio_output_track_returns_silence_when_buffer_empty() -> None:
    buffer = PlaybackBuffer()
    track = AudioOutputTrack(buffer, preroll_ms=0.0)
    frame = await track.recv()
    pcm = bytes(frame.planes[0])
    assert frame.sample_rate == 48000
    assert frame.pts == 0
    assert pcm.count(0) == len(pcm)


@pytest.mark.anyio
async def test_audio_output_track_pts_increases_monotonically() -> None:
    buffer = PlaybackBuffer()
    chunk = AssistantAudioChunk(
        response_id="r1",
        completion_id="c1",
        generation=1,
        sequence=1,
        pcm=b"\x01\x02" * 480,
        authorized=True,
    )
    for _ in range(4):
        await buffer.enqueue(chunk, current_generation=1)
    track = AudioOutputTrack(buffer, preroll_ms=0.0)
    first = await track.recv()
    second = await track.recv()
    assert first.pts == 0
    assert second.pts == 960
    assert first.time_base.denominator == 48000
    assert second.time_base.denominator == 48000
    assert len(frame_to_pcm_bytes(first)) == 1920
    assert len(frame_to_pcm_bytes(second)) == 1920


@pytest.mark.anyio
async def test_audio_output_track_reuses_last_samples_on_short_underrun() -> None:
    buffer = PlaybackBuffer()
    partial = AssistantAudioChunk(
        response_id="r1",
        completion_id="c1",
        generation=1,
        sequence=1,
        pcm=b"\x05\x00" * 240,
        authorized=True,
    )
    await buffer.enqueue(partial, current_generation=1)
    track = AudioOutputTrack(buffer, preroll_ms=0.0, short_underrun_ms=40.0)
    frame = await track.recv()
    pcm = bytes(frame.planes[0])
    assert any(byte != 0 for byte in pcm)


@pytest.mark.anyio
async def test_audio_output_track_requires_preroll_before_playback() -> None:
    buffer = PlaybackBuffer(target_depth_ms=100.0, retention_max_ms=5000.0)
    chunk = AssistantAudioChunk(
        response_id="r1",
        completion_id="c1",
        generation=1,
        sequence=1,
        pcm=b"\x01\x02" * 480,
        authorized=True,
    )
    await buffer.enqueue(chunk, current_generation=1)
    track = AudioOutputTrack(buffer, preroll_ms=80.0)
    frame = await track.recv()
    pcm = bytes(frame.planes[0])
    assert pcm.count(0) == len(pcm)


@pytest.mark.anyio
async def test_audio_output_track_keeps_roughly_twenty_millisecond_pacing() -> None:
    buffer = PlaybackBuffer(target_depth_ms=100.0, retention_max_ms=5000.0)
    chunk = AssistantAudioChunk(
        response_id="r1",
        completion_id="c1",
        generation=1,
        sequence=1,
        pcm=b"\x01\x02" * 480,
        authorized=True,
    )
    for _ in range(120):
        await buffer.enqueue(chunk, current_generation=1)

    track = AudioOutputTrack(buffer, preroll_ms=0.0)
    started_at = monotonic()
    for _ in range(100):
        frame = await track.recv()
        assert frame.samples == 960
        assert len(frame_to_pcm_bytes(frame)) == 1920
    elapsed = monotonic() - started_at
    assert 1.85 <= elapsed <= 2.35


@pytest.mark.anyio
async def test_audio_output_track_preserves_five_second_assistant_duration_under_burst_enqueue() -> None:
    buffer = PlaybackBuffer(target_depth_ms=100.0, retention_max_ms=10000.0)
    chunk = AssistantAudioChunk(
        response_id="r1",
        completion_id="c1",
        generation=1,
        sequence=1,
        pcm=b"\x01\x02" * 480,
        authorized=True,
    )
    received_pcm_bytes = 0
    for _ in range(250):
        await buffer.enqueue(chunk, current_generation=1)
        received_pcm_bytes += len(chunk.pcm)

    track = AudioOutputTrack(buffer, preroll_ms=0.0)
    started_at = monotonic()
    output_frame_count = 0
    for _ in range(250):
        frame = await track.recv()
        assert frame.samples == 960
        assert len(frame_to_pcm_bytes(frame)) == 1920
        output_frame_count += 1
    elapsed = monotonic() - started_at
    source_duration_ms = (received_pcm_bytes / (24000 * 2)) * 1000.0
    output_duration_ms = output_frame_count * 20.0
    assert abs(source_duration_ms - output_duration_ms) <= 20.0
    assert 4.8 <= elapsed <= 5.6
