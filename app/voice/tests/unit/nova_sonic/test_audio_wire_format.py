import base64
import json

from ai_interviewer_voice.runtimes.nova_sonic.protocol.payloads import (
    build_audio_input_event,
    build_audio_input_start_event,
    dumps_event_payload,
)
from ai_interviewer_voice.runtimes.nova_sonic.sdk_client import build_json_input_chunk


def test_audio_input_event_base64_encodes_raw_pcm() -> None:
    pcm = bytes(range(256)) * 8

    payload = build_audio_input_event(
        prompt_name="prompt-1",
        content_name="audio-1",
        pcm=pcm,
    )

    encoded = payload["event"]["audioInput"]["content"]
    assert isinstance(encoded, str)
    assert not encoded.startswith("b'")
    assert base64.b64decode(encoded, validate=True) == pcm


def test_audio_input_event_has_only_expected_keys() -> None:
    payload = build_audio_input_event(
        prompt_name="prompt-1",
        content_name="audio-1",
        pcm=b"\x00\x01",
    )

    assert set(payload["event"]["audioInput"].keys()) == {"promptName", "contentName", "content"}


def test_audio_input_start_event_matches_sample_contract() -> None:
    payload = build_audio_input_start_event("prompt-1", "audio-1")
    config = payload["event"]["contentStart"]["audioInputConfiguration"]

    assert payload["event"]["contentStart"]["promptName"] == "prompt-1"
    assert payload["event"]["contentStart"]["contentName"] == "audio-1"
    assert config == {
        "mediaType": "audio/lpcm",
        "sampleRateHertz": 16000,
        "sampleSizeBits": 16,
        "channelCount": 1,
        "audioType": "SPEECH",
        "encoding": "base64",
    }


def test_audio_input_json_bytes_are_utf8_and_not_raw_pcm() -> None:
    pcm = b"\x00\x01\x02\x03"
    payload = build_audio_input_event(
        prompt_name="prompt-1",
        content_name="audio-1",
        pcm=pcm,
    )

    body = dumps_event_payload(payload)
    chunk = build_json_input_chunk(payload)

    assert isinstance(body, bytes)
    assert body.decode("utf-8")
    assert chunk.value.bytes_ == body
    assert chunk.value.bytes_ != pcm


def test_audio_input_does_not_include_audio_input_configuration() -> None:
    payload = build_audio_input_event(
        prompt_name="prompt-1",
        content_name="audio-1",
        pcm=b"\x00\x01",
    )

    assert "audioInputConfiguration" not in payload["event"]["audioInput"]


def test_can_serialize_2048_byte_silent_pcm() -> None:
    pcm = bytes(2048)
    payload = build_audio_input_event(
        prompt_name="prompt-1",
        content_name="audio-1",
        pcm=pcm,
    )
    encoded = payload["event"]["audioInput"]["content"]
    body = json.loads(dumps_event_payload(payload).decode("utf-8"))

    assert len(pcm) == 2048
    assert base64.b64decode(encoded, validate=True) == pcm
    assert body["event"]["audioInput"]["promptName"] == "prompt-1"
    assert body["event"]["audioInput"]["contentName"] == "audio-1"
