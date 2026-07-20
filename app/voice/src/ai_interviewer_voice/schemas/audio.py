from dataclasses import dataclass


@dataclass(frozen=True)
class AudioFrame:
    pcm: bytes
    sample_rate_hz: int
    channels: int
    timestamp_ms: int | None = None
