from __future__ import annotations

from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi import HTTPException

from ai_interviewer_api.auth.deps import DEV_TOKENS
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.agents.question_design.adapter import (
    adapt_question_design_output,
    build_question_design_input,
)
from ai_interviewer_api.agents.question_design.schemas import QuestionDesignOutput, QuestionFieldSuggestion
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
    assert result.fields[0].aiQuestionExamples == ["正常と異常をどのように見分けますか。"]
    assert result.used_tools == ["search_existing_fields"]


def test_suggest_fields_with_bedrock_keeps_existing_endpoint_contract(monkeypatch) -> None:
    before_proposals = dict(store.tables["proposals"])
    before_fields = dict(store.tables["knowledge_fields"])

    monkeypatch.setattr(
        field_suggestions,
        "run_question_design",
        lambda *args, **kwargs: QuestionDesignOutput(
            reply="質問項目候補を提案します。",
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
        ),
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
    assert result["modelId"] == field_suggestions.settings.bedrock_model_id
    assert [field["name"] for field in result["fields"]] == ["判断基準"]
    assert store.tables["proposals"] == before_proposals
    assert store.tables["knowledge_fields"] == before_fields


def test_suggest_fields_with_bedrock_returns_safe_empty_response_when_strands_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        field_suggestions,
        "run_question_design",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("strands failure")),
    )

    result = field_suggestions.suggest_fields_with_bedrock(
        FieldSuggestionRequest(content="質問項目を考えて"),
        DEV_TOKENS["dev-manager"],
    )

    assert result["fields"] == []
    assert result["bedrockInvoked"] is True
    assert "一時的に質問項目を生成できませんでした" in result["reply"]


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
            FieldSuggestionRequest(content="こんにちは"),
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
            FieldSuggestionRequest(content="こんにちは"),
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
