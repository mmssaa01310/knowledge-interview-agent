from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from html import escape
from typing import Any, ClassVar, Protocol

import boto3

from ai_interviewer_voice.runtimes.transcribe_polly.config import (
    TranscribePollyRuntimeConfig,
)

logger = logging.getLogger(__name__)

_POLLY_SPEECH_RATE_PRESETS = frozenset(
    {"x-slow", "slow", "medium", "fast", "x-fast"}
)
_POLLY_SPEECH_RATE_PERCENTAGE = re.compile(r"^(\d+(?:\.\d+)?)%$")
_TEST_CAPTURE_LOCK = threading.Lock()


def record_test_polly_text_chunk(
    *,
    response_id: str,
    chunk_index: int,
    text: str,
) -> None:
    """Record the exact runtime chunk sent for synthesis in an explicit test run."""

    _append_test_jsonl(
        "VOICE_TEST_POLLY_TEXT_CHUNKS_FILE",
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "responseId": response_id,
            "chunkIndex": chunk_index,
            "text": text,
        },
    )


def _append_test_jsonl(environment_key: str, record: dict[str, Any]) -> None:
    if os.getenv("APP_ENV") != "test":
        return
    output_path = os.getenv(environment_key, "").strip()
    if not output_path:
        return
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _TEST_CAPTURE_LOCK:
        with path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")


class PollySynthesisError(RuntimeError):
    pass


class PollySynthesisPort(Protocol):
    async def synthesize(self, text: str) -> bytes: ...

    async def get_cached(self, text: str) -> bytes | None: ...

    async def warm(self, texts: tuple[str, ...]) -> None: ...


@dataclass
class _SynthesisJob:
    key: str
    text: str
    chunks: list[bytes] = field(default_factory=list)
    subscribers: set[asyncio.Queue[tuple[bytes | None, BaseException | None]]] = field(
        default_factory=set
    )
    task: asyncio.Task[None] | None = None
    done: bool = False
    error: BaseException | None = None


