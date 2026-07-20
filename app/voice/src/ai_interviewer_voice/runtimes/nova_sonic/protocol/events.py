from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class CompletionStartEvent:
    completion_id: str | None = None
    generation_stage: str = "unknown"
    interrupted: bool | None = None
    event_type: Literal["completion_start"] = "completion_start"


@dataclass(frozen=True)
class ContentStartEvent:
    content_id: str | None = None
    completion_id: str | None = None
    role: str | None = None
    modality: str | None = None
    generation_stage: str = "unknown"
    interrupted: bool | None = None
    event_type: Literal["content_start"] = "content_start"


@dataclass(frozen=True)
class TextOutputEvent:
    text: str
    content_id: str | None = None
    completion_id: str | None = None
    generation_stage: str = "unknown"
    interrupted: bool | None = None
    event_type: Literal["text_output"] = "text_output"


@dataclass(frozen=True)
class AudioOutputEvent:
    audio_bytes: bytes
    content_id: str | None = None
    completion_id: str | None = None
    generation_stage: str = "unknown"
    interrupted: bool | None = None
    event_type: Literal["audio_output"] = "audio_output"


@dataclass(frozen=True)
class ContentEndEvent:
    content_id: str | None = None
    completion_id: str | None = None
    role: str | None = None
    modality: str | None = None
    stop_reason: str | None = None
    generation_stage: str = "unknown"
    interrupted: bool | None = None
    event_type: Literal["content_end"] = "content_end"


@dataclass(frozen=True)
class CompletionEndEvent:
    completion_id: str | None = None
    stop_reason: str | None = None
    generation_stage: str = "unknown"
    interrupted: bool | None = None
    event_type: Literal["completion_end"] = "completion_end"


@dataclass(frozen=True)
class UsageEvent:
    input_tokens: int | None = None
    output_tokens: int | None = None
    event_type: Literal["usage_event"] = "usage_event"


@dataclass(frozen=True)
class UserSpeechStartEvent:
    content_id: str | None = None
    completion_id: str | None = None
    event_type: Literal["user_speech_start"] = "user_speech_start"


@dataclass(frozen=True)
class UserSpeechEndEvent:
    content_id: str | None = None
    completion_id: str | None = None
    event_type: Literal["user_speech_end"] = "user_speech_end"


@dataclass(frozen=True)
class ToolUseEvent:
    tool_use_id: str | None = None
    tool_name: str | None = None
    content: str | None = None
    content_id: str | None = None
    completion_id: str | None = None
    event_type: Literal["tool_use"] = "tool_use"


@dataclass(frozen=True)
class ToolResultEvent:
    tool_use_id: str | None = None
    content: str | None = None
    content_id: str | None = None
    completion_id: str | None = None
    event_type: Literal["tool_result"] = "tool_result"


@dataclass(frozen=True)
class ErrorEvent:
    code: str
    message: str
    content_id: str | None = None
    completion_id: str | None = None
    event_type: Literal["error"] = "error"


@dataclass(frozen=True)
class UnknownEvent:
    raw_event_type: str | None
    event_keys: tuple[str, ...]
    top_level_keys: tuple[str, ...]
    completion_id: str | None = None
    content_id: str | None = None
    safe_shape: dict[str, Any] | None = None
    event_type: Literal["unknown_event"] = "unknown_event"


NovaSonicProtocolEvent = (
    CompletionStartEvent
    | ContentStartEvent
    | TextOutputEvent
    | AudioOutputEvent
    | ContentEndEvent
    | CompletionEndEvent
    | UsageEvent
    | UserSpeechStartEvent
    | UserSpeechEndEvent
    | ToolUseEvent
    | ToolResultEvent
    | ErrorEvent
    | UnknownEvent
)


