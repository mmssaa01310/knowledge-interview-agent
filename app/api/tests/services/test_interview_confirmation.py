from ai_interviewer_api.services.interview_confirmation import is_unambiguous_confirmation
from ai_interviewer_api.services import voice_interview
from ai_interviewer_api.core.interview_locale import (
    localized_interview_fallbacks,
    resolve_interview_locale,
)


def test_confirmation_accepts_common_speech_recognition_variants() -> None:
    assert is_unambiguous_confirmation("はい、大丈夫です。") is True
    assert is_unambiguous_confirmation("はい そうです") is True
    assert is_unambiguous_confirmation("問題ありません") is True


def test_confirmation_leaves_mixed_or_negative_replies_to_ai() -> None:
    assert is_unambiguous_confirmation("はい、でも違います") is False
    assert is_unambiguous_confirmation("うん、だから") is False
    assert is_unambiguous_confirmation("いいえ") is False


def test_voice_confirmation_fast_path_does_not_need_a_model_call(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def fail_model(**_: object) -> None:
        raise AssertionError("an unambiguous confirmation must not call the model")

    monkeypatch.setattr(voice_interview, "_run_voice_json_output", fail_model)

    result = voice_interview._evaluate_confirmation_response(
        current_question={"questionId": "q-001", "text": "趣味を教えてください。"},
        candidate_answer="バスケです",
        user_reply="はい、大丈夫です。",
        field_state={"answerState": "AWAITING_CONFIRMATION"},
    )

    assert result.outcome == "CONFIRM"
    assert result.record_answer == "バスケです"


def test_interview_locale_is_record_scoped_and_supports_portuguese() -> None:
    assert resolve_interview_locale(
        {"interviewLocale": "pt-BR"},
        {"interviewPlan": {"interviewLocale": "en-US"}},
    ) == "pt-BR"
    assert localized_interview_fallbacks("pt-BR")["completion"].startswith("A entrevista")
