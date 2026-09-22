from __future__ import annotations

import pytest

from ai_interviewer_api.agents.interview_knowledge.schemas import (
    AnswerAssessment,
    AnswerAspectAssessment,
    ASRAmbiguousSpan,
    FieldUpdate,
    StructuredInterviewOutput,
    TranscriptAssessment,
)
from ai_interviewer_api.services.conversation_control import (
    has_evaluable_answer_content,
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
            "PARTIALLY_ANSWERED",
            "ASK_MISSING_INFORMATION",
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


def test_answered_probe_is_a_separate_follow_up_decision() -> None:
    normalized = normalize_conversation_output(
        StructuredInterviewOutput(
            answerStatus="ANSWERED",
            probeDecision="PROBE",
            probeDimension="JUDGMENT",
            probeQuestion="何を根拠に判断しましたか？",
        )
    )

    assert normalized.answerStatus == "ANSWERED"
    assert normalized.probeDecision == "PROBE"
    assert next_action_for_evaluation(
        normalized.answerEvaluation,
        probe_decision=normalized.probeDecision,
    ) == "FOLLOW_UP"


def test_partial_answer_status_cannot_request_a_probe() -> None:
    normalized = normalize_conversation_output(
        StructuredInterviewOutput(
            answerStatus="PARTIAL",
            probeDecision="PROBE",
            probeDimension="JUDGMENT",
        )
    )

    assert normalized.answerStatus == "PARTIAL"
    assert normalized.probeDecision == "NONE"
    assert normalized.probeDimension is None


@pytest.mark.parametrize(
    "output",
    [
        StructuredInterviewOutput(
            dialogueAct="CLARIFICATION_REQUEST",
            answerStatus="ANSWERED",
            probeDecision="PROBE",
        ),
        StructuredInterviewOutput(
            transcriptAssessment=TranscriptAssessment(
                recognitionStatus="AMBIGUOUS",
                ambiguousSpans=[
                    ASRAmbiguousSpan(
                        span="聞き取り候補",
                        semanticRole="organization_name",
                        important=True,
                    )
                ],
            ),
            answerStatus="ANSWERED",
            probeDecision="PROBE",
        ),
        StructuredInterviewOutput(
            utteranceCompleteness="INCOMPLETE",
            answerStatus="ANSWERED",
            probeDecision="PROBE",
        ),
    ],
    ids=["clarification", "not-heard", "incomplete"],
)
def test_unanswered_turns_never_become_probe(output: StructuredInterviewOutput) -> None:
    normalized = normalize_conversation_output(output)

    assert normalized.answerStatus == "NOT_ANSWERED"
    assert normalized.probeDecision == "NONE"
    assert normalized.probeQuestion is None


def test_missing_information_overrides_contradictory_sufficient_label() -> None:
    normalized = normalize_conversation_output(
        StructuredInterviewOutput(
            answerAssessment=AnswerAssessment(sufficiency="SUFFICIENT"),
            missingInformation=["具体的な出来事"],
        )
    )

    assert normalized.answerEvaluation == "PARTIALLY_ANSWERED"
    assert normalized.answerAssessment.sufficiency == "PARTIAL"


def test_incomplete_tail_keeps_a_completed_aspect_as_a_partial_answer() -> None:
    normalized = normalize_conversation_output(
        StructuredInterviewOutput(
            utteranceCompleteness="INCOMPLETE",
            answerAssessment=AnswerAssessment(
                sufficiency="PARTIAL",
                probeType="CLARIFY",
            ),
            aspectAssessments=[
                AnswerAspectAssessment(
                    aspectId="expertise",
                    label="現在の専門性",
                    status="ANSWERED",
                    evidence="システム開発",
                ),
                AnswerAspectAssessment(
                    aspectId="work_style",
                    label="仕事の進め方",
                    status="MISSING",
                ),
            ],
            missingInformation=["仕事の進め方"],
            fieldUpdates=[
                FieldUpdate(
                    fieldId="field-experience",
                    itemId="expertise",
                    value="システム開発",
                    evidenceTranscriptIds=["answer-1"],
                )
            ],
        )
    )

    assert has_evaluable_answer_content(normalized) is True
    assert normalized.answerEvaluation == "PARTIALLY_ANSWERED"
    assert normalized.answerStatus == "PARTIAL"
    assert next_action_for_evaluation(
        normalized.answerEvaluation,
        has_missing_information=True,
    ) == "ASK_MISSING_INFORMATION"


def test_unanswerable_incomplete_tail_still_waits_for_continuation() -> None:
    normalized = normalize_conversation_output(
        StructuredInterviewOutput(
            utteranceCompleteness="INCOMPLETE",
            fieldUpdates=[
                FieldUpdate(
                    fieldId="field-experience",
                    value="システム開発を担当し",
                    evidenceTranscriptIds=["answer-2"],
                )
            ],
        )
    )

    assert has_evaluable_answer_content(normalized) is False
    assert normalized.answerEvaluation == "INCOMPLETE_UTTERANCE"
    assert normalized.answerStatus == "NOT_ANSWERED"
    assert next_action_for_evaluation(normalized.answerEvaluation) == "WAIT_FOR_CONTINUATION"


def test_aspect_assessments_keep_answered_support_and_only_add_missing_skill() -> None:
    normalized = normalize_conversation_output(
        StructuredInterviewOutput(
            aspectAssessments=[
                AnswerAspectAssessment(
                    aspectId="theme",
                    label="挑戦したいテーマ",
                    status="ANSWERED",
                    evidence="新しい技術を活用したテーマを立ち上げたい",
                ),
                AnswerAspectAssessment(
                    aspectId="skill",
                    label="身につけたい能力",
                    status="MISSING",
                ),
                AnswerAspectAssessment(
                    aspectId="support",
                    label="必要な支援",
                    status="ANSWERED",
                    evidence="今は特に必要ないが、セミナーや記事は利用したい",
                ),
            ],
            missingInformation=["必要な支援", "身につけたい能力"],
        )
    )

    assert normalized.answerEvaluation == "PARTIALLY_ANSWERED"
    assert normalized.answerStatus == "PARTIAL"
    assert normalized.missingInformation == ["身につけたい能力"]


def test_optional_missing_aspect_does_not_turn_answer_into_partial() -> None:
    normalized = normalize_conversation_output(
        StructuredInterviewOutput(
            aspectAssessments=[
                AnswerAspectAssessment(
                    aspectId="theme",
                    label="挑戦したいテーマ",
                    status="ANSWERED",
                ),
                AnswerAspectAssessment(
                    aspectId="example",
                    label="具体例",
                    status="MISSING",
                    required=False,
                ),
            ],
            missingInformation=["具体例"],
        )
    )

    assert normalized.answerEvaluation == "ANSWERED"
    assert normalized.answerStatus == "ANSWERED"
    assert normalized.missingInformation == []


def test_nonessential_asr_ambiguity_does_not_become_not_heard() -> None:
    normalized = normalize_conversation_output(
        StructuredInterviewOutput(
            transcriptAssessment=TranscriptAssessment(
                recognitionStatus="AMBIGUOUS",
                ambiguousSpans=[
                    ASRAmbiguousSpan(
                        span="えっと",
                        semanticRole="hesitation",
                        important=False,
                    )
                ],
            )
        )
    )

    assert normalized.answerEvaluation == "ANSWERED"


def test_clarification_kind_is_preserved_for_target_questions() -> None:
    normalized = normalize_conversation_output(
        StructuredInterviewOutput(
            dialogueAct="CLARIFICATION_REQUEST",
            clarificationKind="ASR_TARGET",
        )
    )

    assert normalized.answerEvaluation == "CLARIFICATION_REQUEST"
    assert normalized.clarificationKind == "ASR_TARGET"


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


def test_user_question_intent_cannot_become_an_answer_update() -> None:
    normalized = normalize_conversation_output(
        StructuredInterviewOutput(
            dialogueAct="ANSWER",
            userIntent="QUESTION_INTENT_CONFIRMATION",
            interpretationMatch="PARTIAL_MATCH",
            fieldUpdates=[
                FieldUpdate(
                    fieldId="field-role",
                    value="推測値",
                    evidenceTranscriptIds=["message-1"],
                    answerResolution="AUTO_CONFIRM",
                )
            ],
        )
    )

    assert normalized.answerEvaluation == "CLARIFICATION_REQUEST"
    assert normalized.answerStatus == "NOT_ANSWERED"
    assert normalized.clarificationKind == "QUESTION_MEANING"
    assert normalized.fieldUpdates == []
