import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi import HTTPException

from ai_interviewer_api.auth.deps import DEV_TOKENS, UserContext
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.routes import (
    acknowledge_document,
    approve_all,
    approve_proposal,
    bulk_approve,
    create_document,
    create_field,
    create_knowledge,
    create_knowledge_db,
    create_record,
    create_record_message,
    create_summary_proposal,
    delete_knowledge_db,
    generate_fields,
    get_knowledge_db,
    list_knowledges,
    list_records,
    suggest_fields,
    update_knowledge_db,
    update_read_status,
)
from ai_interviewer_api.schemas.requests import (
    BulkApproveRequest,
    ChatMessageCreate,
    DocumentCreate,
    FieldSuggestionRequest,
    KnowledgeDbCreate,
    KnowledgeDbUpdate,
    KnowledgeCreate,
    KnowledgeFieldCreate,
    ReadStatusUpdate,
    RecordCreate,
)
from ai_interviewer_api.agents.question_design.agent import load_question_design_prompt
from ai_interviewer_api.agents.question_design.schemas import QuestionDesignOutput, QuestionFieldSuggestion
from ai_interviewer_api.agents.question_design.service import DEFAULT_CLARIFICATION
from ai_interviewer_api.services.field_suggestions import suggest_fields_with_bedrock


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.tables.clear()


def create_test_knowledge(knowledge_db_id: str, user: UserContext) -> dict:
    return create_knowledge(
        knowledge_db_id,
        KnowledgeCreate(
            name="保全ノウハウ",
            purpose="保全",
            targetEquipment="圧入機A",
        ),
        user,
    )


def test_knowledge_record_proposal_and_document_flow() -> None:
    user = DEV_TOKENS["dev-manager"]
    knowledge_db = create_knowledge_db(
        KnowledgeDbCreate(
            name="保全ノウハウ DB",
            description="圧入工程の暗黙知を集める",
        ),
        user,
    )
    knowledge = create_test_knowledge(knowledge_db["id"], user)

    field = create_field(
        knowledge["id"],
        KnowledgeFieldCreate(
            name="現象",
            inputType="long_text",
            required=True,
            askByAi=True,
            displayOrder=1,
        ),
        user,
    )
    assert field["knowledgeId"] == knowledge["id"]

    record = create_record(
        knowledge["id"],
        RecordCreate(title="圧入機A 朝一の荷重ばらつき"),
        user,
    )

    message = create_record_message(
        record["id"],
        ChatMessageCreate(content="圧入荷重が朝一に不安定になります"),
        user,
    )
    proposal_id = message["proposalId"]

    proposal = approve_proposal(proposal_id, user)
    assert proposal["status"] == "approved"

    document = create_document(
        knowledge["id"],
        DocumentCreate(fileName="圧入機A_保全手順.pdf", contentType="application/pdf"),
        user,
    )
    assert document["ingestionStatus"] == "queued"


def test_ai_summary_proposal_requires_approval_before_record_update() -> None:
    user = DEV_TOKENS["dev-manager"]
    knowledge_db = create_knowledge_db(KnowledgeDbCreate(name="summary db"), user)
    knowledge = create_test_knowledge(knowledge_db["id"], user)
    record = create_record(knowledge["id"], RecordCreate(title="圧入機A 朝一の荷重ばらつき"), user)
    create_record_message(record["id"], ChatMessageCreate(content="朝一だけ圧入荷重が不安定です"), user)

    proposal = create_summary_proposal(record["id"], user)
    stored_record = store.get("records", record["id"])

    assert proposal["proposalType"] == "record_summary"
    assert proposal["status"] == "needs_review"
    assert proposal["structuredData"]["summary"]
    assert stored_record["summary"] is None

    approve_proposal(proposal["id"], user)
    approved_record = store.get("records", record["id"])

    assert approved_record["summary"] == proposal["structuredData"]["summary"]


