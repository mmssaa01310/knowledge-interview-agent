from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from ai_interviewer_voice.schemas.audio import AudioFrame
from ai_interviewer_voice.schemas.events import VoiceRuntimeEvent
from ai_interviewer_voice.schemas.sessions import AssistantReply, VoiceRuntimeContext


class RealtimeVoiceRuntime(Protocol):
    @property
    def provider_name(self) -> str:
        ...

    @property
    def output_sample_rate_hz(self) -> int:
        ...

    async def start(self, context: VoiceRuntimeContext) -> None:
        ...

    async def push_audio(self, frame: AudioFrame) -> None:
        ...

    async def send_reply(self, reply: AssistantReply) -> None:
        ...

    async def interrupt(self) -> None:
        ...

    def events(self) -> AsyncIterator[VoiceRuntimeEvent]:
        ...

    async def close(self) -> None:
        ...
