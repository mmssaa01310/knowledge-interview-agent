"""
Role:
    confirmation prefaceの固定PCMをRuntimeイベントとして送出する。

Summary:
    同梱した24kHz mono s16音声を一意なresponse/generationへ割り当て、
    Nova completionとは独立したAssistant segmentとしてWebRTC経路へ渡す。

Relations:
    Uses RuntimeEventSink and AssistantEventRecorderPort. Used by EvaluationTurnCoordinator.
"""

from __future__ import annotations

import base64
import gzip
from dataclasses import dataclass
from importlib.resources import files
from typing import Protocol

from ai_interviewer_voice.runtimes.nova_sonic.runtime_ports import (
    AssistantEventRecorderPort,
    RuntimeEventSink,
)
from ai_interviewer_voice.runtimes.nova_sonic.session_state import PendingToolCall
from ai_interviewer_voice.schemas.events import (
    AssistantAudioChunk,
    AssistantSpeechEnded,
    AssistantSpeechStarted,
    AssistantTranscriptFinal,
)


PREFACE_TEXT = "確認します。"
PREFACE_SAMPLE_RATE_HZ = 24000
PREFACE_CHANNELS = 1
PREFACE_SAMPLE_WIDTH_BYTES = 2
PCM_CHUNK_BYTES = 960
_ASSET_PACKAGE = "ai_interviewer_voice.runtimes.nova_sonic.assets"
_ASSET_NAME = "confirmation_preface_matthew_24k_s16_mono.pcm.gz.b64"


class SegmentGenerationPort(Protocol):
    def reserve_segment_generation(self) -> int: ...


class AudioSequencePort(Protocol):
    def next_audio_sequence(self) -> int: ...


@dataclass(frozen=True)
class LocalPrefaceSegment:
    response_id: str
    generation: int
    sample_rate_hz: int
    pcm_bytes: int

    @property
    def audio_duration_ms(self) -> float:
        return (
            self.pcm_bytes
            / PREFACE_SAMPLE_WIDTH_BYTES
            / PREFACE_CHANNELS
            / self.sample_rate_hz
            * 1000
        )


class LocalConfirmationPrefacePlayer:
    def __init__(
        self,
        *,
        event_sink: RuntimeEventSink,
        assistant_event_recorder: AssistantEventRecorderPort,
        generation_port: SegmentGenerationPort,
        audio_sequence_port: AudioSequencePort,
    ) -> None:
        self._event_sink = event_sink
        self._assistant_event_recorder = assistant_event_recorder
        self._generation_port = generation_port
        self._audio_sequence_port = audio_sequence_port
        self._pcm = _load_preface_pcm()

    async def enqueue(self, pending: PendingToolCall) -> LocalPrefaceSegment:
        turn_id = (
            pending.trace.turn_id
            if pending.trace is not None and pending.trace.turn_id
            else pending.tool_use_id or pending.completion_id
        )
        response_id = f"local-preface-response:{turn_id}"
        generation = self._generation_port.reserve_segment_generation()
        segment = LocalPrefaceSegment(
            response_id=response_id,
            generation=generation,
            sample_rate_hz=PREFACE_SAMPLE_RATE_HZ,
            pcm_bytes=len(self._pcm),
        )

        await self._event_sink.emit(
            AssistantSpeechStarted(response_id=response_id, generation=generation)
        )
        await self._event_sink.emit(
            AssistantTranscriptFinal(
                text=PREFACE_TEXT,
                response_id=response_id,
                generation=generation,
            )
        )
        for offset in range(0, len(self._pcm), PCM_CHUNK_BYTES):
            await self._event_sink.emit(
                AssistantAudioChunk(
                    response_id=response_id,
                    completion_id=pending.completion_id,
                    generation=generation,
                    sequence=self._audio_sequence_port.next_audio_sequence(),
                    pcm=self._pcm[offset : offset + PCM_CHUNK_BYTES],
                    authorized=True,
                    sample_rate_hz=PREFACE_SAMPLE_RATE_HZ,
                )
            )
        await self._event_sink.emit(
            AssistantSpeechEnded(
                response_id=response_id,
                generation=generation,
                audio_duration_ms=round(segment.audio_duration_ms),
            )
        )
        await self._assistant_event_recorder.record(
            "assistant_transcript_final",
            response_id=response_id,
            generation=generation,
            transcript=PREFACE_TEXT,
            detail={
                "source": "local_fixed_preface",
                "turnId": turn_id,
                "completionId": pending.completion_id,
                "toolUseId": pending.tool_use_id,
            },
        )
        return segment


def _load_preface_pcm() -> bytes:
    encoded = "".join(
        files(_ASSET_PACKAGE).joinpath(_ASSET_NAME).read_text(encoding="ascii").split()
    )
    pcm = gzip.decompress(base64.b64decode(encoded, validate=True))
    if not pcm or len(pcm) % PREFACE_SAMPLE_WIDTH_BYTES != 0:
        raise RuntimeError("invalid local confirmation preface PCM")
    return pcm
