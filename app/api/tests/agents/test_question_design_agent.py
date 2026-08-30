from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import ModuleType
from typing import Any
from unittest.mock import Mock

import pytest

from ai_interviewer_api.agents.common.tools import search_existing_fields, search_past_knowledge
from ai_interviewer_api.agents.question_design.agent import (
    load_question_design_prompt,
    load_question_design_validation_prompt,
)
from ai_interviewer_api.agents.question_design.schemas import (
    ExistingQuestionField,
    QuestionDesignInput,
    QuestionDesignMessage,
    QuestionDesignOutput,
    QuestionFieldSuggestion,
    QuestionDesignValidation,
)
from ai_interviewer_api.agents.question_design.service import (
    DEFAULT_CLARIFICATION,
    QUESTION_DESIGN_VALIDATION_FAILED,
    QuestionDesignInternalError,
    run_question_design,
)


@dataclass
class FakeAgentResult:
    structured_output: Any = None
    text: str = ""

    def __str__(self) -> str:
        return self.text


def test_run_question_design_returns_structured_output() -> None:
    expected = QuestionDesignOutput(
        reply="インタビュー前に確認しておきたい質問項目を提案します。",
        design_status="ready",
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
            user_instruction="月次請求処理について質問項目を作って",
            existing_fields=[ExistingQuestionField(name="現象", description="発生していること")],
            recent_messages=[QuestionDesignMessage(role="user", content="こんにちは")],
        ),
        agent_runner=fake_runner,
        validator_runner=lambda *args, **kwargs: QuestionDesignValidation(is_aligned=True),
    )

    assert result.reply == expected.reply
    assert result.suggestions[0].label == "判断基準"
    assert result.used_tools == ["search_existing_fields"]


def test_run_question_design_parses_json_string_fallback() -> None:
    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        kwargs["invocation_state"]["used_tools"].append("search_past_knowledge")
        return FakeAgentResult(
            text='{"reply":"質問項目を整理しました。","design_status":"ready","suggestions":[{"label":"前提条件","question":"前提条件として何を確認すべきですか。","description":"前提の把握","input_type":"long_text"}],"used_tools":[]}'
        )

    result = run_question_design(
        QuestionDesignInput(user_instruction="整理して"),
        agent_runner=fake_runner,
        validator_runner=lambda *args, **kwargs: QuestionDesignValidation(is_aligned=True),
    )

    assert result.reply == "質問項目を整理しました。"
    assert result.suggestions[0].label == "前提条件"
    assert result.used_tools == ["search_past_knowledge"]


def test_run_question_design_reports_invalid_output_after_one_retry() -> None:
    generation_calls = 0

    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        nonlocal generation_calls
        generation_calls += 1
        return FakeAgentResult(text="not-json")

    with pytest.raises(QuestionDesignInternalError) as exc_info:
        run_question_design(
            QuestionDesignInput(user_instruction="候補をください"),
            agent_runner=fake_runner,
            validator_runner=lambda *args, **kwargs: QuestionDesignValidation(is_aligned=True),
        )

    assert exc_info.value.code == "question_design_output_invalid"
    assert generation_calls == 2


def test_run_question_design_recovers_from_invalid_output_on_retry() -> None:
    generation_calls = 0

    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        nonlocal generation_calls
        generation_calls += 1
        if generation_calls == 1:
            return FakeAgentResult(text="not-json")
        return FakeAgentResult(
            structured_output=QuestionDesignOutput(
                reply="質問項目を提案します。",
                design_status="ready",
                suggestions=[
                    QuestionFieldSuggestion(
                        label="関心事",
                        question="最近関心を持っていることを教えてください。",
                    )
                ],
            )
        )

    result = run_question_design(
        QuestionDesignInput(user_instruction="人となりを聞く質問を考えて"),
        agent_runner=fake_runner,
        validator_runner=lambda *args, **kwargs: QuestionDesignValidation(is_aligned=True),
    )

    assert result.design_status == "ready"
    assert [suggestion.label for suggestion in result.suggestions] == ["関心事"]
    assert generation_calls == 2


