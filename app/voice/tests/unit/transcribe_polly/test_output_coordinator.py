from __future__ import annotations

import asyncio
from time import monotonic

import pytest

from ai_interviewer_voice.runtimes.transcribe_polly.output_coordinator import (
    AudioOutputCoordinator,
    AudioOutputRequest,
    OutputKind,
)


async def _pcm_chunks(*chunks: bytes):
    for chunk in chunks:
        yield chunk


@pytest.mark.anyio
async def test_one_second_pcm_is_emitted_at_realtime_deadlines() -> None:
    emitted_at: list[float] = []
    started: list[str] = []
    completed: list[str] = []
    interrupted: list[str] = []
    coordinator = AudioOutputCoordinator(
        sample_rate_hz=16000,
        emit_frame=lambda request, pcm: _append_async(emitted_at, monotonic()),
        on_started=lambda request: _append_async(started, request.response_id),
        on_completed=lambda request, duration: _append_async(
            completed, request.response_id
        ),
        on_interrupted=lambda request: _append_async(
            interrupted, request.response_id
        ),
        is_generation_current=lambda generation: generation == 1,
    )

    started_at = monotonic()
    result = await coordinator.play(
        AudioOutputRequest(
            response_id="formal-1",
            generation=1,
            kind=OutputKind.FORMAL_REPLY,
            pcm_chunks=_pcm_chunks(bytes(32000)),
        )
    )
    elapsed = monotonic() - started_at

    assert result.audio_duration_ms == 1000
    assert elapsed >= 0.95
    assert len(emitted_at) == 50
    assert started == ["formal-1"]
    assert completed == ["formal-1"]
    assert interrupted == []
    await coordinator.close()


@pytest.mark.anyio
async def test_cancel_stops_frames_and_emits_only_interrupted() -> None:
    completed: list[str] = []
    interrupted: list[str] = []
    coordinator = AudioOutputCoordinator(
        sample_rate_hz=16000,
        emit_frame=lambda request, pcm: asyncio.sleep(0),
        on_started=lambda request: asyncio.sleep(0),
        on_completed=lambda request, duration: _append_async(
            completed, request.response_id
        ),
        on_interrupted=lambda request: _append_async(
            interrupted, request.response_id
        ),
        is_generation_current=lambda generation: generation == 1,
    )
    play_task = asyncio.create_task(
        coordinator.play(
            AudioOutputRequest(
                response_id="formal-cancelled",
                generation=1,
                kind=OutputKind.FORMAL_REPLY,
                pcm_chunks=_pcm_chunks(bytes(32000)),
            )
        )
    )
    await asyncio.sleep(0.08)
    assert coordinator.active_response_id == "formal-cancelled"
    assert await coordinator.cancel_current() == "formal-cancelled"
    await asyncio.gather(play_task, return_exceptions=True)

    assert completed == []
    assert interrupted == ["formal-cancelled"]
    await coordinator.close()


@pytest.mark.anyio
async def test_empty_pcm_is_completed_without_speech_started() -> None:
    started: list[str] = []
    completed: list[tuple[str, int]] = []
    interrupted: list[str] = []
    coordinator = AudioOutputCoordinator(
        sample_rate_hz=16000,
        emit_frame=lambda request, pcm: asyncio.sleep(0),
        on_started=lambda request: _append_async(started, request.response_id),
        on_completed=lambda request, duration: _append_async(
            completed, (request.response_id, duration)
        ),
        on_interrupted=lambda request: _append_async(
            interrupted, request.response_id
        ),
        is_generation_current=lambda generation: generation == 1,
    )

    result = await coordinator.play(
        AudioOutputRequest(
            response_id="formal-empty",
            generation=1,
            kind=OutputKind.FORMAL_REPLY,
            pcm_chunks=_pcm_chunks(b""),
        )
    )

    assert result.accepted is True
    assert result.cancelled is False
    assert result.audio_duration_ms == 0
    assert started == []
    assert completed == [("formal-empty", 0)]
    assert interrupted == []
    await coordinator.close()


@pytest.mark.anyio
async def test_formal_preempts_notice_without_waiting_for_notice_end() -> None:
    started: list[str] = []
    interrupted: list[str] = []
    coordinator = AudioOutputCoordinator(
        sample_rate_hz=16000,
        emit_frame=lambda request, pcm: asyncio.sleep(0),
        on_started=lambda request: _append_async(started, request.response_id),
        on_completed=lambda request, duration: asyncio.sleep(0),
        on_interrupted=lambda request: _append_async(
            interrupted, request.response_id
        ),
        is_generation_current=lambda generation: generation == 1,
    )
    notice = asyncio.create_task(
        coordinator.play(
            AudioOutputRequest(
                response_id="notice",
                generation=1,
                kind=OutputKind.PROCESSING_ACK,
                pcm_chunks=_pcm_chunks(bytes(16000)),
            )
        )
    )
    await asyncio.sleep(0.03)
    formal_started_at = monotonic()
    formal = asyncio.create_task(
        coordinator.play(
            AudioOutputRequest(
                response_id="formal",
                generation=1,
                kind=OutputKind.FORMAL_REPLY,
                pcm_chunks=_pcm_chunks(bytes(640)),
            )
        )
    )
    await formal

    assert monotonic() - formal_started_at < 0.2
    assert started[:2] == ["notice", "formal"]
    assert interrupted == ["notice"]
    await asyncio.gather(notice, return_exceptions=True)
    await coordinator.close()


async def _append_async(target: list, value) -> None:
    target.append(value)
