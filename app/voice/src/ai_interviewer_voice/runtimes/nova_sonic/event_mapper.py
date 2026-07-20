from __future__ import annotations

from ai_interviewer_voice.runtimes.nova_sonic.protocol.events import (
    AudioOutputEvent,
    CompletionEndEvent,
    CompletionStartEvent,
    ContentEndEvent,
    ContentStartEvent,
    ErrorEvent,
    NovaSonicProtocolEvent,
    TextOutputEvent,
    ToolResultEvent,
    ToolUseEvent,
    UnknownEvent,
    UsageEvent,
    UserSpeechEndEvent,
    UserSpeechStartEvent,
)
from ai_interviewer_voice.schemas.events import (
    AssistantAudioChunk,
    AssistantSpeechEnded,
    AssistantSpeechStarted,
    AssistantTranscriptFinal,
    RuntimeError,
    UserSpeechEnded,
    UserSpeechStarted,
    VoiceRuntimeEvent,
)


def map_protocol_event(
    event: NovaSonicProtocolEvent,
    *,
    response_id: str | None,
    completion_id: str | None,
    generation: int | None,
    sequence: int,
    authorized: bool,
    transcript_text: str | None = None,
) -> VoiceRuntimeEvent | None:
    if isinstance(event, CompletionStartEvent):
        return AssistantSpeechStarted(response_id=response_id, generation=generation)
    if isinstance(event, ContentStartEvent):
        return AssistantSpeechStarted(response_id=response_id, generation=generation)
    if isinstance(event, TextOutputEvent):
        return AssistantTranscriptFinal(
            text=transcript_text if transcript_text is not None else event.text,
            response_id=response_id,
            generation=generation,
        )
    if isinstance(event, AudioOutputEvent):
        return AssistantAudioChunk(
            response_id=response_id or "unattributed",
            completion_id=completion_id or "unattributed",
            generation=generation or 0,
            sequence=sequence,
            pcm=event.audio_bytes,
            authorized=authorized,
        )
    if isinstance(event, ContentEndEvent):
        return AssistantSpeechEnded(response_id=response_id, generation=generation)
    if isinstance(event, CompletionEndEvent):
        return AssistantSpeechEnded(response_id=response_id, generation=generation)
    if isinstance(event, UsageEvent):
        return None
    if isinstance(event, UserSpeechStartEvent):
        return UserSpeechStarted()
    if isinstance(event, UserSpeechEndEvent):
        return UserSpeechEnded()
    if isinstance(event, (ToolUseEvent, ToolResultEvent)):
        return None
    if isinstance(event, ErrorEvent):
        return RuntimeError(
            message=event.message,
            detail={"code": event.code, "event_type": event.event_type},
        )
    if isinstance(event, UnknownEvent):
        return None
    return None