def test_run_question_design_does_not_assume_fixed_domain_terms_without_input() -> None:
    captured_prompt: str | None = None

    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        nonlocal captured_prompt
        captured_prompt = args[0]
        return FakeAgentResult(
            structured_output=QuestionDesignOutput(
                reply="質問項目を作るために、まず今回のインタビューのテーマや目的を教えてください。",
                design_status="needs_info",
                clarification_question="質問項目を作るために、まず今回のインタビューのテーマや目的を教えてください。",
                suggestions=[],
                used_tools=[],
            )
        )

    run_question_design(
        QuestionDesignInput(
            knowledge_name="自由入力ナレッジ",
            knowledge_description="幅広いテーマを扱う",
            user_instruction="月次請求処理について質問項目を考えて",
        ),
        agent_runner=fake_runner,
    )

    assert captured_prompt is not None
    assert "対象設備:" not in captured_prompt
    assert "保全" not in captured_prompt
    assert "製造" not in captured_prompt


def test_run_question_design_clears_suggestions_when_design_status_is_needs_info() -> None:
    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        return FakeAgentResult(
            structured_output=QuestionDesignOutput(
                reply="汎用項目を提案します。",
                design_status="needs_info",
                clarification_question="質問項目を作るために、まず今回のインタビューのテーマや目的を教えてください。",
                reason="missing_materials",
                suggestions=[
                    QuestionFieldSuggestion(
                        label="業務の概要",
                        question="業務の概要を教えてください。",
                    )
                ],
                used_tools=[],
            )
        )

    result = run_question_design(
        QuestionDesignInput(user_instruction="こんにちは"),
        agent_runner=fake_runner,
    )

    assert result.design_status == "needs_info"
    assert result.reply == DEFAULT_CLARIFICATION
    assert result.suggestions == []


def test_run_question_design_uses_fixed_reply_for_needs_info() -> None:
    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        return FakeAgentResult(
            structured_output=QuestionDesignOutput(
                reply="設備Xとは何ですか？また、それはどのような環境で使用されていますか？",
                design_status="needs_info",
                clarification_question="設備Xとは何ですか？また、それはどのような環境で使用されていますか？",
                suggestions=[],
                used_tools=[],
            )
        )

    result = run_question_design(
        QuestionDesignInput(user_instruction="質問考えて"),
        agent_runner=fake_runner,
    )

    assert result.design_status == "needs_info"
    assert result.reply == DEFAULT_CLARIFICATION
    assert result.clarification_question == DEFAULT_CLARIFICATION
    assert result.suggestions == []


def test_run_question_design_discards_suggestions_when_needs_info_even_if_llm_returns_them() -> None:
    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        return FakeAgentResult(
            structured_output=QuestionDesignOutput(
                reply="設備Xとは何ですか？また、それはどのような環境で使用されていますか？",
                design_status="needs_info",
                clarification_question="設備Xとは何ですか？また、それはどのような環境で使用されていますか？",
                suggestions=[
                    QuestionFieldSuggestion(
                        label="業務の概要",
                        question="業務の概要を教えてください。",
                    )
                ],
                used_tools=[],
            )
        )

    result = run_question_design(
        QuestionDesignInput(user_instruction="こんにちは"),
        agent_runner=fake_runner,
    )

    assert result.design_status == "needs_info"
    assert result.reply == DEFAULT_CLARIFICATION
    assert result.clarification_question == DEFAULT_CLARIFICATION
    assert result.suggestions == []


def test_run_question_design_returns_needs_info_for_greeting_only() -> None:
    runner_called = False

    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        nonlocal runner_called
        runner_called = True
        return FakeAgentResult(
            structured_output=QuestionDesignOutput(
                reply="汎用項目ではなく、まずテーマを確認します。",
                design_status="needs_info",
                clarification_question="質問項目を作るために、まず今回のインタビューのテーマや目的を教えてください。",
                suggestions=[],
                used_tools=[],
            )
        )

    result = run_question_design(
        QuestionDesignInput(user_instruction="こんにちは"),
        agent_runner=fake_runner,
    )

    assert result.design_status == "needs_info"
    assert result.suggestions == []
    assert "テーマや目的" in result.reply
    assert runner_called is True


