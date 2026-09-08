from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from time import monotonic, time
from typing import Any

try:
    from websockets.asyncio.client import connect as websocket_connect
except ModuleNotFoundError:  # pragma: no cover - production installs the dependency
    websocket_connect: Any = None

from ai_interviewer_voice.clients.interview_api import InterviewApiClient, InterviewApiError
from ai_interviewer_voice.config import settings
from ai_interviewer_voice.runtimes.openai_realtime.client import (
    OpenAIRealtimeCallClient,
    OpenAIRealtimeProviderError,
)
from ai_interviewer_voice.runtimes.openai_realtime.config import OpenAIRealtimeConfig
from ai_interviewer_voice.services.interview_bridge import InterviewBridge
from ai_interviewer_voice.services.voice_session_service import (
    AuthorizedVoiceSession,
    VoiceSessionService,
)


logger = logging.getLogger(__name__)

BridgeFactory = Callable[[], InterviewBridge]
ClosedCallback = Callable[["OpenAIRealtimeSession"], Awaitable[None]]


class OpenAIRealtimeSession:
    def __init__(
        self,
        *,
        voice_session: AuthorizedVoiceSession,
        call_id: str,
        config: OpenAIRealtimeConfig,
        call_client: OpenAIRealtimeCallClient,
        interview_bridge: InterviewBridge,
        voice_session_service: VoiceSessionService,
        on_closed: ClosedCallback,
    ) -> None:
        self.voice_session = voice_session
        self.call_id = call_id
        self._config = config
        self._call_client = call_client
        self._interview_bridge = interview_bridge
        self._voice_session_service = voice_session_service
        self._on_closed = on_closed
        self._websocket: Any = None
        self._run_task: asyncio.Task[None] | None = None
        self._expiry_task: asyncio.Task[None] | None = None
        self._turn_tasks: set[asyncio.Task[None]] = set()
        self._initial_task: asyncio.Task[None] | None = None
        self._turn_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._finish_lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self._startup_error: OpenAIRealtimeProviderError | None = None
        self._closed = False
        self._finished = False
        self._session_update_sent = False
        self._active_response_pending = False
        self._active_response_id: str | None = None
        self._processed_transcript_items: set[str] = set()
        self._speech_started_at_ms: int | None = None
        self._state_version = voice_session.state_version
        self._current_question_id = voice_session.current_question_id
        self._session_started_at = monotonic()
        self._timeline_started_at = monotonic()
        self._timeline: dict[str, int] = {}
        self._usage_events: list[dict[str, Any]] = []

    async def start(self) -> None:
        self._run_task = asyncio.create_task(
            self._run_sideband(),
            name=f"openai-realtime-sideband-{self.voice_session.voice_session_id}",
        )
        self._expiry_task = asyncio.create_task(
            self._expire_after_limit(),
            name=f"openai-realtime-expiry-{self.voice_session.voice_session_id}",
        )
        try:
            await asyncio.wait_for(
                self._ready.wait(),
                timeout=self._config.sideband_connect_timeout_seconds,
            )
        except TimeoutError as exc:
            await self.close(reason="sideband_connect_timeout")
            raise OpenAIRealtimeProviderError("openai_realtime_sideband_connect_failed") from exc
        if self._startup_error is not None:
            error = self._startup_error
            await self.close(reason="sideband_start_failed")
            raise error

    async def close(self, *, reason: str) -> None:
        if self._closed and self._finished:
            return
        self._closed = True
        current_task = asyncio.current_task()
        for task in (*self._turn_tasks, self._initial_task):
            if task is not None and not task.done() and task is not current_task:
                task.cancel()
        if self._run_task is not None and self._run_task is not current_task and not self._run_task.done():
            self._run_task.cancel()
            await asyncio.gather(self._run_task, return_exceptions=True)
        if self._expiry_task is not None and self._expiry_task is not current_task:
            self._expiry_task.cancel()
            await asyncio.gather(self._expiry_task, return_exceptions=True)
        await self._finish(reason=reason)

    async def _run_sideband(self) -> None:
        sideband_url = f"wss://api.openai.com/v1/realtime?call_id={self.call_id}"
        try:
            if websocket_connect is None:
                raise OpenAIRealtimeProviderError("openai_realtime_dependency_missing")
            async with websocket_connect(
                sideband_url,
                additional_headers={"Authorization": f"Bearer {self._config.api_key}"},
                open_timeout=self._config.sideband_connect_timeout_seconds,
                ping_interval=20,
                ping_timeout=20,
            ) as websocket:
                self._websocket = websocket
                self._mark("sideband_connected")
                self._ready.set()
                async for raw_event in websocket:
                    if not isinstance(raw_event, str):
                        continue
                    try:
                        event = json.loads(raw_event)
                    except json.JSONDecodeError:
                        logger.warning(
                            "openai_realtime_invalid_sideband_event voice_session_id=%s",
                            self.voice_session.voice_session_id,
                        )
                        continue
                    if not isinstance(event, dict):
                        continue
                    await self._handle_event(event)
        except asyncio.CancelledError:
            raise
        except OpenAIRealtimeProviderError as exc:
            self._startup_error = exc if not self._ready.is_set() else None
            logger.warning(
                "openai_realtime_sideband_failed voice_session_id=%s error_type=%s",
                self.voice_session.voice_session_id,
                exc.__class__.__name__,
            )
            self._ready.set()
        except Exception as exc:  # noqa: BLE001 - external WebSocket boundary
            self._startup_error = OpenAIRealtimeProviderError(
                "openai_realtime_sideband_connect_failed"
            ) if not self._ready.is_set() else None
            logger.warning(
                "openai_realtime_sideband_failed voice_session_id=%s error_type=%s",
                self.voice_session.voice_session_id,
                exc.__class__.__name__,
            )
            self._ready.set()
        finally:
            self._websocket = None
            await self._finish(reason="sideband_closed")

    async def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        self._record_usage(event)
        if event_type == "session.created":
            if not self._session_update_sent:
                await self._send_event(
                    {
                        "type": "session.update",
                        "session": self._config.sideband_update_payload(),
                    }
                )
                self._session_update_sent = True
            if self._initial_task is None:
                self._initial_task = asyncio.create_task(
                    self._send_initial_reply(),
                    name=f"openai-realtime-initial-{self.voice_session.voice_session_id}",
                )
            return
        if event_type == "input_audio_buffer.speech_started":
            self._speech_started_at_ms = self._wall_ms()
            self._mark("user_speech_started")
            if self._active_response_pending:
                self._mark("barge_in_detected")
                cancel_event: dict[str, Any] = {"type": "response.cancel"}
                if self._active_response_id:
                    cancel_event["response_id"] = self._active_response_id
                await self._send_event(cancel_event)
                await self._send_event({"type": "output_audio_buffer.clear"})
            return
        if event_type == "input_audio_buffer.speech_stopped":
            self._mark("user_speech_ended")
            return
        if event_type == "conversation.item.input_audio_transcription.completed":
            item_id = str(event.get("item_id") or "")
            transcript = str(event.get("transcript") or "").strip()
            if not item_id or not transcript:
                return
            if item_id in self._processed_transcript_items:
                logger.info(
                    "openai_realtime_transcript_duplicate_ignored call_id=%s voice_session_id=%s item_id=%s",
                    self.call_id,
                    self.voice_session.voice_session_id,
                    item_id,
                )
                return
            self._processed_transcript_items.add(item_id)
            self._mark("user_transcript_final")
            task = asyncio.create_task(
                self._process_turn(item_id=item_id, transcript=transcript),
                name=f"openai-realtime-turn-{item_id}",
            )
            self._turn_tasks.add(task)
            task.add_done_callback(self._turn_tasks.discard)
            logger.info(
                "openai_realtime_transcript_final call_id=%s voice_session_id=%s item_id=%s task_id=%s transcript_chars=%s",
                self.call_id,
                self.voice_session.voice_session_id,
                item_id,
                id(task),
                len(transcript),
            )
            return
        if event_type == "response.created":
            response = event.get("response")
            if isinstance(response, dict):
                self._active_response_id = str(response.get("id") or "") or None
            self._active_response_pending = True
            return
        if event_type == "response.output_audio.delta":
            self._mark_once("assistant_first_audio")
            return
        if event_type == "response.output_audio_transcript.delta":
            self._mark_once("assistant_first_transcript_delta")
            return
        if event_type == "response.output_audio.done":
            self._mark_once("assistant_audio_done")
            return
        if event_type in {"response.cancelled", "response.canceled"}:
            self._mark("response_cancelled")
            self._active_response_pending = False
            self._active_response_id = None
            return
        if event_type == "response.done":
            response = event.get("response")
            status = response.get("status") if isinstance(response, dict) else None
            if status in {"cancelled", "canceled", "incomplete"}:
                self._mark("response_cancelled")
            self._active_response_pending = False
            self._active_response_id = None
            if isinstance(response, dict):
                metadata = response.get("metadata")
                if isinstance(metadata, dict) and metadata.get("kikiori_kind") == "initial":
                    try:
                        if status == "completed":
                            await self._interview_bridge.mark_initial_reply_sent(
                                self.voice_session.voice_session_id
                            )
                        else:
                            await self._interview_bridge.mark_initial_reply_failed(
                                self.voice_session.voice_session_id
                            )
                    except Exception as exc:  # noqa: BLE001 - persistence must not tear down media
                        logger.warning(
                            "openai_realtime_initial_reply_status_failed voice_session_id=%s error_type=%s",
                            self.voice_session.voice_session_id,
                            exc.__class__.__name__,
                        )
            return
        if event_type == "error":
            error = event.get("error")
            error_code = error.get("code") if isinstance(error, dict) else None
            logger.warning(
                "openai_realtime_error voice_session_id=%s error_code=%s",
                self.voice_session.voice_session_id,
                error_code,
            )

    async def _send_initial_reply(self) -> None:
        try:
            # Keep the initial question in the same serialized state transition
            # as answer turns. A user can speak immediately after the media
            # connection opens, so the initial response must not race a turn
            # that is already being processed.
            async with self._turn_lock:
                claim = await self._interview_bridge.claim_initial_reply(
                    self.voice_session.voice_session_id
                )
                if not claim.claimed or not claim.initial_reply_text:
                    return
                await self._send_backend_reply(
                    reply_text=claim.initial_reply_text,
                    response_id=f"initial-response-{self.voice_session.voice_session_id}",
                    turn_id=f"initial-{self.voice_session.voice_session_id}",
                    question_id=claim.initial_question_id,
                    kind="initial",
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - interview boundary
            logger.warning(
                "openai_realtime_initial_reply_failed voice_session_id=%s error_type=%s",
                self.voice_session.voice_session_id,
                exc.__class__.__name__,
            )
            try:
                await self._interview_bridge.mark_initial_reply_failed(
                    self.voice_session.voice_session_id
                )
            except Exception as mark_exc:  # noqa: BLE001 - preserve the original failure
                logger.warning(
                    "openai_realtime_initial_reply_failure_status_failed voice_session_id=%s error_type=%s",
                    self.voice_session.voice_session_id,
                    mark_exc.__class__.__name__,
                )

    async def _process_turn(self, *, item_id: str, transcript: str) -> None:
        task = asyncio.current_task()
        logger.info(
            "openai_realtime_process_turn_start call_id=%s voice_session_id=%s item_id=%s task_id=%s",
            self.call_id,
            self.voice_session.voice_session_id,
            item_id,
            id(task) if task is not None else None,
        )
        # A user can speak as soon as the media connection is live.  Do not let
        # that turn acquire the state lock before the initial configured
        # question has been claimed and sent.  Await this outside the lock so
        # the initial task can acquire it and finish normally.
        initial_task = self._initial_task
        current_task = asyncio.current_task()
        if initial_task is not None and initial_task is not current_task:
            try:
                await asyncio.shield(initial_task)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - initial reply is already logged
                logger.warning(
                    "openai_realtime_initial_reply_wait_failed voice_session_id=%s error_type=%s",
                    self.voice_session.voice_session_id,
                    exc.__class__.__name__,
                )
        async with self._turn_lock:
            if self._closed:
                return
            process_started_at = monotonic()
            self._mark("interview_process_start")
            try:
                result = await self._interview_bridge.process_turn(
                    voice_session_id=self.voice_session.voice_session_id,
                    transcript=transcript,
                    answer_to_question_id=self._current_question_id,
                    turn_type="ANSWER",
                    expected_state_version=self._state_version,
                    client_turn_id=f"openai-{item_id}",
                    started_at_ms=self._speech_started_at_ms,
                    ended_at_ms=self._wall_ms(),
                )
            except InterviewApiError as exc:
                logger.warning(
                    "openai_realtime_interview_failed voice_session_id=%s error_code=%s category=%s",
                    self.voice_session.voice_session_id,
                    exc.code,
                    exc.category,
                )
                await self.close(reason="interview_api_failed")
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - interview boundary
                logger.exception(
                    "openai_realtime_interview_failed voice_session_id=%s error_type=%s",
                    self.voice_session.voice_session_id,
                    exc.__class__.__name__,
                )
                await self.close(reason="interview_processing_failed")
                return

            self._state_version = result.state_version
            self._current_question_id = result.question_id
            self._log_interview_latency(result.latency_metrics or {})
            self._mark("reply_ready")
            logger.info(
                "openai_realtime_interview_ready call_id=%s voice_session_id=%s item_id=%s task_id=%s turn_id=%s response_id=%s process_ms=%s retrieval_executed=%s",
                self.call_id,
                self.voice_session.voice_session_id,
                item_id,
                id(task) if task is not None else None,
                result.turn_id,
                result.response_id,
                round((monotonic() - process_started_at) * 1000),
                result.retrieval_executed,
            )
            await self._send_backend_reply(
                reply_text=result.reply_text,
                response_id=result.response_id,
                turn_id=result.turn_id,
                question_id=result.question_id,
                kind="interview",
                interview_status=result.interview_status,
            )

    async def _send_backend_reply(
        self,
        *,
        reply_text: str,
        response_id: str,
        turn_id: str,
        question_id: str | None,
        kind: str,
        interview_status: str | None = None,
    ) -> None:
        self._active_response_pending = True
        metadata = {
            "kikiori_kind": kind,
            "kikiori_response_id": response_id,
            "kikiori_turn_id": turn_id,
        }
        if question_id:
            metadata["kikiori_question_id"] = question_id
        if interview_status:
            metadata["kikiori_interview_status"] = interview_status
        await self._send_event(
            {
                "type": "response.create",
                "response": {
                    "conversation": "none",
                    "output_modalities": ["audio"],
                    "parallel_tool_calls": False,
                    "instructions": (
                        "KIKIORI Backendが提示した入力テキストを、内容を変更・要約・追加せず、"
                        "そのまま日本語音声で読み上げてください。前置き、相槌、待機発話、"
                        "新しい質問や事実の追加は禁止です。"
                    ),
                    "metadata": metadata,
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": reply_text,
                                }
                            ],
                        }
                    ],
                },
            }
        )
        self._mark("realtime_response_create")

    async def _send_event(self, event: dict[str, Any]) -> None:
        websocket = self._websocket
        if websocket is None or self._closed:
            return
        async with self._send_lock:
            if self._websocket is None or self._closed:
                return
            await self._websocket.send(json.dumps(event, ensure_ascii=False))

    async def _expire_after_limit(self) -> None:
        try:
            await asyncio.sleep(self._config.max_session_minutes * 60)
            logger.info(
                "openai_realtime_session_timeout voice_session_id=%s max_minutes=%s",
                self.voice_session.voice_session_id,
                self._config.max_session_minutes,
            )
            await self.close(reason="max_session_minutes")
        except asyncio.CancelledError:
            raise

    async def _finish(self, *, reason: str) -> None:
        async with self._finish_lock:
            if self._finished:
                return
            self._finished = True
            try:
                await self._call_client.hangup(self.call_id)
            except Exception as exc:  # noqa: BLE001 - cleanup must continue
                logger.warning(
                    "openai_realtime_hangup_failed voice_session_id=%s error_type=%s",
                    self.voice_session.voice_session_id,
                    exc.__class__.__name__,
                )
            duration_ms = round((monotonic() - self._session_started_at) * 1000)
            logger.info(
                "openai_realtime_session_closed voice_session_id=%s reason=%s model=%s duration_ms=%s usage_events=%s",
                self.voice_session.voice_session_id,
                reason,
                self._config.model,
                duration_ms,
                len(self._usage_events),
            )
            if self._usage_events:
                try:
                    await self._voice_session_service.create_connection_event(
                        self.voice_session.voice_session_id,
                        event_type="openai_realtime_session_usage",
                        connection_status="closed",
                        detail={
                            "model": self._config.model,
                            "sessionDurationMs": duration_ms,
                            "usage": self._usage_events,
                        },
                    )
                except Exception as exc:  # noqa: BLE001 - cleanup must continue
                    logger.warning(
                        "openai_realtime_usage_persist_failed voice_session_id=%s error_type=%s",
                        self.voice_session.voice_session_id,
                        exc.__class__.__name__,
                    )
            try:
                await self._on_closed(self)
            except Exception as exc:  # noqa: BLE001 - cleanup must remain idempotent
                logger.warning(
                    "openai_realtime_session_remove_failed voice_session_id=%s error_type=%s",
                    self.voice_session.voice_session_id,
                    exc.__class__.__name__,
                )

    def _record_usage(self, event: dict[str, Any]) -> None:
        usage: Any = event.get("usage")
        if usage is None and isinstance(event.get("response"), dict):
            usage = event["response"].get("usage")
        if isinstance(usage, dict):
            self._usage_events.append(
                {
                    "eventType": event.get("type"),
                    "usage": usage,
                }
            )

    def _log_interview_latency(self, metrics: dict[str, float | int]) -> None:
        metric_events = {
            "interpreter_start_ms": "interpreter_start",
            "interpreter_end_ms": "interpreter_end",
            "rag_start_ms": "rag_start",
            "rag_end_ms": "rag_end",
            "question_llm_start_ms": "question_generator_start",
            "question_first_token_ms": "question_first_token",
            "question_first_sentence_ms": "question_first_sentence",
            "question_llm_end_ms": "question_generator_end",
        }
        for metric_name, event_name in metric_events.items():
            value = metrics.get(metric_name)
            if isinstance(value, (int, float)):
                logger.info(
                    "openai_realtime_latency voice_session_id=%s event=%s timestamp_ms=%s",
                    self.voice_session.voice_session_id,
                    event_name,
                    round(value),
                )

    def _mark(self, event_name: str) -> None:
        timestamp = self._wall_ms()
        self._timeline[event_name] = timestamp
        logger.info(
            "openai_realtime_latency voice_session_id=%s event=%s timestamp_ms=%s elapsed_ms=%s",
            self.voice_session.voice_session_id,
            event_name,
            timestamp,
            round((monotonic() - self._timeline_started_at) * 1000),
        )

    def _mark_once(self, event_name: str) -> None:
        if event_name not in self._timeline:
            self._mark(event_name)

    @staticmethod
    def _wall_ms() -> int:
        return round(time() * 1000)