def test_viewer_cannot_create_knowledge_db() -> None:
    with pytest.raises(HTTPException) as exc_info:
        create_knowledge_db(KnowledgeDbCreate(name="viewer should fail"), DEV_TOKENS["dev-viewer"])

    assert exc_info.value.status_code == 403


def test_knowledge_db_update_delete_and_generated_fields() -> None:
    user = DEV_TOKENS["dev-manager"]
    knowledge_db = create_knowledge_db(KnowledgeDbCreate(name="before"), user)
    knowledge = create_test_knowledge(knowledge_db["id"], user)
    assert [item["id"] for item in list_knowledges(knowledge_db["id"], user)] == [knowledge["id"]]

    updated = update_knowledge_db(knowledge_db["id"], KnowledgeDbUpdate(name="after"), user)
    assert updated["name"] == "after"

    suggestions = generate_fields(knowledge["id"], user)
    assert [item["name"] for item in suggestions] == ["設備名", "現象 / 症状", "対処方法"]

    assert delete_knowledge_db(knowledge_db["id"], user) == {"deleted": True}
    with pytest.raises(HTTPException) as exc_info:
        get_knowledge_db(knowledge_db["id"], user)

    assert exc_info.value.status_code == 404


def test_field_suggestions_use_selected_model(monkeypatch: pytest.MonkeyPatch) -> None:
    user = DEV_TOKENS["dev-manager"]
    knowledge_db = create_knowledge_db(KnowledgeDbCreate(name="model assist db"), user)
    knowledge = create_test_knowledge(knowledge_db["id"], user)

    def fake_suggest(payload: FieldSuggestionRequest, current_user: UserContext) -> dict:
        assert current_user == user
        assert payload.context.defaultModelId == "anthropic.claude-3-5-sonnet-20240620-v1:0"
        assert [message.content for message in payload.recentMessages] == [
            "設備別に項目を分けたい",
            "設備名と現象の聞き取りを重視します。",
        ]
        return {
            "reply": "Claudeでヒアリング項目を1件作成しました。",
            "modelId": payload.context.defaultModelId,
            "fields": [
                {
                    "name": "判断基準",
                    "description": "熟練者が正常/異常を分ける基準",
                    "inputType": "long_text",
                    "required": True,
                    "askByAi": True,
                    "aiQuestionExamples": ["正常と異常をどのように見分けますか。"],
                    "options": [],
                    "displayOrder": 1,
                }
            ],
        }

    monkeypatch.setattr(
        "ai_interviewer_api.routers.knowledge_fields.suggest_fields_with_bedrock",
        fake_suggest,
    )

    result = suggest_fields(
        knowledge["id"],
        FieldSuggestionRequest(
            content="保全ノウハウ用の項目を作って",
            context={
                "name": "保全",
                "defaultModelId": "anthropic.claude-3-5-sonnet-20240620-v1:0",
            },
            recentMessages=[
                {"role": "user", "content": "設備別に項目を分けたい"},
                {"role": "assistant", "content": "設備名と現象の聞き取りを重視します。"},
            ],
        ),
        user,
    )

    assert result["modelId"] == "anthropic.claude-3-5-sonnet-20240620-v1:0"
    assert result["fields"][0]["name"] == "判断基準"


def test_field_suggestion_prompt_documents_adjustable_and_fixed_scope() -> None:
    prompt = load_question_design_prompt()

    assert "質問設計エージェント" in prompt
    assert "ヒアリング前に、確認すべき質問項目を設計します" in prompt
    assert "インタビューエージェントではありません" in prompt
    assert "正式DBへの保存" in prompt
    assert "read-only tool" in prompt
    assert "「対象設備」「設備」「保全」「製造」「現場」「熟練者」" in prompt


def test_field_suggestion_effective_prompt_uses_field_design_prompt_only() -> None:
    prompt = load_question_design_prompt()

    assert "質問設計エージェント" in prompt
    assert "インタビューエージェントではありません" in prompt
    assert "実務で再利用できる知識を聞き出すAIインタビュアー" not in prompt