def test_run_question_design_returns_ready_when_materials_are_sufficient() -> None:
    runner_called = False

    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        nonlocal runner_called
        runner_called = True
        return FakeAgentResult(
            structured_output=QuestionDesignOutput(
                reply="月次請求処理の質問項目を提案します。",
                design_status="ready",
                suggestions=[
                    QuestionFieldSuggestion(
                        label="処理の開始条件",
                        question="月次請求処理を開始する前に何を確認しますか。",
                    )
                ],
                used_tools=[],
            )
        )

    result = run_question_design(
        QuestionDesignInput(user_instruction="新人に引き継ぐため、月次請求処理で確認すべき質問項目を作りたい"),
        agent_runner=fake_runner,
        validator_runner=lambda *args, **kwargs: QuestionDesignValidation(is_aligned=True),
    )

    assert result.design_status == "ready"
    assert len(result.suggestions) == 1
    assert result.suggestions[0].label == "処理の開始条件"
    assert runner_called is True


def test_run_question_design_allows_agent_to_handle_empty_input_when_called_directly() -> None:
    runner_called = False

    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        nonlocal runner_called
        runner_called = True
        return FakeAgentResult(
            structured_output=QuestionDesignOutput(
                reply="質問項目を作るために、まず今回のインタビューのテーマや目的を教えてください。",
                design_status="needs_info",
                clarification_question="質問項目を作るために、まず今回のインタビューのテーマや目的を教えてください。",
                suggestions=[],
                used_tools=[],
            )
        )

    result = run_question_design(
        QuestionDesignInput(user_instruction="   "),
        agent_runner=fake_runner,
    )

    assert result.design_status == "needs_info"
    assert result.suggestions == []
    assert runner_called is True


def test_run_question_design_allows_agent_to_decide_needs_info_for_short_vague_request() -> None:
    runner_called = False

    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        nonlocal runner_called
        runner_called = True
        return FakeAgentResult(
            structured_output=QuestionDesignOutput(
                reply="まだ対象が広いため、テーマを絞りたいです。",
                design_status="needs_info",
                clarification_question="どの業務について、何を明らかにしたいのかを教えてください。",
                suggestions=[],
                used_tools=[],
            )
        )

    result = run_question_design(
        QuestionDesignInput(user_instruction="質問作って"),
        agent_runner=fake_runner,
    )

    assert result.design_status == "needs_info"
    assert result.suggestions == []
    assert result.reply == DEFAULT_CLARIFICATION
    assert runner_called is True


def test_run_question_design_retries_once_when_validation_requests_retry() -> None:
    generation_calls: list[str | None] = []

    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        prompt = args[0]
        if "retry_instruction:\nnone" in prompt:
            generation_calls.append(None)
            return FakeAgentResult(
                structured_output=QuestionDesignOutput(
                    reply="質問項目候補を提案します。",
                    design_status="ready",
                    suggestions=[
                        QuestionFieldSuggestion(
                            label="業務の概要",
                            question="業務の概要を教えてください。",
                        )
                    ],
                )
            )
        generation_calls.append("retry")
        return FakeAgentResult(
            structured_output=QuestionDesignOutput(
                reply="月次請求処理の質問項目を提案します。",
                design_status="ready",
                suggestions=[
                    QuestionFieldSuggestion(
                        label="処理の開始条件",
                        question="月次請求処理を開始する前に何を確認しますか。",
                    )
                ],
            )
        )

    validation_calls = 0

    def fake_validator(*args: Any, **kwargs: Any) -> QuestionDesignValidation:
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 1:
            return QuestionDesignValidation(
                is_aligned=False,
                validation_reason="too_generic",
                issues=["汎用テンプレ項目に寄りすぎています。"],
                should_retry=True,
                retry_instruction="ユーザーの依頼意図に合わせて、汎用テンプレではなく具体的な質問項目に絞ってください。",
            )
        return QuestionDesignValidation(is_aligned=True)

    result = run_question_design(
        QuestionDesignInput(user_instruction="新人に引き継ぐため、月次請求処理で確認すべき質問項目を作りたい"),
        agent_runner=fake_runner,
        validator_runner=fake_validator,
    )

    assert result.design_status == "ready"
    assert [suggestion.label for suggestion in result.suggestions] == ["処理の開始条件"]
    assert validation_calls == 2
    assert generation_calls == [None, "retry"]


