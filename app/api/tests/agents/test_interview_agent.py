from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import ModuleType
from typing import Any
from unittest.mock import Mock

from ai_interviewer_api.agents.common.strands_runtime import resolve_bedrock_region
from ai_interviewer_api.agents.common.tools import (
    search_equipment_master,
    search_existing_fields,
    search_past_knowledge,
)
from ai_interviewer_api.agents.interview.agent import load_interview_agent_prompt
from ai_interviewer_api.agents.interview.schemas import (
    InterviewField,
    InterviewMessage,
    InterviewTurnInput,
    InterviewTurnOutput,
)
from ai_interviewer_api.agents.interview.service import run_interview_turn
from ai_interviewer_api.services.prompts.loader import (
    get_field_fill_system_prompt,
)


@dataclass
class FakeAgentResult:
    structured_output: Any = None
    text: str = ""

    def __str__(self) -> str:
        return self.text


def test_run_interview_turn_returns_structured_output() -> None:
    expected = InterviewTurnOutput(
        reply="状況を確認しました。次の点をもう少し教えてください。",
        answer_status="answered",
        next_questions=["その作業の直前に行った操作は何ですか。"],
        draft_updates={"symptom": "センサー接触不良の疑い"},
        used_tools=[],
    )

    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        assert "latest_expert_message:" in args[0]
        assert "current_interviewer_question:" in args[0]
        assert kwargs["structured_output_model"] is InterviewTurnOutput
        kwargs["invocation_state"]["used_tools"].append("search_existing_fields")
        return FakeAgentResult(structured_output=expected)

    result = run_interview_turn(
        InterviewTurnInput(
            knowledge_id="knowledge-1",
            user_message="センサー接触の不良です。",
            conversation_history=[InterviewMessage(role="assistant", content="状況を教えてください。")],
            approved_fields=[InterviewField(name="症状", description="発生している症状")],
        ),
        agent_runner=fake_runner,
    )

    assert isinstance(result, InterviewTurnOutput)
    assert result.reply == expected.reply
    assert result.answer_status == "answered"
    assert result.next_questions == expected.next_questions
    assert result.draft_updates == expected.draft_updates
    assert result.used_tools == ["search_existing_fields"]


def test_run_interview_turn_parses_json_string_fallback() -> None:
    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        kwargs["invocation_state"]["used_tools"].append("search_past_knowledge")
        return FakeAgentResult(
            text='{"reply":"復旧手順を整理します。","next_questions":["最初に確認した箇所はどこですか。"],"draft_updates":{"action":"接点確認"},"used_tools":[]}'
        )

    result = run_interview_turn(
        InterviewTurnInput(user_message="復旧までの流れを話します。"),
        agent_runner=fake_runner,
    )

    assert result.reply == "復旧手順を整理します。"
    assert result.answer_status == "answered"
    assert result.next_questions == ["最初に確認した箇所はどこですか。"]
    assert result.draft_updates == {"action": "接点確認"}
    assert result.used_tools == ["search_past_knowledge"]


def test_run_interview_turn_limits_next_questions_to_one() -> None:
    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        return FakeAgentResult(
            structured_output=InterviewTurnOutput(
                reply="確認を続けます。いつ発生しましたか。",
                next_questions=[
                    "いつ発生しましたか。",
                    "直前の操作は何でしたか。",
                ],
                draft_updates={},
                used_tools=[],
            )
        )

    result = run_interview_turn(
        InterviewTurnInput(user_message="荷重がばらつきました。"),
        agent_runner=fake_runner,
    )

    assert result.next_questions == ["いつ発生しましたか。"]


