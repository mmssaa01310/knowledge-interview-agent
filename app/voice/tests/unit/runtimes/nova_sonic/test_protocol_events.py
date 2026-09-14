import json

from ai_interviewer_voice.runtimes.nova_sonic.protocol.events import (
    AudioOutputEvent,
    CompletionEndEvent,
    CompletionStartEvent,
    ContentEndEvent,
    ContentStartEvent,
    TextOutputEvent,
    ToolUseEvent,
    UnknownEvent,
    UserSpeechEndEvent,
    UserSpeechStartEvent,
    decode_output_bytes,
)


def test_additional_model_fields_json_string_is_parsed() -> None:
    payload = {
        "event": {
            "contentStart": {
                "contentName": "assistant-1",
                "completionId": "c1",
                "role": "ASSISTANT",
                "type": "TEXT",
                "additionalModelFields": json.dumps(
                    {"generationStage": "SPECULATIVE", "interrupted": True}
                ),
            }
        }
    }

    event = decode_output_bytes(json.dumps(payload).encode("utf-8"))

    assert isinstance(event, ContentStartEvent)
    assert event.generation_stage == "SPECULATIVE"
    assert event.interrupted is True


def test_additional_model_fields_dict_is_parsed() -> None:
    payload = {
        "event": {
            "textOutput": {
                "contentName": "assistant-1",
                "completionId": "c1",
                "content": "Connection test successful.",
                "additionalModelFields": {
                    "generationStage": "FINAL",
                    "interrupted": False,
                },
            }
        }
    }

    event = decode_output_bytes(json.dumps(payload).encode("utf-8"))

    assert isinstance(event, TextOutputEvent)
    assert event.generation_stage == "FINAL"
    assert event.interrupted is False


def test_unknown_event_records_multiple_event_keys_and_redacts_shape() -> None:
    payload = {
        "event": {
            "mysteryEvent": {
                "content": "do not log me",
                "promptName": "prompt-1",
                "contentName": "content-1",
            },
            "secondEvent": {},
        }
    }

    event = decode_output_bytes(json.dumps(payload).encode("utf-8"))

    assert isinstance(event, UnknownEvent)
    assert event.event_keys == ("mysteryEvent", "secondEvent")
    assert event.safe_shape["content_length"] == len("do not log me")
    assert event.safe_shape["audio_content_length"] == 0
    assert event.safe_shape["promptName_present"] is True
    assert event.safe_shape["contentId_present"] is True


def test_completion_end_extracts_stop_reason() -> None:
    payload = {
        "event": {
            "completionEnd": {
                "completionId": "c1",
                "stopReason": "FINISHED",
            }
        }
    }

    event = decode_output_bytes(json.dumps(payload).encode("utf-8"))

    assert isinstance(event, CompletionEndEvent)
    assert event.completion_id == "c1"
    assert event.stop_reason == "FINISHED"


def test_unknown_event_can_be_followed_by_completion_events() -> None:
    unknown = decode_output_bytes(
        json.dumps({"event": {"mysteryEvent": {"contentName": "content-1"}}}).encode("utf-8")
    )
    start = decode_output_bytes(
        json.dumps({"event": {"completionStart": {"completionId": "c1"}}}).encode("utf-8")
    )
    end = decode_output_bytes(
        json.dumps({"event": {"completionEnd": {"completionId": "c1"}}}).encode("utf-8")
    )

    assert isinstance(unknown, UnknownEvent)
    assert isinstance(start, CompletionStartEvent)
    assert isinstance(end, CompletionEndEvent)


def test_user_speech_events_are_decoded_as_known_events() -> None:
    start = decode_output_bytes(
        json.dumps({"event": {"userSpeechStart": {"promptName": "prompt-1", "sessionId": "s1"}}}).encode("utf-8")
    )
    end = decode_output_bytes(
        json.dumps({"event": {"userSpeechEnd": {"promptName": "prompt-1", "sessionId": "s1"}}}).encode("utf-8")
    )

    assert isinstance(start, UserSpeechStartEvent)
    assert isinstance(end, UserSpeechEndEvent)


def test_tool_use_event_decodes_tool_identifiers() -> None:
    payload = {
        "event": {
            "toolUse": {
                "completionId": "c1",
                "contentName": "tool-1",
                "toolUseId": "tool-use-1",
                "toolName": "process_interview_turn",
                "content": "{}",
            }
        }
    }

    event = decode_output_bytes(json.dumps(payload).encode("utf-8"))

    assert isinstance(event, ToolUseEvent)
    assert event.completion_id == "c1"
    assert event.content_id == "tool-1"
    assert event.tool_use_id == "tool-use-1"
    assert event.tool_name == "process_interview_turn"


def test_tool_content_end_decodes_stop_reason() -> None:
    payload = {
        "event": {
            "contentEnd": {
                "completionId": "c1",
                "contentName": "tool-1",
                "type": "TOOL",
                "stopReason": "TOOL_USE",
            }
        }
    }

    event = decode_output_bytes(json.dumps(payload).encode("utf-8"))

    assert isinstance(event, ContentEndEvent)
    assert event.content_id == "tool-1"
    assert event.modality == "TOOL"
    assert event.stop_reason == "TOOL_USE"


def test_invalid_audio_payload_is_rejected_without_utf8_fallback() -> None:
    payload = {
        "event": {
            "audioOutput": {
                "contentName": "assistant-audio",
                "completionId": "c1",
                "content": "not-base64!!",
            }
        }
    }

    event = decode_output_bytes(json.dumps(payload).encode("utf-8"))

    assert isinstance(event, AudioOutputEvent)
    assert event.audio_bytes == b""
