from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator

from ai_interviewer_voice.runtimes.base import RealtimeVoiceRuntime
from ai_interviewer_voice.schemas.audio import AudioFrame
from ai_interviewer_voice.schemas.events import (
    AssistantAudioChunk,
    AssistantSpeechEnded,
    AssistantSpeechStarted,
    RuntimeClosed,
    RuntimeReady,
    UserSpeechEnded,
    UserSpeechStarted,
    UserTranscriptFinal,
    VoiceRuntimeEvent,
)
from ai_interviewer_voice.schemas.sessions import VoiceRuntimeContext


class FakeRuntime(RealtimeVoiceRuntime):
    def __init__(self) -> None:
        self._events: asyncio.Queue[VoiceRuntimeEvent] = asyncio.Queue()
        self._started = False
        self._closed = False
        self._audio_sequence = 0
        self._frames_received = 0
        self._speech_active = False

    @property
    def provider_name(self) -> str:
        return "fake"

    async def start(self, context: VoiceRuntimeContext) -> None:
        self._started = True
        await self._events.put(RuntimeReady())

    async def push_audio(self, frame: AudioFrame) -> None:
        if not self._started or self._closed:
            return
        self._frames_received += 1
        if not self._speech_active:
            self._speech_active = True
            await self._events.put(UserSpeechStarted())
            await self._events.put(AssistantSpeechStarted(response_id="fake-response", generation=1))

        if self._frames_received == 8:
            await self._events.put(UserTranscriptFinal(text="fake runtime transcript"))

        for chunk in _fake_pcm_chunks(sample_rate_hz=24000, duration_ms=20, count=1):
            self._audio_sequence += 1
            await self._events.put(
                AssistantAudioChunk(
                    response_id="fake-response",
                    completion_id="fake-completion",
                    generation=1,
                    sequence=self._audio_sequence,
                    pcm=chunk,
                    authorized=True,
                )
            )

        if self._frames_received == 10:
            await self._events.put(UserSpeechEnded())
            await self._events.put(AssistantSpeechEnded(response_id="fake-response", generation=1))

    async def send_reply(self, reply) -> None:
        if not self._started or self._closed:
            return
        await self._events.put(AssistantSpeechStarted(response_id=reply.response_id, generation=1))
        for chunk in _fake_pcm_chunks(sample_rate_hz=24000, duration_ms=20, count=2):
            self._audio_sequence += 1
            await self._events.put(
                AssistantAudioChunk(
                    response_id=reply.response_id,
                    completion_id=f"{reply.response_id}-completion",
                    generation=1,
                    sequence=self._audio_sequence,
                    pcm=chunk,
                    authorized=True,
                )
            )
        await self._events.put(AssistantSpeechEnded(response_id=reply.response_id, generation=1))

    async def interrupt(self) -> None:
        return None

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._events.put(RuntimeClosed())

    async def _event_generator(self) -> AsyncIterator[VoiceRuntimeEvent]:
        while True:
            event = await self._events.get()
            yield event
            if isinstance(event, RuntimeClosed):
                break

    def events(self) -> AsyncIterator[VoiceRuntimeEvent]:
        return self._event_generator()


def _fake_pcm_chunks(*, sample_rate_hz: int, duration_ms: int, count: int) -> list[bytes]:
    samples = int(sample_rate_hz * (duration_ms / 1000.0))
    chunks: list[bytes] = []
    for _ in range(count):
        data = bytearray()
        for i in range(samples):
            sample = int(3000 * math.sin((2.0 * math.pi * 440.0 * i) / sample_rate_hz))
            data.extend(int(sample).to_bytes(2, "little", signed=True))
        chunks.append(bytes(data))
    return chunks
