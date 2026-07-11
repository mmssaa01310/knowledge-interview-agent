from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import ModuleType
from typing import Any
from unittest.mock import Mock

from ai_interviewer_api.agents.common.tools import search_existing_fields, search_past_knowledge
from ai_interviewer_api.agents.question_design.agent import load_question_design_prompt
from ai_interviewer_api.agents.question_design.schemas import (
    ExistingQuestionField,
    QuestionDesignInput,
    QuestionDesignMessage,
    QuestionDesignOutput,
    QuestionFieldSuggestion,
)
from ai_interviewer_api.agents.question_design.service import run_question_design


@dataclass
class FakeAgentResult:
    structured_output: Any = None
    text: str = ""

    def __str__(self) -> str:
        return self.text


def test_run_question_design_returns_structured_output() -> None:
    expected = QuestionDesignOutput(
        reply="ヒアリング前に確認しておきたい質問項目を提案します。",
        suggestions=[
            QuestionFieldSuggestion(
                label="判断基準",
                question="正常と異常をどのように見分けますか。",
                description="判断の根拠を聞き取る",
            )
        ],
        used_tools=[],
    )

    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        assert "user_instruction:" in args[0]
        assert kwargs["structured_output_model"] is QuestionDesignOutput
        kwargs["invocation_state"]["used_tools"].append("search_existing_fields")
        return FakeAgentResult(structured_output=expected)

    result = run_question_design(
        QuestionDesignInput(
            knowledge_name="汎用ナレッジ",
            user_instruction="質問項目を作って",
            existing_fields=[ExistingQuestionField(name="現象", description="発生していること")],
            recent_messages=[QuestionDesignMessage(role="user", content="こんにちは")],
        ),
        agent_runner=fake_runner,
    )

    assert result.reply == expected.reply
    assert result.suggestions[0].label == "判断基準"
    assert result.used_tools == ["search_existing_fields"]


def test_run_question_design_parses_json_string_fallback() -> None:
    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        kwargs["invocation_state"]["used_tools"].append("search_past_knowledge")
        return FakeAgentResult(
            text='{"reply":"質問項目を整理しました。","suggestions":[{"label":"前提条件","question":"前提条件として何を確認すべきですか。","description":"前提の把握","input_type":"long_text"}],"used_tools":[]}'
        )

    result = run_question_design(
        QuestionDesignInput(user_instruction="整理して"),
        agent_runner=fake_runner,
    )

    assert result.reply == "質問項目を整理しました。"
    assert result.suggestions[0].label == "前提条件"
    assert result.used_tools == ["search_past_knowledge"]


def test_run_question_design_handles_invalid_output_safely() -> None:
    result = run_question_design(
        QuestionDesignInput(user_instruction="候補をください"),
        agent_runner=lambda *args, **kwargs: FakeAgentResult(text="not-json"),
    )

    assert result.reply
    assert result.suggestions == []
    assert result.used_tools == []


def test_run_question_design_does_not_assume_fixed_domain_terms_without_input() -> None:
    captured_prompt: str | None = None

    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        nonlocal captured_prompt
        captured_prompt = args[0]
        return FakeAgentResult(
            structured_output=QuestionDesignOutput(
                reply="汎用的な質問項目を提案します。",
                suggestions=[],
                used_tools=[],
            )
        )

    run_question_design(
        QuestionDesignInput(
            knowledge_name="自由入力ナレッジ",
            knowledge_description="幅広いテーマを扱う",
            user_instruction="質問項目を考えて",
        ),
        agent_runner=fake_runner,
    )

    assert captured_prompt is not None
    assert "対象設備:" not in captured_prompt
    assert "保全" not in captured_prompt
    assert "製造" not in captured_prompt


def test_question_design_prompt_contains_required_contract() -> None:
    prompt = load_question_design_prompt()

    assert "質問設計エージェント" in prompt
    assert "インタビューエージェントではありません" in prompt
    assert "正式DBへの保存" in prompt
    assert "read-only tool" in prompt
    assert "「対象設備」「設備」「保全」「製造」「現場」「熟練者」" in prompt


def test_question_design_imports_do_not_construct_bedrock_model_or_agent(monkeypatch) -> None:
    import strands.models

    runtime_module = importlib.import_module("ai_interviewer_api.agents.common.strands_runtime")
    question_agent_module = importlib.import_module("ai_interviewer_api.agents.question_design.agent")
    question_service_module = importlib.import_module("ai_interviewer_api.agents.question_design.service")

    original_bedrock_model = strands.models.BedrockModel

    class FailOnInitBedrockModel(original_bedrock_model):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("BedrockModel should not be instantiated during module import")

    monkeypatch.setattr(strands.models, "BedrockModel", FailOnInitBedrockModel)
    monkeypatch.setattr(question_agent_module, "build_question_design_agent", Mock(side_effect=AssertionError("should not build agent during import")))

    importlib.reload(runtime_module)
    importlib.reload(question_agent_module)
    importlib.reload(question_service_module)


def test_question_design_tools_are_read_only_stubs() -> None:
    tool_context = {"tool_use": {"toolUseId": "tool-1"}, "agent": None, "invocation_state": {}}

    assert (
        search_existing_fields("観点", tool_context=tool_context)
        == "No existing fields data source is connected yet."
    )
    assert (
        search_past_knowledge("手順", tool_context=tool_context)
        == "No past knowledge data source is connected yet."
    )


def test_question_design_tool_modules_do_not_depend_on_repositories_or_store() -> None:
    modules: list[ModuleType] = [
        importlib.import_module("ai_interviewer_api.agents.common.tools.existing_fields"),
        importlib.import_module("ai_interviewer_api.agents.common.tools.past_knowledge"),
    ]

    for module in modules:
        assert "store" not in module.__dict__
        assert "boto3" not in module.__dict__
        assert not any(name.startswith("ai_interviewer_api.repositories") for name in module.__dict__)
