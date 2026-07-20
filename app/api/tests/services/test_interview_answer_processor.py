from __future__ import annotations

import pytest

from ai_interviewer_api.services.interview_answer_processor import (
    ANSWER_STATE_AWAITING_CONFIRMATION,
    ANSWER_STATE_CANDIDATE_PENDING,
    ANSWER_STATE_CONFIRMED,
    AnswerEvaluation,
    InterviewAnswerProcessor,
)


def _state() -> dict:
    return {
        "currentFieldId": "field-1",
        "currentQuestionId": "question-1",
        "completedFieldIds": [],
        "pendingFieldIds": ["field-1"],
        "fieldStates": {},
    }


def _process(
    evaluation: AnswerEvaluation,
    *,
    policy: str = "never",
    transcript: str = "回答",
) -> tuple[dict, object, list[str]]:
    calls: list[str] = []

    def evaluator(**_: object) -> AnswerEvaluation:
        calls.append("evaluated")
        return evaluation

    state = _state()
    result = InterviewAnswerProcessor(evaluator=evaluator).process_turn_sync(
        record_id="record-1",
        question_id="question-1",
        field_id="field-1",
        transcript=transcript,
        current_state=state,
        question={"questionId": "question-1", "text": "質問ですか？"},
        field={"id": "field-1", "name": "項目"},
        evidence_transcript_id="message-1",
        retrieval_policy=policy,
    )
    return state, result, calls


@pytest.mark.parametrize(
    ("evaluation", "answer_state", "candidate"),
    [
        (AnswerEvaluation(decision="CONFIRMABLE", normalized_answer="整形済み回答", is_relevant=True, is_sufficient=True), ANSWER_STATE_AWAITING_CONFIRMATION, "整形済み回答"),
        (AnswerEvaluation(decision="NEEDS_MORE_INFORMATION", normalized_answer="取得済み情報", is_relevant=True, missing_information=["根拠"]), ANSWER_STATE_CANDIDATE_PENDING, "取得済み情報"),
        (AnswerEvaluation(decision="NOT_ANSWER", is_relevant=False), ANSWER_STATE_CANDIDATE_PENDING, None),
        (AnswerEvaluation(decision="UNCLEAR", is_relevant=False), ANSWER_STATE_CANDIDATE_PENDING, None),
    ],
)
def test_candidate_state_transitions(
    evaluation: AnswerEvaluation,
    answer_state: str,
    candidate: str | None,
) -> None:
    state, _, calls = _process(evaluation)
    field_state = state["fieldStates"]["field-1"]

    assert calls == ["evaluated"]
    assert field_state["answerState"] == answer_state
    assert field_state["candidateAnswer"] == candidate
    assert field_state["answerSummary"] is None
    assert state["completedFieldIds"] == []


def test_retrieval_never_still_runs_initial_evaluation() -> None:
    state, result, calls = _process(
        AnswerEvaluation(
            decision="CONFIRMABLE",
            normalized_answer="候補",
            retrieval_needed=False,
        ),
        policy="never",
    )

    assert calls == ["evaluated"]
    assert result.retrieval_executed is False
    assert state["fieldStates"]["field-1"]["candidateAnswer"] == "候補"


def test_confirmation_is_the_only_transition_that_saves_answer() -> None:
    state, _, _ = _process(AnswerEvaluation(decision="CONFIRMABLE", normalized_answer="確定候補"))
    processor = InterviewAnswerProcessor(evaluator=lambda **_: AnswerEvaluation(decision="UNCLEAR"))

    result = processor.process_turn_sync(
        record_id="record-1",
        question_id="question-1",
        field_id="field-1",
        transcript="はい、そうです",
        current_state=state,
        question={"questionId": "question-1", "text": "質問ですか？"},
        field={"id": "field-1", "name": "項目"},
        evidence_transcript_id="message-2",
        retrieval_policy="never",
    )

    field_state = state["fieldStates"]["field-1"]
    assert result.action == "confirmed"
    assert field_state["answerState"] == ANSWER_STATE_CONFIRMED
    assert field_state["answerSummary"] == "確定候補"
    assert field_state["candidateAnswer"] is None
    assert state["completedFieldIds"] == ["field-1"]
    assert state["pendingFieldIds"] == []


