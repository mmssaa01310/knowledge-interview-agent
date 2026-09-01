from __future__ import annotations

from collections.abc import Mapping

import pytest

from ai_interviewer_api.agents.interview_knowledge.schemas import (
    QuestionGenerationOutput,
    StructuredInterviewOutput,
)
from ai_interviewer_api.agents.interview_knowledge.service import (
    generate_structured_interview_result,
)
from ai_interviewer_api.auth.deps import DEV_TOKENS, UserContext
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.schemas.retrieval import RetrievedKnowledgeContext
from ai_interviewer_api.services.interview_document_retrieval import (
    retrieve_interview_document_context,
    validate_document_question_candidate,
)


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.tables.clear()


def _seed_interview_context(
    user: UserContext,
    *,
    profile: str = "fixed_form",
    field_count: int = 1,
    retrieval_policy: str = "required",
) -> tuple[dict, dict, list[dict]]:
    knowledge = {
        "id": "document-context-knowledge",
        "tenantId": user.tenant_id,
        "name": "設備保全インタビュー",
        "description": "設備の荷重ばらつきと発生条件を確認する",
        "purpose": "設備の発生条件を整理する",
        "targetEquipment": "med900",
        "interviewPlan": {"profile": profile, "modelId": "global.openai.gpt-5.6-terra"},
    }
    record = {
        "id": "document-context-record",
        "tenantId": user.tenant_id,
        "knowledgeId": knowledge["id"],
        "knowledgeName": knowledge["name"],
        "title": "med900 荷重ばらつき",
        "status": "in_progress",
        "ownerUserId": user.user_id,
    }
    fields = [
        {
            "id": f"document-context-field-{index}",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge["id"],
            "name": "発生条件" if index == 1 else f"確認項目{index}",
            "description": "荷重ばらつきが発生するタイミング",
            "inputType": "long_text",
            "required": True,
            "askByAi": True,
            "retrievalPolicy": retrieval_policy,
            "aiQuestionExamples": ["荷重のばらつきは、どんなタイミングで発生しますか？"],
            "displayOrder": index,
        }
        for index in range(1, field_count + 1)
    ]
    store.upsert("knowledges", knowledge)
    store.upsert("records", record)
    for field in fields:
        store.upsert("knowledge_fields", field)
    store.upsert(
        "documents",
        {
            "id": "document-context-document",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge["id"],
            "fileName": "med900保全手順.pdf",
            "contentType": "application/pdf",
            "ingestionStatus": "indexed",
            "progressPercent": 100,
        },
    )
    store.upsert(
        "document_chunks",
        {
            "id": "document-context-chunk",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge["id"],
            "documentId": "document-context-document",
            "status": "indexed",
            "title": "med900保全手順.pdf",
            "content": "med900の荷重ばらつきは朝一の暖機前と停止後に発生しやすい。",
        },
    )
    return record, knowledge, fields


class _CapturingStructuredProvider:
    def __init__(self) -> None:
        self.question_context: Mapping[str, object] | None = None

    def interpret(self, **_: object) -> StructuredInterviewOutput:
        return StructuredInterviewOutput()

    def generate_question(
        self,
        *,
        context: Mapping[str, object],
        **_: object,
    ) -> QuestionGenerationOutput:
        self.question_context = context
        return QuestionGenerationOutput(questionText="発生条件を詳しく教えてください。")


def test_document_retrieval_uses_only_indexed_documents_and_scoped_chunks() -> None:
    user = DEV_TOKENS["dev-manager"]
    record, knowledge, fields = _seed_interview_context(user)
    store.upsert(
        "documents",
        {
            "id": "queued-document",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge["id"],
            "fileName": "queued.pdf",
            "ingestionStatus": "processing",
        },
    )
    store.upsert(
        "document_chunks",
        {
            "id": "queued-chunk",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge["id"],
            "documentId": "queued-document",
            "status": "indexed",
            "content": "med900の朝一の情報ではない混入データ。",
        },
    )
    store.upsert(
        "document_chunks",
        {
            "id": "other-tenant-chunk",
            "tenantId": "tenant-other",
            "knowledgeId": knowledge["id"],
            "status": "indexed",
            "content": "med900の別テナント情報。",
        },
    )

    contexts = retrieve_interview_document_context(
        record=record,
        knowledge=knowledge,
        user=user,
        current_field=fields[0],
        retrieval_policy="required",
    )

    assert [item.source_id for item in contexts] == ["document-context-chunk"]
    assert contexts[0].content.startswith("med900の荷重ばらつき")


