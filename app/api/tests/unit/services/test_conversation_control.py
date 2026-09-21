from __future__ import annotations

import pytest

from ai_interviewer_api.agents.interview_knowledge.schemas import (
    AnswerAssessment,
    FieldUpdate,
    StructuredInterviewOutput,
    TranscriptAssessment,
)
from ai_interviewer_api.services.conversation_control import (
    llm_input_summary,
    next_action_for_evaluation,
    normalize_conversation_output,
)


@pytest.mark.parametrize(
    ("category", "output", "evaluation", "action"),
    [
        (
            "定型情報・完全回答",
            StructuredInterviewOutput(
                answerAssessment=AnswerAssessment(sufficiency="SUFFICIENT")
            ),
            "ANSWERED",
            "MOVE_NEXT",
        ),
        (
            "責任範囲・一部回答",
            StructuredInterviewOutput(
                answerAssessment=AnswerAssessment(
                    sufficiency="PARTIAL", probeType="CRITERIA"
                ),
                missingInformation=["判断範囲"],
            ),
            "PARTIALLY_ANSWERED",
            "ASK_MISSING_INFORMATION",
        ),
        (
            "経験・別観点の回答",
            StructuredInterviewOutput(dialogueAct="IRRELEVANT"),
            "OFF_TARGET",
            "KEEP_CURRENT_QUESTION",
        ),
        (
            "意味の確認",
            StructuredInterviewOutput(dialogueAct="CLARIFICATION_REQUEST"),
            "CLARIFICATION_REQUEST",
            "EXPLAIN_QUESTION",
        ),
        (
            "短いが完結した回答",
            StructuredInterviewOutput(
                utteranceCompleteness="COMPLETE",
                answerAssessment=AnswerAssessment(
                    sufficiency="EXAMPLE_MISSING", probeType="EXAMPLE"
                ),
            ),
            "PARTIALLY_ANSWERED",
            "ASK_MISSING_INFORMATION",
        ),
        (
            "発話途中",
            StructuredInterviewOutput(utteranceCompleteness="INCOMPLETE"),
            "INCOMPLETE_UTTERANCE",
            "WAIT_FOR_CONTINUATION",
        ),
        (
            "文字起こし欠落",
            StructuredInterviewOutput(
                transcriptAssessment=TranscriptAssessment(
                    recognitionStatus="MISSING",
                    recognitionEvidence=["no_final_or_partial_transcript"],
                )
            ),
            "NOT_HEARD",
            "ASK_REPEAT",
        ),
        (
            "正常文字起こし・情報不足",
            StructuredInterviewOutput(
                transcriptAssessment=TranscriptAssessment(
                    recognitionStatus="CLEAR"
                ),
                answerAssessment=AnswerAssessment(
                    sufficiency="REASON_MISSING", probeType="REASON"
                ),
            ),
            "PARTIALLY_ANSWERED",
            "ASK_MISSING_INFORMATION",
        ),
        (
            "直前回答の訂正",
            StructuredInterviewOutput(dialogueAct="CORRECTION"),
            "CORRECTION",
            "APPLY_CORRECTION",
        ),
        (
            "質問の再読",
            StructuredInterviewOutput(dialogueAct="REPEAT_REQUEST"),
            "REPEAT_REQUEST",
            "REPEAT_CURRENT_QUESTION",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_common_conversation_evaluation_distinguishes_turn_types(
    category: str,
    output: StructuredInterviewOutput,
    evaluation: str,
    action: str,
) -> None:
    normalized = normalize_conversation_output(output)

    assert normalized.answerEvaluation == evaluation, category
    assert next_action_for_evaluation(normalized.answerEvaluation) == action


def test_related_field_is_not_confirmed_when_interpreter_marks_it_off_target() -> None:
    output = StructuredInterviewOutput(
        dialogueAct="IRRELEVANT",
        fieldUpdates=[
            FieldUpdate(
                fieldId="field-process",
                value="顧客価値を考えてから開発する",
                evidenceTranscriptIds=["message-1"],
            )
        ],
    )

    normalized = normalize_conversation_output(output)

    assert normalized.answerEvaluation == "OFF_TARGET"
    assert next_action_for_evaluation(normalized.answerEvaluation) == "KEEP_CURRENT_QUESTION"


def test_missing_information_overrides_contradictory_sufficient_label() -> None:
    normalized = normalize_conversation_output(
        StructuredInterviewOutput(
            answerAssessment=AnswerAssessment(sufficiency="SUFFICIENT"),
            missingInformation=["具体的な出来事"],
        )
    )

    assert normalized.answerEvaluation == "PARTIALLY_ANSWERED"
    assert normalized.answerAssessment.sufficiency == "PARTIAL"


def test_proposal_request_with_a_generated_candidate_is_not_question_help() -> None:
    normalized = normalize_conversation_output(
        StructuredInterviewOutput(
            dialogueAct="QUESTION_TO_ASSISTANT",
            fieldUpdates=[
                FieldUpdate(
                    fieldId="field-request",
                    value="利用者が検索結果をCSVで取得できる",
                    candidateSource="assistant_proposal",
                    evidenceTranscriptIds=["message-2"],
                )
            ],
        )
    )

    assert normalized.answerEvaluation == "ANSWERED"


def test_question_generator_trace_input_contains_semantic_context() -> None:
    summary = llm_input_summary(
        {
            "currentQuestion": {
                "questionId": "q-1",
                "text": "責任範囲を教えてください。",
                "targetType": "field",
                "targetId": "field-role",
            },
            "questionPurpose": "役割と責任範囲を確認する",
            "requiredAspects": [{"itemId": "decision", "label": "判断範囲"}],
            "acquiredInformation": [{"itemId": "role", "value": "リーダー"}],
            "missingInformation": ["判断範囲"],
            "answerEvaluation": "PARTIALLY_ANSWERED",
            "sameQuestionAskedBefore": True,
            "latestUtterance": {"messageId": "m-1", "rawTranscript": "リーダーです"},
        }
    )

    assert summary["currentQuestionId"] == "q-1"
    assert summary["questionPurpose"] == "役割と責任範囲を確認する"
    assert summary["acquiredInformation"] == [{"itemId": "role", "value": "リーダー"}]
    assert summary["missingInformation"] == ["判断範囲"]
    assert summary["sameQuestionAskedBefore"] is True
    assert summary["latestTranscriptChars"] == len("リーダーです")
