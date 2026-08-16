"""
Role:
    WebRTC data channelへのvoiceイベント配送。

Summary:
    backendのruntimeイベントをfrontend向けJSONへ変換し、
    channel open前のpayload保持とopen後のflushも扱う。

Relations:
    Uses voice event schemas.
    Used by WebRTC peer_connection to notify browser state and assistant segment events.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from aiortc import RTCDataChannel

from ai_interviewer_voice.schemas.events import (
    AssistantBackchannel,
    AssistantInterrupted,
    AssistantResponsePreparing,
    AssistantSpeechEnded,
    AssistantSpeechStarted,
    AssistantTranscriptFinal,
    InputStateChanged,
    RuntimeError,
    RuntimeReconnecting,
    UserSpeechEnded,
    UserSpeechStarted,
    UserTranscriptFinal,
    UserTranscriptPartial,
    VoiceRuntimeEvent,
)


@dataclass(frozen=True)
class VoiceEventContext:
    voice_session_id: str
    question_id: str | None = None
    state_version: int | None = None
    interview_status: str | None = None


class VoiceEventsDataChannel:
    def __init__(self) -> None:
        self._channel: RTCDataChannel | None = None
        self._pending_payloads: list[dict[str, Any]] = []

    def bind(self, channel: RTCDataChannel) -> None:
        if channel.label != "voice-events":
            return
        self._channel = channel
        self.flush_pending()

    def close(self) -> None:
        if self._channel is None:
            return
        self._channel.close()

    def flush_pending(self) -> None:
        if self._channel is None or self._channel.readyState != "open":
            return
        pending = self._pending_payloads
        self._pending_payloads = []
        for payload in pending:
            self._channel.send(json.dumps(payload, ensure_ascii=False))

    def send_connection_state(self, *, voice_session_id: str, state: str) -> None:
        self._send_json(
            {
                "type": "connection_state",
                "voiceSessionId": voice_session_id,
                "state": state,
            }
        )

    def send_runtime_ready(self, *, context: VoiceEventContext) -> None:
        self._send_json(
            {
                "type": "runtime_ready",
                "voiceSessionId": context.voice_session_id,
            }
        )

    def send_interview_state(self, *, context: VoiceEventContext) -> None:
        self._send_json(
            {
                "type": "interview_state",
                "voiceSessionId": context.voice_session_id,
                "status": context.interview_status,
                "questionId": context.question_id,
                "stateVersion": context.state_version,
            }
        )

    def send_interview_completed(self, *, context: VoiceEventContext) -> None:
        self._send_json(
            {
                "type": "interview_completed",
                "voiceSessionId": context.voice_session_id,
                "status": context.interview_status,
                "questionId": context.question_id,
                "stateVersion": context.state_version,
            }
        )

    def send_initial_reply_sent(self, *, context: VoiceEventContext, response_id: str) -> None:
        self._send_json(
            {
                "type": "initial_reply_sent",
                "voiceSessionId": context.voice_session_id,
                "responseId": response_id,
                "questionId": context.question_id,
                "stateVersion": context.state_version,
            }
        )

    def send_event(self, event: VoiceRuntimeEvent, *, context: VoiceEventContext) -> None:
        payload = _serialize_runtime_event(event, context=context)
        if payload is None:
            return
        self._send_json(payload)

    def _send_json(self, payload: dict[str, Any]) -> None:
        if self._channel is None:
            self._pending_payloads.append(payload)
            return
        if self._channel.readyState != "open":
            self._pending_payloads.append(payload)
            return
        self.flush_pending()
        self._channel.send(json.dumps(payload, ensure_ascii=False))


def _serialize_runtime_event(event: VoiceRuntimeEvent, *, context: VoiceEventContext) -> dict[str, Any] | None:
    if isinstance(event, UserSpeechStarted):
        return {"type": "user_speech_started", "voiceSessionId": context.voice_session_id}
    if isinstance(event, UserSpeechEnded):
        return {"type": "user_speech_ended", "voiceSessionId": context.voice_session_id}
    if isinstance(event, UserTranscriptPartial):
        return {"type": "user_transcript_partial", "voiceSessionId": context.voice_session_id, "text": event.text}
    if isinstance(event, UserTranscriptFinal):
        return {
            "type": "user_transcript_final",
            "voiceSessionId": context.voice_session_id,
            "text": event.text,
            "turnType": "ANSWER",
            "questionId": context.question_id,
            "stateVersion": context.state_version,
        }
    if isinstance(event, InputStateChanged):
        return {
            "type": "input_state_changed",
            "voiceSessionId": context.voice_session_id,
            "inputState": event.input_state,
            "generation": event.generation,
        }
    if isinstance(event, AssistantSpeechStarted):
        return {
            "type": "assistant_speech_started",
            "voiceSessionId": context.voice_session_id,
            "responseId": event.response_id,
            "generation": event.generation,
        }
    if isinstance(event, AssistantResponsePreparing):
        return {
            "type": "assistant_response_preparing",
            "voiceSessionId": context.voice_session_id,
            "responseId": event.response_id,
            "generation": event.generation,
        }
    if isinstance(event, AssistantTranscriptFinal):
        return {
            "type": "assistant_transcript_final",
            "voiceSessionId": context.voice_session_id,
            "responseId": event.response_id,
            "generation": event.generation,
            "text": event.text,
            "questionId": context.question_id,
            "stateVersion": context.state_version,
        }
    if isinstance(event, AssistantSpeechEnded):
        return {
            "type": "assistant_speech_ended",
            "voiceSessionId": context.voice_session_id,
            "responseId": event.response_id,
            "generation": event.generation,
            "audioDurationMs": event.audio_duration_ms,
        }
    if isinstance(event, AssistantInterrupted):
        return {
            "type": "assistant_interrupted",
            "voiceSessionId": context.voice_session_id,
            "responseId": event.response_id,
            "generation": event.generation,
        }
    if isinstance(event, AssistantBackchannel):
        return {
            "type": "assistant_backchannel",
            "voiceSessionId": context.voice_session_id,
            "kind": event.kind,
            "responseId": event.response_id,
            "generation": event.generation,
        }
    if isinstance(event, RuntimeReconnecting):
        return {
            "type": "runtime_reconnecting",
            "voiceSessionId": context.voice_session_id,
        }
    if isinstance(event, RuntimeError):
        return {
            "type": "error",
            "voiceSessionId": context.voice_session_id,
            "message": event.message,
            "fatal": event.fatal,
        }
    return None
