from __future__ import annotations

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi import HTTPException

from ai_interviewer_api.auth.deps import DEV_TOKENS
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.agents.question_design.adapter import (
    adapt_question_design_output,
    build_question_design_input,
)
from ai_interviewer_api.agents.question_design.schemas import QuestionDesignOutput, QuestionFieldSuggestion
from ai_interviewer_api.agents.question_design.service import (
    DEFAULT_CLARIFICATION,
    QuestionDesignInternalError,
)
from ai_interviewer_api.schemas.requests import FieldSuggestionRequest, KnowledgeFieldCreate
from ai_interviewer_api.services import field_suggestions


def test_build_question_design_input_maps_existing_contract() -> None:
    interview_input = build_question_design_input(
        FieldSuggestionRequest(
            content="質問項目を考えて",
            context={
                "name": "汎用ナレッジ",
                "description": "幅広いテーマを扱う",
                "category": "業務整理",
                "targetBusiness": "",
                "targetEquipment": " ",
                "systemPrompt": "口調は簡潔にしてください。",
            },
            existingFields=[
                KnowledgeFieldCreate(
                    name="現象",
                    description="発生していること",
                    inputType="long_text",
                    required=True,
                    aiQuestionExamples=["何が起きていますか。"],
                )
            ],
            recentMessages=[
                {"role": "assistant", "content": "テーマを教えてください。"},
                {"role": "user", "content": "質問項目を考えて"},
            ],
            maxFields=6,
        )
    )

    assert interview_input.knowledge_name == "汎用ナレッジ"
    assert interview_input.knowledge_description == "幅広いテーマを扱う"
    assert interview_input.category == "業務整理"
    assert interview_input.target_business is None
    assert interview_input.target_equipment is None
    assert interview_input.custom_prompt == "口調は簡潔にしてください。"
    assert interview_input.user_instruction == "質問項目を考えて"
    assert interview_input.desired_count == 6
    assert [field.name for field in interview_input.existing_fields] == ["現象"]
    assert [message.role for message in interview_input.recent_messages] == ["assistant", "user"]


def test_adapt_question_design_output_maps_to_existing_response_shape() -> None:
    result = adapt_question_design_output(
        QuestionDesignOutput(
            reply="質問項目候補を提案します。",
            design_status="ready",
            suggestions=[
                QuestionFieldSuggestion(
                    label="判断基準",
                    question="正常と異常をどのように見分けますか。",
                    description="見分け方を確認する",
                    input_type="long_text",
                    required=True,
                    ask_by_ai=True,
                )
            ],
            used_tools=["search_existing_fields"],
        )
    )

    assert result.reply == "質問項目候補を提案します。"
    assert result.fields[0].name == "判断基準"
    assert result.fields[0].description is None
    assert result.fields[0].aiQuestionExamples == ["正常と異常をどのように見分けますか。"]
    assert result.used_tools == ["search_existing_fields"]


def test_suggest_fields_with_bedrock_keeps_existing_endpoint_contract(monkeypatch) -> None:
    before_proposals = dict(store.tables["proposals"])
    before_fields = dict(store.tables["knowledge_fields"])
    captured_temperature = None

    def fake_run_question_design(*args, **kwargs):
        nonlocal captured_temperature
        captured_temperature = kwargs.get("temperature")
        return QuestionDesignOutput(
            reply="質問項目候補を提案します。",
            design_status="ready",
            suggestions=[
                QuestionFieldSuggestion(
                    label="判断基準",
                    question="正常と異常をどのように見分けますか。",
                    description="見分け方を確認する",
                ),
                QuestionFieldSuggestion(
                    label="現象",
                    question="何が起きていますか。",
                    description="既存項目と重複",
                ),
            ],
            used_tools=["search_existing_fields"],
        )

    monkeypatch.setattr(
        field_suggestions,
        "run_question_design",
        fake_run_question_design,
    )

    result = field_suggestions.suggest_fields_with_bedrock(
        FieldSuggestionRequest(
            content="質問項目を考えて",
            existingFields=[
                KnowledgeFieldCreate(
                    name="現象",
                    description="発生していること",
                    inputType="long_text",
                    required=True,
                )
            ],
        ),
        DEV_TOKENS["dev-manager"],
    )

    assert result["bedrockInvoked"] is True
    assert result["modelId"] == field_suggestions.settings.question_design_model_id
    assert captured_temperature == 0.0
    assert [field["name"] for field in result["fields"]] == ["判断基準"]
    assert store.tables["proposals"] == before_proposals
    assert store.tables["knowledge_fields"] == before_fields


