from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.repositories.document_knowledge import (
    INDEXED_STATUSES,
    document_knowledge_repository,
)
from ai_interviewer_api.repositories.store import store

MAX_PRIOR_KNOWLEDGE_ENTRIES = 24
MAX_PRIOR_KNOWLEDGE_ITEM_CHARS = 2400
MAX_PRIOR_KNOWLEDGE_CONTEXT_CHARS = 18_000


def build_prior_knowledge_context(
    knowledge: Mapping[str, Any],
    user: UserContext,
    *,
    limit: int = MAX_PRIOR_KNOWLEDGE_ENTRIES,
) -> list[dict[str, Any]]:
    """Return directly entered facts and glossary entries for one interview.

    This is deliberately separate from lexical document RAG. Registered
    prior knowledge is configuration context and remains available even when
    a field's optional document retrieval policy is ``never``. Only active,
    indexed entries from the same tenant and Knowledge are returned.
    """

    knowledge_id = str(knowledge.get("id") or "").strip()
    tenant_id = str(user.tenant_id or "").strip()
    if not knowledge_id or not tenant_id:
        return []

    entries = [
        row
        for row in store.list("documents", tenant_id)
        if row.get("knowledgeId") == knowledge_id
        and row.get("sourceType") == "prior_knowledge"
        and row.get("deletedAt") is None
        and row.get("ingestionStatus") in INDEXED_STATUSES
    ]
    entries.sort(key=lambda row: (str(row.get("createdAt") or ""), str(row.get("id") or "")))

    result: list[dict[str, Any]] = []
    total_chars = 0
    for entry in entries[: max(0, min(int(limit), MAX_PRIOR_KNOWLEDGE_ENTRIES))]:
        entry_id = str(entry.get("id") or "").strip()
        if not entry_id:
            continue
        # Direct entries retain their normalized body in the scoped metadata
        # row, so the normal interview path does not perform one repository
        # read per entry. The repository fallback supports migrated entries
        # whose body exists only in indexed chunks.
        content = str(entry.get("content") or "").replace("\x00", "")
        if not content.strip():
            content = document_knowledge_repository.get_document_content(
                document_id=entry_id,
                knowledge_id=knowledge_id,
                tenant_id=tenant_id,
            ) or ""
        content = str(content or "").replace("\x00", "")
        if not content.strip():
            continue
        remaining = MAX_PRIOR_KNOWLEDGE_CONTEXT_CHARS - total_chars
        if remaining <= 0:
            break
        bounded_content = content[: min(MAX_PRIOR_KNOWLEDGE_ITEM_CHARS, remaining)]
        result.append(
            {
                "source_id": entry_id,
                "title": str(entry.get("title") or entry.get("fileName") or "事前知識").strip(),
                "knowledge_type": str(entry.get("knowledgeType") or "known_fact"),
                "content_format": str(entry.get("contentFormat") or "text"),
                "content": bounded_content,
                "revision": str(entry.get("updatedAt") or entry.get("createdAt") or ""),
            }
        )
        total_chars += len(bounded_content)
    return result
