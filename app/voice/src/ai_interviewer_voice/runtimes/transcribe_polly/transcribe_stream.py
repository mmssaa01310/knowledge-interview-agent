from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from aws_sdk_transcribe_streaming.client import TranscribeStreamingClient
from aws_sdk_transcribe_streaming.config import Config
from aws_sdk_transcribe_streaming.models import (
    AudioEvent,
    AudioStreamAudioEvent,
    LanguageCode,
    MediaEncoding,
    PartialResultsStability,
    StartStreamTranscriptionInput,
    TranscriptResultStreamTranscriptEvent,
)

from ai_interviewer_voice.runtimes.transcribe_polly.config import (
    TranscribePollyRuntimeConfig,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranscribeResult:
    text: str
    stable_text: str
    is_partial: bool
    result_id: str | None = None


class TranscribeStreamingPort(Protocol):
    async def start(
        self,
        *,
        on_result: Callable[[TranscribeResult], Awaitable[None]],
        on_reconnecting: Callable[[int], Awaitable[None]],
        on_fatal_error: Callable[[Exception], Awaitable[None]],
    ) -> None: ...

    async def send_audio(self, pcm: bytes) -> None: ...

    async def close(self) -> None: ...


class AwsTranscribeStreamingPort:
    def __init__(
        self,
        config: TranscribePollyRuntimeConfig,
        *,
        client: Any | None = None,
    ) -> None:
        self._config = config
        self._client = client or TranscribeStreamingClient(
            Config(region=config.aws_region)
        )
        self._on_result: Callable[[TranscribeResult], Awaitable[None]] | None = None
        self._on_reconnecting: Callable[[int], Awaitable[None]] | None = None
        self._on_fatal_error: Callable[[Exception], Awaitable[None]] | None = None
        max_chunks = max(1, config.reconnect_audio_buffer_ms // config.transcribe_chunk_ms)
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=max_chunks)
        self._task: asyncio.Task[None] | None = None
        self._connected = asyncio.Event()
        self._initial_error: Exception | None = None
        self._closed = False

    async def start(
        self,
        *,
        on_result: Callable[[TranscribeResult], Awaitable[None]],
        on_reconnecting: Callable[[int], Awaitable[None]],
        on_fatal_error: Callable[[Exception], Awaitable[None]],
    ) -> None:
        self._on_result = on_result
        self._on_reconnecting = on_reconnecting
        self._on_fatal_error = on_fatal_error
        self._closed = False
        self._task = asyncio.create_task(self._run())
        connected_wait = asyncio.create_task(self._connected.wait())
        done, _ = await asyncio.wait(
            {connected_wait, self._task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if connected_wait not in done:
            connected_wait.cancel()
        if self._initial_error is not None:
            raise self._initial_error
        if self._task.done():
            exception = self._task.exception()
            if exception is not None:
                raise exception

    async def send_audio(self, pcm: bytes) -> None:
        if self._closed or not pcm:
            return
        while self._audio_queue.full():
            try:
                self._audio_queue.get_nowait()
                self._audio_queue.task_done()
            except asyncio.QueueEmpty:
                break
        self._audio_queue.put_nowait(pcm)

    async def close(self) -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        last_error: Exception | None = None
        for attempt in range(self._config.transcribe_reconnect_attempts + 1):
            if self._closed:
                return
            if attempt and self._on_reconnecting is not None:
                await self._on_reconnecting(attempt)
            try:
                await self._run_connection()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect all SDK transport failures
                last_error = exc
                logger.warning("transcribe_stream_failed attempt=%s error=%s", attempt + 1, exc)
                if not self._connected.is_set() and attempt >= self._config.transcribe_reconnect_attempts:
                    self._initial_error = exc
                if attempt < self._config.transcribe_reconnect_attempts:
                    await asyncio.sleep(0.1 * (2**attempt))
        if last_error is not None and self._on_fatal_error is not None:
            await self._on_fatal_error(last_error)

    async def _run_connection(self) -> None:
        stream = await self._client.start_stream_transcription(
            StartStreamTranscriptionInput(
                language_code=LanguageCode(self._config.language_code),
                media_sample_rate_hertz=self._config.input_sample_rate_hz,
                media_encoding=MediaEncoding.PCM,
                enable_partial_results_stabilization=True,
                partial_results_stability=PartialResultsStability(
                    self._config.partial_results_stability
                ),
            )
        )
        _, output_stream = await stream.await_output()
        self._connected.set()
        sender = asyncio.create_task(self._send_loop(stream.input_stream))
        receiver = asyncio.create_task(self._receive_loop(output_stream))
        tasks = {sender, receiver}
        done: set[asyncio.Task[None]] = set()
        try:
            done, _ = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            for task in tasks:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await stream.close()
        for task in done:
            exception = task.exception()
            if exception is not None:
                raise exception
        if not self._closed:
            raise ConnectionError("Transcribe stream closed unexpectedly")

    async def _send_loop(self, input_stream: Any) -> None:
        while not self._closed:
            pcm = await self._audio_queue.get()
            try:
                await input_stream.send(
                    AudioStreamAudioEvent(AudioEvent(audio_chunk=pcm))
                )
            finally:
                self._audio_queue.task_done()

    async def _receive_loop(self, output_stream: Any) -> None:
        async for event in output_stream:
            if not isinstance(event, TranscriptResultStreamTranscriptEvent):
                continue
            transcript = event.value.transcript
            if transcript is None:
                continue
            for result in transcript.results or []:
                if not result.alternatives:
                    continue
                alternative = result.alternatives[0]
                text = str(alternative.transcript or "").strip()
                if not text:
                    continue
                stable_text = _stable_text(alternative.items or [], fallback=text if not result.is_partial else "")
                if self._on_result is not None:
                    await self._on_result(
                        TranscribeResult(
                            text=text,
                            stable_text=stable_text,
                            is_partial=bool(result.is_partial),
                            result_id=result.result_id,
                        )
                    )


def _stable_text(items: list[Any], *, fallback: str) -> str:
    stable_items = [
        str(item.content or "")
        for item in items
        if item.stable is True and item.content
    ]
    if not stable_items:
        return fallback
    return "".join(stable_items).strip()