def test_field_suggestions_accept_context_system_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_question_design(question_input, **kwargs):
        assert question_input.custom_prompt == "質問例は現場向けの口調にしてください。"
        return QuestionDesignOutput(
            reply="こんにちは。どの場面の質問項目を作りたいですか？",
            design_status="needs_info",
            clarification_question="こんにちは。どの場面の質問項目を作りたいですか？",
            suggestions=[],
        )

    monkeypatch.setattr(
        "ai_interviewer_api.services.field_suggestions.run_question_design",
        fake_run_question_design,
    )

    result = suggest_fields_with_bedrock(
        FieldSuggestionRequest(
            content="こんちは",
            context={
                "name": "保全ノウハウ",
                "systemPrompt": "質問例は現場向けの口調にしてください。",
            },
        ),
        DEV_TOKENS["dev-manager"],
    )

    assert result["bedrockInvoked"] is True
    assert result["fields"] == []
    assert result["reply"] == DEFAULT_CLARIFICATION


def test_field_suggestions_raise_504_when_bedrock_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_question_design(*args, **kwargs):
        raise EndpointConnectionError(endpoint_url="https://bedrock-runtime.ap-northeast-1.amazonaws.com")

    monkeypatch.setattr(
        "ai_interviewer_api.services.field_suggestions.run_question_design",
        fake_run_question_design,
    )

    with pytest.raises(HTTPException) as exc_info:
        suggest_fields_with_bedrock(
            FieldSuggestionRequest(
                content="月次請求処理の質問項目を作って",
                context={"name": "保全ノウハウ"},
            ),
            DEV_TOKENS["dev-manager"],
        )

    assert exc_info.value.status_code == 504
    assert exc_info.value.detail == "bedrock_unreachable"


