"""
Role:
    realtime voice層で共有するイベント契約の定義。

Summary:
    Runtime、WebRTC transport、frontend data channel間で受け渡す
    user/assistant/connection関連イベントを型として揃える。

Relations:
    Used by runtimes, WebRTC transport, and frontend event serialization paths.
"""

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class RuntimeReady:
    event_type: Literal["runtime_ready"] = "runtime_ready"


@dataclass(frozen=True)
class UserSpeechStarted:
    event_type: Literal["user_speech_started"] = "user_speech_started"


@dataclass(frozen=True)
class UserSpeechEnded:
    event_type: Literal["user_speech_ended"] = "user_speech_ended"


@dataclass(frozen=True)
class UserTranscriptPartial:
    text: str
    event_type: Literal["user_transcript_partial"] = "user_transcript_partial"


@dataclass(frozen=True)
class UserTranscriptFinal:
    text: str
    event_type: Literal["user_transcript_final"] = "user_transcript_final"


@dataclass(frozen=True)
class InputStateChanged:
    input_state: Literal[
        "ASSISTANT_SPEAKING",
        "ANSWER_LISTENING",
        "ANSWER_PROCESSING",
        "CONFIRMATION_LISTENING",
        "INTERVIEW_COMPLETED",
        "INPUT_UNAVAILABLE",
    ]
    generation: int | None = None
    event_type: Literal["input_state_changed"] = "input_state_changed"


@dataclass(frozen=True)
class AssistantAudioChunk:
    response_id: str
    completion_id: str
    generation: int
    sequence: int
    pcm: bytes
    authorized: bool
    sample_rate_hz: int = 24000
    event_type: Literal["assistant_audio_chunk"] = "assistant_audio_chunk"


@dataclass(frozen=True)
class AssistantTranscriptFinal:
    text: str
    response_id: str | None = None
    generation: int | None = None
    event_type: Literal["assistant_transcript_final"] = "assistant_transcript_final"


@dataclass(frozen=True)
class AssistantSpeechStarted:
    response_id: str | None = None
    generation: int | None = None
    event_type: Literal["assistant_speech_started"] = "assistant_speech_started"


@dataclass(frozen=True)
class AssistantResponsePreparing:
    response_id: str | None = None
    generation: int | None = None
    event_type: Literal["assistant_response_preparing"] = "assistant_response_preparing"


@dataclass(frozen=True)
class AssistantSpeechEnded:
    response_id: str | None = None
    generation: int | None = None
    audio_duration_ms: int | None = None
    event_type: Literal["assistant_speech_ended"] = "assistant_speech_ended"


@dataclass(frozen=True)
class AssistantInterrupted:
    response_id: str | None = None
    generation: int | None = None
    event_type: Literal["assistant_interrupted"] = "assistant_interrupted"


@dataclass(frozen=True)
class AssistantBackchannel:
    kind: Literal["listen_ack", "processing_ack", "long_processing_notice"]
    response_id: str
    generation: int
    text: str
    event_type: Literal["assistant_backchannel"] = "assistant_backchannel"


@dataclass(frozen=True)
class RuntimeReconnecting:
    event_type: Literal["runtime_reconnecting"] = "runtime_reconnecting"


@dataclass(frozen=True)
class RuntimeError:
    message: str
    detail: dict = field(default_factory=dict)
    fatal: bool = True
    event_type: Literal["runtime_error"] = "runtime_error"


@dataclass(frozen=True)
class RuntimeClosed:
    event_type: Literal["runtime_closed"] = "runtime_closed"


VoiceRuntimeEvent = (
    RuntimeReady
    | InputStateChanged
    | UserSpeechStarted
    | UserSpeechEnded
    | UserTranscriptPartial
    | UserTranscriptFinal
    | AssistantAudioChunk
    | AssistantTranscriptFinal
    | AssistantSpeechStarted
    | AssistantResponsePreparing
    | AssistantSpeechEnded
    | AssistantInterrupted
    | AssistantBackchannel
    | RuntimeReconnecting
    | RuntimeError
    | RuntimeClosed
)
