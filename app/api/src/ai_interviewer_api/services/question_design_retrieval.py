from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from ai_interviewer_api.agents.question_design.schemas import RetrievedKnowledgeContext
from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.schemas.requests import FieldSuggestionRequest

MAX_RETRIEVED_CONTEXT = 8
MAX_CONTEXT_CONTENT_CHARS = 1800
APPROVED_RECORD_STATUS = "approved"
APPROVED_PROPOSAL_STATUS = "approved"
INDEXED_DOCUMENT_STATUSES = frozenset({"indexed", "completed"})
INDEXED_CHUNK_STATUSES = frozenset({"indexed", "completed"})


@dataclass(frozen=True)
class _SearchCandidate:
    source_type: str
    source_id: str
    title: str
    content: str
    search_text: str
    priority: int


def retrieve_question_design_context(
    payload: FieldSuggestionRequest,
    *,
    knowledge_id: str,
    user: UserContext,
    limit: int = MAX_RETRIEVED_CONTEXT,
) -> list[RetrievedKnowledgeContext]:
    """Retrieve approved, read-only context before question design generation.

    The local repository is currently backed by the in-memory store. The
    candidate contract is intentionally independent from that implementation
    so the same service can later use the Elasticsearch repository.
    """

    query = _build_query(payload)
    if not query:
        return []

    candidates: list[_SearchCandidate] = []
    _add_field_candidates(candidates, knowledge_id=knowledge_id, tenant_id=user.tenant_id)
    _add_approved_record_candidates(candidates, knowledge_id=knowledge_id, tenant_id=user.tenant_id)
    _add_approved_proposal_candidates(candidates, knowledge_id=knowledge_id, tenant_id=user.tenant_id)
    _add_document_chunk_candidates(candidates, knowledge_id=knowledge_id, tenant_id=user.tenant_id)
    _add_document_candidates(candidates, knowledge_id=knowledge_id, tenant_id=user.tenant_id)

    scored: list[tuple[float, int, _SearchCandidate]] = []
    for candidate in candidates:
        score = _score(query, candidate.search_text)
        if score > 0:
            scored.append((score, candidate.priority, candidate))

    scored.sort(key=lambda item: (-item[0], -item[1], item[2].source_id))
    results: list[RetrievedKnowledgeContext] = []
    seen_sources: set[tuple[str, str]] = set()
    for score, _, candidate in scored:
        source_key = (candidate.source_type, candidate.source_id)
        if source_key in seen_sources:
            continue
        seen_sources.add(source_key)
        results.append(
            RetrievedKnowledgeContext(
                source_type=candidate.source_type,
                source_id=candidate.source_id,
                title=candidate.title,
                content=_truncate(candidate.content),
                score=round(score, 4),
            )
        )
        if len(results) >= max(1, min(limit, MAX_RETRIEVED_CONTEXT)):
            break
    return results


def _build_query(payload: FieldSuggestionRequest) -> str:
    context = payload.context
    parts = [
        payload.content,
        context.name,
        context.description,
        context.category,
        context.targetBusiness,
        context.targetEquipment,
    ]
    for field in payload.existingFields:
        parts.extend([field.name, field.description])
    parts.extend(message.content for message in payload.recentMessages)
    return _compact(" ".join(str(part or "") for part in parts))


def _add_field_candidates(candidates: list[_SearchCandidate], *, knowledge_id: str, tenant_id: str) -> None:
    for field in store.list("knowledge_fields", tenant_id):
        if field.get("knowledgeId") != knowledge_id:
            continue
        name = _text(field.get("name"))
        description = _text(field.get("description"))
        examples = [
            _text(example)
            for example in field.get("aiQuestionExamples", [])
            if _text(example)
        ]
        content = " / ".join(
            part
            for part in (
                f"質問項目: {name}" if name else "",
                f"詳細項目: {description}" if description else "",
                f"質問例: {' / '.join(examples)}" if examples else "",
            )
            if part
        )
        _append_candidate(
            candidates,
            source_type="knowledge_field",
            source_id=str(field.get("id") or ""),
            title=name or "既存質問項目",
            content=content,
            priority=4,
        )


def _add_approved_record_candidates(
    candidates: list[_SearchCandidate],
    *,
    knowledge_id: str,
    tenant_id: str,
) -> None:
    approved_records = [
        record
        for record in store.list("records", tenant_id)
        if record.get("knowledgeId") == knowledge_id
        and record.get("status") == APPROVED_RECORD_STATUS
    ]
    for record in approved_records:
        record_id = str(record.get("id") or "")
        parts = [
            _text(record.get("title")),
            _text(record.get("targetEquipment")),
            _text(record.get("targetProcess")),
        ]
        for message in store.list("messages", tenant_id):
            if message.get("recordId") != record_id:
                continue
            if message.get("isActualUtterance") is False:
                continue
            if message.get("role") != "user" and message.get("messageType") != "confirmed_answer":
                continue
            parts.append(_text(message.get("content")))

        state = store.get("interview_states", f"interview-state-{record_id}") or {}
        field_states = state.get("fieldStates") if isinstance(state, dict) else {}
        if isinstance(field_states, dict):
            for field_state in field_states.values():
                if not isinstance(field_state, dict):
                    continue
                parts.extend(
                    _text(field_state.get(key))
                    for key in ("recordAnswer", "candidateAnswer")
                    if _text(field_state.get(key))
                )

        _append_candidate(
            candidates,
            source_type="approved_record",
            source_id=record_id,
            title=_text(record.get("title")) or "承認済みインタビュー記録",
            content=" / ".join(part for part in parts if part),
            priority=2,
        )