def decode_output_bytes(payload: bytes, active_content_id: str | None = None) -> NovaSonicProtocolEvent:
    if not payload:
        return UnknownEvent(
            raw_event_type="empty",
            event_keys=(),
            top_level_keys=(),
            content_id=active_content_id,
            safe_shape={"top_level_keys": [], "event_keys": []},
        )

    try:
        decoded = payload.decode("utf-8")
        document = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return AudioOutputEvent(audio_bytes=payload, content_id=active_content_id)

    if not isinstance(document, dict):
        return UnknownEvent(
            raw_event_type=type(document).__name__,
            event_keys=(),
            top_level_keys=(),
            content_id=active_content_id,
            safe_shape={"top_level_keys": [], "event_keys": []},
        )

    top_level_keys = tuple(str(key) for key in document.keys())
    event_container = document.get("event")
    if not isinstance(event_container, dict):
        return UnknownEvent(
            raw_event_type=None,
            event_keys=(),
            top_level_keys=top_level_keys,
            content_id=active_content_id,
            safe_shape=_build_safe_shape(document, top_level_keys, ()),
        )

    event_keys = tuple(str(key) for key in event_container.keys())
    fields = _resolve_event_fields(event_container, active_content_id=active_content_id)
    additional = _parse_additional_model_fields(fields.event_payload)

    if "completionStart" in event_container:
        return CompletionStartEvent(
            completion_id=fields.completion_id,
            generation_stage=additional["generationStage"],
            interrupted=additional["interrupted"],
        )
    if "contentStart" in event_container:
        return ContentStartEvent(
            content_id=fields.content_id,
            completion_id=fields.completion_id,
            role=_extract_str(fields.event_payload, "role"),
            modality=_extract_str(fields.event_payload, "type") or _extract_str(fields.event_payload, "contentType"),
            generation_stage=additional["generationStage"],
            interrupted=additional["interrupted"],
        )
    if "textOutput" in event_container:
        return TextOutputEvent(
            text=str(fields.event_payload.get("content") or fields.event_payload.get("text") or ""),
            content_id=fields.content_id,
            completion_id=fields.completion_id,
            generation_stage=additional["generationStage"],
            interrupted=additional["interrupted"],
        )
    if "audioOutput" in event_container:
        return AudioOutputEvent(
            audio_bytes=_extract_audio_bytes(fields.event_payload),
            content_id=fields.content_id,
            completion_id=fields.completion_id,
            generation_stage=additional["generationStage"],
            interrupted=additional["interrupted"],
        )
    if "contentEnd" in event_container:
        return ContentEndEvent(
            content_id=fields.content_id,
            completion_id=fields.completion_id,
            role=_extract_str(fields.event_payload, "role"),
            modality=_extract_str(fields.event_payload, "type") or _extract_str(fields.event_payload, "contentType"),
            stop_reason=_extract_str(fields.event_payload, "stopReason"),
            generation_stage=additional["generationStage"],
            interrupted=additional["interrupted"],
        )
    if "completionEnd" in event_container:
        return CompletionEndEvent(
            completion_id=fields.completion_id,
            stop_reason=_extract_str(fields.event_payload, "stopReason"),
            generation_stage=additional["generationStage"],
            interrupted=additional["interrupted"],
        )
    if "usageEvent" in event_container:
        return UsageEvent(
            input_tokens=_extract_int(fields.event_payload, "inputTokens"),
            output_tokens=_extract_int(fields.event_payload, "outputTokens"),
        )
    if "userSpeechStart" in event_container:
        return UserSpeechStartEvent(
            content_id=fields.content_id,
            completion_id=fields.completion_id,
        )
    if "userSpeechEnd" in event_container:
        return UserSpeechEndEvent(
            content_id=fields.content_id,
            completion_id=fields.completion_id,
        )
    if "toolUse" in event_container:
        return ToolUseEvent(
            tool_use_id=_extract_str(fields.event_payload, "toolUseId"),
            tool_name=_extract_str(fields.event_payload, "toolName") or _extract_str(fields.event_payload, "name"),
            content=_extract_str(fields.event_payload, "content"),
            content_id=fields.content_id,
            completion_id=fields.completion_id,
        )
    if "toolResult" in event_container:
        return ToolResultEvent(
            tool_use_id=_extract_str(fields.event_payload, "toolUseId"),
            content=_extract_str(fields.event_payload, "content"),
            content_id=fields.content_id,
            completion_id=fields.completion_id,
        )
    if "error" in event_container:
        return ErrorEvent(
            code=str(fields.event_payload.get("code") or "error"),
            message=str(fields.event_payload.get("message") or "unknown error"),
            content_id=fields.content_id,
            completion_id=fields.completion_id,
        )

    first_key = event_keys[0] if event_keys else None
    return UnknownEvent(
        raw_event_type=first_key,
        event_keys=event_keys,
        top_level_keys=top_level_keys,
        completion_id=fields.completion_id,
        content_id=fields.content_id,
        safe_shape=_build_safe_shape(document, top_level_keys, event_keys),
    )


