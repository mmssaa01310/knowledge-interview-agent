from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

from ai_interviewer_voice.config import settings
from ai_interviewer_voice.runtimes.transcribe_polly.config import (
    TranscribePollyRuntimeConfig,
)
from ai_interviewer_voice.runtimes.transcribe_polly.polly_synthesizer import (
    PollySynthesizer,
    record_test_polly_text_chunk,
)
from ai_interviewer_voice.runtimes.transcribe_polly.text_chunker import (
    PollyTextChunker,
    PollyTextChunkerConfig,
)

SAMPLE_TEXT = "現在の役割と、日々どのような責任を担っているかを具体的に教えてください。"
EXPECTED_CHUNKS = [
    "現在の役割と、",
    "日々どのような責任を担っているかを具体的に教えてください。",
]


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


async def run(output_dir: Path) -> dict[str, Any]:
    os.environ["APP_ENV"] = "test"
    output_dir.mkdir(parents=True, exist_ok=True)
    request_log = output_dir / "polly-aws-requests.jsonl"
    runtime_chunk_log = output_dir / "polly-text-chunks.jsonl"
    os.environ["VOICE_TEST_POLLY_AWS_REQUESTS_FILE"] = str(request_log)
    os.environ["VOICE_TEST_POLLY_TEXT_CHUNKS_FILE"] = str(runtime_chunk_log)

    config = TranscribePollyRuntimeConfig(
        aws_region=settings.aws_region,
        polly_voice_id=settings.polly_voice_id,
        polly_engine=settings.polly_engine,
        polly_speech_rate=settings.polly_speech_rate,
    )
    chunker = PollyTextChunker(
        PollyTextChunkerConfig(
            first_min_chars=config.first_chunk_min_chars,
            first_target_chars=config.first_chunk_target_chars,
            following_min_chars=config.following_chunk_min_chars,
            following_target_chars=config.following_chunk_target_chars,
        )
    )
    chunks: list[str] = []
    # Deliberately split the input at arbitrary LLM delta boundaries. Speech
    # chunk boundaries must come from the speech buffer, not those deltas.
    for delta in (
        "現在の役割と",
        "、日々どのような責任を",
        "担っているかを具体的に",
        "教えて",
        "ください。",
    ):
        chunks.extend(chunker.push(delta))
    chunks.extend(chunker.push("", final=True))
    for index, chunk in enumerate(chunks):
        record_test_polly_text_chunk(
            response_id="real-aws-polly-chunk-probe",
            chunk_index=index,
            text=chunk,
        )
    if chunks != EXPECTED_CHUNKS:
        raise AssertionError(f"Unexpected speech chunk boundaries: {chunks!r}")

    synthesizer = PollySynthesizer(config)
    client = synthesizer._get_client_sync()
    endpoint_url = str(client.meta.endpoint_url)
    hostname = urlparse(endpoint_url).hostname or ""
    if not endpoint_url.startswith("https://") or not (
        hostname.endswith(".amazonaws.com")
        or hostname.endswith(".amazonaws.com.cn")
        or hostname.endswith(".api.aws")
    ):
        raise RuntimeError(f"Polly endpoint is not an AWS service endpoint: {endpoint_url}")

    audio_bytes: list[int] = []
    for chunk in chunks:
        pcm = await synthesizer.synthesize(chunk)
        if not pcm or len(pcm) % 2:
            raise RuntimeError(f"Polly returned incomplete PCM for chunk {index + 1}")
        audio_bytes.append(len(pcm))

    runtime_chunks = load_jsonl(runtime_chunk_log)
    aws_requests = load_jsonl(request_log)
    aws_texts = [record.get("text") for record in aws_requests]
    if aws_texts != chunks:
        raise AssertionError(
            f"Successful AWS Polly request texts differ from runtime chunks: {aws_texts!r}"
        )
    if any(not record.get("requestId") for record in aws_requests):
        raise AssertionError("A successful Polly AWS response did not include a request ID")
    return {
        "acceptance": "Real AWS Polly Japanese chunk probe",
        "status": "PASS",
        "sampleText": SAMPLE_TEXT,
        "expectedChunks": EXPECTED_CHUNKS,
        "region": config.aws_region,
        "endpoint": endpoint_url,
        "engine": config.polly_engine,
        "voiceId": config.polly_voice_id,
        "runtimeChunks": runtime_chunks,
        "awsRequests": aws_requests,
        "audioPcmBytesPerChunk": audio_bytes,
        "runtimeChunkLog": str(runtime_chunk_log),
        "awsRequestLog": str(request_log),
    }


async def main() -> int:
    if len(sys.argv) != 2:
        print("usage: realPollyChunkProbe.py <output-directory>", file=sys.stderr)
        return 2
    output_dir = Path(sys.argv[1]).resolve()
    report_path = output_dir / "polly-chunk-acceptance-report.json"
    try:
        report = await run(output_dir)
    except Exception as exc:  # noqa: BLE001 - persist acceptance failure evidence
        report = {
            "acceptance": "Real AWS Polly Japanese chunk probe",
            "status": "FAIL",
            "errorType": type(exc).__name__,
            "error": str(exc),
            "sampleText": SAMPLE_TEXT,
            "expectedChunks": EXPECTED_CHUNKS,
            "runtimeChunks": load_jsonl(output_dir / "polly-text-chunks.jsonl"),
            "awsRequests": load_jsonl(output_dir / "polly-aws-requests.jsonl"),
        }
    write_report(report_path, report)
    print("Polly chunks submitted to the real AWS endpoint:")
    for index, record in enumerate(report.get("awsRequests", []), start=1):
        print(f"  Chunk {index}: {record.get('text', '')}")
    print(f"Real AWS Polly chunk probe: {report['status']} ({report_path})")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
