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
        self._startup_watchdog_task: asyncio.Task[None] | None = None
        self._turn_tasks: set[asyncio.Task[None]] = set()
        self._initial_task: asyncio.Task[None] | None = None
        self._turn_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._finish_lock = asyncio.Lock()
        # Transport readiness and conversation readiness are deliberately
        # separate.  A sideband WebSocket can be connected while the
        # canonical initial question is still waiting to be dispatched.
        self._transport_ready = asyncio.Event()
        self._conversation_ready = asyncio.Event()
        self._startup_enforced = False
        self._startup_state = "INITIALIZING"
        self._initial_question_dispatched = False
        self._startup_error: OpenAIRealtimeProviderError | None = None
        self._closed = False
        self._finished = False
        self._initialization_lock = asyncio.Lock()
        self._initialization_state = "NOT_STARTED"
        self._session_update_sent = False
        self._session_created_received = False
        self._session_updated_received = False
        self._active_response_pending = False
        self._active_response_id: str | None = None
        self._processed_transcript_items: set[str] = set()
        self._pending_first_user_turns: dict[str, str] = {}
        self._event_counts: dict[str, int] = {}
        self._transcript_final_count = 0
        self._unique_transcript_item_ids: set[str] = set()
        self._process_turn_task_count = 0
        self._backend_process_count = 0
        self._response_create_count = 0
        self._openai_response_ids: set[str] = set()
        self._backend_response_ids: set[str] = set()
        self._speech_started_at_ms: int | None = None
        self._state_version = voice_session.state_version
        self._current_question_id = voice_session.current_question_id
        self._interview_status = voice_session.interview_status
        self._session_started_at = monotonic()
        self._timeline_started_at = monotonic()
        self._timeline: dict[str, int] = {}
        self._usage_events: list[dict[str, Any]] = []

    async def start(self) -> None:
        self._startup_enforced = True
        self._set_startup_state("INITIALIZING")
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
                self._transport_ready.wait(),
                timeout=self._config.sideband_connect_timeout_seconds,
            )
        except TimeoutError as exc:
            await self.close(reason="sideband_connect_timeout")
            raise OpenAIRealtimeProviderError("openai_realtime_sideband_connect_failed") from exc
        if self._startup_error is not None:
            error = self._startup_error
            await self.close(reason="sideband_start_failed")
            raise error
        # Return the SDP now. Waiting for initial dispatch here prevents the
        # browser from applying the answer and establishing the media session.
        # Canonical turns remain gated by _conversation_ready independently.
        self._mark_once("sdp_answer_ready")
        self._startup_watchdog_task = asyncio.create_task(
            self._wait_for_conversation_start(),
            name=f"openai-realtime-startup-{self.voice_session.voice_session_id}",
        )

    async def _wait_for_conversation_start(self) -> None:
        try:
            await asyncio.wait_for(
                self._conversation_ready.wait(),
                timeout=self._config.sideband_connect_timeout_seconds,
            )
        except TimeoutError:
            logger.warning(
                "openai_realtime_initialization_timeout call_id=%s voice_session_id=%s "
                "failure_reason=INITIALIZATION_TIMEOUT sideband_connected=%s "
                "session_created_received=%s session_update_sent=%s "
                "session_updated_received=%s initial_response_sent=%s "
                "conversation_ready=%s pending_user_turn_count=%s elapsed_ms=%s",
                self.call_id,
                self.voice_session.voice_session_id,
                self._transport_ready.is_set(),
                self._session_created_received,
                self._session_update_sent,
                self._session_updated_received,
                self._initial_question_dispatched,
                self._conversation_ready.is_set(),
                len(self._pending_first_user_turns),
                round((monotonic() - self._session_started_at) * 1000),
            )
            self._fail_startup(OpenAIRealtimeProviderError("openai_realtime_initialization_timeout"))
        if self._startup_error is not None and not self._closed:
            await self.close(reason="initial_dispatch_failed")

    async def close(self, *, reason: str) -> None:
        if self._closed and self._finished:
            return
        self._closed = True
        if not self._conversation_ready.is_set() and self._startup_error is None:
            self._fail_startup(OpenAIRealtimeProviderError("openai_realtime_startup_cancelled"))
        current_task = asyncio.current_task()
        for task in (*self._turn_tasks, self._initial_task, self._startup_watchdog_task):
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
                self._transport_ready.set()
                await self._initialize_once()
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
            if not self._conversation_ready.is_set():
                self._fail_startup(exc)
            logger.warning(
                "openai_realtime_sideband_failed voice_session_id=%s error_type=%s",
                self.voice_session.voice_session_id,
                exc.__class__.__name__,
            )
        except Exception as exc:  # noqa: BLE001 - external WebSocket boundary
            if not self._conversation_ready.is_set():
                self._fail_startup(
                    OpenAIRealtimeProviderError("openai_realtime_sideband_connect_failed")
                )
            logger.warning(
                "openai_realtime_sideband_failed voice_session_id=%s error_type=%s",
                self.voice_session.voice_session_id,
                exc.__class__.__name__,
            )
        finally:
            if not self._conversation_ready.is_set() and not self._closed:
                self._fail_startup(
                    OpenAIRealtimeProviderError("openai_realtime_sideband_closed_before_ready")
                )
            self._websocket = None
            await self._finish(reason="sideband_closed")

    async def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        self._event_counts[event_type] = self._event_counts.get(event_type, 0) + 1
        self._record_usage(event)
        if event_type == "session.created":
            self._session_created_received = True
            self._mark_once("session_created_received")
            # session.created is useful for observing/synchronizing the
            # session, but sideband initialization is started from WebSocket
            # OPEN and must not depend on this event being replayed.
            await self._initialize_once()
            await self._maybe_dispatch_initial_reply()
            return
        if event_type == "session.updated":
            self._session_updated_received = True
            self._mark_once("session_updated_received")
            if self._session_update_sent:
                self._set_initialization_state("SESSION_UPDATED")
            await self._maybe_dispatch_initial_reply()
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
            self._transcript_final_count += 1
            if item_id:
                self._unique_transcript_item_ids.add(item_id)
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
            self._process_turn_task_count += 1
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
                if self._active_response_id:
                    self._openai_response_ids.add(self._active_response_id)
                metadata = response.get("metadata")
                if (
                    isinstance(metadata, dict)
                    and metadata.get("kikiori_kind") == "initial"
                ):
                    self._mark_once("initial_response_created")
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
            if not self._conversation_ready.is_set():
                self._fail_startup(
                    OpenAIRealtimeProviderError("openai_realtime_session_error")
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
                if not claim.claimed:
                    if claim.reason == "already_sent":
                        # This is only expected after a reconnect.  The
                        # canonical initial output was dispatched by the
                        # previous connection, so do not send it twice.
                        self._set_initialization_state("INITIAL_RESPONSE_SENT")
                        self._initial_question_dispatched = True
                        self._set_startup_state("INITIAL_QUESTION_DISPATCHED")
                        self._mark_once("initial_response_already_sent")
                        self._mark_conversation_ready()
                        return
                    self._fail_startup(
                        OpenAIRealtimeProviderError(
                            "openai_realtime_initial_reply_claim_failed"
                        )
                    )
                    return
                if not claim.initial_reply_text:
                    self._fail_startup(
                        OpenAIRealtimeProviderError(
                            "openai_realtime_initial_reply_missing"
                        )
                    )
                    return
                await self._send_backend_reply(
                    reply_text=claim.initial_reply_text,
                    response_id=f"initial-response-{self.voice_session.voice_session_id}",
                    turn_id=f"initial-{self.voice_session.voice_session_id}",
                    question_id=claim.initial_question_id,
                    kind="initial",
                )
                self._set_initialization_state("INITIAL_RESPONSE_SENT")
                self._initial_question_dispatched = True
                self._set_startup_state("INITIAL_QUESTION_DISPATCHED")
                self._mark_once("initial_response_create")
                self._mark_conversation_ready()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - interview boundary
            startup_error = exc if isinstance(exc, OpenAIRealtimeProviderError) else OpenAIRealtimeProviderError(
                "openai_realtime_initial_reply_failed"
            )
            self._fail_startup(startup_error)
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

    async def _initialize_once(self) -> None:
        """Start sideband initialization without relying on session.created."""
        async with self._initialization_lock:
            if self._closed or self._session_update_sent:
                return
            if not self.voice_session.initial_reply_text or not self.voice_session.initial_reply_text.strip():
                self._fail_startup(
                    OpenAIRealtimeProviderError("openai_realtime_initial_reply_missing")
                )
                return
            sent = await self._send_event(
                {
                    "type": "session.update",
                    "session": self._config.sideband_update_payload(),
                }
            )
            if not sent:
                self._fail_startup(
                    OpenAIRealtimeProviderError("openai_realtime_session_update_failed")
                )
                return
            self._session_update_sent = True
            self._set_initialization_state("SESSION_UPDATE_SENT")
            self._mark_once("session_update_sent")

        # A session.updated event may have arrived before this method was
        # scheduled (for example while websocket.send yielded).  Re-check the
        # idempotent dispatch condition after releasing the initialization lock.
        await self._maybe_dispatch_initial_reply()

    async def _maybe_dispatch_initial_reply(self) -> None:
        """Schedule exactly one initial response after the update is ACKed."""
        async with self._initialization_lock:
            if self._closed or self._startup_error is not None:
                return
            if not self._session_update_sent or not self._session_updated_received:
                return
            if self._initial_question_dispatched or self._initial_task is not None:
                return
            self._set_startup_state("INITIAL_QUESTION_READY")
            self._mark_once("initial_question_ready")
            self._initial_task = asyncio.create_task(
                self._send_initial_reply(),
                name=f"openai-realtime-initial-{self.voice_session.voice_session_id}",
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
        # Production sessions must not process a user turn merely because the
        # sideband transport is connected.  The first turn waits for the
        # canonical initial response.create to be sent.  Keeping this await
        # outside _turn_lock lets the initial task complete its serialized
        # claim and dispatch normally.
        if self._startup_enforced:
            self._pending_first_user_turns[item_id] = transcript
            logger.info(
                "openai_realtime_pending_first_user_turn call_id=%s voice_session_id=%s item_id=%s task_id=%s",
                self.call_id,
                self.voice_session.voice_session_id,
                item_id,
                id(task) if task is not None else None,
            )
            try:
                await asyncio.shield(self._conversation_ready.wait())
            except asyncio.CancelledError:
                raise
            finally:
                self._pending_first_user_turns.pop(item_id, None)
                if not self._conversation_ready.is_set():
                    logger.warning(
                        "openai_realtime_pending_first_user_turn_aborted call_id=%s voice_session_id=%s item_id=%s",
                        self.call_id,
                        self.voice_session.voice_session_id,
                        item_id,
                    )
            if self._startup_error is not None or self._closed:
                logger.warning(
                    "openai_realtime_first_user_turn_not_processed call_id=%s voice_session_id=%s item_id=%s startup_error=%s closed=%s",
                    self.call_id,
                    self.voice_session.voice_session_id,
                    item_id,
                    self._startup_error.code if self._startup_error else None,
                    self._closed,
                )
                return
        else:
            # Preserve direct unit-test construction semantics.  Real runtime
            # sessions always call start(), which enables the lifecycle gate
            # above; tests that exercise _process_turn in isolation may still
            # provide the initial task explicitly.
            initial_task = self._initial_task
            current_task = asyncio.current_task()
            if initial_task is None or initial_task is current_task:
                initial_task = None
            if initial_task is not None:
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
            answer_question_id = self._current_question_id
            client_turn_id = f"openai-{item_id}"
            started_at_ms = self._speech_started_at_ms
            result = None
            for attempt in range(2):
                attempted_state_version = self._state_version
                try:
                    self._backend_process_count += 1
                    result = await self._interview_bridge.process_turn(
                        voice_session_id=self.voice_session.voice_session_id,
                        transcript=transcript,
                        answer_to_question_id=answer_question_id,
                        expected_state_version=attempted_state_version,
                        client_turn_id=client_turn_id,
                        started_at_ms=started_at_ms,
                        ended_at_ms=self._wall_ms(),
                    )
                    break
                except InterviewApiError as exc:
                    if exc.code != "turn_state_conflict":
                        logger.warning(
                            "openai_realtime_interview_failed voice_session_id=%s item_id=%s error_code=%s category=%s",
                            self.voice_session.voice_session_id,
                            item_id,
                            exc.code,
                            exc.category,
                        )
                        await self.close(reason="interview_api_failed")
                        return

                    try:
                        snapshot = await self._interview_bridge.load_voice_session(
                            self.voice_session.voice_session_id
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as resync_error:  # noqa: BLE001 - keep stale turns from being replayed
                        logger.error(
                            "openai_realtime_state_resync_failed call_id=%s voice_session_id=%s item_id=%s "
                            "client_turn_id=%s error_type=%s",
                            self.call_id,
                            self.voice_session.voice_session_id,
                            item_id,
                            client_turn_id,
                            resync_error.__class__.__name__,
                        )
                        await self.close(reason="turn_state_resync_failed")
                        return

                    self._state_version = snapshot.state_version
                    self._current_question_id = snapshot.current_question_id
                    self._interview_status = snapshot.interview_status
                    question_unchanged = (
                        answer_question_id is not None
                        and snapshot.current_question_id == answer_question_id
                    )
                    session_active = snapshot.interview_status not in {"completed", "stopped"}
                    may_retry = (
                        attempt == 0
                        and question_unchanged
                        and session_active
                        and client_turn_id == f"openai-{item_id}"
                    )
                    logger.warning(
                        "openai_realtime_state_resync call_id=%s voice_session_id=%s item_id=%s "
                        "client_turn_id=%s expected_state_version=%s state_version_before=%s "
                        "state_version_after=%s current_question_id_before=%s "
                        "current_question_id_after=%s retry=%s attempt=%s",
                        self.call_id,
                        self.voice_session.voice_session_id,
                        item_id,
                        client_turn_id,
                        attempted_state_version,
                        attempted_state_version,
                        snapshot.state_version,
                        answer_question_id,
                        snapshot.current_question_id,
                        may_retry,
                        attempt + 1,
                    )
                    if may_retry:
                        continue
                    if attempt > 0:
                        logger.error(
                            "openai_realtime_state_conflict_retry_exhausted call_id=%s "
                            "voice_session_id=%s item_id=%s client_turn_id=%s state_version=%s "
                            "current_question_id=%s",
                            self.call_id,
                            self.voice_session.voice_session_id,
                            item_id,
                            client_turn_id,
                            self._state_version,
                            self._current_question_id,
                        )
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

            if result is None:
                # A second conflict is synchronized above but never retried.
                logger.error(
                    "openai_realtime_state_conflict_retry_exhausted call_id=%s voice_session_id=%s "
                    "item_id=%s client_turn_id=%s state_version=%s current_question_id=%s",
                    self.call_id,
                    self.voice_session.voice_session_id,
                    item_id,
                    client_turn_id,
                    self._state_version,
                    self._current_question_id,
                )
                return

            self._state_version = result.state_version
            self._current_question_id = result.question_id
            self._interview_status = result.interview_status
            self._backend_response_ids.add(result.response_id)
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
        self._response_create_count += 1
        self._backend_response_ids.add(response_id)
        metadata = {
            "kikiori_kind": kind,
            "kikiori_response_id": response_id,
            "kikiori_turn_id": turn_id,
        }
        if question_id:
            metadata["kikiori_question_id"] = question_id
        if interview_status:
            metadata["kikiori_interview_status"] = interview_status
        sent = await self._send_event(
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
        if not sent:
            self._active_response_pending = False
            raise OpenAIRealtimeProviderError("openai_realtime_response_send_failed")
        self._mark("realtime_response_create")

    def _set_startup_state(self, state: str) -> None:
        previous_state = self._startup_state
        self._startup_state = state
        logger.info(
            "openai_realtime_startup_state voice_session_id=%s call_id=%s previous_state=%s state=%s",
            self.voice_session.voice_session_id,
            self.call_id,
            previous_state,
            state,
        )

    def _set_initialization_state(self, state: str) -> None:
        previous_state = self._initialization_state
        if previous_state == state:
            return
        self._initialization_state = state
        logger.info(
            "openai_realtime_initialization_state voice_session_id=%s call_id=%s "
            "previous_state=%s state=%s",
            self.voice_session.voice_session_id,
            self.call_id,
            previous_state,
            state,
        )

    def _mark_conversation_ready(self) -> None:
        if not self._initial_question_dispatched:
            return
        self._set_startup_state("READY_FOR_USER_TURN")
        self._mark_once("ready_for_user_turn")
        self._conversation_ready.set()

    def _fail_startup(self, error: OpenAIRealtimeProviderError) -> None:
        if self._startup_error is None:
            self._startup_error = error
        self._set_initialization_state("INITIALIZATION_FAILED")
        self._transport_ready.set()
        self._conversation_ready.set()

    async def _send_event(self, event: dict[str, Any]) -> bool:
        websocket = self._websocket
        if websocket is None or self._closed:
            return False
        async with self._send_lock:
            if self._websocket is None or self._closed:
                return False
            await self._websocket.send(json.dumps(event, ensure_ascii=False))
        return True

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
                "openai_realtime_trace_counts call_id=%s voice_session_id=%s "
                "event_counts=%s transcript_final_count=%s unique_item_ids=%s "
                "process_turn_task_count=%s backend_process_count=%s "
                "response_create_count=%s openai_response_ids=%s backend_response_ids=%s",
                self.call_id,
                self.voice_session.voice_session_id,
                self._event_counts,
                self._transcript_final_count,
                len(self._unique_transcript_item_ids),
                self._process_turn_task_count,
                self._backend_process_count,
                self._response_create_count,
                sorted(self._openai_response_ids),
                sorted(self._backend_response_ids),
            )
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
        logger.info(
            "openai_realtime_call_created voice_session_id=%s call_id=%s model=%s",
            voice_session.voice_session_id,
            call_id,
            self._config.model,
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
