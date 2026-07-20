"""
Role:
    Assistant音声イベントをInterview APIへ非同期記録するPort。

Summary:
    Runtimeのイベント処理を待たせず記録taskを起動し、API失敗を音声経路から隔離する。
    セッション識別子は共有session contextから取得する。

Relations:
    Uses InterviewBridge and RuntimeSessionContext. Used by ProtocolEventDispatcher.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ai_interviewer_voice.runtimes.nova_sonic.runtime_ports import RuntimeSessionContext
from ai_interviewer_voice.services.interview_bridge import InterviewBridge


logger = logging.getLogger(__name__)


class AssistantEventRecorder:
    def __init__(
        self,
        *,
        interview_bridge: InterviewBridge | None,
        session_context: RuntimeSessionContext,
    ) -> None:
        self._interview_bridge = interview_bridge
        self._session_context = session_context

    async def record(
        self,
        event_type: str,
        *,
        response_id: str | None,
        generation: int | None,
        transcript: str | None,
        detail: dict[str, Any],
    ) -> None:
        voice_session_id = self._session_context.voice_session_id
        if self._interview_bridge is None or voice_session_id is None:
            return
        asyncio.create_task(
            self._record_background(
                event_type,
                voice_session_id=voice_session_id,
                response_id=response_id,
                generation=generation,
                transcript=transcript,
                detail=detail,
            )
        )

    async def _record_background(
        self,
        event_type: str,
        *,
        voice_session_id: str,
        response_id: str | None,
        generation: int | None,
        transcript: str | None,
        detail: dict[str, Any],
    ) -> None:
        if self._interview_bridge is None:
            return
        try:
            await self._interview_bridge.create_assistant_event(
                voice_session_id=voice_session_id,
                event_type=event_type,
                response_id=response_id,
                generation=generation,
                transcript=transcript,
                detail=detail,
            )
        except Exception as exc:
            logger.debug("assistant_event_record_failed: %s", exc)
