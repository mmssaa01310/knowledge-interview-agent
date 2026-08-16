from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_interviewer_api.agents.interview.agent import load_interview_agent_prompt
from ai_interviewer_api.agents.interview.schemas import (
    InterviewField,
    InterviewFieldEvaluation,
    InterviewMessage,
    InterviewQuestion,
    InterviewState,
    InterviewTurnInput,
    InterviewTurnOutput,
)
from ai_interviewer_api.agents.interview.service import run_interview_turn


@dataclass
class FakeAgentResult:
    structured_output: Any = None
    text: str = ""

    def __str__(self) -> str:
        return self.text


def _build_input() -> InterviewTurnInput:
    return InterviewTurnInput(
        knowledge_id="knowledge-1",
        knowledge_name="保全ノウハウ",
        user_message="接点を磨いたら復旧しました。",
        conversation_history=[
            InterviewMessage(
                id="msg-1",
                role="assistant",
                content="最初にどこを確認しましたか。",
                questionId="q-001",
                questionType="configured_field",
                fieldId="field-1",
            ),
            InterviewMessage(
                id="msg-2",
                role="user",
                content="接点を確認しました。",
                answerToQuestionId="q-001",
                answerToFieldId="field-1",
            ),
        ],
        approved_fields=[
            InterviewField(
                fieldId="field-1",
                name="対処方法",
                description="復旧時の対処",
                aiQuestionExamples=["最初にどこを確認しましたか。"],
            )
        ],
        current_field=InterviewField(
            fieldId="field-1",
            name="対処方法",
            description="復旧時の対処",
            aiQuestionExamples=["最初にどこを確認しましたか。"],
        ),
        current_question=InterviewQuestion(
            questionId="q-001",
            questionType="configured_field",
            fieldId="field-1",
            text="最初にどこを確認しましたか。",
        ),
        interview_state=InterviewState(
            status="in_progress",
            currentFieldId="field-1",
            currentQuestionId="q-001",
            pendingFieldIds=["field-1"],
            completedFieldIds=[],
            askedQuestions=[
                InterviewQuestion(
                    questionId="q-001",
                    questionType="configured_field",
                    fieldId="field-1",
                    text="最初にどこを確認しましたか。",
                )
            ],
            followUpCounts={"field-1": 0},
            fieldStates={},
            lastProcessedUserMessageId=None,
        ),
    )


def test_run_interview_turn_returns_structured_output() -> None:
    expected = InterviewTurnOutput(
        reply="確認できました。",
        field_evaluation=InterviewFieldEvaluation(
            fieldId="field-1",
            isComplete=False,
            answerSummary="接点を確認して復旧した。",
            recordAnswer=None,
            missingInformation=["なぜ接点を疑ったか"],
            nextAction="follow_up",
        ),
        follow_up_question="なぜ接点不良を疑ったのですか。",
        used_tools=[],
    )

    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        assert "current_question:" in args[0]
        assert "follow_up_count_for_current_field: 0" in args[0]
        kwargs["invocation_state"]["used_tools"].append("search_existing_fields")
        return FakeAgentResult(structured_output=expected)

    result = run_interview_turn(_build_input(), agent_runner=fake_runner)

    assert result.reply == "確認できました。"
    assert result.field_evaluation.fieldId == "field-1"
    assert result.field_evaluation.isComplete is False
    assert result.field_evaluation.recordAnswer == ""
    assert result.follow_up_question == "なぜ接点不良を疑ったのですか。"
    assert result.used_tools == ["search_existing_fields"]


def test_run_interview_turn_parses_json_string_fallback() -> None:
    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        kwargs["invocation_state"]["used_tools"].append("search_past_knowledge")
        return FakeAgentResult(
            text=(
                '{"reply":"整理しました。","field_evaluation":{"fieldId":"field-1","isComplete":true,'
                '"answerSummary":"接点確認で復旧","missingInformation":[],"nextAction":"next_field"},'
                '"follow_up_question":null,"used_tools":[]}'
            )
        )

    result = run_interview_turn(_build_input(), agent_runner=fake_runner)

    assert result.reply == "整理しました。"
    assert result.field_evaluation.isComplete is True
    assert result.field_evaluation.nextAction == "next_field"
    assert result.used_tools == ["search_past_knowledge"]


def test_run_interview_turn_handles_invalid_output_safely() -> None:
    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        return FakeAgentResult(text="not-json-response")

    result = run_interview_turn(_build_input(), agent_runner=fake_runner)

    assert result.reply
    assert result.field_evaluation.fieldId == "field-1"
    assert result.field_evaluation.nextAction == "follow_up"
    assert result.follow_up_question
    assert result.used_tools == []


def test_run_interview_turn_filters_non_read_only_tool_names_from_used_tools() -> None:
    def fake_runner(*args: Any, **kwargs: Any) -> FakeAgentResult:
        kwargs["invocation_state"]["used_tools"].extend(["search_past_knowledge", "InterviewTurnOutput"])
        return FakeAgentResult(
            structured_output=InterviewTurnOutput(
                reply="状況を整理します。",
                field_evaluation=InterviewFieldEvaluation(
                    fieldId="field-1",
                    isComplete=True,
                    answerSummary="復旧手順を確認した。",
                    missingInformation=[],
                    nextAction="next_field",
                ),
                used_tools=["InterviewTurnOutput", "search_existing_fields"],
            )
        )

    result = run_interview_turn(_build_input(), agent_runner=fake_runner)

    assert result.used_tools == ["search_existing_fields", "search_past_knowledge"]


def test_retrieval_never_disables_tools_but_still_runs_evaluation() -> None:
    interview_input = _build_input()
    assert interview_input.current_field is not None
    interview_input.current_field.retrievalPolicy = "never"
    factory_calls: list[bool] = []

    def fake_factory(**kwargs: Any):  # type: ignore[no-untyped-def]
        factory_calls.append(kwargs["allow_retrieval"])

        def fake_runner(*args: Any, **runner_kwargs: Any) -> FakeAgentResult:
            return FakeAgentResult(
                structured_output=InterviewTurnOutput(
                    reply="評価しました。",
                    field_evaluation=InterviewFieldEvaluation(
                        fieldId="field-1",
                        isComplete=True,
                        answerSummary="接点を確認して復旧した",
                        missingInformation=[],
                        nextAction="next_field",
                        decision="CONFIRMABLE",
                    ),
                )
            )

        return fake_runner

    result = run_interview_turn(interview_input, agent_factory=fake_factory)

    assert factory_calls == [False]
    assert result.field_evaluation.decision == "CONFIRMABLE"
    assert result.field_evaluation.answerSummary == "接点を確認して復旧した"


def test_interview_prompt_contains_required_contract() -> None:
    prompt = load_interview_agent_prompt()

    assert "インタビューエージェント" in prompt
    assert "質問設計エージェントではありません" in prompt
    assert "暗黙知回答エージェントでもありません" in prompt
    assert "正式データベースへの本登録" in prompt
    assert "read-only tool" in prompt
    assert "field_evaluation.isComplete" in prompt
    assert "follow_up_question" in prompt
    assert "現在の設定項目に必要な情報が揃ったか" in prompt
    assert "1ターンで複数質問にならない" in prompt
