from typing import Any

from ai_interviewer_api.agents.question_design import provider as question_design_provider
from ai_interviewer_api.agents.question_design.schemas import (
    QuestionDesignOutput,
    QuestionFieldSuggestion,
)


def test_question_design_runner_uses_bedrock_structured_output_contract(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_request(self, **kwargs):
        captured.update(kwargs)
        return QuestionDesignOutput(
            reply="質問項目候補を提案します。",
            design_status="ready",
            suggestions=[
                QuestionFieldSuggestion(
                    label="切り分け手順",
                    question="故障原因をどの順序で切り分けますか。",
                )
            ],
        ).model_dump()

    monkeypatch.setattr(
        question_design_provider.BedrockResponsesStructuredProvider,
        "request_structured_output",
        fake_request,
    )

    runner = question_design_provider.BedrockQuestionDesignRunner(
        model_id="global.openai.gpt-5.6-luna",
        region_name="ap-northeast-1",
    )
    result = runner(
        "user_instruction: 故障原因の切り分けをする質問を考えて\nretrieved_knowledge: ...",
        structured_output_model=QuestionDesignOutput,
    )

    assert result.suggestions[0].label == "切り分け手順"
    assert runner._provider.model_id == "global.openai.gpt-5.6-luna"
    assert captured["schema_name"] == "question_design_output"
    assert captured["schema"]["type"] == "object"
    assert captured["reasoning_effort"] == "low"
    assert captured["max_output_tokens"] == 6000
    assert "retrieved_knowledge" in captured["user_payload"]["question_design_prompt"]
