from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.repositories.document_knowledge import (
    document_knowledge_repository,
)
from ai_interviewer_api.schemas.retrieval import (
    DocumentQuestionCandidate,
    RetrievedKnowledgeContext,
)

MAX_INTERVIEW_DOCUMENT_CONTEXT = 6


def retrieve_interview_document_context(
    *,
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
    current_question: Mapping[str, Any] | None = None,
    current_field: Mapping[str, Any] | None = None,
    target: Mapping[str, Any] | None = None,
    state: Mapping[str, Any] | None = None,
    messages: Sequence[Mapping[str, Any]] = (),
    retrieval_policy: str = "auto",
    limit: int = MAX_INTERVIEW_DOCUMENT_CONTEXT,
) -> list[RetrievedKnowledgeContext]:
    """Retrieve only indexed document content for an interview turn.

    The service is intentionally channel agnostic. Text, structured, and
    voice processing all call this function from the API service boundary.
    Authorization is enforced by the caller's scoped record/knowledge lookup;
    this function still applies the tenant and knowledge filters before any
    content is returned to an LLM.
    """

    if str(retrieval_policy or "auto").strip().lower() == "never":
        return []

    query = build_interview_document_query(
        record=record,
        knowledge=knowledge,
        current_question=current_question,
        current_field=current_field,
        target=target,
        state=state,
        messages=messages,
    )
    if not query:
        return []

    return retrieve_indexed_document_context(
        query=query,
        knowledge_id=str(knowledge.get("id") or record.get("knowledgeId") or ""),
        tenant_id=user.tenant_id,
        limit=limit,
    )


def retrieve_indexed_document_context(
    *,
    query: str,
    knowledge_id: str,
    tenant_id: str,
    limit: int = MAX_INTERVIEW_DOCUMENT_CONTEXT,
) -> list[RetrievedKnowledgeContext]:
    """Retrieve indexed documents for callers that already built a query."""

    if not query or not knowledge_id or not tenant_id:
        return []
    return document_knowledge_repository.search(
        query=query,
        knowledge_id=knowledge_id,
        tenant_id=tenant_id,
        limit=min(int(limit), MAX_INTERVIEW_DOCUMENT_CONTEXT),
    )


def validate_document_question_candidate(
    *,
    value: str | None,
    source_ids: Sequence[str],
    contexts: Sequence[RetrievedKnowledgeContext],
) -> DocumentQuestionCandidate | None:
    """Validate an AI-extracted candidate against the backend search result.

    The model may decide which value answers a field, but it cannot introduce
    an unsupported document source or a value that is absent from the source
    text. This keeps document-derived candidates in the confirmation-only
    boundary until the user accepts them.
    """

    candidate_value = _compact(str(value or ""))
    if not candidate_value:
        return None

    context_by_id = {
        str(context.source_id).strip(): context
        for context in contexts
        if str(context.source_id).strip()
        and context.source_type in {"document", "document_chunk"}
    }
    valid_source_ids = tuple(
        dict.fromkeys(
            str(source_id).strip()
            for source_id in source_ids
            if str(source_id).strip() in context_by_id
        )
    )
    if not valid_source_ids:
        return None

    compact_candidate = _compact_for_match(candidate_value)
    if not any(
        compact_candidate in _compact_for_match(
            f"{context_by_id[source_id].title}\n{context_by_id[source_id].content}"
        )
        for source_id in valid_source_ids
    ):
        return None
    return DocumentQuestionCandidate(
        value=candidate_value,
        source_ids=valid_source_ids,
    )


def build_interview_document_query(
    *,
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    current_question: Mapping[str, Any] | None = None,
    current_field: Mapping[str, Any] | None = None,
    target: Mapping[str, Any] | None = None,
    state: Mapping[str, Any] | None = None,
    messages: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Build a bounded semantic search query from the current turn context."""

    parts: list[str] = []
    for source in (knowledge, record, current_question or {}, current_field or {}, target or {}):
        for key in (
            "name",
            "description",
            "purpose",
            "targetBusiness",
            "targetEquipment",
            "title",
            "text",
            "label",
            "value",
            "questionPlan",
        ):
            value = source.get(key)
            if isinstance(value, Mapping):
                value = " ".join(str(item) for item in value.values())
            if isinstance(value, (list, tuple, set)):
                value = " ".join(str(item) for item in value)
            if value is not None:
                parts.append(str(value))

    if state:
        for key in ("nextQuestionTarget", "lastTentativeTarget"):
            value = state.get(key)
            if value:
                parts.append(str(value))
    for message in messages[-12:]:
        if message.get("isActualUtterance") is False:
            continue
        content = str(message.get("content") or "").strip()
        if content:
            parts.append(content)
    return _compact(" ".join(parts))


def _compact(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).strip()


def _compact_for_match(value: str) -> str:
    return "".join(_compact(value).casefold().split())