def test_field_suggestions_raise_503_for_bedrock_throttling(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_question_design(*args, **kwargs):
        raise ClientError(
            error_response={"Error": {"Code": "ThrottlingException", "Message": "rate exceeded"}},
            operation_name="Converse",
        )

    monkeypatch.setattr(
        "ai_interviewer_api.services.field_suggestions.run_question_design",
        fake_run_question_design,
    )

    with pytest.raises(HTTPException) as exc_info:
        suggest_fields_with_bedrock(
            FieldSuggestionRequest(
                content="月次請求処理の質問項目を作って",
                context={"name": "保全ノウハウ"},
            ),
            DEV_TOKENS["dev-manager"],
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "bedrock_ThrottlingException"


def test_field_suggestions_raise_502_for_non_transient_bedrock_client_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run_question_design(*args, **kwargs):
        raise ClientError(
            error_response={"Error": {"Code": "ValidationException", "Message": "bad request"}},
            operation_name="Converse",
        )

    monkeypatch.setattr(
        "ai_interviewer_api.services.field_suggestions.run_question_design",
        fake_run_question_design,
    )

    with pytest.raises(HTTPException) as exc_info:
        suggest_fields_with_bedrock(
            FieldSuggestionRequest(
                content="月次請求処理の質問項目を作って",
                context={"name": "保全ノウハウ"},
            ),
            DEV_TOKENS["dev-manager"],
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "bedrock_ValidationException"


def test_field_suggestions_invoke_bedrock_for_greeting_only(monkeypatch: pytest.MonkeyPatch) -> None:
    runner_called = False

    def fake_run_question_design(question_input, **kwargs):
        nonlocal runner_called
        runner_called = True
        return QuestionDesignOutput(
            reply="まずテーマや目的を確認します。",
            design_status="needs_info",
            clarification_question="質問項目を作るために、まず今回ヒアリングしたいテーマや目的を教えてください。",
            suggestions=[],
        )

    monkeypatch.setattr(
        "ai_interviewer_api.services.field_suggestions.run_question_design",
        fake_run_question_design,
    )

    result = suggest_fields_with_bedrock(
        FieldSuggestionRequest(
            content="こんにちは！",
            context={
                "name": "保全ノウハウ",
                "targetBusiness": "保全",
                "targetEquipment": "圧入機A",
            },
        ),
        DEV_TOKENS["dev-manager"],
    )

    assert result["fields"] == []
    assert result["bedrockInvoked"] is True
    assert "テーマや目的" in result["reply"]
    assert runner_called is True


def test_field_suggestions_allow_reply_only_turn_without_field_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    user = DEV_TOKENS["dev-manager"]

    def fake_run_question_design(question_input, **kwargs):
        assert question_input.user_instruction == "まず何を決めればいい？"
        return QuestionDesignOutput(
            reply="まずは対象を1つに絞るか、カテゴリ別に分けるかを決めたいです。どちらを想定していますか？",
            design_status="needs_info",
            clarification_question="まずは対象を1つに絞るか、カテゴリ別に分けるかを決めたいです。どちらを想定していますか？",
            suggestions=[],
        )

    monkeypatch.setattr(
        "ai_interviewer_api.services.field_suggestions.run_question_design",
        fake_run_question_design,
    )

    result = suggest_fields_with_bedrock(
        FieldSuggestionRequest(content="まず何を決めればいい？"),
        user,
    )

    assert result["fields"] == []
    assert result["bedrockInvoked"] is True
    assert result["reply"] == DEFAULT_CLARIFICATION


def test_field_suggestions_keep_conversational_reply_when_suggestions_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    user = DEV_TOKENS["dev-manager"]

    def fake_run_question_design(question_input, **kwargs):
        assert [message.content for message in question_input.recent_messages] == [
            "ナレッジDBの目的を教えてください。ヒアリング項目の候補や質問例を提案します。",
            "こんちは",
            "まず、ナレッジDBの目的を確認しましょう。保全ナレッジを構造化するための基本的な情報を収集する項目を提案します。",
            "あなたは誰？",
        ]
        return QuestionDesignOutput(
            reply="私は質問項目候補を整理するAI設定アシスタントです。対象や知識化したいテーマを教えていただければ、次に深掘りする観点を整理します。",
            design_status="ready",
            suggestions=[
                QuestionFieldSuggestion(
                    label="現象",
                    question="どのような現象ですか？",
                    description="発生している症状",
                )
            ],
        )

    monkeypatch.setattr(
        "ai_interviewer_api.services.field_suggestions.run_question_design",
        fake_run_question_design,
    )

    result = suggest_fields_with_bedrock(
        FieldSuggestionRequest(
            content="あなたは誰？",
            existingFields=[
                KnowledgeFieldCreate(
                    name="現象",
                    description="発生している症状",
                    inputType="long_text",
                    required=True,
                    askByAi=True,
                    aiQuestionExamples=["どのような現象ですか？"],
                    displayOrder=1,
                )
            ],
            recentMessages=[
                {"role": "assistant", "content": "ナレッジDBの目的を教えてください。ヒアリング項目の候補や質問例を提案します。"},
                {"role": "user", "content": "こんちは"},
                {"role": "assistant", "content": "まず、ナレッジDBの目的を確認しましょう。保全ナレッジを構造化するための基本的な情報を収集する項目を提案します。"},
                {"role": "user", "content": "あなたは誰？"},
            ],
        ),
        user,
    )

    assert result["fields"] == []
    assert result["bedrockInvoked"] is True
    assert "AI設定アシスタント" in result["reply"]


def test_field_suggestions_return_reply_only_when_question_design_needs_more_materials(monkeypatch: pytest.MonkeyPatch) -> None:
    runner_called = False

    def fake_run_question_design(question_input, **kwargs):
        nonlocal runner_called
        runner_called = True
        return QuestionDesignOutput(
            reply="まずテーマや目的を確認します。",
            design_status="needs_info",
            clarification_question="質問項目を作るために、まず今回ヒアリングしたいテーマや目的を教えてください。",
            suggestions=[],
        )

    monkeypatch.setattr(
        "ai_interviewer_api.services.field_suggestions.run_question_design",
        fake_run_question_design,
    )

    result = suggest_fields_with_bedrock(
        FieldSuggestionRequest(content="こんにちは"),
        DEV_TOKENS["dev-manager"],
    )

    assert result.keys() == {"reply", "fields", "modelId", "bedrockInvoked"}
    assert result["fields"] == []
    assert result["reply"] == "質問項目を作るために、まず今回ヒアリングしたいテーマや目的を教えてください。"
    assert result["bedrockInvoked"] is True
    assert runner_called is True


def test_bulk_approve_marks_proposals_with_list_bulk_method() -> None:
    user = DEV_TOKENS["dev-manager"]
    knowledge_db = create_knowledge_db(KnowledgeDbCreate(name="bulk approve db"), user)
    knowledge = create_test_knowledge(knowledge_db["id"], user)
    record = create_record(knowledge["id"], RecordCreate(title="record"), user)
    message = create_record_message(record["id"], ChatMessageCreate(content="圧入荷重が不安定"), user)

    result = bulk_approve(BulkApproveRequest(recordIds=[record["id"]]), user)
    proposal = store.get("proposals", message["proposalId"])

    assert result["approvedCount"] == 1
    assert proposal["status"] == "approved"
    assert proposal["approvalMethod"] == "list_bulk"


def test_cross_tenant_access_is_rejected_for_child_resources() -> None:
    owner = DEV_TOKENS["dev-manager"]
    other_tenant_user = UserContext("user-other", "tenant-other", "knowledge_manager", "別テナント管理者")
    knowledge_db = create_knowledge_db(KnowledgeDbCreate(name="tenant scoped db"), owner)
    knowledge = create_test_knowledge(knowledge_db["id"], owner)
    record = create_record(knowledge["id"], RecordCreate(title="tenant scoped record"), owner)
    document = create_document(
        knowledge["id"],
        DocumentCreate(fileName="manual.pdf", contentType="application/pdf"),
        owner,
    )

    with pytest.raises(HTTPException) as list_records_exc:
        list_records(knowledge["id"], other_tenant_user)
    with pytest.raises(HTTPException) as message_exc:
        create_record_message(record["id"], ChatMessageCreate(content="別テナントから更新"), other_tenant_user)
    with pytest.raises(HTTPException) as read_exc:
        update_read_status(document["id"], ReadStatusUpdate(readStatus="read", readProgress=100), other_tenant_user)
    with pytest.raises(HTTPException) as ack_exc:
        acknowledge_document(document["id"], other_tenant_user)

    assert list_records_exc.value.status_code == 403
    assert message_exc.value.status_code == 403
    assert read_exc.value.status_code == 403
    assert ack_exc.value.status_code == 403


def test_record_bulk_approval_skips_low_confidence_and_already_approved_proposals() -> None:
    user = DEV_TOKENS["dev-manager"]
    knowledge_db = create_knowledge_db(KnowledgeDbCreate(name="approval rules db"), user)
    knowledge = create_test_knowledge(knowledge_db["id"], user)
    record = create_record(knowledge["id"], RecordCreate(title="record"), user)
    first_message = create_record_message(record["id"], ChatMessageCreate(content="圧入荷重が不安定"), user)
    second_message = create_record_message(record["id"], ChatMessageCreate(content="異音がします"), user)
    low_confidence = store.get("proposals", second_message["proposalId"])
    low_confidence["confidence"] = 0.2
    store.upsert("proposals", low_confidence)

    approved_once = approve_proposal(first_message["proposalId"], user)
    result = approve_all(record["id"], user)

    assert approved_once["status"] == "approved"
    assert result["approvedCount"] == 0
    assert result["skippedCount"] == 2
    assert {item["reason"] for item in result["skippedItems"]} == {
        "status_not_approvable",
        "confidence_too_low",
    }
