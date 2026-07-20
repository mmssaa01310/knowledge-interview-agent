"""
Role:
    Nova Sonic入力ストリームへのイベント列送信を担当するPort。

Summary:
    動的なstream参照、content名採番、payload送信と送信観測値の更新を集約し、
    tool turn処理からRuntimeのストリーム内部を隠蔽する。

Relations:
    Uses sdk_client.send_payload and NovaObservability. Used by Runtime and ToolTurnCoordinator.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from ai_interviewer_voice.runtimes.nova_sonic.protocol.payloads import sanitize_payload_for_debug
from ai_interviewer_voice.runtimes.nova_sonic.runtime_ports import NovaObservability
from ai_interviewer_voice.runtimes.nova_sonic.sdk_client import send_payload


logger = logging.getLogger(__name__)


class ToolResultOutputPort(Protocol):
    @property
    def is_open(self) -> bool: ...

    @property
    def prompt_name(self) -> str: ...

    def next_content_name(self, prefix: str) -> str: ...

    async def send_sequence(self, sequence: list[tuple[str, dict[str, Any]]]) -> None: ...


class NovaStreamWriter:
    def __init__(self, observability: NovaObservability) -> None:
        self._observability = observability
        self._stream: Any | None = None
        self._prompt_name = "prompt"
        self._content_counter = 0

    @property
    def stream(self) -> Any | None:
        return self._stream

    @stream.setter
    def stream(self, stream: Any | None) -> None:
        self._stream = stream

    @property
    def is_open(self) -> bool:
        return self._stream is not None

    @property
    def prompt_name(self) -> str:
        return self._prompt_name

    def reset(self, *, prompt_name: str) -> None:
        self._stream = None
        self._prompt_name = prompt_name
        self._content_counter = 0

    def next_content_name(self, prefix: str) -> str:
        self._content_counter += 1
        return f"{prefix}-{self._content_counter}"

    async def send_sequence(self, sequence: list[tuple[str, dict[str, Any]]]) -> None:
        if self._stream is None:
            raise RuntimeError("NovaSonicRuntime stream is not open")
        for stage_name, payload in sequence:
            logger.debug(
                "nova_input_payload stage=%s payload=%s",
                stage_name,
                sanitize_payload_for_debug(payload),
            )
            await send_payload(self._stream, payload)
            self._record_sent_payload(stage_name, payload)

    def _record_sent_payload(self, stage_name: str, payload: dict[str, Any]) -> None:
        observed = self._observability.output
        observed.last_input_event = stage_name
        observed.last_sent_event = stage_name
        event_container = payload.get("event")
        if not isinstance(event_container, dict):
            return
        first_key = next(iter(event_container.keys()), None)
        content_name = None
        if first_key is not None:
            candidate = event_container.get(first_key)
            if isinstance(candidate, dict):
                resolved = candidate.get("contentName")
                if resolved is not None:
                    content_name = str(resolved)
        observed.last_sent_content_name = content_name
        now = self._observability.elapsed_ms()
        if stage_name == "tool_result_content_start_sent":
            observed.tool_result_content_start_sent = True
            observed.tool_result_content_start_sent_at_ms = now
        elif stage_name == "tool_result_sent":
            observed.tool_result_sent_at_ms = now
        elif stage_name == "tool_result_content_end_sent":
            observed.tool_result_content_end_sent = True
            observed.tool_result_content_end_sent_at_ms = now
