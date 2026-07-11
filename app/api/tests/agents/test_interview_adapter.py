from __future__ import annotations

from typing import Any

import pytest

from ai_interviewer_api.agents.interview.adapter import (
    adapt_interview_turn_output,
    build_interview_turn_input,
    run_adapted_interview_turn,
)
from ai_interviewer_api.agents.interview.schemas import InterviewTurnOutput
from ai_interviewer_api.core.config import settings
from ai_interviewer_api.repositories.store import store


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.tables.clear()


def test_feature_flag_defaults_to_off() -> None:
    assert settings.strands_interview_agent_enabled is False


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
            {"role": "assistant", "content": "どの現象から始まりましたか。"},
            {"role": "user", "content": "朝一だけ圧入荷重が不安定です。"},
            {"role": "ai", "content": "前回交換した部品はありますか。"},
        ],
        knowledge_fields=[
            {
                "id": "field-2",
                "name": "対処方法",
                "description": "復旧時の処置",
                "inputType": "long_text",
                "required": False,
                "askByAi": False,
                "displayOrder": 2,
            },
            {
                "id": "field-1",
                "name": "現象",
                "description": "発生している症状",
                "inputType": "long_text",
                "required": True,
                "askByAi": True,
                "displayOrder": 1,
            },
        ],
    )

    assert interview_input.knowledge_id == "knowledge-1"
    assert interview_input.knowledge_name == "保全ノウハウ"
    assert interview_input.target_business == "保全"
    assert interview_input.target_equipment == "圧入機A"
    assert interview_input.record_title == "圧入機A 朝一の荷重ばらつき"
    assert interview_input.custom_prompt == "停止判断を優先して確認してください。"
    assert interview_input.user_message == "朝一だけ圧入荷重が不安定です。"
    assert [message.role for message in interview_input.conversation_history] == [
        "assistant",
        "user",
        "assistant",
    ]
    assert [message.content for message in interview_input.conversation_history] == [
        "どの現象から始まりましたか。",
        "朝一だけ圧入荷重が不安定です。",
        "前回交換した部品はありますか。",
    ]
    assert [field.name for field in interview_input.approved_fields] == ["現象"]
    assert interview_input.approved_fields[0].fieldId == "field-1"


def test_build_interview_turn_input_keeps_empty_context_out_of_runtime_prompt() -> None:
    interview_input = build_interview_turn_input(
        record={
            "id": "record-1",
            "knowledgeId": "knowledge-1",
            "title": "汎用ヒアリング",
            "targetEquipment": "   ",
        },
        knowledge={
            "id": "knowledge-1",
            "name": "汎用ナレッジ",
            "description": "自由入力のヒアリング",
            "targetBusiness": "   ",
            "targetEquipment": "",
            "systemPrompt": "追加の前提情報があれば短く触れてください。",
        },
        messages=[
            {"role": "assistant", "content": "今回のテーマを教えてください。"},
            {"role": "user", "content": "開始します。"},
        ],
        knowledge_fields=[],
    )

    def fake_runner(*args: Any, **kwargs: Any) -> InterviewTurnOutput:
        prompt = args[0]
        assert "- 対象テーマ:" not in prompt
        assert "- 関連情報:" not in prompt
        assert "target_business" not in prompt
        assert "target_equipment" not in prompt
        assert "追加の前提情報があれば短く触れてください。" in prompt
        return InterviewTurnOutput(reply="了解しました。", answer_status="answered", used_tools=[])

    result = run_adapted_interview_turn(
        {
            "id": "record-1",
            "knowledgeId": "knowledge-1",
            "title": "汎用ヒアリング",
            "targetEquipment": "   ",
        },
        {
            "id": "knowledge-1",
            "name": "汎用ナレッジ",
            "description": "自由入力のヒアリング",
            "targetBusiness": "   ",
            "targetEquipment": "",
            "systemPrompt": "追加の前提情報があれば短く触れてください。",
        },
        [
            {"role": "assistant", "content": "今回のテーマを教えてください。"},
            {"role": "user", "content": "開始します。"},
        ],
        [],
        agent_runner=fake_runner,
    )

    assert result.reply_text == "了解しました。"


