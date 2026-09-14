from ai_interviewer_api.agents.question_design.schemas import RetrievedKnowledgeContext
from ai_interviewer_api.auth.deps import DEV_TOKENS
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.schemas.requests import FieldSuggestionRequest
from ai_interviewer_api.services.question_design_retrieval import (
    retrieve_question_design_context,
)


def test_retrieve_question_design_context_uses_scoped_approved_sources() -> None:
    user = DEV_TOKENS["dev-manager"]
    knowledge_id = "question-design-retrieval-knowledge"

    store.upsert(
        "knowledge_fields",
        {
            "id": "retrieval-field-1",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge_id,
            "name": "故障原因の切り分け",
            "description": "症状、発生条件、確認した測定値",
            "aiQuestionExamples": [],
        },
    )
    store.upsert(
        "records",
        {
            "id": "retrieval-approved-record",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge_id,
            "status": "approved",
            "title": "承認済みの故障記録",
        },
    )
    store.upsert(
        "messages",
        {
            "id": "retrieval-approved-message",
            "tenantId": user.tenant_id,
            "recordId": "retrieval-approved-record",
            "role": "user",
            "content": "故障原因の切り分けでは、症状と発生条件を確認する。",
        },
    )
    store.upsert(
        "records",
        {
            "id": "retrieval-unapproved-record",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge_id,
            "status": "submitted",
            "title": "未承認記録 故障原因の切り分け",
        },
    )
    store.upsert(
        "knowledge_chunks",
        {
            "id": "retrieval-indexed-chunk",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge_id,
            "status": "indexed",
            "content": "故障原因を切り分けるときは、正常時との差分と発生条件を確認する。",
        },
    )
    store.upsert(
        "knowledge_fields",
        {
            "id": "retrieval-other-knowledge-field",
            "tenantId": user.tenant_id,
            "knowledgeId": "other-knowledge",
            "name": "故障原因の切り分け",
            "description": "別ナレッジの情報",
        },
    )

    results = retrieve_question_design_context(
        FieldSuggestionRequest(content="故障原因の切り分けをする質問を考えて"),
        knowledge_id=knowledge_id,
        user=user,
    )

    source_ids = {item.source_id for item in results}
    source_types = {item.source_type for item in results}
    assert "retrieval-field-1" in source_ids
    assert "retrieval-approved-record" in source_ids
    assert "retrieval-indexed-chunk" in source_ids
    assert "retrieval-unapproved-record" not in source_ids
    assert "retrieval-other-knowledge-field" not in source_ids
    assert {"knowledge_field", "approved_record", "document_chunk"} <= source_types
    assert all(isinstance(item, RetrievedKnowledgeContext) for item in results)