@dataclass(frozen=True)
class _ResolvedEventFields:
    event_payload: dict[str, Any]
    content_id: str | None
    completion_id: str | None


def _resolve_event_fields(event_container: dict[str, Any], *, active_content_id: str | None) -> _ResolvedEventFields:
    first_key = next(iter(event_container.keys()), None)
    payload = event_container.get(first_key) if first_key is not None else {}
    if not isinstance(payload, dict):
        payload = {}
    return _ResolvedEventFields(
        event_payload=payload,
        content_id=_extract_content_id(payload) or active_content_id,
        completion_id=_extract_completion_id(payload),
    )


def _extract_content_id(value: dict[str, Any]) -> str | None:
    for key in ("contentName", "contentId"):
        resolved = value.get(key)
        if resolved:
            return str(resolved)
    return None


def _extract_completion_id(value: dict[str, Any]) -> str | None:
    for key in ("completionId",):
        resolved = value.get(key)
        if resolved:
            return str(resolved)
    return None


def _extract_str(value: dict[str, Any], key: str) -> str | None:
    resolved = value.get(key)
    if resolved is None:
        return None
    return str(resolved)


def _extract_int(value: dict[str, Any], key: str) -> int | None:
    resolved = value.get(key)
    if isinstance(resolved, int):
        return resolved
    return None


def _extract_audio_bytes(value: dict[str, Any]) -> bytes:
    content = value.get("content")
    if isinstance(content, str):
        try:
            return base64.b64decode(content, validate=True)
        except Exception:
            return b""
    if isinstance(content, bytes):
        return content
    audio = value.get("bytes")
    if isinstance(audio, bytes):
        return audio
    if isinstance(audio, str):
        try:
            return base64.b64decode(audio, validate=True)
        except Exception:
            return b""
    return b""


def _parse_additional_model_fields(value: dict[str, Any]) -> dict[str, Any]:
    raw = value.get("additionalModelFields")
    parsed: dict[str, Any] = {}
    if isinstance(raw, str):
        try:
            candidate = json.loads(raw)
            if isinstance(candidate, dict):
                parsed = candidate
        except json.JSONDecodeError:
            parsed = {}
    elif isinstance(raw, dict):
        parsed = raw

    generation_stage = parsed.get("generationStage")
    if generation_stage not in {"FINAL", "SPECULATIVE"}:
        generation_stage = "unknown"
    interrupted = parsed.get("interrupted")
    if not isinstance(interrupted, bool):
        interrupted = None
    return {
        "generationStage": generation_stage,
        "interrupted": interrupted,
    }


def _build_safe_shape(
    document: dict[str, Any],
    top_level_keys: tuple[str, ...],
    event_keys: tuple[str, ...],
) -> dict[str, Any]:
    event_container = document.get("event")
    first_payload = {}
    if isinstance(event_container, dict):
        first_key = next(iter(event_container.keys()), None)
        candidate = event_container.get(first_key) if first_key is not None else {}
        if isinstance(candidate, dict):
            first_payload = candidate

    return {
        "top_level_keys": list(top_level_keys),
        "event_keys": list(event_keys),
        "exception_keys": list(document.get("exception", {}).keys()) if isinstance(document.get("exception"), dict) else [],
        "role": _extract_str(first_payload, "role"),
        "type": _extract_str(first_payload, "type") or _extract_str(first_payload, "contentType"),
        "stopReason": _extract_str(first_payload, "stopReason"),
        "generationStage": _parse_additional_model_fields(first_payload)["generationStage"],
        "sessionId_present": bool(first_payload.get("sessionId")),
        "promptName_present": bool(first_payload.get("promptName")),
        "completionId_present": bool(_extract_completion_id(first_payload)),
        "contentId_present": bool(_extract_content_id(first_payload)),
        "content_length": _safe_content_length(first_payload.get("content"), treat_audio=False),
        "audio_content_length": _safe_content_length(first_payload.get("content"), treat_audio=True),
    }


def _safe_content_length(content: Any, *, treat_audio: bool) -> int:
    if isinstance(content, bytes):
        return len(content) if treat_audio else 0
    if isinstance(content, str):
        if treat_audio:
            try:
                return len(base64.b64decode(content, validate=True))
            except Exception:
                return 0
        return len(content)
    return 0
