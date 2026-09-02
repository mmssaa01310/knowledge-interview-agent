from __future__ import annotations

from typing import Any

from ai_interviewer_api.services.voice_transcript_feedback import (
    build_transcribe_polly_transcript_feedback,
)


FIELD_LABELS = {
    "field-name": "氏名",
    "field-department": "部署",
    "field-responsibility": "担当領域",
    "field-profile": "基本プロフィール",
}


def _uncertain_result(
    updates: list[dict[str, Any]],
    *,
    target_id: str = "field-profile",
    raw_transcript: str = "え山田太郎でかい家族部に所属しています。",
) -> dict[str, Any]:
    return {
        "reply": "この部分をもう一度お願いします。",
        "question": {
            "questionId": "q-profile",
            "targetType": "field",
            "targetId": target_id,
        },
        "interviewState": {
            "lastTranscriptAssessment": {
                "rawTranscript": raw_transcript,
                "normalizedTranscript": raw_transcript,
                "correctionStatus": "UNCERTAIN",
            },
            "lastProcessedUserMessageId": "voice-msg-turn-1",
            "lastStructuredOutput": {"fieldUpdates": updates},
            "fieldStates": {
                field_id: {"answerState": "UNANSWERED"}
                for field_id in FIELD_LABELS
            },
        },
    }


def _update(field_id: str, value: str) -> dict[str, Any]:
    return {
        "fieldId": field_id,
        "value": value,
        "evidenceTranscriptIds": ["voice-msg-turn-1"],
        "answerResolution": "AUTO_CONFIRM",
    }


def _feedback(result: dict[str, Any]) -> str:
    feedback = build_transcribe_polly_transcript_feedback(
        result,
        {"id": "turn-1"},
        field_labels=FIELD_LABELS,
        locale="ja-JP",
    )
    assert feedback is not None
    return feedback


def test_only_unclear_affiliation_is_requested_again_without_raw_asr_text() -> None:
    reply = _feedback(
        _uncertain_result(
            [
                _update("field-name", "山田太郎"),
                _update("field-responsibility", "社内システムの開発"),
            ]
        )
    )

    assert "山田太郎" in reply
    assert "社内システムの開発" in reply
    assert "所属がうまく聞き取れなかった" in reply
    assert "所属だけもう一度お願いします" in reply
    assert "家族部" not in reply
    assert "この部分をもう一度お願いします" not in reply


def test_multiple_unclear_profile_items_are_named() -> None:
    reply = _feedback(
        _uncertain_result([_update("field-responsibility", "社内システムの開発")])
    )

    assert "社内システムの開発" in reply
    assert "お名前と所属" in reply
    assert "お名前と所属だけもう一度お願いします" in reply


def test_almost_unheard_profile_requests_the_profile_again() -> None:
    reply = _feedback(_uncertain_result([]))

    assert "回答がうまく聞き取れませんでした" in reply
    assert "プロフィール" in reply
    assert "この部分をもう一度お願いします" not in reply


def test_unique_correction_candidate_is_confirmed_without_auto_accepting_it() -> None:
    result = {
        "reply": "この部分をもう一度お願いします。",
        "question": {
            "questionId": "q-transcript-confirmation",
            "targetType": "transcript_confirmation",
            "targetId": "transcript_confirmation",
        },
        "interviewState": {
            "lastTranscriptAssessment": {
                "rawTranscript": "家族部",
                "normalizedTranscript": "開発部",
                "correctionStatus": "CORRECTED",
                "correctionCandidates": ["開発部"],
            },
            "pendingTranscriptConfirmation": {
                "targetRefs": [
                    {"targetType": "field", "targetId": "field-department"}
                ]
            },
        },
    }

    reply = _feedback(result)

    assert reply == "所属は「開発部」という理解でよろしいですか？"
    assert "家族部" not in reply


def test_ambiguous_correction_does_not_speak_a_guessed_candidate() -> None:
    result = _uncertain_result(
        [],
        target_id="field-department",
        raw_transcript="家族部か開発部か聞き取れませんでした。",
    )
    result["interviewState"]["lastTranscriptAssessment"].update(
        {
            "correctionCandidates": ["開発部", "改善部"],
            "normalizedTranscript": "",
        }
    )

    reply = _feedback(result)

    assert reply == "回答がうまく聞き取れませんでした。もう一度、所属についてお話しください。"
    assert "開発部" not in reply
    assert "改善部" not in reply


def test_ambiguous_correction_does_not_read_back_a_field_candidate() -> None:
    result = _uncertain_result(
        [_update("field-responsibility", "運用まで関わっています")],
        target_id="field-responsibility",
    )
    result["interviewState"]["lastTranscriptAssessment"].update(
        {
            "correctionCandidates": ["運用まで関わっています", "輸送まで関わっています"],
        }
    )

    reply = _feedback(result)

    assert "運用まで関わっています" not in reply
    assert "輸送まで関わっています" not in reply
    assert "担当業務" in reply


def test_long_grounded_value_is_not_read_back_as_the_whole_answer() -> None:
    long_value = "社内システムの設計と開発を担当しています。" + ("詳細" * 40)
    reply = _feedback(_uncertain_result([_update("field-name", long_value)]))

    assert long_value not in reply
    assert "お名前は聞き取れました" in reply


def test_rejected_correction_uses_the_original_item_for_voice_retry() -> None:
    result = {
        "reply": "この部分をもう一度お願いします。",
        "question": {
            "questionId": "q-role",
            "targetType": "field",
            "targetId": "field-responsibility",
        },
        "interviewState": {
            "lastTranscriptAssessment": {"correctionStatus": "NONE"},
            "lastStructuredOutput": {"dialogueAct": "REJECTION"},
        },
    }
    turn = {
        "id": "turn-2",
        "baseInterviewState": {
            "pendingTranscriptConfirmation": {
                "targetRefs": [
                    {
                        "targetType": "field",
                        "targetId": "field-responsibility",
                    }
                ]
            }
        },
    }

    reply = build_transcribe_polly_transcript_feedback(
        result,
        turn,
        field_labels=FIELD_LABELS,
        locale="ja-JP",
    )

    assert reply is not None
    assert "担当業務" in reply
    assert "この部分をもう一度お願いします" not in reply


def test_generic_retry_reply_is_replaced_when_voice_path_requests_a_retry() -> None:
    result = _uncertain_result([], target_id="field-department")
    result["interviewState"]["lastTranscriptAssessment"]["correctionStatus"] = "NONE"

    reply = build_transcribe_polly_transcript_feedback(
        result,
        {"id": "turn-1"},
        field_labels=FIELD_LABELS,
        locale="ja-JP",
        force_retry=True,
    )

    assert reply == "回答がうまく聞き取れませんでした。もう一度、所属についてお話しください。"
