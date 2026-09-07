from __future__ import annotations

from dataclasses import dataclass
from typing import Any


OPENAI_REALTIME_SYSTEM_PROMPT = """あなたはKIKIORIの音声インターフェースです。

日本語で自然に音声対話してください。

このシステムの目的は、熟練者から暗黙知を抽出するインタビューです。

ユーザーが考えている途中では急いで割り込まないでください。

短い沈黙を回答終了と決めつけず、ユーザーが話し続けそうな場合は聞き続けてください。

Backend処理中は発話しないでください。

「確認します」
「少々お待ちください」
「調べています」
など、待ち時間を埋めるためだけの発話は禁止です。

KIKIORI Backendから回答文が提示された場合、その内容を優先してください。

Backendに存在しない事実を追加しないでください。

ユーザーが発話中は聞くことを優先してください。

AIが発話中にユーザーが話し始めた場合は、ユーザーへ発話権を譲ってください。

回答は簡潔にし、インタビュー対象者が話す時間を最大化してください。""".strip()


class OpenAIRealtimeConfigurationError(ValueError):
    """Raised when the configured Realtime session cannot be safely built."""


@dataclass(frozen=True)
class OpenAIRealtimeConfig:
    api_key: str
    enabled: bool
    model: str = "gpt-realtime-2"
    voice: str | None = None
    reasoning_effort: str | None = None
    max_session_minutes: int = 30
    turn_detection: str = "semantic_vad"
    semantic_eagerness: str = "low"
    transcription_model: str = "gpt-4o-transcribe"
    sideband_connect_timeout_seconds: float = 10.0

    def validate_for_session(self) -> None:
        if not self.enabled:
            raise OpenAIRealtimeConfigurationError("openai_realtime_disabled")
        if not self.api_key.strip():
            raise OpenAIRealtimeConfigurationError("openai_realtime_secret_missing")
        if self.api_key.strip().lower().startswith("sk-admin"):
            raise OpenAIRealtimeConfigurationError("openai_realtime_admin_key_unsupported")
        if not self.model.strip():
            raise OpenAIRealtimeConfigurationError("openai_realtime_model_missing")
        if self.max_session_minutes <= 0:
            raise OpenAIRealtimeConfigurationError("openai_realtime_max_session_invalid")
        if self.turn_detection not in {"semantic_vad", "server_vad"}:
            raise OpenAIRealtimeConfigurationError("openai_realtime_turn_detection_invalid")
        if self.semantic_eagerness not in {"auto", "low", "medium", "high"}:
            raise OpenAIRealtimeConfigurationError(
                "openai_realtime_semantic_eagerness_invalid"
            )
        if self.reasoning_effort and self.reasoning_effort not in {
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
        }:
            raise OpenAIRealtimeConfigurationError(
                "openai_realtime_reasoning_effort_invalid"
            )

    def session_payload(self) -> dict[str, Any]:
        self.validate_for_session()
        turn_detection: dict[str, Any]
        if self.turn_detection == "semantic_vad":
            turn_detection = {
                "type": "semantic_vad",
                "eagerness": self.semantic_eagerness,
                "create_response": False,
                "interrupt_response": False,
            }
        else:
            turn_detection = {
                "type": "server_vad",
                "silence_duration_ms": 500,
                "create_response": False,
                "interrupt_response": False,
            }

        audio_input: dict[str, Any] = {
            "turn_detection": turn_detection,
            "transcription": {
                "model": self.transcription_model,
                "language": "ja",
            },
        }
        audio_output: dict[str, Any] = {}
        if self.voice:
            audio_output["voice"] = self.voice

        session: dict[str, Any] = {
            "type": "realtime",
            "model": self.model,
            "instructions": OPENAI_REALTIME_SYSTEM_PROMPT,
            "output_modalities": ["audio"],
            "audio": {
                "input": audio_input,
                "output": audio_output,
            },
            "parallel_tool_calls": False,
        }
        if self.reasoning_effort:
            session["reasoning"] = {"effort": self.reasoning_effort}
        return session

    def sideband_update_payload(self) -> dict[str, Any]:
        payload = self.session_payload()
        payload.pop("model", None)
        return payload
