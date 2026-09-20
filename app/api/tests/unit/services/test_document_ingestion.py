from __future__ import annotations

import pytest
from fastapi import HTTPException

from ai_interviewer_api.auth.deps import DEV_TOKENS
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.documents import (
    create_prior_knowledge,
    delete_document,
    get_document_content,
)
from ai_interviewer_api.schemas.requests import PriorKnowledgeCreate
from ai_interviewer_api.services.document_ingestion import (
    DocumentIngestionError,
    chunk_document_text,
    normalize_prior_knowledge_content,
    ingest_text_document,
)


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.tables.clear()


def _document() -> dict:
    return {
        "id": "ingestion-document",
        "tenantId": "tenant-demo",
        "createdByUserId": "user-manager",
        "updatedByUserId": "user-manager",
        "knowledgeId": "ingestion-knowledge",
        "fileName": "保全メモ.md",
        "contentType": "text/markdown",
        "ingestionStatus": "processing",
        "progressPercent": 20,
        "chunkCount": 0,
        "deletedAt": None,
    }


def test_text_knowledge_is_chunked_and_indexed() -> None:
    document = _document()
    store.upsert("documents", document)
    content = "```markdown\n  - 入れ子の用語\n```"

    result = ingest_text_document(document, content)

    assert result.document["ingestionStatus"] == "indexed"
    assert result.document["progressPercent"] == 100
    assert result.document["chunkCount"] == 1
    assert result.chunks[0]["status"] == "indexed"
    assert result.chunks[0]["content"] == content
    assert result.document["content"] == content
    assert result.document["contentFormat"] == "markdown"


def test_prior_knowledge_normalization_preserves_structure_and_formats_json() -> None:
    markdown = "\n\n# 手順\n\n\n  - 子項目\n\n\n"
    assert normalize_prior_knowledge_content(markdown) == "# 手順\n\n\n  - 子項目"

    policy = '{\n  "Version":"2012-10-17",\n  "Statement":[{"Effect":"Allow","Action":["s3:GetObject"],"Resource":"*"}]\n}'
    assert normalize_prior_knowledge_content(policy) == (
        '{\n'
        '  "Version": "2012-10-17",\n'
        '  "Statement": [\n'
        '    {\n'
        '      "Effect": "Allow",\n'
        '      "Action": [\n'
        '        "s3:GetObject"\n'
        '      ],\n'
        '      "Resource": "*"\n'
        '    }\n'
        '  ]\n'
        '}'
    )


def test_text_knowledge_rejects_empty_content_and_chunks_long_content() -> None:
    document = _document()
    store.upsert("documents", document)
    with pytest.raises(DocumentIngestionError):
        ingest_text_document(document, " \n")

    chunks = chunk_document_text(
        _document(),
        "a" * 2500,
    )
    assert len(chunks) >= 2
    assert chunks[0]["sequence"] == 1
    assert chunks[1]["sequence"] == 2


def test_prior_knowledge_endpoint_indexes_text_and_supports_read_delete() -> None:
    store.upsert(
        "knowledges",
        {
            "id": "ingestion-knowledge",
            "tenantId": "tenant-demo",
            "name": "保全ナレッジ",
        },
    )

    content = "\n\n  # 暖機\n設備起動後に行う予熱運転。\n" + ("詳細手順を確認する。" * 120)
    result = create_prior_knowledge(
        "ingestion-knowledge",
        PriorKnowledgeCreate(
            title="専門用語",
            knowledgeType="glossary",
            content=content,
        ),
        DEV_TOKENS["dev-manager"],
    )

    assert result["sourceType"] == "prior_knowledge"
    assert result["knowledgeType"] == "glossary"
    assert result["contentFormat"] == "markdown"
    assert result["ingestionStatus"] == "indexed"
    assert "content" not in result
    opened = get_document_content(result["id"], DEV_TOKENS["dev-manager"])
    assert opened["content"] == content.strip("\n")
    assert delete_document(result["id"], DEV_TOKENS["dev-manager"]) == {"deleted": True}
    assert store.get("documents", result["id"]) is None
    assert store.list("document_chunks", "tenant-demo") == []


def test_prior_knowledge_endpoint_rejects_whitespace_only_content() -> None:
    store.upsert(
        "knowledges",
        {
            "id": "ingestion-knowledge",
            "tenantId": "tenant-demo",
            "name": "保全ナレッジ",
        },
    )

    with pytest.raises(HTTPException) as error:
        create_prior_knowledge(
            "ingestion-knowledge",
            PriorKnowledgeCreate(title="空本文", content=" \n\t"),
            DEV_TOKENS["dev-manager"],
        )

    assert error.value.status_code == 422
    assert error.value.detail == "prior_knowledge_content_required"
    assert store.list("documents", "tenant-demo") == []