def test_run_interview_turn_discards_structured_updates_when_answer_is_not_answered() -> None:
    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        return FakeAgentResult(
            structured_output=InterviewTurnOutput(
                reply="まだ現在の質問への回答が確認できません。",
                answer_status="not_answered",
                reask_question="発生直前の設備状態について、もう少し具体的に教えてください。",
                answer_evaluation_reason="現在の質問への具体回答が不足しています。",
                next_questions=["次の工程は何ですか。"],
                draft_updates={"symptom": "荷重ばらつき"},
                used_tools=[],
            )
        )

    result = run_interview_turn(
        InterviewTurnInput(user_message="よく分かりません。"),
        agent_runner=fake_runner,
    )

    assert result.answer_status == "not_answered"
    assert result.reask_question == "発生直前の設備状態について、もう少し具体的に教えてください。"
    assert result.answer_evaluation_reason == "現在の質問への具体回答が不足しています。"
    assert result.next_questions == []
    assert result.draft_updates == {}


def test_run_interview_turn_uses_reask_question_when_reply_is_empty() -> None:
    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        return FakeAgentResult(
            structured_output=InterviewTurnOutput(
                reply="",
                answer_status="not_answered",
                reask_question="そのときの設備状態を具体的に教えてください。",
                draft_updates={"symptom": "荷重ばらつき"},
                used_tools=[],
            )
        )

    result = run_interview_turn(
        InterviewTurnInput(user_message="ちょっと分かりません。"),
        agent_runner=fake_runner,
    )

    assert result.reply == "そのときの設備状態を具体的に教えてください。"
    assert result.answer_status == "not_answered"
    assert result.next_questions == []
    assert result.draft_updates == {}


def test_run_interview_turn_handles_invalid_output_safely() -> None:
    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        return FakeAgentResult(text="not-json-response")

    result = run_interview_turn(
        InterviewTurnInput(user_message="会話を続けてください。"),
        agent_runner=fake_runner,
    )

    assert result.reply
    assert result.next_questions == []
    assert result.draft_updates == {}
    assert result.used_tools == []


def test_run_interview_turn_filters_non_read_only_tool_names_from_used_tools() -> None:
    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        kwargs["invocation_state"]["used_tools"].extend(["search_past_knowledge", "InterviewTurnOutput"])
        return FakeAgentResult(
            structured_output=InterviewTurnOutput(
                reply="状況を整理します。",
                used_tools=["InterviewTurnOutput", "search_existing_fields"],
            )
        )

    result = run_interview_turn(
        InterviewTurnInput(user_message="進めてください。"),
        agent_runner=fake_runner,
    )

    assert result.used_tools == ["search_existing_fields", "search_past_knowledge"]


def test_run_interview_turn_keeps_used_tools_empty_when_no_read_only_tool_was_used() -> None:
    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        kwargs["invocation_state"]["used_tools"].append("InterviewTurnOutput")
        return FakeAgentResult(
            structured_output=InterviewTurnOutput(
                reply="状況を整理します。",
                used_tools=["InterviewTurnOutput"],
            )
        )

    result = run_interview_turn(
        InterviewTurnInput(user_message="進めてください。"),
        agent_runner=fake_runner,
    )

    assert result.used_tools == []


def test_interview_prompt_contains_required_contract() -> None:
    prompt = load_interview_agent_prompt()

    assert "インタビューエージェント" in prompt
    assert "質問設計エージェントではありません" in prompt
    assert "暗黙知回答エージェントでもありません" in prompt
    assert "正式データベースへの本登録" in prompt
    assert "read-only tool" in prompt
    assert "次に確認すべきこと" in prompt
    assert "質問する場合は、1ターンにつき質問を1つだけにする" in prompt
    assert "質問が不要な場面では、質問を含めなくてよい" in prompt
    assert "複数の質問を並べない" in prompt
    assert "しつこく聞かない" in prompt
    assert "終了確認を1つだけ行う" in prompt
    assert "answer_status" in prompt
    assert "reask_question" in prompt
    assert "answer_evaluation_reason" in prompt
    assert "回答になっていない場合は `answer_status` を `not_answered` にする" in prompt
    assert "なんで？" in prompt
    assert "元の論点を1つだけ聞き直してください" in prompt
    assert "draft_updates" in prompt
    assert "承認済み field を勝手に上書きしない" in prompt
    assert "対象設備" not in prompt
    assert "保全" not in prompt
    assert "製造" not in prompt