def test_run_question_design_reports_validation_failure_when_retry_still_not_aligned() -> None:
    generation_calls = 0

    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        nonlocal generation_calls
        generation_calls += 1
        return FakeAgentResult(
            structured_output=QuestionDesignOutput(
                reply="質問項目候補を提案します。",
                design_status="ready",
                suggestions=[
                    QuestionFieldSuggestion(
                        label="業務の概要",
                        question="業務の概要を教えてください。",
                    )
                ],
            )
        )

    def fake_validator(*args: Any, **kwargs: Any) -> QuestionDesignValidation:
        return QuestionDesignValidation(
            is_aligned=False,
            validation_reason="still_generic",
            issues=["依頼意図に対して具体性が不足しています。"],
            should_retry=True,
            retry_instruction="依頼意図に沿うように具体化してください。",
        )

    with pytest.raises(QuestionDesignInternalError) as exc_info:
        run_question_design(
            QuestionDesignInput(user_instruction="質問考えて"),
            agent_runner=fake_runner,
            validator_runner=fake_validator,
        )

    assert exc_info.value.code == QUESTION_DESIGN_VALIDATION_FAILED
    assert generation_calls == 2


def test_run_question_design_reports_invalid_validation_after_one_retry() -> None:
    validation_calls = 0

    def fake_validator(*args: Any, **kwargs: Any) -> FakeAgentResult:
        nonlocal validation_calls
        validation_calls += 1
        return FakeAgentResult(text="not-json")

    with pytest.raises(QuestionDesignInternalError) as exc_info:
        run_question_design(
            QuestionDesignInput(user_instruction="人となりを聞く質問を考えて"),
            agent_runner=lambda *args, **kwargs: FakeAgentResult(
                structured_output=QuestionDesignOutput(
                    reply="質問項目を提案します。",
                    design_status="ready",
                    suggestions=[
                        QuestionFieldSuggestion(
                            label="趣味",
                            question="休日に楽しんでいることを教えてください。",
                        )
                    ],
                )
            ),
            validator_runner=fake_validator,
        )

    assert exc_info.value.code == "question_design_validation_output_invalid"
    assert validation_calls == 2


def test_run_question_design_does_not_validate_when_needs_info() -> None:
    validator_factory_called = False

    def fake_validator_factory(*args: Any, **kwargs: Any) -> Any:
        nonlocal validator_factory_called
        validator_factory_called = True
        raise AssertionError("validator must be initialized lazily")

    result = run_question_design(
        QuestionDesignInput(user_instruction="こんにちは"),
        agent_runner=lambda *args, **kwargs: FakeAgentResult(
            structured_output=QuestionDesignOutput(
                reply="テーマを教えてください。",
                design_status="needs_info",
                clarification_question="テーマを教えてください。",
                suggestions=[],
            )
        ),
        validator_factory=fake_validator_factory,
    )

    assert result.design_status == "needs_info"
    assert result.reply == DEFAULT_CLARIFICATION
    assert validator_factory_called is False


def test_question_design_prompt_contains_required_contract() -> None:
    prompt = load_question_design_prompt()

    assert "質問設計エージェント" in prompt
    assert "インタビューエージェントではありません" in prompt
    assert "正式DBへの保存" in prompt
    assert "retrieved_knowledge" in prompt
    assert "Backendが事前検索した参考情報" in prompt
    assert "「対象設備」「設備」「保全」「製造」「現場」「熟練者」" in prompt
    assert "design_status" in prompt
    assert "needs_info" in prompt
    assert "汎用テンプレ項目" in prompt
    assert "3 件から 5 件" in prompt
    assert "`description` には、回答に含めてほしい詳細項目を簡潔に列挙" in prompt
    assert "`reason` は原則として省略" in prompt
    assert "追加情報が必要だと判断する" in prompt
    assert "ready` の場合だけ `suggestions` を返す" in prompt
    assert "質問対象または聞きたい観点を具体的に示していれば" in prompt
    assert "今回の `user_instruction` を優先" in prompt


def test_question_design_validation_prompt_contains_required_contract() -> None:
    prompt = load_question_design_validation_prompt()

    assert "質問設計結果の検証者" in prompt
    assert "ユーザーの目的に合っているか" in prompt
    assert "入力にない対象を勝手に決めつけていないか" in prompt
    assert "汎用テンプレ項目で穴埋めしていないか" in prompt
    assert "is_aligned" in prompt
    assert "should_retry" in prompt
    assert "retry_instruction" in prompt
    assert "質問対象または聞きたい観点を示している場合" in prompt


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
