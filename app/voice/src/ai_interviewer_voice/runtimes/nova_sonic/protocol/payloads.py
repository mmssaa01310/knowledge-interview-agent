"""
Role:
    Nova Sonicプロトコル送信用payloadの組み立て。

Summary:
    session開始、prompt開始、text/audio入出力などのイベントpayloadを生成し、
    Runtimeとtransportで共有する音声設定定数も提供する。

Relations:
    Used by nova_sonic.runtime and WebRTC transport for protocol requests and audio format settings.
"""

from __future__ import annotations

import base64
import json
from copy import deepcopy
from typing import Any


DEFAULT_PROMPT_NAME = "default-prompt"
SYSTEM_CONTENT_NAME = "system-content"
AUDIO_OUTPUT_SAMPLE_RATE_HZ = 24000
AUDIO_OUTPUT_CHANNELS = 1
AUDIO_OUTPUT_SAMPLE_SIZE_BITS = 16


def build_session_start_event(endpointing_sensitivity: str = "MEDIUM") -> dict[str, Any]:
    return {
        "event": {
            "sessionStart": {
                "inferenceConfiguration": {
                    "maxTokens": 1024,
                    "topP": 0.9,
                    "temperature": 0.7,
                },
                "turnDetectionConfiguration": {
                    "endpointingSensitivity": endpointing_sensitivity,
                },
            }
        }
    }


def build_prompt_start_event(
    prompt_name: str,
    *,
    voice_id: str = "matthew",
    forced_tool_name: str | None = None,
) -> dict[str, Any]:
    payload = {
        "event": {
            "promptStart": {
                "promptName": prompt_name,
                "textOutputConfiguration": {
                    "mediaType": "text/plain",
                },
                "audioOutputConfiguration": {
                    "mediaType": "audio/lpcm",
                    "sampleRateHertz": AUDIO_OUTPUT_SAMPLE_RATE_HZ,
                    "sampleSizeBits": AUDIO_OUTPUT_SAMPLE_SIZE_BITS,
                    "channelCount": AUDIO_OUTPUT_CHANNELS,
                    "voiceId": voice_id,
                    "encoding": "base64",
                    "audioType": "SPEECH",
                },
            }
        }
    }
    if forced_tool_name is not None:
        payload["event"]["promptStart"]["toolUseOutputConfiguration"] = {
            "mediaType": "application/json",
        }
        payload["event"]["promptStart"]["toolConfiguration"] = {
            "tools": [
                {
                    "toolSpec": {
                        "name": forced_tool_name,
                        "description": "Processes the completed user response and returns the exact approved reply that must be spoken.",
                        "inputSchema": {
                            "json": "{\"type\":\"object\",\"properties\":{},\"required\":[]}"
                        },
                    }
                }
            ],
            "toolChoice": {
                "tool": {
                    "name": forced_tool_name,
                }
            },
        }
    return payload


def build_system_content_start_event(prompt_name: str, content_name: str) -> dict[str, Any]:
    return {
        "event": {
            "contentStart": {
                "promptName": prompt_name,
                "contentName": content_name,
                "type": "TEXT",
                "interactive": False,
                "role": "SYSTEM",
                "textInputConfiguration": {
                    "mediaType": "text/plain",
                },
            }
        }
    }


def build_user_text_content_start_event(prompt_name: str, content_name: str) -> dict[str, Any]:
    return {
        "event": {
            "contentStart": {
                "promptName": prompt_name,
                "contentName": content_name,
                "type": "TEXT",
                "interactive": True,
                "role": "USER",
                "textInputConfiguration": {
                    "mediaType": "text/plain",
                },
            }
        }
    }


def build_audio_input_start_event(prompt_name: str, content_name: str) -> dict[str, Any]:
    return {
        "event": {
            "contentStart": {
                "promptName": prompt_name,
                "contentName": content_name,
                "type": "AUDIO",
                "interactive": True,
                "role": "USER",
                "audioInputConfiguration": {
                    "mediaType": "audio/lpcm",
                    "sampleRateHertz": 16000,
                    "sampleSizeBits": 16,
                    "channelCount": 1,
                    "audioType": "SPEECH",
                    "encoding": "base64",
                },
            }
        }
    }


