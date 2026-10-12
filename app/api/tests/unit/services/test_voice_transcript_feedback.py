from __future__ import annotations

from typing import Any

from ai_interviewer_api.services.voice_transcript_feedback import (
    build_transcribe_polly_transcript_feedback,
)


FIELD_LABELS = {
    "field-equipment-name": "設備名",
    "field-machine-number": "号機",
    "field-department": "部署",
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


def _correction_result(
    *,
    raw_transcript: str,
    normalized_transcript: str,
    target_refs: list[dict[str, str]],
    field_states: dict[str, Any] | None = None,
    source_question: str = "対象の設備名と号機を教えてください。",
) -> dict[str, Any]:
    return {
        "reply": "この部分をもう一度お願いします。",
        "question": {
            "questionId": "q-transcript-confirmation",
            "targetType": "transcript_confirmation",
            "targetId": "transcript_confirmation",
            "text": "安全でない文字起こし確認文",
        },
        "interviewState": {
            "lastTranscriptAssessment": {
                "rawTranscript": raw_transcript,
                "normalizedTranscript": normalized_transcript,
                "correctionStatus": "CORRECTED",
                "correctionCandidates": [normalized_transcript],
            },
            "pendingTranscriptConfirmation": {
                "messageId": "voice-msg-turn-1",
                "rawTranscript": raw_transcript,
                "normalizedTranscript": normalized_transcript,
                "correctionCandidates": [normalized_transcript],
                "candidateMappingVersion": 1,
                "targetRefs": target_refs,
                "sourceQuestion": {"text": source_question},
            },
            "fieldStates": field_states or {},
        },
    }


def test_mapped_equipment_and_number_are_confirmed_item_by_item() -> None:
    utterance = "対象の設備名はB一Xです。号機は007です。"
    result = _correction_result(
        raw_transcript="対象の設備名はビー一エックスです。号機は007です。",
        normalized_transcript=utterance,
        target_refs=[
            {"targetType": "field", "targetId": "field-equipment-name"},
            {"targetType": "field", "targetId": "field-machine-number"},
        ],
        field_states={
            "field-equipment-name": {
                "questionPlan": {
                    "requiredItems": [{"itemId": "equipment", "label": "設備名"}]
                },
                "candidateItems": [
                    {
                        "itemId": "equipment",
                        "value": "B一X",
                        "evidenceTranscriptIds": ["voice-msg-turn-1"],
                    }
                ],
            },
            "field-machine-number": {
                "questionPlan": {
                    "requiredItems": [{"itemId": "number", "label": "号機"}]
                },
                "candidateItems": [
                    {
                        "itemId": "number",
                        "value": "007",
                        "evidenceTranscriptIds": ["voice-msg-turn-1"],
                    }
                ],
            },
        },
    )

    reply = _feedback(result)

    assert reply == "設備名は「B一X」、号機は「007」という理解でよろしいですか？"
    assert utterance not in reply


def test_missing_item_mapping_repeats_the_named_target_without_the_full_transcript() -> None:
    utterance = "対象の設備名はB一Xです。号機は007です。"
    result = _correction_result(
        raw_transcript="対象の設備名はビー一エックスです。号機は007です。",
        normalized_transcript=utterance,
        target_refs=[
            {"targetType": "field", "targetId": "field-equipment-name"}
        ],
    )

    reply = _feedback(result)

    assert "設備名" in reply
    assert "もう一度" in reply
    assert utterance not in reply
    assert "この部分" not in reply
    assert "confirmation" not in reply.lower()


def test_unmapped_confirmation_uses_the_original_question_as_a_clear_retry() -> None:
    utterance = "対象の設備名はB一Xです。号機は007です。"
    source_question = "対象の設備名と号機を教えてください。"
    result = _correction_result(
        raw_transcript=utterance,
        normalized_transcript=utterance,
        target_refs=[],
        source_question=source_question,
    )

    reply = _feedback(result)

    assert source_question in reply
    assert utterance not in reply
    assert "この部分" not in reply


def test_ambiguous_equipment_role_is_not_described_as_a_person_name() -> None:
    result = {
        "reply": "この部分をもう一度お願いします。",
        "question": {
            "questionId": "q-equipment",
            "targetType": "field",
            "targetId": "field-equipment-name",
        },
        "interviewState": {
            "lastTranscriptAssessment": {
                "rawTranscript": "b一xです",
                "normalizedTranscript": "",
                "correctionStatus": "UNCERTAIN",
                "correctionCandidates": ["B一X", "B一S"],
                "ambiguousSpans": [
                    {
                        "span": "b一x",
                        "semanticRole": "equipment_name",
                        "important": True,
                    }
                ],
            },
            "fieldStates": {},
        },
    }

    reply = _feedback(result)

    assert "設備名の部分だけ" in reply
    assert "人名" not in reply
    assert "B一X" not in reply
    assert "B一S" not in reply


def test_non_correction_asr_retry_can_report_the_item_that_was_understood() -> None:
    result = {
        "reply": "回答がうまく聞き取れませんでした。",
        "question": {
            "questionId": "q-department",
            "targetType": "field",
            "targetId": "field-department",
        },
        "answerEvaluation": "NOT_HEARD",
        "interviewState": {
            "lastTranscriptAssessment": {
                "correctionStatus": "NONE",
                "correctionCandidates": [],
            },
            "lastClarificationTarget": {
                "type": "ASR_AMBIGUOUS",
                "span": "開発部",
                "semanticRole": "department",
            },
            "lastProcessedUserMessageId": "voice-msg-turn-1",
            "lastStructuredOutput": {
                "fieldUpdates": [
                    {
                        "fieldId": "field-equipment-name",
                        "value": "B一X",
                        "evidenceTranscriptIds": ["voice-msg-turn-1"],
                        "answerResolution": "AUTO_CONFIRM",
                    }
                ]
            },
        },
    }

    reply = _feedback(result)

    assert "設備名は「B一X」" in reply
    assert "所属" in reply
    assert "だけもう一度" in reply
