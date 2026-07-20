from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HELPER_PATH = Path("scripts/smoke_nova_sonic_helpers.py")
_SPEC = importlib.util.spec_from_file_location("smoke_nova_sonic_helpers", _HELPER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
helpers = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = helpers
_SPEC.loader.exec_module(helpers)


def test_iter_pcm_chunks_splits_and_zero_pads_last_chunk() -> None:
    pcm = bytes(range(256)) * 10
    chunks = helpers.iter_pcm_chunks(pcm)

    assert len(chunks) == 2
    assert len(chunks[0]) == helpers.CHUNK_BYTES
    assert len(chunks[1]) == helpers.CHUNK_BYTES
    assert chunks[0] == pcm[: helpers.CHUNK_BYTES]
    assert chunks[1].startswith(pcm[helpers.CHUNK_BYTES :])
    assert chunks[1].endswith(bytes(helpers.CHUNK_BYTES - len(pcm[helpers.CHUNK_BYTES :])))


def test_chunk_duration_seconds_is_derived_from_pcm_size() -> None:
    duration = helpers.chunk_duration_seconds(bytes(helpers.CHUNK_BYTES))
    assert duration == 1024 / 16000


def test_trailing_silence_chunks_cover_at_least_requested_duration() -> None:
    chunks = helpers.trailing_silence_chunks()
    total_duration = sum(helpers.chunk_duration_seconds(chunk) for chunk in chunks)

    assert chunks
    assert all(len(chunk) == helpers.CHUNK_BYTES for chunk in chunks)
    assert total_duration >= helpers.SILENCE_SECONDS


def test_save_pcm_as_wav_writes_file(tmp_path: Path) -> None:
    wav_path = tmp_path / "fixture.wav"
    pcm = bytes(helpers.CHUNK_BYTES * 2)

    helpers.save_pcm_as_wav(pcm=pcm, wav_path=str(wav_path))

    assert wav_path.exists()
    assert wav_path.stat().st_size > len(pcm)


def test_runtime_does_not_import_smoke_helper() -> None:
    runtime_source = Path(
        "src/ai_interviewer_voice/runtimes/nova_sonic/runtime.py"
    ).read_text(encoding="utf-8")

    assert "smoke_nova_sonic_helpers" not in runtime_source