class OpenAIRealtimeCoordinator:
    """Owns OpenAI Realtime calls without entering the existing aiortc runtime."""

    def __init__(
        self,
        *,
        config: OpenAIRealtimeConfig,
        voice_session_service: VoiceSessionService,
        bridge_factory: BridgeFactory | None = None,
    ) -> None:
        self._config = config
        self._voice_session_service = voice_session_service
        self._call_client = OpenAIRealtimeCallClient(config.api_key)
        self._bridge_factory = bridge_factory or self._default_bridge_factory
        self._sessions: dict[str, OpenAIRealtimeSession] = {}
        self._lock = asyncio.Lock()

    async def get(self, voice_session_id: str) -> OpenAIRealtimeSession | None:
        async with self._lock:
            return self._sessions.get(voice_session_id)

    async def create_offer(
        self,
        *,
        voice_session: AuthorizedVoiceSession,
        offer_sdp: str,
    ) -> str:
        self._config.validate_for_session()
        async with self._lock:
            if voice_session.voice_session_id in self._sessions:
                raise OpenAIRealtimeProviderError("voice_session_already_connected", status_code=409)
        call_id, answer_sdp = await self._call_client.create_call(
            offer_sdp=offer_sdp,
            session=self._config.session_payload(),
        )
        session = OpenAIRealtimeSession(
            voice_session=voice_session,
            call_id=call_id,
            config=self._config,
            call_client=self._call_client,
            interview_bridge=self._bridge_factory(),
            voice_session_service=self._voice_session_service,
            on_closed=self._remove_closed_session,
        )
        async with self._lock:
            if voice_session.voice_session_id in self._sessions:
                await self._call_client.hangup(call_id)
                raise OpenAIRealtimeProviderError("voice_session_already_connected", status_code=409)
            self._sessions[voice_session.voice_session_id] = session
        try:
            await session.start()
        except Exception:
            async with self._lock:
                self._sessions.pop(voice_session.voice_session_id, None)
            await session.close(reason="offer_setup_failed")
            raise
        return answer_sdp

    async def close(self, voice_session_id: str, *, reason: str) -> None:
        async with self._lock:
            session = self._sessions.pop(voice_session_id, None)
        if session is not None:
            await session.close(reason=reason)

    async def _remove_closed_session(self, session: OpenAIRealtimeSession) -> None:
        async with self._lock:
            if self._sessions.get(session.voice_session.voice_session_id) is session:
                self._sessions.pop(session.voice_session.voice_session_id, None)

    @staticmethod
    def _default_bridge_factory() -> InterviewBridge:
        return InterviewBridge(
            InterviewApiClient(
                settings.api_base_url,
                settings.internal_api_token,
            ),
            turn_save_timeout_seconds=settings.interview_turn_save_timeout_seconds,
            turn_process_timeout_seconds=settings.interview_turn_process_timeout_seconds,
        )


__all__ = [
    "OpenAIRealtimeCoordinator",
    "OpenAIRealtimeProviderError",
    "OpenAIRealtimeSession",
]
