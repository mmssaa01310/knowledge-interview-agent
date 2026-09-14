from ai_interviewer_voice.runtimes.nova_sonic.response_controller import (
    ResponseAuthorizationState,
    ResponseController,
)
from ai_interviewer_voice.schemas.events import (
    AssistantAudioChunk,
    AssistantSpeechEnded,
    AssistantSpeechStarted,
    AssistantTranscriptFinal,
)
from ai_interviewer_voice.schemas.sessions import AssistantReply


def _reply(response_id: str) -> AssistantReply:
    return AssistantReply(
        turn_id="turn-1",
        response_id=response_id,
        text="発話してください",
        action="ask_configured_field",
        question_id="q-001",
        state_version=1,
    )


def test_response_controller_blocks_until_bound_completion() -> None:
    controller = ResponseController()
    controller.on_user_speech_started()
    controller.on_user_transcript_final()
    authorized = controller.authorize(_reply("response-1"), sent_at_ms=100)

    assert controller.accepts_audio_chunk(
        AssistantAudioChunk(
            response_id="response-1",
            completion_id="completion-1",
            generation=authorized.generation,
            sequence=1,
            pcm=b"\x00",
            authorized=True,
        )
    ) is False


def test_response_controller_accepts_only_bound_completion_and_generation() -> None:
    controller = ResponseController()
    controller.on_user_speech_started()
    controller.on_user_transcript_final()
    authorized = controller.authorize(_reply("response-1"), sent_at_ms=100)
    assert controller.bind_completion(completion_id="completion-1", completion_started_at_ms=101) is True

    assert controller.accepts_audio_chunk(
        AssistantAudioChunk(
            response_id="response-1",
            completion_id="completion-1",
            generation=authorized.generation,
            sequence=1,
            pcm=b"\x00",
            authorized=True,
        )
    ) is True
    assert controller.accepts_audio_chunk(
        AssistantAudioChunk(
            response_id="response-1",
            completion_id="completion-2",
            generation=authorized.generation,
            sequence=1,
            pcm=b"\x00",
            authorized=True,
        )
    ) is False
    assert controller.accepts_transcript(
        AssistantTranscriptFinal(
            text="正しい発話",
            response_id="response-1",
            generation=authorized.generation,
        ),
        completion_id="completion-1",
    ) is True
    assert controller.accepts_speech_started(
        AssistantSpeechStarted(
            response_id="response-1",
            generation=authorized.generation,
        ),
        completion_id="completion-1",
    ) is True
    assert controller.accepts_speech_ended(
        AssistantSpeechEnded(
            response_id="response-1",
            generation=authorized.generation,
        ),
        completion_id="completion-1",
    ) is True


def test_response_controller_does_not_bind_old_completion() -> None:
    controller = ResponseController()
    controller.on_user_speech_started()
    controller.on_user_transcript_final()
    controller.authorize(_reply("response-1"), sent_at_ms=100)

    assert controller.bind_completion(completion_id="completion-1", completion_started_at_ms=99) is False
    assert controller.authorization_state == ResponseAuthorizationState.APPROVED_REPLY_PENDING


def test_response_controller_invalidates_old_generation_on_interrupt() -> None:
    controller = ResponseController()
    controller.on_user_speech_started()
    controller.on_user_transcript_final()
    authorized = controller.authorize(_reply("response-1"), sent_at_ms=100)
    controller.bind_completion(completion_id="completion-1", completion_started_at_ms=101)
    interrupted = controller.interrupt()

    assert interrupted is not None
    assert interrupted.response_id == "response-1"
    assert interrupted.generation == authorized.generation
    assert controller.accepts_audio_chunk(
        AssistantAudioChunk(
            response_id="response-1",
            completion_id="completion-1",
            generation=authorized.generation,
            sequence=1,
            pcm=b"\x00",
            authorized=True,
        )
    ) is False
