from __future__ import annotations

from typing import Any

import pytest

from ai_interviewer_api.agents.interview.adapter import (
    adapt_interview_turn_output,
    build_interview_turn_input,
    run_adapted_interview_turn,
)
from ai_interviewer_api.agents.interview.schemas import InterviewFieldEvaluation, InterviewTurnOutput
from ai_interviewer_api.repositories.store import store


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.tables.clear()


def test_build_interview_turn_input_maps_existing_record_context() -> None:
    interview_input = build_interview_turn_input(
        record={
            "id": "record-1",
            "knowledgeId": "knowledge-1",
            "title": "圧入機A 朝一の荷重ばらつき",
            "targetEquipment": "圧入機A",
        },
        knowledge={
            "id": "knowledge-1",
            "name": "保全ノウハウ",
            "description": "圧入工程のインタビュー",
            "targetBusiness": "保全",
            "targetEquipment": "圧入機A",
            "systemPrompt": "停止判断を優先して確認してください。",
        },
        messages=[
            {
                "id": "msg-1",
                "role": "assistant",
                "content": "どの現象から始まりましたか。",
                "questionId": "q-001",
                "questionType": "configured_field",
                "fieldId": "field-1",
            },
            {
                "id": "msg-2",
                "role": "user",
                "content": "朝一だけ圧入荷重が不安定です。",
                "answerToQuestionId": "q-001",
                "answerToFieldId": "field-1",
            },
        ],
        knowledge_fields=[
            {
                "id": "field-1",
                "name": "現象",
                "description": "発生している症状",
                "aiQuestionExamples": ["どのような現象が起きていますか。"],
                "inputType": "long_text",
                "required": True,
                "askByAi": True,
                "displayOrder": 1,
            },
        ],
        interview_state={
            "status": "in_progress",
            "currentFieldId": "field-1",
            "currentQuestionId": "q-001",
            "completedFieldIds": [],
            "pendingFieldIds": ["field-1"],
            "askedQuestions": [
                {
                    "questionId": "q-001",
                    "questionType": "configured_field",
                    "fieldId": "field-1",
                    "text": "どの現象から始まりましたか。",
                }
            ],
            "followUpCounts": {"field-1": 0},
            "fieldStates": {},
            "lastProcessedUserMessageId": None,
        },
    )

    assert interview_input.knowledge_id == "knowledge-1"
    assert interview_input.current_field is not None
    assert interview_input.current_field.fieldId == "field-1"
    assert interview_input.current_question is not None
    assert interview_input.current_question.questionId == "q-001"
    assert interview_input.follow_up_count == 0
    assert interview_input.user_message == "朝一だけ圧入荷重が不安定です。"


def test_run_adapted_interview_turn_preserves_custom_prompt_and_never_saves() -> None:
    def fake_runner(*args: Any, **kwargs: Any) -> InterviewTurnOutput:
        prompt = args[0]
        assert "runtime_custom_prompt:" in prompt
        assert "停止判断を優先して確認してください。" in prompt
        assert "current_field:" in prompt
        assert "current_question:" in prompt
        return InterviewTurnOutput(
            reply="状況を整理します。",
            field_evaluation=InterviewFieldEvaluation(
                fieldId="field-1",
                isComplete=False,
                answerSummary="朝一だけ荷重がばらつく。",
                missingInformation=["発生タイミング"],
                nextAction="follow_up",
            ),
            follow_up_question="朝一のどのタイミングで発生しますか。",
            used_tools=["search_existing_fields"],
        )

    result = run_adapted_interview_turn(
        {
            "id": "record-1",
            "knowledgeId": "knowledge-1",
            "title": "圧入機A 朝一の荷重ばらつき",
        },
        {
            "id": "knowledge-1",
            "name": "保全ノウハウ",
            "systemPrompt": "停止判断を優先して確認してください。",
        },
        [
            {
                "id": "msg-1",
                "role": "assistant",
                "content": "どの現象から始まりましたか。",
                "questionId": "q-001",
                "questionType": "configured_field",
                "fieldId": "field-1",
            },
            {
                "id": "msg-2",
                "role": "user",
                "content": "朝一だけ圧入荷重が不安定です。",
                "answerToQuestionId": "q-001",
                "answerToFieldId": "field-1",
            },
        ],
        [
            {
                "id": "field-1",
                "name": "現象",
                "description": "発生している症状",
                "inputType": "long_text",
                "required": True,
                "askByAi": True,
                "displayOrder": 1,
            }
        ],
        interview_state={
            "status": "in_progress",
            "currentFieldId": "field-1",
            "currentQuestionId": "q-001",
            "completedFieldIds": [],
            "pendingFieldIds": ["field-1"],
            "askedQuestions": [
                {
                    "questionId": "q-001",
                    "questionType": "configured_field",
                    "fieldId": "field-1",
                    "text": "どの現象から始まりましたか。",
                }
            ],
            "followUpCounts": {"field-1": 0},
            "fieldStates": {},
            "lastProcessedUserMessageId": None,
        },
        agent_runner=fake_runner,
    )

    assert result.reply_text == "状況を整理します。"
    assert result.follow_up_question == "朝一のどのタイミングで発生しますか。"
    assert result.field_evaluation["fieldId"] == "field-1"
    assert result.field_evaluation["nextAction"] == "follow_up"
    assert result.used_tools == ["search_existing_fields"]
    assert store.tables["proposals"] == {}
    assert store.tables["records"] == {}
    assert store.tables["messages"] == {}


def test_adapt_interview_turn_output_keeps_structured_metadata() -> None:
    result = adapt_interview_turn_output(
        InterviewTurnOutput(
            reply="復旧手順を整理します。",
            field_evaluation=InterviewFieldEvaluation(
                fieldId="field-2",
                isComplete=True,
                answerSummary="交換後に再発していない。",
                missingInformation=[],
                nextAction="next_field",
            ),
            follow_up_question=None,
            used_tools=["search_past_knowledge"],
        )
    )

    assert result.reply_text == "復旧手順を整理します。"
    assert result.field_evaluation["fieldId"] == "field-2"
    assert result.field_evaluation["isComplete"] is True
    assert result.follow_up_question is None
    assert result.used_tools == ["search_past_knowledge"]
