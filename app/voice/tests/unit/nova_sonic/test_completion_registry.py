from ai_interviewer_voice.runtimes.nova_sonic.completion_registry import CompletionRegistry
from ai_interviewer_voice.runtimes.nova_sonic.session_state import CompletionStatus, ContentState


def test_completion_registry_resets_output_state_when_response_binding_changes() -> None:
    registry = CompletionRegistry()
    state = registry.bind_completion_response(
        completion_id="c1",
        response_id="resp-1",
        generation=1,
        started_at_ms=10,
    )
    state.assistant_audio_chunks = 3
    state.assistant_final_text_received = True
    state.assistant_audio_end_received = True
    state.assistant_final_text_end_received = True
    state.completion_end_received = True
    state.stop_reason = "END_TURN"
    state.status = CompletionStatus.PROTOCOL_COMPLETE
    state.spoken_transcript = "確認します。"

    rebound = registry.bind_completion_response(
        completion_id="c1",
        response_id="resp-2",
        generation=2,
        started_at_ms=20,
    )

    assert rebound.response_id == "resp-2"
    assert rebound.generation == 2
    assert rebound.assistant_audio_chunks == 0
    assert rebound.assistant_final_text_received is False
    assert rebound.assistant_audio_end_received is False
    assert rebound.assistant_final_text_end_received is False
    assert rebound.completion_end_received is False
    assert rebound.stop_reason is None
    assert rebound.status == CompletionStatus.GENERATING
    assert rebound.spoken_transcript == ""


def test_completion_registry_removes_content_by_completion() -> None:
    registry = CompletionRegistry()
    registry.content_states["content-1"] = ContentState(content_id="content-1", completion_id="c1")
    registry.content_states["content-2"] = ContentState(content_id="content-2", completion_id="c2")
    registry.transcript_buffers["content-1"] = ["a"]
    registry.content_roles["content-1"] = "ASSISTANT"
    removed = registry.remove_completion_content("c1")

    assert removed == ["content-1"]
    assert "content-1" not in registry.transcript_buffers
    assert "content-1" not in registry.content_roles
    assert "content-1" not in registry.content_states
