from __future__ import annotations

from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile

from ai_interviewer_api.auth.deps import DEV_TOKENS
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.documents import (
    delete_document,
    get_document_content,
    upload_document,
)
from ai_interviewer_api.services.document_ingestion import (
    DocumentIngestionError,
    chunk_document_text,
    extract_document_text,
    ingest_document,
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
        "ingestionStatus": "queued",
        "progressPercent": 10,
        "chunkCount": 0,
        "deletedAt": None,
    }


def test_text_upload_is_extracted_chunked_and_indexed() -> None:
    document = _document()
    store.upsert("documents", document)

    result = ingest_document(document, "# 発生条件\n朝一の暖機前に荷重がばらつく。".encode())

    assert result.document["ingestionStatus"] == "indexed"
    assert result.document["progressPercent"] == 100
    assert result.document["chunkCount"] == 1
    assert result.chunks[0]["status"] == "indexed"
    assert "荷重がばらつく" in result.chunks[0]["content"]


def test_supported_office_extractors_return_searchable_text() -> None:
    with pytest.raises(DocumentIngestionError):
        extract_document_text(
            file_name="empty.txt",
            content_type="text/plain",
            raw_bytes=b" \n",
        )

    chunks = chunk_document_text(
        _document(),
        "a" * 2500,
    )
    assert len(chunks) >= 2
    assert chunks[0]["sequence"] == 1
    assert chunks[1]["sequence"] == 2


@pytest.mark.anyio
async def test_upload_endpoint_returns_indexed_document_summary() -> None:
    store.upsert(
        "knowledges",
        {
            "id": "ingestion-knowledge",
            "tenantId": "tenant-demo",
            "name": "保全ナレッジ",
        },
    )
    file = UploadFile(
        file=BytesIO("設備 med900 は朝一の暖機前に確認する。".encode()),
        filename="med900.md",
    )

    result = await upload_document(
        "ingestion-knowledge",
        file,
        DEV_TOKENS["dev-manager"],
    )

    assert result["ingestionStatus"] == "indexed"
    assert "content" not in result
    assert result["chunkCount"] == 1
    assert len(store.list("document_chunks", "tenant-demo")) == 1

    opened = get_document_content(result["id"], DEV_TOKENS["dev-manager"])
    assert opened["document"]["id"] == result["id"]
    assert "設備 med900" in opened["content"]

    assert delete_document(result["id"], DEV_TOKENS["dev-manager"]) == {"deleted": True}
    assert store.get("documents", result["id"]) is None
    assert store.list("document_chunks", "tenant-demo") == []


@pytest.mark.anyio
async def test_upload_endpoint_marks_unsupported_file_as_failed() -> None:
    store.upsert(
        "knowledges",
        {
            "id": "ingestion-knowledge",
            "tenantId": "tenant-demo",
            "name": "保全ナレッジ",
        },
    )
    file = UploadFile(file=BytesIO(b"legacy"), filename="legacy.doc")

    with pytest.raises(HTTPException) as error:
        await upload_document("ingestion-knowledge", file, DEV_TOKENS["dev-manager"])

    assert error.value.status_code == 415
    failed = store.list("documents", "tenant-demo")
    assert failed[0]["ingestionStatus"] == "failed"
