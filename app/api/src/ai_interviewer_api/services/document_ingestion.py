from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from ai_interviewer_api.core.config import settings
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.repositories.document_knowledge import (
    DocumentKnowledgeBackendError,
    document_knowledge_repository,
)
from ai_interviewer_api.repositories.store import store

logger = logging.getLogger(__name__)
MAX_PRIOR_KNOWLEDGE_CONTENT_CHARS = 100_000
_MARKDOWN_PATTERN = re.compile(
    r"(?m)^(?:\s{0,3}#{1,6}\s+\S|\s*[-*+]\s+\S|\s*\d+\.\s+\S|\s*>\s+\S|\s*```)|"
    r"\[[^\]]+\]\([^\n)]+\)|\*\*[^*\n]+\*\*|`[^`\n]+`"
)


class DocumentIngestionError(ValueError):
    """Raised when directly entered knowledge cannot be indexed."""


@dataclass(frozen=True)
class DocumentIngestionResult:
    document: dict[str, Any]
    content: str
    chunks: list[dict[str, Any]]


def ingest_text_document(
    document: dict[str, Any],
    content: str,
) -> DocumentIngestionResult:
    """Chunk and index text entered in the prior-knowledge editor.

    Direct knowledge entry intentionally bypasses every file parser. It uses
    the same repository contract as legacy documents so interview retrieval
    remains tenant- and Knowledge-scoped across both backends.
    """

    try:
        normalized = normalize_prior_knowledge_content(content)
        return _index_document_content(document, normalized)
    except DocumentKnowledgeBackendError:
        _update_document(
            document["id"],
            ingestionStatus="failed",
            errorMessage="document_backend_unavailable",
        )
        raise
    except DocumentIngestionError as error:
        _update_document(
            document["id"],
            ingestionStatus="failed",
            errorMessage=str(error),
        )
        raise
    except Exception as error:  # noqa: BLE001 - keep indexing failures user-safe
        logger.exception("text_document_ingestion_failed document_id=%s", document["id"])
        _update_document(
            document["id"],
            ingestionStatus="failed",
            errorMessage="document_ingestion_failed",
        )
        raise DocumentIngestionError("document_ingestion_failed") from error


def _index_document_content(
    document: dict[str, Any],
    content: str,
) -> DocumentIngestionResult:
    content_format = detect_content_format(content)
    indexed_document = _update_document(
        document["id"],
        content=content,
        contentFormat=content_format,
        contentType="text/markdown" if content_format == "markdown" else "text/plain",
        ingestionStatus="processing",
        progressPercent=20,
    )
    chunks = chunk_document_text(indexed_document, content)
    _update_document(
        document["id"],
        ingestionStatus="chunked",
        progressPercent=70,
        chunkCount=len(chunks),
    )
    document_knowledge_repository.replace_document(
        indexed_document,
        content=content,
        chunks=chunks,
    )
    result_document = _update_document(
        document["id"],
        ingestionStatus="indexed",
        progressPercent=100,
        chunkCount=len(chunks),
        lastIngestedAt=utc_now(),
        errorMessage=None,
    )
    return DocumentIngestionResult(
        document=result_document,
        content=content,
        chunks=chunks,
    )


def normalize_prior_knowledge_content(text: str) -> str:
    """Canonicalize entered knowledge without changing its meaning.

    Plain text and Markdown keep non-empty line indentation, line breaks, and
    trailing spaces. Only outer blank lines, whitespace-only lines, and long
    runs of blank lines are normalized. Valid JSON objects/arrays are formatted
    with two-space indentation because they are structured data; malformed JSON
    is left as text instead of being guessed or rewritten.
    """

    normalized = (
        str(text)
        .replace("\x00", "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )
    lines = normalized.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    compact_lines: list[str] = []
    blank_line_count = 0
    for line in lines:
        if not line.strip():
            blank_line_count += 1
            if blank_line_count <= 2:
                compact_lines.append("")
            continue
        blank_line_count = 0
        compact_lines.append(line)

    normalized = "\n".join(compact_lines)
    if not normalized.strip():
        raise DocumentIngestionError("document_content_empty")
    if len(normalized) > MAX_PRIOR_KNOWLEDGE_CONTENT_CHARS:
        raise DocumentIngestionError("document_content_too_large")

    try:
        parsed = json.loads(normalized)
    except (json.JSONDecodeError, TypeError):
        return normalized
    if not isinstance(parsed, (dict, list)):
        return normalized

    formatted = json.dumps(parsed, ensure_ascii=False, indent=2)
    if len(formatted) > MAX_PRIOR_KNOWLEDGE_CONTENT_CHARS:
        raise DocumentIngestionError("document_content_too_large")
    return formatted


def detect_content_format(content: str) -> Literal["text", "markdown"]:
    """Derive display metadata; this does not change how content is interpreted."""

    return "markdown" if _MARKDOWN_PATTERN.search(content) else "text"


def chunk_document_text(
    document: dict[str, Any],
    content: str,
) -> list[dict[str, Any]]:
    chunk_size = max(100, int(settings.document_chunk_size_chars))
    overlap = max(0, min(int(settings.document_chunk_overlap_chars), chunk_size - 1))
    chunks: list[dict[str, Any]] = []
    start = 0
    chunk_number = 1
    while start < len(content):
        end = min(len(content), start + chunk_size)
        chunk_content = content[start:end]
        if chunk_content.strip():
            chunks.append(
                {
                    "id": f"{document['id']}:chunk:{chunk_number}",
                    "tenantId": document["tenantId"],
                    "createdByUserId": document.get("createdByUserId"),
                    "updatedByUserId": document.get("updatedByUserId"),
                    "knowledgeId": document["knowledgeId"],
                    "documentId": document["id"],
                    "title": document.get("fileName") or "事前知識チャンク",
                    "sequence": chunk_number,
                    "status": "indexed",
                    "ingestionStatus": "indexed",
                    "content": chunk_content,
                    "createdAt": utc_now(),
                    "updatedAt": utc_now(),
                    "deletedAt": None,
                }
            )
            chunk_number += 1
        if end >= len(content):
            break
        start = end - overlap
    if not chunks:
        raise DocumentIngestionError("document_chunks_empty")
    return chunks


def _update_document(document_id: str, **changes: Any) -> dict[str, Any]:
    document = store.get("documents", document_id)
    if not document:
        raise DocumentIngestionError("document_not_found")
    document.update(changes)
    document["updatedAt"] = utc_now()
    store.upsert("documents", document)
    return document