def build_text_input_event(prompt_name: str, content_name: str, text: str) -> dict[str, Any]:
    return {
        "event": {
            "textInput": {
                "promptName": prompt_name,
                "contentName": content_name,
                "content": text,
            }
        }
    }


def build_content_end_event(prompt_name: str, content_name: str) -> dict[str, Any]:
    return {
        "event": {
            "contentEnd": {
                "promptName": prompt_name,
                "contentName": content_name,
            }
        }
    }


def build_runtime_start_sequence(
    *,
    prompt_name: str,
    system_content_name: str,
    system_prompt: str,
    endpointing_sensitivity: str = "MEDIUM",
    voice_id: str = "matthew",
    forced_tool_name: str | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    return [
        ("session_start_sent", build_session_start_event(endpointing_sensitivity)),
        ("prompt_start_sent", build_prompt_start_event(prompt_name, voice_id=voice_id, forced_tool_name=forced_tool_name)),
        ("system_content_start_sent", build_system_content_start_event(prompt_name, system_content_name)),
        ("system_text_sent", build_text_input_event(prompt_name, system_content_name, system_prompt)),
        ("system_content_end_sent", build_content_end_event(prompt_name, system_content_name)),
    ]


def build_user_text_sequence(*, prompt_name: str, content_name: str, text: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        ("user_text_content_start_sent", build_user_text_content_start_event(prompt_name, content_name)),
        ("user_text_sent", build_text_input_event(prompt_name, content_name, text)),
        ("user_text_content_end_sent", build_content_end_event(prompt_name, content_name)),
    ]


def build_audio_start_sequence(*, prompt_name: str, content_name: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        ("audio_content_start_sent", build_audio_input_start_event(prompt_name, content_name)),
    ]


def build_audio_end_sequence(*, prompt_name: str, content_name: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        ("audio_content_end_sent", build_content_end_event(prompt_name, content_name)),
    ]


def build_audio_input_event(*, prompt_name: str, content_name: str, pcm: bytes) -> dict[str, Any]:
    if not isinstance(pcm, bytes):
        raise TypeError("pcm must be bytes")

    content = base64.b64encode(pcm).decode("ascii")
    return {
        "event": {
            "audioInput": {
                "promptName": prompt_name,
                "contentName": content_name,
                "content": content,
            }
        }
    }


def build_prompt_end_event(prompt_name: str) -> dict[str, Any]:
    return {
        "event": {
            "promptEnd": {
                "promptName": prompt_name,
            }
        }
    }


def build_session_end_event() -> dict[str, Any]:
    return {
        "event": {
            "sessionEnd": {}
        }
    }


def build_tool_result_start_event(
    *,
    prompt_name: str,
    content_name: str,
    tool_use_id: str,
) -> dict[str, Any]:
    return {
        "event": {
            "contentStart": {
                "promptName": prompt_name,
                "contentName": content_name,
                "interactive": False,
                "type": "TOOL",
                "role": "TOOL",
                "toolResultInputConfiguration": {
                    "toolUseId": tool_use_id,
                    "type": "TEXT",
                    "textInputConfiguration": {
                        "mediaType": "text/plain",
                    },
                },
            }
        }
    }


def build_tool_result_event(
    *,
    prompt_name: str,
    content_name: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "event": {
            "toolResult": {
                "promptName": prompt_name,
                "contentName": content_name,
                "content": json.dumps(result, ensure_ascii=False),
            }
        }
    }


def build_tool_result_sequence(
    *,
    prompt_name: str,
    content_name: str,
    tool_use_id: str,
    result: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    return [
        ("tool_result_content_start_sent", build_tool_result_start_event(prompt_name=prompt_name, content_name=content_name, tool_use_id=tool_use_id)),
        ("tool_result_sent", build_tool_result_event(prompt_name=prompt_name, content_name=content_name, result=result)),
        ("tool_result_content_end_sent", build_content_end_event(prompt_name, content_name)),
    ]


def dumps_event_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def sanitize_payload_for_debug(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = deepcopy(payload)
    _scrub(sanitized)
    return sanitized


def _scrub(node: Any) -> None:
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if value is None:
                node.pop(key)
                continue
            if key in {"text", "content"}:
                node[key] = "<redacted>"
                continue
            _scrub(value)
    elif isinstance(node, list):
        for value in node:
            _scrub(value)
