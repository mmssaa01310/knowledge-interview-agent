from ai_interviewer_api.services.interview_confirmation import is_unambiguous_confirmation
from ai_interviewer_api.core.interview_locale import (
    localized_interview_fallbacks,
    resolve_interview_locale,
)


def test_confirmation_accepts_common_speech_recognition_variants() -> None:
    assert is_unambiguous_confirmation("はい、大丈夫です。") is True
    assert is_unambiguous_confirmation("はい そうです") is True
    assert is_unambiguous_confirmation("はい、それで合っています") is True
    assert is_unambiguous_confirmation("問題ありません") is True


def test_confirmation_accepts_localized_english_affirmations() -> None:
    assert is_unambiguous_confirmation("Yes", locale="en-US") is True
    assert is_unambiguous_confirmation("Yes, it's correct.", locale="en-US") is True
    assert is_unambiguous_confirmation("Correct.", locale="en-US") is True
    assert is_unambiguous_confirmation("Yes, but that is wrong.", locale="en-US") is False


def test_confirmation_leaves_mixed_or_negative_replies_to_ai() -> None:
    assert is_unambiguous_confirmation("はい、でも違います") is False
    assert is_unambiguous_confirmation("うん、だから") is False
    assert is_unambiguous_confirmation("いいえ") is False


def test_interview_locale_is_record_scoped_and_supports_portuguese() -> None:
    assert resolve_interview_locale(
        {"interviewLocale": "pt-BR"},
        {"interviewPlan": {"interviewLocale": "en-US"}},
    ) == "pt-BR"
    assert localized_interview_fallbacks("pt-BR")["completion"].startswith("A entrevista")