def test_run_adapted_interview_turn_preserves_custom_prompt_and_never_saves() -> None:
    record = {
        "id": "record-1",
        "knowledgeId": "knowledge-1",
        "title": "圧入機A 朝一の荷重ばらつき",
    }
    knowledge = {
        "id": "knowledge-1",
        "name": "保全ノウハウ",
        "systemPrompt": "停止判断を優先して確認してください。",
    }
    messages = [
        {"role": "assistant", "content": "状況を教えてください。"},
        {"role": "user", "content": "朝一だけ圧入荷重が不安定です。"},
    ]
    knowledge_fields = [
        {
            "id": "field-1",
            "name": "現象",
            "description": "発生している症状",
            "inputType": "long_text",
            "required": True,
            "askByAi": True,
            "displayOrder": 1,
        }
    ]

    def fake_runner(*args: Any, **kwargs: Any) -> InterviewTurnOutput:
        prompt = args[0]
        assert "runtime_custom_prompt:" in prompt
        assert "停止判断を優先して確認してください。" in prompt
        assert "conversation_history:" in prompt
        assert "approved_fields:" in prompt
        return InterviewTurnOutput(
            reply="停止判断に関わる条件から順に確認します。\n朝一だけ不安定になる条件を教えてください。",
            answer_status="answered",
            next_questions=["朝一のどのタイミングで発生しますか。"],
            draft_updates={"symptom": "朝一の荷重ばらつき"},
            used_tools=["search_existing_fields"],
        )

    result = run_adapted_interview_turn(
        record,
        knowledge,
        messages,
        knowledge_fields,
        agent_runner=fake_runner,
    )

    assert result.reply_text == "停止判断に関わる条件から順に確認します。\n朝一だけ不安定になる条件を教えてください。"
    assert result.reply_chunks == [
        "停止判断に関わる条件から順に確認します。",
        "朝一だけ不安定になる条件を教えてください。",
    ]
    assert result.answer_status == "answered"
    assert result.next_questions == ["朝一のどのタイミングで発生しますか。"]
    assert result.draft_updates == {"symptom": "朝一の荷重ばらつき"}
    assert result.used_tools == ["search_existing_fields"]
    assert store.tables["proposals"] == {}
    assert store.tables["records"] == {}
    assert store.tables["messages"] == {}


def test_adapt_interview_turn_output_keeps_unsaved_metadata() -> None:
    result = adapt_interview_turn_output(
        InterviewTurnOutput(
            reply="復旧手順を整理します。次に確認する観点を続けます。",
            answer_status="answered",
            next_questions=["直前に交換した部品はありますか。"],
            draft_updates={"action": "接点確認"},
            used_tools=["search_past_knowledge"],
        )
    )

    assert result.reply_chunks == [
        "復旧手順を整理します。",
        "次に確認する観点を続けます。",
    ]
    assert result.answer_status == "answered"
    assert result.next_questions == ["直前に交換した部品はありますか。"]
    assert result.draft_updates == {"action": "接点確認"}
    assert result.used_tools == ["search_past_knowledge"]


def test_adapt_interview_turn_output_keeps_not_answered_evaluation_metadata() -> None:
    result = adapt_interview_turn_output(
        InterviewTurnOutput(
            reply="現在の質問にまだ答えきれていません。",
            answer_status="not_answered",
            reask_question="発生直前の状態を、もう少し具体的に教えてください。",
            answer_evaluation_reason="質問への直接回答が不足しています。",
            next_questions=[],
            draft_updates={},
            used_tools=[],
        )
    )

    assert result.answer_status == "not_answered"
    assert result.reask_question == "発生直前の状態を、もう少し具体的に教えてください。"
    assert result.answer_evaluation_reason == "質問への直接回答が不足しています。"
    assert result.next_questions == []
    assert result.draft_updates == {}
