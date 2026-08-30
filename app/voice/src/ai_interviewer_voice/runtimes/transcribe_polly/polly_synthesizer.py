from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from typing import Any, ClassVar, Protocol

import boto3

from ai_interviewer_voice.runtimes.transcribe_polly.config import (
    TranscribePollyRuntimeConfig,
)

logger = logging.getLogger(__name__)


class PollySynthesisError(RuntimeError):
    pass


class PollySynthesisPort(Protocol):
    async def synthesize(self, text: str) -> bytes: ...

    async def get_cached(self, text: str) -> bytes | None: ...

    async def warm(self, texts: tuple[str, ...]) -> None: ...


class PollySynthesizer:
    _shared_cache: ClassVar[dict[str, bytes]] = {}
    _cache_lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    def __init__(
        self,
        config: TranscribePollyRuntimeConfig,
        *,
        client: Any | None = None,
        client_factory: Callable[..., Any] = boto3.client,
    ) -> None:
        self._config = config
        self._client = client
        self._client_factory = client_factory

    async def get_cached(self, text: str) -> bytes | None:
        return self._shared_cache.get(self._cache_key(text))

    async def warm(self, texts: tuple[str, ...]) -> None:
        for text in dict.fromkeys(item.strip() for item in texts if item.strip()):
            if await self.get_cached(text) is not None:
                continue
            try:
                await self.synthesize(text)
            except Exception as exc:  # noqa: BLE001 - cache warm must not fail runtime startup
                logger.warning("polly_cache_warm_failed text_hash=%s error=%s", self._cache_key(text), exc)

    async def synthesize(self, text: str) -> bytes:
        normalized = text.strip()
        if not normalized:
            return b""
        key = self._cache_key(normalized)
        cached = self._shared_cache.get(key)
        if cached is not None:
            return cached

        last_error: Exception | None = None
        for attempt in range(self._config.polly_retry_attempts + 1):
            try:
                pcm = await asyncio.to_thread(self._synthesize_sync, normalized)
                async with self._cache_lock:
                    self._shared_cache[key] = pcm
                return pcm
            except Exception as exc:  # noqa: BLE001 - SDK exceptions are normalized below
                last_error = exc
                if attempt >= self._config.polly_retry_attempts:
                    break
                delay_ms = self._config.polly_retry_base_delay_ms * (2**attempt)
                await asyncio.sleep(delay_ms / 1000)
        raise PollySynthesisError(str(last_error or "polly synthesis failed")) from last_error

    def _synthesize_sync(self, text: str) -> bytes:
        if self._client is None:
            self._client = self._client_factory(
                "polly",
                region_name=self._config.aws_region,
            )
        response = self._client.synthesize_speech(
            Engine=self._config.polly_engine,
            LanguageCode=self._config.polly_language_code,
            OutputFormat="pcm",
            SampleRate=str(self._config.polly_sample_rate_hz),
            Text=text,
            TextType="text",
            VoiceId=self._config.polly_voice_id,
        )
        stream = response.get("AudioStream")
        if stream is None:
            raise PollySynthesisError("Polly response did not include AudioStream")
        try:
            pcm = stream.read()
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        if not pcm or len(pcm) % 2:
            raise PollySynthesisError("Polly returned invalid PCM")
        return bytes(pcm)

    def _cache_key(self, text: str) -> str:
        material = (
            f"{self._config.polly_language_code}|{self._config.polly_engine}|{self._config.polly_voice_id}|"
            f"{self._config.polly_sample_rate_hz}|{text}"
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()