@pytest.mark.parametrize(
    "confirmation_text",
    ["はい", "はい、そうです", "はい、それで合っています", "問題ありません"],
)
def test_explicit_confirmation_phrases_confirm_the_held_candidate(
    confirmation_text: str,
) -> None:
    state, _, _ = _process(AnswerEvaluation(decision="CONFIRMABLE", normalized_answer="確定候補"))
    evaluator_calls: list[str] = []
    processor = InterviewAnswerProcessor(
        evaluator=lambda **_: evaluator_calls.append("called") or AnswerEvaluation(decision="UNCLEAR")
    )

    result = processor.process_turn_sync(
        record_id="record-1",
        question_id="question-1",
        field_id="field-1",
        transcript=confirmation_text,
        current_state=state,
        question={"questionId": "question-1", "text": "質問ですか？"},
        field={"id": "field-1", "name": "項目"},
        evidence_transcript_id="message-2",
        retrieval_policy="never",
    )

    assert evaluator_calls == []
    assert result.action == "confirmed"
    assert state["fieldStates"]["field-1"]["answerSummary"] == "確定候補"


def test_knowledge_required_with_never_policy_does_not_confirm() -> None:
    state, result, _ = _process(
        AnswerEvaluation(
            decision="CONFIRMABLE",
            normalized_answer="推測候補",
            retrieval_needed=True,
        )
    )

    assert result.action == "ask_follow_up"
    assert state["fieldStates"]["field-1"]["answerSummary"] is None
    assert state["completedFieldIds"] == []


@pytest.mark.parametrize(
    ("transcript", "evaluation", "expected_candidate", "expected_action"),
    [
        (
            "今日は本当に暑いですね。昼は冷やし中華にしようと思っています。",
            AnswerEvaluation(decision="NOT_ANSWER", is_relevant=False),
            None,
            "ask_follow_up",
        ),
        (
            "とりあえず機械が動いたので復旧としました。",
            AnswerEvaluation(
                decision="NEEDS_MORE_INFORMATION",
                normalized_answer="機械が動作したことを確認して復旧と判断した",
                is_relevant=True,
                is_sufficient=False,
                missing_information=["品質確認", "連続運転確認", "数値確認"],
                follow_up_question="動作確認以外に、品質や荷重、連続運転などはどのように確認しましたか？",
            ),
            "機械が動作したことを確認して復旧と判断した",
            "ask_follow_up",
        ),
        (
            "圧入機Bの2号機です。あ、違う、Bじゃなくて圧入機Aの2号機です。",
            AnswerEvaluation(
                decision="CONFIRMABLE",
                normalized_answer="圧入機Aの2号機",
                is_relevant=True,
                is_sufficient=True,
            ),
            "圧入機Aの2号機",
            "ask_confirmation",
        ),
        (
            "サーボ過負荷が出ました。ところで昨日の野球は面白かったです。",
            AnswerEvaluation(
                decision="CONFIRMABLE",
                normalized_answer="サーボ過負荷が発生した",
                is_relevant=True,
                is_sufficient=True,
            ),
            "サーボ過負荷が発生した",
            "ask_confirmation",
        ),
    ],
)
def test_evaluation_boundary_keeps_only_ai_normalized_candidate(
    transcript: str,
    evaluation: AnswerEvaluation,
    expected_candidate: str | None,
    expected_action: str,
) -> None:
    state, result, _ = _process(evaluation, transcript=transcript)
    field_state = state["fieldStates"]["field-1"]

    assert result.action == expected_action
    assert field_state["candidateAnswer"] == expected_candidate
    assert field_state["answerSummary"] is None
    assert transcript != field_state.get("answerSummary")
    assert state["completedFieldIds"] == []


def test_previous_field_correction_does_not_update_current_field() -> None:
    state = _state()
    state["currentFieldId"] = "field-2"
    state["pendingFieldIds"] = ["field-2"]
    state["completedFieldIds"] = ["field-1"]
    state["fieldStates"] = {
        "field-1": {
            "fieldId": "field-1",
            "status": "completed",
            "answerState": "CONFIRMED",
            "answerSummary": "圧入機B",
            "candidateAnswer": None,
            "missingInformation": [],
        }
    }
    processor = InterviewAnswerProcessor(
        evaluator=lambda **_: AnswerEvaluation(
            decision="CORRECT_PREVIOUS_FIELD",
            normalized_answer="圧入機Aの2号機",
            target_field_id="field-1",
            follow_up_question="設備名を圧入機Aの2号機へ訂正してよろしいですか？",
        )
    )

    result = processor.process_turn_sync(
        record_id="record-1",
        question_id="question-2",
        field_id="field-2",
        transcript="野球の話を削除して、設備名を圧入機Aへ訂正して",
        current_state=state,
        question={"questionId": "question-2", "text": "現象を教えてください。"},
        field={"id": "field-2", "name": "現象"},
        evidence_transcript_id="message-2",
        retrieval_policy="never",
    )

    assert result.field_id == "field-1"
    assert state["fieldStates"]["field-1"]["candidateAnswer"] == "圧入機Aの2号機"
    assert state["fieldStates"]["field-1"]["answerSummary"] is None
    assert state["fieldStates"]["field-2"]["candidateAnswer"] is None
    assert "field-1" not in state["completedFieldIds"]