class PollySynthesizer:
    """Polly adapter with bounded caching and progressive PCM delivery."""

    _shared_cache: ClassVar[OrderedDict[str, bytes]] = OrderedDict()
    _shared_cache_bytes: ClassVar[int] = 0
    _cache_lock: ClassVar[threading.RLock] = threading.RLock()
    _cache_max_entries: ClassVar[int] = 128
    _cache_max_bytes: ClassVar[int] = 32 * 1024 * 1024
    # Keep the first blocking read below one 100ms Transcribe chunk so Polly
    # output can reach the WebRTC queue without waiting for a large body read.
    _read_size: ClassVar[int] = 2048

    def __init__(
        self,
        config: TranscribePollyRuntimeConfig,
        *,
        client: Any | None = None,
        client_factory: Callable[..., Any] = boto3.client,
    ) -> None:
        self._config = config
        self._speech_rate = _normalize_speech_rate(config.polly_speech_rate)
        self._client = client
        self._client_factory = client_factory
        self._client_init_lock = threading.Lock()
        self._jobs: dict[str, _SynthesisJob] = {}
        self._jobs_lock = asyncio.Lock()

    async def get_cached(self, text: str) -> bytes | None:
        key = self._cache_key(text.strip())
        with self._cache_lock:
            cached = self._shared_cache.get(key)
            if cached is not None:
                self._shared_cache.move_to_end(key)
            return cached

    async def warm(self, texts: tuple[str, ...]) -> None:
        unique_texts = tuple(dict.fromkeys(item.strip() for item in texts if item.strip()))

        async def warm_one(text: str) -> None:
            if await self.get_cached(text) is not None:
                return
            try:
                await self.synthesize(text)
            except Exception as exc:  # noqa: BLE001 - cache warm must not fail runtime startup
                logger.warning(
                    "polly_cache_warm_failed text_hash=%s error=%s",
                    self._cache_key(text),
                    exc,
                )

        await asyncio.gather(*(warm_one(text) for text in unique_texts))

    async def synthesize(self, text: str) -> bytes:
        normalized = text.strip()
        if not normalized:
            return b""
        cached = await self.get_cached(normalized)
        if cached is not None:
            return cached

        pcm = bytearray()
        async for chunk in self._stream_job(normalized):
            pcm.extend(chunk)
        return bytes(pcm)

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """Yield Polly PCM as it arrives instead of waiting for the full body."""

        normalized = text.strip()
        if not normalized:
            return
        cached = await self.get_cached(normalized)
        if cached is not None:
            yield cached
            return

        async for chunk in self._stream_job(normalized):
            yield chunk

    async def _stream_job(self, text: str) -> AsyncIterator[bytes]:
        key = self._cache_key(text)
        async with self._jobs_lock:
            job = self._jobs.get(key)
            if job is None:
                job = _SynthesisJob(key=key, text=text)
                self._jobs[key] = job
                job.task = asyncio.create_task(self._run_job(job))
            queue: asyncio.Queue[tuple[bytes | None, BaseException | None]] = asyncio.Queue()
            job.subscribers.add(queue)
            for chunk in job.chunks:
                queue.put_nowait((chunk, None))
            if job.done:
                queue.put_nowait((None, job.error))

        try:
            while True:
                chunk, error = await queue.get()
                if chunk is None:
                    if error is not None:
                        if isinstance(error, PollySynthesisError):
                            raise error
                        raise PollySynthesisError("polly synthesis failed") from error
                    return
                yield chunk
        finally:
            async with self._jobs_lock:
                job.subscribers.discard(queue)
                if not job.subscribers and not job.done and job.task is not None:
                    job.task.cancel()

    async def _run_job(self, job: _SynthesisJob) -> None:
        try:
            async for chunk in self._synthesize_stream_uncached(job.text, job.key):
                job.chunks.append(chunk)
                for queue in tuple(job.subscribers):
                    queue.put_nowait((chunk, None))
        except asyncio.CancelledError as exc:
            job.error = exc
        except BaseException as exc:
            job.error = exc
        finally:
            job.done = True
            for queue in tuple(job.subscribers):
                queue.put_nowait((None, job.error))
            async with self._jobs_lock:
                if self._jobs.get(job.key) is job:
                    self._jobs.pop(job.key, None)

    async def _synthesize_stream_uncached(
        self,
        text: str,
        key: str,
    ) -> AsyncIterator[bytes]:
        last_error: Exception | None = None
        for attempt in range(self._config.polly_retry_attempts + 1):
            stream: Any | None = None
            emitted = False
            pcm = bytearray()
            pending_byte = b""
            try:
                response = await asyncio.to_thread(self._synthesize_response_sync, text)
                stream = response.get("AudioStream")
                if stream is None:
                    raise PollySynthesisError("Polly response did not include AudioStream")
                while True:
                    block = await asyncio.to_thread(stream.read, self._read_size)
                    if not block:
                        break
                    raw = pending_byte + bytes(block)
                    if len(raw) % 2:
                        pending_byte = raw[-1:]
                        raw = raw[:-1]
                    else:
                        pending_byte = b""
                    if not raw:
                        continue
                    emitted = True
                    pcm.extend(raw)
                    yield raw
                if pending_byte or not pcm:
                    raise PollySynthesisError("Polly returned invalid PCM")
                self._cache_put(key, bytes(pcm))
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - SDK exceptions are normalized below
                last_error = exc
                # Retrying after audio has already reached the speaker would
                # duplicate the beginning of the utterance. Only retry before
                # the first PCM chunk is emitted.
                if emitted or attempt >= self._config.polly_retry_attempts:
                    break
                delay_ms = self._config.polly_retry_base_delay_ms * (2**attempt)
                await asyncio.sleep(delay_ms / 1000)
            finally:
                if stream is not None:
                    close = getattr(stream, "close", None)
                    if callable(close):
                        try:
                            await asyncio.to_thread(close)
                        except Exception:  # noqa: BLE001 - preserve synthesis error
                            logger.debug("polly_audio_stream_close_failed", exc_info=True)
        raise PollySynthesisError(str(last_error or "polly synthesis failed")) from last_error

    def _synthesize_response_sync(self, text: str) -> dict[str, Any]:
        client = self._get_client_sync()
        response = client.synthesize_speech(
            Engine=self._config.polly_engine,
            LanguageCode=self._config.polly_language_code,
            OutputFormat="pcm",
            SampleRate=str(self._config.polly_sample_rate_hz),
            Text=(
                '<speak><prosody rate="'
                f'{self._speech_rate}">'
                f"{escape(text, quote=False)}"
                "</prosody></speak>"
            ),
            TextType="ssml",
            VoiceId=self._config.polly_voice_id,
        )
        self._record_test_text_chunk(text, response)
        return response

    def _record_test_text_chunk(self, text: str, response: dict[str, Any]) -> None:
        """Capture accepted Polly request text only in an explicit E2E test run."""

        if (
            os.getenv("APP_ENV") != "test"
            or not os.getenv("VOICE_TEST_POLLY_AWS_REQUESTS_FILE", "").strip()
        ):
            return
        response_metadata = response.get("ResponseMetadata")
        request_id = (
            response_metadata.get("RequestId")
            if isinstance(response_metadata, dict)
            else None
        )
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "requestId": request_id,
            "engine": self._config.polly_engine,
            "voiceId": self._config.polly_voice_id,
            "text": text,
        }
        _append_test_jsonl("VOICE_TEST_POLLY_AWS_REQUESTS_FILE", record)

    def _get_client_sync(self) -> Any:
        if self._client is not None:
            return self._client
        with self._client_init_lock:
            if self._client is None:
                self._client = self._client_factory(
                    "polly",
                    region_name=self._config.aws_region,
                )
            return self._client

    def _cache_put(self, key: str, pcm: bytes) -> None:
        with self._cache_lock:
            previous = self._shared_cache.pop(key, None)
            if previous is not None:
                self._shared_cache_bytes -= len(previous)
            self._shared_cache[key] = pcm
            self._shared_cache_bytes += len(pcm)
            while (
                len(self._shared_cache) > self._cache_max_entries
                or self._shared_cache_bytes > self._cache_max_bytes
            ):
                _, removed = self._shared_cache.popitem(last=False)
                self._shared_cache_bytes -= len(removed)

    def _cache_key(self, text: str) -> str:
        material = (
            f"{self._config.aws_region}|{self._config.polly_language_code}|{self._config.polly_engine}|"
            f"{self._config.polly_voice_id}|{self._speech_rate}|"
            f"{self._config.polly_sample_rate_hz}|{text}"
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _normalize_speech_rate(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in _POLLY_SPEECH_RATE_PRESETS:
        return normalized
    match = _POLLY_SPEECH_RATE_PERCENTAGE.fullmatch(normalized)
    if match is not None and 20 <= float(match.group(1)) <= 200:
        return normalized
    raise ValueError(
        "POLLY_SPEECH_RATE must be one of x-slow, slow, medium, fast, x-fast, "
        "or a percentage between 20% and 200%"
    )
