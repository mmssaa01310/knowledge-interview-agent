from __future__ import annotations

import pytest

from ai_interviewer_api.services.interview_answer_processor import (
    ANSWER_STATE_AWAITING_CONFIRMATION,
    ANSWER_STATE_CANDIDATE_PENDING,
    ANSWER_STATE_CONFIRMED,
    AnswerEvaluation,
    ConfirmationEvaluation,
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
    confirmation: ConfirmationEvaluation | None = None,
) -> tuple[dict, object, list[str]]:
    calls: list[str] = []

    def evaluator(**_: object) -> AnswerEvaluation:
        calls.append("evaluated")
        return evaluation

    state = _state()
    result = InterviewAnswerProcessor(
        evaluator=evaluator,
        confirmation_evaluator=(lambda **_: confirmation) if confirmation else None,
    ).process_turn_sync(
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
        (AnswerEvaluation(decision="CONFIRMABLE", normalized_answer="分析用要約", record_answer="整形済み回答", is_relevant=True, is_sufficient=True), ANSWER_STATE_AWAITING_CONFIRMATION, "整形済み回答"),
        (AnswerEvaluation(decision="NEEDS_MORE_INFORMATION", normalized_answer="分析用要約", record_answer="取得済み情報", is_relevant=True, missing_information=["根拠"]), ANSWER_STATE_CANDIDATE_PENDING, "取得済み情報"),
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


def test_case_1_high_confidence_answer_is_auto_confirmed_without_confirmation() -> None:
    state, result, _ = _process(
        AnswerEvaluation(
            decision="CONFIRMABLE",
            answer_resolution="AUTO_CONFIRM",
            normalized_answer="med900",
            record_answer="med900",
            is_relevant=True,
            is_sufficient=True,
        ),
        transcript="med900",
    )

    field_state = state["fieldStates"]["field-1"]
    assert result.action == "confirmed"
    assert result.reply_text == ""
    assert field_state["answerState"] == ANSWER_STATE_CONFIRMED
    assert field_state["answerResolution"] == "AUTO_CONFIRM"
    assert field_state["recordAnswer"] == "med900"
    assert field_state["candidateAnswer"] is None
    assert state["completedFieldIds"] == ["field-1"]


def test_case_2_tentative_answer_is_carried_without_yes_no_confirmation() -> None:
    state, result, _ = _process(
        AnswerEvaluation(
            decision="CONFIRMABLE",
            answer_resolution="TENTATIVE",
            normalized_answer="朝",
            record_answer="朝",
            is_relevant=True,
            is_sufficient=True,
        ),
        transcript="たぶん朝かな",
    )

    field_state = state["fieldStates"]["field-1"]
    assert result.action == "tentative"
    assert "よろしい" not in result.reply_text
    assert field_state["answerState"] == ANSWER_STATE_CANDIDATE_PENDING
    assert field_state["answerResolution"] == "TENTATIVE"
    assert field_state["candidateAnswer"] == "朝"
    assert state["tentativeBridgeFieldId"] == "field-1"
    assert state["completedFieldIds"] == []


def test_case_3_retry_discards_semantically_invalid_transcript_without_confirmation() -> None:
    state, result, _ = _process(
        AnswerEvaluation(
            decision="UNCLEAR",
            answer_resolution="RETRY",
            is_relevant=False,
            follow_up_question="うまく聞き取れませんでした。発生条件を教えてください。",
        ),
        transcript="画面",
    )

    field_state = state["fieldStates"]["field-1"]
    assert result.action == "ask_follow_up"
    assert result.reply_text == "うまく聞き取れませんでした。発生条件を教えてください。"
    assert "よろしい" not in result.reply_text
    assert field_state["answerState"] == "UNANSWERED"
    assert field_state["answerResolution"] is None
    assert field_state["candidateAnswer"] is None
    assert state["completedFieldIds"] == []


def test_case_4_correction_replaces_tentative_candidate_from_previous_field() -> None:
    evaluations = iter(
        [
            AnswerEvaluation(
                decision="CONFIRMABLE",
                answer_resolution="TENTATIVE",
                normalized_answer="朝",
                record_answer="朝",
                is_relevant=True,
                is_sufficient=True,
            ),
            AnswerEvaluation(
                decision="CORRECT_PREVIOUS_FIELD",
                answer_resolution="TENTATIVE",
                normalized_answer="停止後に多い",
                record_answer="停止後に多い",
                target_field_id="field-1",
                is_relevant=True,
                is_sufficient=True,
            ),
        ]
    )
    processor = InterviewAnswerProcessor(evaluator=lambda **_: next(evaluations))
    current_state = _state()

    first = processor.process_turn_sync(
        record_id="record-1",
        question_id="question-1",
        field_id="field-1",
        transcript="たぶん朝かな",
        current_state=current_state,
        question={"questionId": "question-1", "text": "発生条件を教えてください。"},
        field={"id": "field-1", "name": "発生条件"},
        evidence_transcript_id="message-1",
        retrieval_policy="never",
    )

    second = processor.process_turn_sync(
        record_id="record-1",
        question_id="question-2",
        field_id="field-2",
        transcript="いや、停止後に多い",
        current_state=current_state,
        question={"questionId": "question-2", "text": "原因を教えてください。"},
        field={"id": "field-2", "name": "原因"},
        evidence_transcript_id="message-2",
        retrieval_policy="never",
    )

    assert first.action == "tentative"
    assert second.action == "tentative"
    assert current_state["fieldStates"]["field-1"]["candidateAnswer"] == "停止後に多い"
    assert current_state["fieldStates"]["field-1"]["answerResolution"] == "TENTATIVE"
    assert current_state["fieldStates"]["field-1"]["rawAnswerHistory"] == [
        "たぶん朝かな",
        "いや、停止後に多い",
    ]


def test_retrieval_never_still_runs_initial_evaluation() -> None:
    state, result, calls = _process(
        AnswerEvaluation(
            decision="CONFIRMABLE",
            normalized_answer="分析用要約",
            record_answer="候補",
            retrieval_needed=False,
        ),
        policy="never",
    )

    assert calls == ["evaluated"]
    assert result.retrieval_executed is False
    assert state["fieldStates"]["field-1"]["candidateAnswer"] == "候補"


def test_confirmation_question_uses_llm_natural_question_not_llm_summary() -> None:
    _, result, _ = _process(
        AnswerEvaluation(
            decision="CONFIRMABLE",
            normalized_answer="宮崎",
            confirmation_question="宮崎さんでよろしいですか？",
        ),
        transcript="宮崎です",
    )

    assert result.reply_text == "宮崎さんでよろしいですか？"


def test_confirmation_is_the_only_transition_that_saves_answer() -> None:
    state, _, _ = _process(
        AnswerEvaluation(decision="CONFIRMABLE", normalized_answer="確定候補", record_answer="確定候補")
    )
    processor = InterviewAnswerProcessor(
        evaluator=lambda **_: AnswerEvaluation(decision="UNCLEAR"),
        confirmation_evaluator=lambda **_: ConfirmationEvaluation(
            outcome="CONFIRM", record_answer="確定候補"
        ),
    )

    result = processor.process_turn_sync(
        record_id="record-1",
        question_id="question-1",
        field_id="field-1",
        transcript="確認します",
        current_state=state,
        question={"questionId": "question-1", "text": "質問ですか？"},
        field={"id": "field-1", "name": "項目"},
        evidence_transcript_id="message-2",
        retrieval_policy="never",
    )

    field_state = state["fieldStates"]["field-1"]
    assert result.action == "confirmed"
    assert field_state["answerState"] == ANSWER_STATE_CONFIRMED
    assert field_state["answerSummary"] is None
    assert field_state["recordAnswer"] == "確定候補"
    assert field_state["candidateAnswer"] is None
    assert state["completedFieldIds"] == ["field-1"]
    assert state["pendingFieldIds"] == []


def test_confirmation_does_not_guess_from_user_words_without_llm_evaluation() -> None:
    state, _, _ = _process(AnswerEvaluation(decision="CONFIRMABLE", normalized_answer="確定候補"))
    evaluator_calls: list[str] = []
    processor = InterviewAnswerProcessor(
        evaluator=lambda **_: evaluator_calls.append("called") or AnswerEvaluation(decision="UNCLEAR")
    )

    result = processor.process_turn_sync(
        record_id="record-1",
        question_id="question-1",
        field_id="field-1",
        transcript="はい",
        current_state=state,
        question={"questionId": "question-1", "text": "質問ですか？"},
        field={"id": "field-1", "name": "項目"},
        evidence_transcript_id="message-2",
        retrieval_policy="never",
    )

    assert evaluator_calls == []
    assert result.action == "ask_follow_up"
    assert state["fieldStates"]["field-1"]["answerSummary"] is None
    assert state["fieldStates"]["field-1"]["answerState"] == ANSWER_STATE_AWAITING_CONFIRMATION


def test_confirmation_uses_llm_record_answer_and_captured_items() -> None:
    state, _, _ = _process(
        AnswerEvaluation(
            decision="CONFIRMABLE",
            normalized_answer="設備保全です",
            record_answer="設備保全です",
            captured_items=[{"itemId": "current_role", "value": "設備保全"}],
        ),
        transcript="設備保全です",
    )
    processor = InterviewAnswerProcessor(
        evaluator=lambda **_: AnswerEvaluation(decision="UNCLEAR"),
        confirmation_evaluator=lambda **_: ConfirmationEvaluation(
            outcome="REVISE_WITH_CONTENT",
            record_answer="保全管理です",
            revised_answer="保全管理です",
            confirmation_question="担当業務は保全管理でよろしいですか？",
            captured_items=[{"itemId": "current_role", "value": "保全管理"}],
        ),
    )

    correction = processor.process_turn_sync(
        record_id="record-1",
        question_id="question-1",
        field_id="field-1",
        transcript="いいえ、保全管理です。",
        current_state=state,
        question={"questionId": "question-1", "text": "担当業務を教えてください。"},
        field={"id": "field-1", "name": "担当業務"},
        evidence_transcript_id="message-2",
        retrieval_policy="never",
    )

    assert correction.action == "ask_confirmation"
    assert state["fieldStates"]["field-1"]["rawAnswer"] == "いいえ、保全管理です。"
    assert state["fieldStates"]["field-1"]["rawAnswerHistory"] == [
        "設備保全です",
        "いいえ、保全管理です。",
    ]
    assert state["fieldStates"]["field-1"]["candidateAnswer"] == "保全管理です"
    assert correction.reply_text == "担当業務は保全管理でよろしいですか？"
    assert state["fieldStates"]["field-1"]["capturedItems"] == [
        {"itemId": "current_role", "value": "保全管理", "evidenceTranscriptIds": []}
    ]

    confirmed_processor = InterviewAnswerProcessor(
        evaluator=lambda **_: AnswerEvaluation(decision="UNCLEAR"),
        confirmation_evaluator=lambda **_: ConfirmationEvaluation(
            outcome="CONFIRM", record_answer="保全管理です"
        ),
    )
    confirmed_processor.process_turn_sync(
        record_id="record-1",
        question_id="question-1",
        field_id="field-1",
        transcript="はい",
        current_state=state,
        question={"questionId": "question-1", "text": "担当業務を教えてください。"},
        field={"id": "field-1", "name": "担当業務"},
        evidence_transcript_id="message-3",
        retrieval_policy="never",
    )

    assert state["fieldStates"]["field-1"]["recordAnswer"] == "保全管理です"


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
                normalized_answer="分析用要約",
                record_answer="機械が動作したことを確認して復旧と判断した",
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
                normalized_answer="分析用要約",
                record_answer="圧入機Aの2号機",
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
                normalized_answer="分析用要約",
                record_answer="サーボ過負荷が発生した",
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
            record_answer="圧入機Aの2号機",
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


def _planned_question() -> dict:
    return {
        "questionId": "question-1",
        "text": "状況を教えてください。",
        "questionPlan": {
            "purpose": "発生状況を記録する",
            "requiredItems": [
                {"itemId": "when", "label": "発生時期"},
                {"itemId": "symptom", "label": "症状"},
            ],
            "optionalItems": [{"itemId": "cause", "label": "推定原因"}],
        },
    }


def test_question_plan_merges_multiple_captured_items_and_backend_completes() -> None:
    state = _state()
    processor = InterviewAnswerProcessor(
        evaluator=lambda **_: AnswerEvaluation(
            decision="NEEDS_MORE_INFORMATION",
            captured_items=[
                {"itemId": "when", "value": "昨日"},
                {"itemId": "symptom", "value": "異音"},
            ],
            answer_disposition="ANSWERED",
        )
    )

    result = processor.process_turn_sync(
        record_id="record-1",
        question_id="question-1",
        field_id="field-1",
        transcript="昨日から異音がします",
        current_state=state,
        question=_planned_question(),
        field={"id": "field-1", "name": "状況"},
        evidence_transcript_id="message-1",
        retrieval_policy="never",
    )

    assert result.decision == "COMPLETE"
    assert result.action == "ask_confirmation"
    assert result.completion_status == "COMPLETE"
    assert result.missing_required_item_ids == []
    assert {item["itemId"] for item in state["fieldStates"]["field-1"]["candidateItems"]} == {
        "when",
        "symptom",
    }


def test_question_plan_accumulates_required_items_across_turns() -> None:
    state = _state()
    evaluations = iter(
        [
            AnswerEvaluation(
                decision="NEEDS_MORE_INFORMATION",
                captured_items=[{"itemId": "when", "value": "昨日"}],
                answer_disposition="ANSWERED",
            ),
            AnswerEvaluation(
                decision="CONFIRMABLE",
                captured_items=[{"itemId": "symptom", "value": "異音"}],
                answer_disposition="ANSWERED",
            ),
        ]
    )
    processor = InterviewAnswerProcessor(
        evaluator=lambda **_: next(evaluations),
        confirmation_evaluator=lambda **_: ConfirmationEvaluation(
            outcome="CONFIRM", record_answer="昨日です\n異音がします"
        ),
    )

    first = processor.process_turn_sync(
        record_id="record-1",
        question_id="question-1",
        field_id="field-1",
        transcript="昨日です",
        current_state=state,
        question=_planned_question(),
        field={"id": "field-1", "name": "状況"},
        evidence_transcript_id="message-1",
        retrieval_policy="never",
    )
    second = processor.process_turn_sync(
        record_id="record-1",
        question_id="question-2",
        field_id="field-1",
        transcript="異音がします",
        current_state=state,
        question={**_planned_question(), "questionId": "question-2"},
        field={"id": "field-1", "name": "状況"},
        evidence_transcript_id="message-2",
        retrieval_policy="never",
    )

    assert first.decision == "NEEDS_FOLLOWUP"
    assert first.missing_required_item_ids == ["symptom"]
    assert "症状" in first.reply_text
    assert second.decision == "COMPLETE"
    assert state["fieldStates"]["field-1"]["missingRequiredItemIds"] == []

    confirmation = processor.process_turn_sync(
        record_id="record-1",
        question_id="question-3",
        field_id="field-1",
        transcript="はい",
        current_state=state,
        question={**_planned_question(), "questionId": "question-3"},
        field={"id": "field-1", "name": "状況"},
        evidence_transcript_id="message-3",
        retrieval_policy="never",
    )

    assert confirmation.action == "confirmed"
    assert state["fieldStates"]["field-1"]["recordAnswer"] == "昨日です\n異音がします"
    assert state["fieldStates"]["field-1"]["answerSummary"] is None


def test_required_items_block_confirmation_until_all_items_are_captured() -> None:
    state = _state()
    evaluations = iter(
        [
            AnswerEvaluation(
                decision="CONFIRMABLE",
                normalized_answer="自己紹介として、氏名が回答されました。",
                confirmation_question="自己紹介として、氏名が回答されました。という理解でよろしいですか？",
                captured_items=[{"itemId": "name", "value": "宮崎"}],
                answer_disposition="ANSWERED",
            ),
            AnswerEvaluation(
                decision="CONFIRMABLE",
                normalized_answer="自己紹介として、氏名と担当業務が回答されました。",
                confirmation_question="宮崎さんと設備保全でよろしいですか？",
                captured_items=[{"itemId": "role", "value": "設備保全"}],
                answer_disposition="ANSWERED",
            ),
        ]
    )
    question = {
        "questionId": "question-1",
        "text": "自己紹介をお願いします。",
        "questionPlan": {
            "purpose": "自己紹介を記録する",
            "requiredItems": [
                {"itemId": "name", "label": "氏名"},
                {"itemId": "role", "label": "担当業務"},
            ],
        },
    }
    processor = InterviewAnswerProcessor(evaluator=lambda **_: next(evaluations))

    first = processor.process_turn_sync(
        record_id="record-1",
        question_id="question-1",
        field_id="field-1",
        transcript="宮崎です",
        current_state=state,
        question=question,
        field={"id": "field-1", "name": "自己紹介"},
        evidence_transcript_id="message-1",
        retrieval_policy="never",
    )

    assert first.decision == "NEEDS_FOLLOWUP"
    assert first.action == "ask_follow_up"
    assert first.missing_required_item_ids == ["role"]
    assert first.reply_text == "担当業務について、具体的に教えてください。"
    assert state["fieldStates"]["field-1"]["answerState"] == ANSWER_STATE_CANDIDATE_PENDING

    second = processor.process_turn_sync(
        record_id="record-1",
        question_id="question-2",
        field_id="field-1",
        transcript="設備保全です",
        current_state=state,
        question={**question, "questionId": "question-2"},
        field={"id": "field-1", "name": "自己紹介"},
        evidence_transcript_id="message-2",
        retrieval_policy="never",
    )

    assert second.decision == "COMPLETE"
    assert second.action == "ask_confirmation"
    assert second.missing_required_item_ids == []
    assert second.reply_text == "宮崎さんと設備保全でよろしいですか？"


def test_confirmation_state_with_missing_required_items_is_reopened_as_follow_up() -> None:
    state = _state()
    state["fieldStates"] = {
        "field-1": {
            "fieldId": "field-1",
            "answerState": ANSWER_STATE_AWAITING_CONFIRMATION,
            "candidateAnswer": "宮崎です",
            "missingRequiredItemIds": ["role"],
            "capturedItems": [{"itemId": "name", "value": "宮崎"}],
        },
    }
    processor = InterviewAnswerProcessor(
        evaluator=lambda **_: pytest.fail("missing required items must not evaluate confirmation")
    )

    result = processor.process_turn_sync(
        record_id="record-1",
        question_id="question-2",
        field_id="field-1",
        transcript="はい",
        current_state=state,
        question={
            "questionId": "question-2",
            "text": "自己紹介をお願いします。",
            "questionPlan": {
                "requiredItems": [
                    {"itemId": "name", "label": "氏名"},
                    {"itemId": "role", "label": "担当業務"},
                ],
            },
        },
        field={"id": "field-1", "name": "自己紹介"},
        evidence_transcript_id="message-2",
        retrieval_policy="never",
    )

    assert result.decision == "NEEDS_FOLLOWUP"
    assert result.action == "ask_follow_up"
    assert result.reply_text == "担当業務について、具体的に教えてください。"


def test_evaluation_error_does_not_become_user_answer_failure() -> None:
    state = _state()
    state["fieldStates"] = {
        "field-1": {
            "fieldId": "field-1",
            "status": "asking",
            "answerState": ANSWER_STATE_CANDIDATE_PENDING,
            "candidateAnswer": "既存候補",
            "candidateItems": [{"itemId": "when", "value": "昨日"}],
        }
    }
    processor = InterviewAnswerProcessor(
        evaluator=lambda **_: AnswerEvaluation(
            decision="UNCLEAR",
            evaluation_status="EVALUATION_ERROR",
        )
    )

    result = processor.process_turn_sync(
        record_id="record-1",
        question_id="question-1",
        field_id="field-1",
        transcript="回答",
        current_state=state,
        question=_planned_question(),
        field={"id": "field-1", "name": "状況"},
        evidence_transcript_id="message-1",
        retrieval_policy="never",
    )

    assert result.decision == "EVALUATION_ERROR"
    assert "判断できませんでした" not in result.reply_text
    assert state["fieldStates"]["field-1"]["candidateAnswer"] == "既存候補"
