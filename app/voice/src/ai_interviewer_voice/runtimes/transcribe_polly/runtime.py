from __future__ import annotations

from collections.abc import AsyncIterator

from ai_interviewer_voice.schemas.audio import AudioFrame
from ai_interviewer_voice.schemas.events import VoiceRuntimeEvent
from ai_interviewer_voice.schemas.sessions import AssistantReply, VoiceRuntimeContext


class TranscribePollyRuntime:
    @property
    def provider_name(self) -> str:
        return "transcribe_polly"

    async def start(self, context: VoiceRuntimeContext) -> None:
        raise NotImplementedError("transcribe_polly runtime is not implemented in v1")

    async def push_audio(self, frame: AudioFrame) -> None:
        raise NotImplementedError("transcribe_polly runtime is not implemented in v1")

    async def send_reply(self, reply: AssistantReply) -> None:
        raise NotImplementedError("transcribe_polly runtime is not implemented in v1")

    async def interrupt(self) -> None:
        raise NotImplementedError("transcribe_polly runtime is not implemented in v1")

    def events(self) -> AsyncIterator[VoiceRuntimeEvent]:
        raise NotImplementedError("transcribe_polly runtime is not implemented in v1")

    async def close(self) -> None:
        raise NotImplementedError("transcribe_polly runtime is not implemented in v1")