def test_suggest_fields_with_bedrock_uses_selected_gpt_model(monkeypatch) -> None:
    captured_model_id = None

    def fake_run_question_design(*args, **kwargs):
        nonlocal captured_model_id
        captured_model_id = kwargs.get("model_id")
        return QuestionDesignOutput(
            reply="質問項目候補を提案します。",
            design_status="ready",
            suggestions=[
                QuestionFieldSuggestion(
                    label="判断基準",
                    question="判断基準を教えてください。",
                )
            ],
        )

    monkeypatch.setattr(field_suggestions, "run_question_design", fake_run_question_design)

    result = field_suggestions.suggest_fields_with_bedrock(
        FieldSuggestionRequest(
            content="保全業務の質問項目を作って",
            context={"defaultModelId": "global.openai.gpt-5.6-luna"},
        ),
        DEV_TOKENS["dev-manager"],
    )

    assert captured_model_id == "global.openai.gpt-5.6-luna"
    assert result["modelId"] == "global.openai.gpt-5.6-luna"


def test_suggest_fields_with_bedrock_migrates_legacy_model_to_gpt_default(monkeypatch) -> None:
    captured_model_id = None

    def fake_run_question_design(*args, **kwargs):
        nonlocal captured_model_id
        captured_model_id = kwargs.get("model_id")
        return QuestionDesignOutput(
            reply="質問項目候補を提案します。",
            design_status="ready",
            suggestions=[QuestionFieldSuggestion(label="判断基準", question="判断基準を教えてください。")],
        )

    monkeypatch.setattr(field_suggestions, "run_question_design", fake_run_question_design)

    result = field_suggestions.suggest_fields_with_bedrock(
        FieldSuggestionRequest(
            content="保全業務の質問項目を作って",
            context={"defaultModelId": "apac.amazon.nova-pro-v1:0"},
        ),
        DEV_TOKENS["dev-manager"],
    )

    assert captured_model_id == field_suggestions.settings.question_design_model_id
    assert result["modelId"] == field_suggestions.settings.question_design_model_id


def test_suggest_fields_with_bedrock_returns_empty_fields_when_validation_failed(monkeypatch) -> None:
    monkeypatch.setattr(
        field_suggestions,
        "run_question_design",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            QuestionDesignInternalError("question_design_validation_failed")
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        field_suggestions.suggest_fields_with_bedrock(
            FieldSuggestionRequest(content="質問考えて"),
            DEV_TOKENS["dev-manager"],
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "question_design_validation_failed"


def test_suggest_fields_with_bedrock_returns_safe_empty_response_when_strands_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        field_suggestions,
        "run_question_design",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("strands failure")),
    )

    with pytest.raises(HTTPException) as exc_info:
        field_suggestions.suggest_fields_with_bedrock(
            FieldSuggestionRequest(content="質問項目を考えて"),
            DEV_TOKENS["dev-manager"],
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "question_design_internal_error"


def test_suggest_fields_with_bedrock_returns_reply_only_when_materials_are_insufficient(monkeypatch) -> None:
    runner_called = False

    def fake_runner(*args, **kwargs):
        nonlocal runner_called
        runner_called = True
        return QuestionDesignOutput(
            reply="まずテーマを確認します。",
            design_status="needs_info",
            clarification_question="質問項目を作るために、まず今回ヒアリングしたいテーマや目的を教えてください。",
            suggestions=[],
        )

    monkeypatch.setattr(field_suggestions, "run_question_design", fake_runner)

    result = field_suggestions.suggest_fields_with_bedrock(
        FieldSuggestionRequest(content="こんにちは"),
        DEV_TOKENS["dev-manager"],
    )

    assert result["fields"] == []
    assert result["bedrockInvoked"] is True
    assert result["reply"] == DEFAULT_CLARIFICATION
    assert "業務の概要" not in result["reply"]
    assert runner_called is True


def test_suggest_fields_with_bedrock_maps_endpoint_connection_error(monkeypatch) -> None:
    monkeypatch.setattr(
        field_suggestions,
        "run_question_design",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            EndpointConnectionError(endpoint_url="https://bedrock-runtime.ap-northeast-1.amazonaws.com")
        ),
    )

    try:
        field_suggestions.suggest_fields_with_bedrock(
            FieldSuggestionRequest(content="月次請求処理の質問項目を作って"),
            DEV_TOKENS["dev-manager"],
        )
    except HTTPException as exc:
        assert exc.status_code == 504
        assert exc.detail == "bedrock_unreachable"
    else:
        raise AssertionError("HTTPException was expected")


def test_suggest_fields_with_bedrock_maps_client_error_without_legacy_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        field_suggestions,
        "run_question_design",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ClientError(
                error_response={"Error": {"Code": "ValidationException", "Message": "bad request"}},
                operation_name="Converse",
            )
        ),
    )

    try:
        field_suggestions.suggest_fields_with_bedrock(
            FieldSuggestionRequest(content="月次請求処理の質問項目を作って"),
            DEV_TOKENS["dev-manager"],
        )
    except HTTPException as exc:
        assert exc.status_code == 502
        assert exc.detail == "bedrock_ValidationException"
    else:
        raise AssertionError("HTTPException was expected")


def test_legacy_prompt_and_direct_bedrock_symbols_are_not_used() -> None:
    assert not hasattr(field_suggestions, "_invoke_bedrock_model")
    assert not hasattr(field_suggestions, "_repair_json_with_bedrock")