def test_run_interview_turn_prompt_does_not_mix_legacy_or_field_suggestion_prompts() -> None:
    field_fill_prompt = get_field_fill_system_prompt()

    captured_prompt: str | None = None

    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        nonlocal captured_prompt
        captured_prompt = args[0]
        return FakeAgentResult(
            structured_output=InterviewTurnOutput(
                reply="状況を整理しました。",
                answer_status="answered",
                used_tools=[],
            )
        )

    run_interview_turn(
        InterviewTurnInput(
            knowledge_id="knowledge-1",
            knowledge_name="汎用インタビュー",
            user_message="開始します。",
            conversation_history=[InterviewMessage(role="assistant", content="まず状況を教えてください。")],
        ),
        agent_runner=fake_runner,
    )

    assert captured_prompt is not None
    assert "あなたは製造業の暗黙知を構造化するためのヒアリング項目設計AIです。" not in captured_prompt
    assert field_fill_prompt not in captured_prompt
    assert "対象業務" not in captured_prompt
    assert "対象設備" not in captured_prompt


def test_read_only_tools_return_not_connected_messages() -> None:
    tool_context = {"tool_use": {"toolUseId": "tool-1"}, "agent": None, "invocation_state": {}}

    assert (
        search_equipment_master("圧入機A", tool_context=tool_context)
        == "No equipment master data source is connected yet."
    )
    assert (
        search_existing_fields("症状", tool_context=tool_context)
        == "No existing fields data source is connected yet."
    )
    assert (
        search_past_knowledge("荷重ばらつき", tool_context=tool_context)
        == "No past knowledge data source is connected yet."
    )


def test_tool_modules_do_not_depend_on_repositories_or_store() -> None:
    modules: list[ModuleType] = [
        importlib.import_module("ai_interviewer_api.agents.common.tools.equipment_master"),
        importlib.import_module("ai_interviewer_api.agents.common.tools.existing_fields"),
        importlib.import_module("ai_interviewer_api.agents.common.tools.past_knowledge"),
    ]

    for module in modules:
        assert "store" not in module.__dict__
        assert "boto3" not in module.__dict__
        assert not any(name.startswith("ai_interviewer_api.repositories") for name in module.__dict__)


def test_runtime_region_resolution_prefers_existing_settings() -> None:
    assert resolve_bedrock_region() == "ap-northeast-1"
    assert resolve_bedrock_region("us-west-2") == "us-west-2"


def test_runtime_region_resolution_falls_back_to_environment(monkeypatch) -> None:
    runtime_module = importlib.import_module("ai_interviewer_api.agents.common.strands_runtime")
    monkeypatch.setattr(runtime_module, "settings", Mock(bedrock_aws_region=""))
    monkeypatch.setenv("BEDROCK_AWS_REGION", "ap-southeast-2")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")

    assert runtime_module.resolve_bedrock_region() == "ap-southeast-2"


def test_imports_do_not_construct_bedrock_model_or_agent(monkeypatch) -> None:
    import strands.models

    runtime_module = importlib.import_module("ai_interviewer_api.agents.common.strands_runtime")
    interview_agent_module = importlib.import_module("ai_interviewer_api.agents.interview.agent")
    interview_service_module = importlib.import_module("ai_interviewer_api.agents.interview.service")

    original_bedrock_model = strands.models.BedrockModel

    class FailOnInitBedrockModel(original_bedrock_model):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("BedrockModel should not be instantiated during module import")

    monkeypatch.setattr(strands.models, "BedrockModel", FailOnInitBedrockModel)
    monkeypatch.setattr(interview_agent_module, "build_interview_agent", Mock(side_effect=AssertionError("should not build agent during import")))

    importlib.reload(runtime_module)
    importlib.reload(interview_agent_module)
    importlib.reload(interview_service_module)
