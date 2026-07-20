from dataclasses import dataclass


@dataclass(frozen=True)
class TranscribePollyRuntimeConfig:
    provider_name: str = "transcribe_polly"