def _add_approved_proposal_candidates(
    candidates: list[_SearchCandidate],
    *,
    knowledge_id: str,
    tenant_id: str,
) -> None:
    for proposal in store.list("proposals", tenant_id):
        if proposal.get("knowledgeId") != knowledge_id:
            continue
        if proposal.get("status") != APPROVED_PROPOSAL_STATUS:
            continue
        structured_data = proposal.get("structuredData")
        content = _serialize(structured_data)
        _append_candidate(
            candidates,
            source_type="approved_proposal",
            source_id=str(proposal.get("id") or ""),
            title="承認済み構造化提案",
            content=content,
            priority=2,
        )


def _add_document_chunk_candidates(
    candidates: list[_SearchCandidate],
    *,
    knowledge_id: str,
    tenant_id: str,
) -> None:
    """Read indexed chunk-shaped rows when an ingestion adapter has created them."""

    for table_name in ("knowledge_chunks", "document_chunks", "knowledge_document_chunks", "chunks"):
        for chunk in store.list(table_name, tenant_id):
            if chunk.get("knowledgeId") != knowledge_id:
                continue
            status = chunk.get("status") or chunk.get("ingestionStatus")
            if status and status not in INDEXED_CHUNK_STATUSES:
                continue
            content = _first_text(chunk, "text", "content", "chunkText", "body", "extractedText")
            if not content:
                continue
            document_id = _text(chunk.get("documentId"))
            title = _text(chunk.get("title")) or _text(chunk.get("fileName")) or "事前知識チャンク"
            source_id = str(chunk.get("id") or f"{table_name}-{document_id}-{len(candidates)}")
            _append_candidate(
                candidates,
                source_type="document_chunk",
                source_id=source_id,
                title=title,
                content=content,
                priority=3,
            )


def _add_document_candidates(
    candidates: list[_SearchCandidate],
    *,
    knowledge_id: str,
    tenant_id: str,
) -> None:
    for document in store.list("documents", tenant_id):
        if document.get("knowledgeId") != knowledge_id:
            continue
        if document.get("ingestionStatus") not in INDEXED_DOCUMENT_STATUSES:
            continue
        content = _first_text(document, "text", "content", "extractedText", "body")
        file_name = _text(document.get("fileName"))
        if not content and not file_name:
            continue
        _append_candidate(
            candidates,
            source_type="document",
            source_id=str(document.get("id") or ""),
            title=file_name or "事前知識文書",
            content=content or f"事前知識文書: {file_name}",
            priority=1,
        )


def _append_candidate(
    candidates: list[_SearchCandidate],
    *,
    source_type: str,
    source_id: str,
    title: str,
    content: str,
    priority: int,
) -> None:
    if not source_id or not content.strip():
        return
    candidates.append(
        _SearchCandidate(
            source_type=source_type,
            source_id=source_id,
            title=title or "参照情報",
            content=_truncate(content),
            search_text=f"{title} {content}",
            priority=priority,
        )
    )


def _score(query: str, content: str) -> float:
    normalized_query = _normalize(query)
    normalized_content = _normalize(content)
    if not normalized_query or not normalized_content:
        return 0.0
    query_fragments = _fragments(normalized_query)
    content_fragments = _fragments(normalized_content)
    if not query_fragments or not content_fragments:
        return 0.0
    overlap = len(query_fragments & content_fragments)
    if overlap == 0:
        return 0.0
    score = overlap / max(1, min(len(query_fragments), 24))
    if normalized_query in normalized_content:
        score += 0.35
    return min(1.0, score)


def _fragments(value: str) -> set[str]:
    fragments: set[str] = set(re.findall(r"[a-z0-9][a-z0-9_-]*", value))
    for run in re.findall(r"[一-龥ぁ-んァ-ヶー]{2,}", value):
        fragments.add(run)
        for size in (2, 3):
            fragments.update(run[index : index + size] for index in range(len(run) - size + 1))
    return fragments


def _first_text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _text(row.get(key))
        if value:
            return value
    return ""


def _serialize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _compact(value)
    try:
        return _compact(json.dumps(value, ensure_ascii=False, sort_keys=True))
    except (TypeError, ValueError):
        return _compact(str(value))


def _text(value: Any) -> str:
    return _compact(str(value)) if value is not None else ""


def _compact(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).strip()


def _normalize(value: str) -> str:
    return _compact(value).lower()


def _truncate(value: str) -> str:
    compacted = _compact(value)
    return compacted[:MAX_CONTEXT_CONTENT_CHARS]
