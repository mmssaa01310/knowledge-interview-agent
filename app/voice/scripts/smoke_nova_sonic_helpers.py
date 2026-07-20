from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from pathlib import Path

import boto3


CHUNK_BYTES = 2048
SAMPLE_RATE = 16000
SAMPLE_SIZE_BYTES = 2
CHANNELS = 1
SILENCE_SECONDS = 1.5
SILENCE_CHUNK = bytes(CHUNK_BYTES)
DEFAULT_POLLY_TEXT = "Please say connection test successful."


@dataclass(frozen=True)
class PcmAudioFixture:
    source: str
    pcm: bytes
    duration_ms: int


def load_or_generate_pcm(*, region_name: str, pcm_path: str | None) -> PcmAudioFixture:
    if pcm_path:
        pcm = Path(pcm_path).read_bytes()
        _validate_pcm_bytes(pcm)
        return PcmAudioFixture(
            source="file",
            pcm=pcm,
            duration_ms=_pcm_duration_ms(pcm),
        )
    return generate_polly_pcm(region_name=region_name)


def generate_polly_pcm(*, region_name: str) -> PcmAudioFixture:
    polly = boto3.client("polly", region_name=region_name)
    voice_id = _resolve_voice_id(polly)
    response = polly.synthesize_speech(
        Text=DEFAULT_POLLY_TEXT,
        OutputFormat="pcm",
        SampleRate="16000",
        VoiceId=voice_id,
        Engine="standard",
    )
    content_type = response.get("ContentType")
    if content_type != "audio/pcm":
        raise RuntimeError(f"Unexpected Polly content type: {content_type}")
    audio_stream = response.get("AudioStream")
    if audio_stream is None:
        raise RuntimeError("Polly AudioStream was missing")
    pcm = audio_stream.read()
    _validate_pcm_bytes(pcm)
    return PcmAudioFixture(
        source="polly",
        pcm=pcm,
        duration_ms=_pcm_duration_ms(pcm),
    )


def iter_pcm_chunks(pcm: bytes) -> list[bytes]:
    _validate_pcm_bytes(pcm)
    chunks: list[bytes] = []
    for start in range(0, len(pcm), CHUNK_BYTES):
        chunk = pcm[start : start + CHUNK_BYTES]
        if len(chunk) < CHUNK_BYTES:
            chunk = chunk + bytes(CHUNK_BYTES - len(chunk))
        chunks.append(chunk)
    return chunks


def chunk_duration_seconds(chunk: bytes) -> float:
    frame_count = len(chunk) // (SAMPLE_SIZE_BYTES * CHANNELS)
    return frame_count / SAMPLE_RATE


def trailing_silence_chunks() -> list[bytes]:
    count = math.ceil(SILENCE_SECONDS / chunk_duration_seconds(SILENCE_CHUNK))
    return [SILENCE_CHUNK] * count


def save_pcm_as_wav(*, pcm: bytes, wav_path: str) -> None:
    with wave.open(wav_path, "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(SAMPLE_SIZE_BYTES)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm)


def _resolve_voice_id(polly) -> str:
    try:
        voices = polly.describe_voices(LanguageCode="en-US", Engine="standard").get("Voices", [])
    except Exception:
        voices = []
    preferred = next((voice for voice in voices if voice.get("Id") == "Joanna"), None)
    if preferred is not None:
        return "Joanna"
    fallback = next((voice for voice in voices if voice.get("LanguageCode") == "en-US"), None)
    if fallback is not None:
        return str(fallback["Id"])
    return "Joanna"


def _validate_pcm_bytes(pcm: bytes) -> None:
    if not pcm:
        raise RuntimeError("PCM audio was empty")
    if len(pcm) % 2 != 0:
        raise RuntimeError("PCM audio byte length must be even")


def _pcm_duration_ms(pcm: bytes) -> int:
    frame_count = len(pcm) // (SAMPLE_SIZE_BYTES * CHANNELS)
    return int(frame_count / SAMPLE_RATE * 1000)
