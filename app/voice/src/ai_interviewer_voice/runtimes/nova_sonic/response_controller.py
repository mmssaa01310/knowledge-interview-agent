"""
Role:
    Nova Sonic応答の承認状態を管理する制御器。

Summary:
    user発話ごとのgeneration、承認済みresponse、active completionを追跡し、
    どの音声・テキストイベントを採用するかを判定する。

Relations:
    Uses voice event schemas and AssistantReply schema.
    Used by nova_sonic.runtime to gate reply dispatch and event acceptance.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ai_interviewer_voice.schemas.events import (
    AssistantAudioChunk,
    AssistantSpeechEnded,
    AssistantSpeechStarted,
    AssistantTranscriptFinal,
)
from ai_interviewer_voice.schemas.sessions import AssistantReply


class ResponseAuthorizationState(str, Enum):
    BLOCKED = "blocked"
    WAITING_FOR_APPROVED_REPLY = "waiting_for_approved_reply"
    APPROVED_REPLY_PENDING = "approved_reply_pending"
    APPROVED_REPLY_STREAMING = "approved_reply_streaming"


@dataclass(frozen=True)
class AuthorizedResponse:
    response_id: str
    generation: int


@dataclass(frozen=True)
class PendingApprovedReply:
    response_id: str
    text: str
    sent_at_ms: int
    bound_completion_id: str | None = None


class ResponseController:
    def __init__(self) -> None:
        self._generation = 0
        self._active_response_id: str | None = None
        self._active_completion_id: str | None = None
        self._state = ResponseAuthorizationState.BLOCKED
        self._pending_reply: PendingApprovedReply | None = None

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def active_response_id(self) -> str | None:
        return self._active_response_id

    @property
    def active_completion_id(self) -> str | None:
        return self._active_completion_id

    @property
    def authorization_state(self) -> ResponseAuthorizationState:
        return self._state

    @property
    def pending_reply(self) -> PendingApprovedReply | None:
        return self._pending_reply

    def on_user_speech_started(self) -> int:
        self._generation += 1
        self._active_response_id = None
        self._active_completion_id = None
        self._pending_reply = None
        self._state = ResponseAuthorizationState.BLOCKED
        return self._generation

    def on_user_transcript_final(self) -> None:
        self._state = ResponseAuthorizationState.WAITING_FOR_APPROVED_REPLY

    def reserve_segment_generation(self) -> int:
        self._generation += 1
        return self._generation

    def reset_to_blocked_if_idle(self) -> None:
        if self._active_response_id is not None:
            return
        if self._active_completion_id is not None:
            return
        if self._pending_reply is not None:
            return
        self._state = ResponseAuthorizationState.BLOCKED

    def authorize(self, reply: AssistantReply, *, sent_at_ms: int) -> AuthorizedResponse:
        self._active_response_id = reply.response_id
        self._active_completion_id = None
        self._pending_reply = PendingApprovedReply(
            response_id=reply.response_id,
            text=reply.text,
            sent_at_ms=sent_at_ms,
            bound_completion_id=None,
        )
        self._state = ResponseAuthorizationState.APPROVED_REPLY_PENDING
        return AuthorizedResponse(
            response_id=reply.response_id,
            generation=self._generation,
        )

    def bind_completion(
        self,
        *,
        completion_id: str,
        completion_started_at_ms: int,
    ) -> bool:
        if self._pending_reply is None:
            return False
        if completion_started_at_ms < self._pending_reply.sent_at_ms:
            return False
        self._pending_reply = PendingApprovedReply(
            response_id=self._pending_reply.response_id,
            text=self._pending_reply.text,
            sent_at_ms=self._pending_reply.sent_at_ms,
            bound_completion_id=completion_id,
        )
        self._active_completion_id = completion_id
        self._state = ResponseAuthorizationState.APPROVED_REPLY_STREAMING
        return True

    def on_completion_finished(self, completion_id: str) -> None:
        if completion_id != self._active_completion_id:
            return
        self._active_response_id = None
        self._active_completion_id = None
        self._pending_reply = None
        self._state = ResponseAuthorizationState.BLOCKED

    def interrupt(self) -> AuthorizedResponse | None:
        if self._active_response_id is None:
            self._active_completion_id = None
            self._pending_reply = None
            self._state = ResponseAuthorizationState.BLOCKED
            return None
        interrupted = AuthorizedResponse(
            response_id=self._active_response_id,
            generation=self._generation,
        )
        self._generation += 1
        self._active_response_id = None
        self._active_completion_id = None
        self._pending_reply = None
        self._state = ResponseAuthorizationState.BLOCKED
        return interrupted

    def is_authorized_completion(self, completion_id: str | None) -> bool:
        return completion_id is not None and completion_id == self._active_completion_id

    def accepts_audio_chunk(self, chunk: AssistantAudioChunk) -> bool:
        return (
            chunk.authorized
            and chunk.response_id == self._active_response_id
            and chunk.completion_id == self._active_completion_id
            and chunk.generation == self._generation
        )

    def accepts_transcript(self, event: AssistantTranscriptFinal, *, completion_id: str | None) -> bool:
        return (
            completion_id is not None
            and completion_id == self._active_completion_id
            and event.response_id == self._active_response_id
            and event.generation == self._generation
        )

    def accepts_speech_started(self, event: AssistantSpeechStarted, *, completion_id: str | None) -> bool:
        return (
            completion_id is not None
            and completion_id == self._active_completion_id
            and event.response_id == self._active_response_id
            and event.generation == self._generation
        )

    def accepts_speech_ended(self, event: AssistantSpeechEnded, *, completion_id: str | None) -> bool:
        return (
            completion_id is not None
            and completion_id == self._active_completion_id
            and event.response_id == self._active_response_id
            and event.generation == self._generation
        )