def test_structured_question_generation_receives_document_context() -> None:
    user = DEV_TOKENS["dev-manager"]
    record, knowledge, _ = _seed_interview_context(user, profile="business_process")
    provider = _CapturingStructuredProvider()

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    assert provider.question_context is not None
    retrieved = provider.question_context["retrieved_knowledge"]
    assert isinstance(retrieved, list)
    assert retrieved[0]["source_id"] == "document-context-chunk"
    assert "暖機前" in retrieved[0]["content"]
    assert result["question"]["retrievedSources"][0]["sourceId"] == "document-context-chunk"
    assert result["retrievalExecuted"] is True


class _DocumentCandidateProvider:
    def __init__(self, candidate: str, source_id: str) -> None:
        self.candidate = candidate
        self.source_id = source_id

    def interpret(self, **_: object) -> StructuredInterviewOutput:
        return StructuredInterviewOutput()

    def generate_question(
        self,
        *,
        target: Mapping[str, object],
        **_: object,
    ) -> QuestionGenerationOutput:
        return QuestionGenerationOutput(
            questionText=f"{target['label']}を教えてください。",
            documentCandidateValue=self.candidate,
            documentCandidateSourceIds=[self.source_id],
        )


def test_document_candidate_requires_explicit_confirmation_before_completion() -> None:
    user = DEV_TOKENS["dev-manager"]
    record, knowledge, fields = _seed_interview_context(user, retrieval_policy="auto")
    fields[0]["name"] = "設備名"
    fields[0]["description"] = "対象設備を特定する"
    store.upsert("knowledge_fields", fields[0])
    store.upsert(
        "document_chunks",
        {
            "id": "document-context-chunk",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge["id"],
            "documentId": "document-context-document",
            "status": "indexed",
            "title": "med900保全手順.pdf",
            "content": "対象設備は圧入機Aです。荷重ばらつきは朝一に発生しやすい。",
        },
    )
    provider = _DocumentCandidateProvider("圧入機A", "document-context-chunk")

    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    question = first["question"]
    field_state = first["interviewState"]["fieldStates"][fields[0]["id"]]
    assert question["text"] == "事前知識では設備名は「圧入機A」となっています。この内容で合っていますか？"
    assert field_state["answerState"] == "AWAITING_CONFIRMATION"
    assert field_state["recordAnswer"] is None

    store.upsert(
        "messages",
        {
            "id": "document-context-confirmation",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "content": "はい",
            "rawTranscript": "はい",
            "isActualUtterance": True,
            "turnType": "ANSWER",
            "answerToQuestionId": question["questionId"],
            "answerToFieldId": fields[0]["id"],
        },
    )

    confirmed = generate_structured_interview_result(record, knowledge, user, provider=provider)
    confirmed_state = confirmed["interviewState"]["fieldStates"][fields[0]["id"]]
    assert confirmed["status"] == "in_progress"
    assert confirmed_state["answerState"] == "CONFIRMED"
    assert confirmed_state["recordAnswer"] == "圧入機A"
    assert confirmed["question"]["targetType"] == "closing"

    store.upsert(
        "messages",
        {
            "id": "document-context-closing",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "content": "ありません。",
            "rawTranscript": "ありません。",
            "isActualUtterance": True,
            "turnType": "ANSWER",
            "answerToQuestionId": confirmed["question"]["questionId"],
        },
    )
    finished = generate_structured_interview_result(record, knowledge, user, provider=provider)
    assert finished["status"] == "completed"


def test_never_policy_does_not_read_document_context() -> None:
    user = DEV_TOKENS["dev-manager"]
    record, knowledge, fields = _seed_interview_context(user, retrieval_policy="never")

    contexts = retrieve_interview_document_context(
        record=record,
        knowledge=knowledge,
        user=user,
        current_field=fields[0],
        retrieval_policy="never",
    )

    assert contexts == []


def test_document_candidate_is_ignored_when_backend_source_does_not_support_value() -> None:
    contexts = [
        RetrievedKnowledgeContext(
            source_type="document_chunk",
            source_id="chunk-1",
            title="設備手順.md",
            content="対象設備は圧入機Aです。",
        )
    ]

    candidate = validate_document_question_candidate(
        value="圧入機B",
        source_ids=["chunk-1"],
        contexts=contexts,
    )

    assert candidate is None
