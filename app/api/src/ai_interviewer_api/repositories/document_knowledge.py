from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from ai_interviewer_api.core.config import settings
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.schemas.retrieval import RetrievedKnowledgeContext

INDEXED_STATUSES = frozenset({"indexed", "completed"})
MAX_SEARCH_RESULTS = 50
MAX_CONTEXT_CONTENT_CHARS = 1800
DOCUMENT_CHUNK_TABLES = (
    "document_chunks",
    "knowledge_chunks",
    "knowledge_document_chunks",
    "chunks",
)


class DocumentKnowledgeBackendError(RuntimeError):
    """Raised when the configured document knowledge backend cannot be used."""


class DocumentKnowledgeRepository(Protocol):
    backend_name: str

    def ensure_ready(self) -> None:
        """Validate the backend and create required indexes when needed."""

    def replace_document(
        self,
        document: Mapping[str, Any],
        *,
        content: str,
        chunks: Sequence[Mapping[str, Any]],
    ) -> None:
        """Replace the searchable representation of one document."""

    def get_document_content(
        self,
        *,
        document_id: str,
        knowledge_id: str,
        tenant_id: str,
    ) -> str | None:
        """Return the indexed full text for one scoped document."""

    def delete_document(
        self,
        *,
        document_id: str,
        knowledge_id: str,
        tenant_id: str,
    ) -> None:
        """Remove one document's searchable representation."""

    def search(
        self,
        *,
        query: str,
        knowledge_id: str,
        tenant_id: str,
        limit: int,
    ) -> list[RetrievedKnowledgeContext]:
        """Return tenant- and Knowledge-scoped indexed document context."""


class PostgresDocumentKnowledgeRepository:
    """Store document chunks in the existing PostgreSQL repository contract."""

    backend_name = "postgres"

    def ensure_ready(self) -> None:
        return None

    def replace_document(
        self,
        document: Mapping[str, Any],
        *,
        content: str,
        chunks: Sequence[Mapping[str, Any]],
    ) -> None:
        del content
        document_id = str(document.get("id") or "")
        tenant_id = str(document.get("tenantId") or "")
        if not document_id or not tenant_id:
            raise DocumentKnowledgeBackendError("document_identity_missing")

        for existing in list(store.list("document_chunks", tenant_id)):
            if existing.get("documentId") == document_id:
                store.delete("document_chunks", str(existing["id"]))

        for chunk in chunks:
            store.upsert("document_chunks", dict(chunk))

    def search(
        self,
        *,
        query: str,
        knowledge_id: str,
        tenant_id: str,
        limit: int,
    ) -> list[RetrievedKnowledgeContext]:
        normalized_query = _compact(query)
        if not normalized_query or not knowledge_id or not tenant_id:
            return []

        candidates = _collect_postgres_candidates(
            knowledge_id=knowledge_id,
            tenant_id=tenant_id,
        )
        scored: list[tuple[float, int, _DocumentCandidate]] = []
        for candidate in candidates:
            score = _score(normalized_query, candidate.search_text)
            if score > 0:
                scored.append((score, candidate.priority, candidate))

        scored.sort(key=lambda item: (-item[0], -item[1], item[2].source_id))
        chunk_document_ids = {
            candidate.document_id
            for _, _, candidate in scored
            if candidate.source_type == "document_chunk" and candidate.document_id
        }
        bounded_limit = max(1, min(int(limit), MAX_SEARCH_RESULTS))
        results: list[RetrievedKnowledgeContext] = []
        seen_sources: set[str] = set()
        for score, _, candidate in scored:
            if (
                candidate.source_type == "document"
                and candidate.source_id in chunk_document_ids
            ):
                continue
            if candidate.source_id in seen_sources:
                continue
            seen_sources.add(candidate.source_id)
            results.append(
                RetrievedKnowledgeContext(
                    source_type=candidate.source_type,
                    source_id=candidate.source_id,
                    title=candidate.title,
                    content=_truncate(candidate.content),
                    score=round(score, 4),
                )
            )
            if len(results) >= bounded_limit:
                break
        return results

    def get_document_content(
        self,
        *,
        document_id: str,
        knowledge_id: str,
        tenant_id: str,
    ) -> str | None:
        document = store.get("documents", document_id)
        if not _document_is_in_scope(document, document_id, knowledge_id, tenant_id):
            return None

        parts = _collect_postgres_document_parts(
            document_id=document_id,
            knowledge_id=knowledge_id,
            tenant_id=tenant_id,
        )
        if parts:
            return _join_document_parts(parts)
        return _raw_document_content(document)

    def delete_document(
        self,
        *,
        document_id: str,
        knowledge_id: str,
        tenant_id: str,
    ) -> None:
        for table_name in (*DOCUMENT_CHUNK_TABLES, "document_read_status"):
            for row in list(store.list(table_name, tenant_id)):
                if str(row.get("documentId") or "") != document_id:
                    continue
                row_id = str(row.get("id") or "")
                if row_id:
                    store.delete(table_name, row_id)


class ElasticsearchDocumentKnowledgeRepository:
    """Store and search document content in Elasticsearch or Elastic Cloud."""

    backend_name = "elasticsearch"

    def __init__(
        self,
        *,
        client: Any | None = None,
        document_index: str | None = None,
        document_chunk_index: str | None = None,
    ) -> None:
        self.client = (
            client if client is not None else _create_elasticsearch_client()
        )
        self.document_index = document_index or settings.elasticsearch_document_index
        self.document_chunk_index = (
            document_chunk_index or settings.elasticsearch_document_chunk_index
        )

    def ensure_ready(self) -> None:
        try:
            self.client.info()
            self._ensure_index(self.document_index)
            self._ensure_index(self.document_chunk_index)
        except DocumentKnowledgeBackendError:
            raise
        except Exception as error:  # noqa: BLE001 - translate SDK errors at boundary
            raise DocumentKnowledgeBackendError("elasticsearch_unavailable") from error

    def replace_document(
        self,
        document: Mapping[str, Any],
        *,
        content: str,
        chunks: Sequence[Mapping[str, Any]],
    ) -> None:
        document_id = str(document.get("id") or "")
        tenant_id = str(document.get("tenantId") or "")
        knowledge_id = str(document.get("knowledgeId") or "")
        if not document_id or not tenant_id or not knowledge_id:
            raise DocumentKnowledgeBackendError("document_identity_missing")

        try:
            for index in (self.document_index, self.document_chunk_index):
                self.client.delete_by_query(
                    index=index,
                    query={
                        "bool": {
                            "filter": [
                                {"term": {"tenantId": tenant_id}},
                                {"term": {"documentId": document_id}},
                            ]
                        }
                    },
                    conflicts="proceed",
                    refresh=True,
                )

            self.client.index(
                index=self.document_index,
                id=document_id,
                document={
                    "tenantId": tenant_id,
                    "knowledgeId": knowledge_id,
                    "documentId": document_id,
                    "sourceType": "document",
                    "sourceId": document_id,
                    "status": "indexed",
                    "title": str(document.get("fileName") or "事前知識文書"),
                    "content": content,
                },
                refresh="wait_for",
            )
            for chunk in chunks:
                self.client.index(
                    index=self.document_chunk_index,
                    id=str(chunk["id"]),
                    document={
                        "tenantId": tenant_id,
                        "knowledgeId": knowledge_id,
                        "documentId": document_id,
                        "sourceType": "document_chunk",
                        "sourceId": str(chunk["id"]),
                        "status": "indexed",
                        "sequence": int(chunk.get("sequence", 0)),
                        "title": str(
                            chunk.get("title")
                            or document.get("fileName")
                            or "事前知識チャンク"
                        ),
                        "content": str(chunk.get("content") or ""),
                    },
                    refresh="wait_for",
                )
        except Exception as error:  # noqa: BLE001 - translate SDK errors at boundary
            raise DocumentKnowledgeBackendError("elasticsearch_index_failed") from error

    def get_document_content(
        self,
        *,
        document_id: str,
        knowledge_id: str,
        tenant_id: str,
    ) -> str | None:
        try:
            response = self.client.search(
                index=[self.document_index, self.document_chunk_index],
                size=MAX_SEARCH_RESULTS,
                query={
                    "bool": {
                        "filter": [
                            {"term": {"tenantId": tenant_id}},
                            {"term": {"knowledgeId": knowledge_id}},
                            {"term": {"documentId": document_id}},
                            {"terms": {"status": sorted(INDEXED_STATUSES)}},
                        ]
                    }
                },
            )
        except Exception as error:  # noqa: BLE001 - translate SDK errors at boundary
            raise DocumentKnowledgeBackendError("elasticsearch_document_read_failed") from error

        body = getattr(response, "body", response)
        hits = body.get("hits", {}).get("hits", []) if isinstance(body, Mapping) else []
        parts: list[tuple[int, str, str]] = []
        for hit in hits:
            if not isinstance(hit, Mapping):
                continue
            source = hit.get("_source")
            if not isinstance(source, Mapping):
                continue
            content = _raw_document_content(source)
            if not content:
                continue
            if _first_text(source, "sourceType") == "document":
                return content
            if _first_text(source, "sourceType") != "document_chunk":
                continue
            try:
                sequence = int(source.get("sequence") or 0)
            except (TypeError, ValueError):
                sequence = 0
            parts.append((sequence, str(hit.get("_id") or ""), content))

        parts.sort(key=lambda item: (item[0], item[1]))
        return _join_document_parts(parts)

    def delete_document(
        self,
        *,
        document_id: str,
        knowledge_id: str,
        tenant_id: str,
    ) -> None:
        try:
            for index in (self.document_index, self.document_chunk_index):
                self.client.delete_by_query(
                    index=index,
                    query={
                        "bool": {
                            "filter": [
                                {"term": {"tenantId": tenant_id}},
                                {"term": {"knowledgeId": knowledge_id}},
                                {"term": {"documentId": document_id}},
                            ]
                        }
                    },
                    conflicts="proceed",
                    refresh=True,
                )
        except Exception as error:  # noqa: BLE001 - translate SDK errors at boundary
            raise DocumentKnowledgeBackendError("elasticsearch_document_delete_failed") from error

    def search(
        self,
        *,
        query: str,
        knowledge_id: str,
        tenant_id: str,
        limit: int,
    ) -> list[RetrievedKnowledgeContext]:
        normalized_query = _compact(query)
        if not normalized_query or not knowledge_id or not tenant_id:
            return []

        indexed_document_ids = {
            str(document.get("id"))
            for document in store.list("documents", tenant_id)
            if document.get("knowledgeId") == knowledge_id
            and document.get("ingestionStatus") in INDEXED_STATUSES
            and document.get("deletedAt") is None
            and document.get("id")
        }
        if not indexed_document_ids:
            return []

        bounded_limit = max(1, min(int(limit), MAX_SEARCH_RESULTS))
        try:
            response = self.client.search(
                index=[self.document_index, self.document_chunk_index],
                size=min(MAX_SEARCH_RESULTS, bounded_limit * 3),
                query={
                    "bool": {
                        "filter": [
                            {"term": {"tenantId": tenant_id}},
                            {"term": {"knowledgeId": knowledge_id}},
                            {"terms": {"documentId": sorted(indexed_document_ids)}},
                            {"terms": {"status": sorted(INDEXED_STATUSES)}},
                        ],
                        "must": [
                            {
                                "multi_match": {
                                    "query": normalized_query,
                                    "fields": ["title^2", "content"],
                                    "operator": "or",
                                }
                            }
                        ],
                    }
                },
            )
        except Exception as error:  # noqa: BLE001 - translate SDK errors at boundary
            raise DocumentKnowledgeBackendError("elasticsearch_search_failed") from error

        body = getattr(response, "body", response)
        hits = body.get("hits", {}).get("hits", []) if isinstance(body, Mapping) else []
        parsed_hits: list[tuple[float, Mapping[str, Any]]] = []
        for hit in hits:
            if not isinstance(hit, Mapping):
                continue
            source = hit.get("_source")
            if not isinstance(source, Mapping):
                continue
            content = _first_text(source, "content")
            source_id = _first_text(source, "sourceId") or str(hit.get("_id") or "")
            if not content or not source_id:
                continue
            raw_score = hit.get("_score")
            try:
                score = float(raw_score or 0)
            except (TypeError, ValueError):
                score = 0.0
            parsed_hits.append((score, source))

        chunk_document_ids = {
            _first_text(source, "documentId")
            for _, source in parsed_hits
            if _first_text(source, "sourceType") == "document_chunk"
        }
        results: list[RetrievedKnowledgeContext] = []
        seen_sources: set[str] = set()
        for raw_score, source in parsed_hits:
            source_type = (
                "document_chunk"
                if _first_text(source, "sourceType") == "document_chunk"
                else "document"
            )
            source_id = _first_text(source, "sourceId")
            document_id = _first_text(source, "documentId")
            if source_type == "document" and document_id in chunk_document_ids:
                continue
            if source_id in seen_sources:
                continue
            seen_sources.add(source_id)
            results.append(
                RetrievedKnowledgeContext(
                    source_type=source_type,
                    source_id=source_id,
                    title=_first_text(source, "title") or "事前知識文書",
                    content=_truncate(_first_text(source, "content")),
                    score=round(_normalize_elasticsearch_score(raw_score), 4),
                )
            )
            if len(results) >= bounded_limit:
                break
        return results

    def _ensure_index(self, index_name: str) -> None:
        if not index_name:
            raise DocumentKnowledgeBackendError("elasticsearch_index_name_missing")
        if self.client.indices.exists(index=index_name):
            return
        self.client.indices.create(
            index=index_name,
            settings={
                "analysis": {
                    "tokenizer": {
                        "kikiori_ngram_tokenizer": {
                            "type": "ngram",
                            "min_gram": 2,
                            "max_gram": 3,
                        }
                    },
                    "analyzer": {
                        "kikiori_text": {
                            "type": "custom",
                            "tokenizer": "kikiori_ngram_tokenizer",
                            "filter": ["lowercase"],
                        }
                    },
                }
            },
            mappings={
                "dynamic": "false",
                "properties": {
                    "tenantId": {"type": "keyword"},
                    "knowledgeId": {"type": "keyword"},
                    "documentId": {"type": "keyword"},
                    "sourceType": {"type": "keyword"},
                    "sourceId": {"type": "keyword"},
                    "status": {"type": "keyword"},
                    "sequence": {"type": "integer"},
                    "title": {
                        "type": "text",
                        "analyzer": "kikiori_text",
                        "search_analyzer": "kikiori_text",
                    },
                    "content": {
                        "type": "text",
                        "analyzer": "kikiori_text",
                        "search_analyzer": "kikiori_text",
                    },
                },
            },
        )


def create_document_knowledge_repository(
    *,
    backend: str | None = None,
    client: Any | None = None,
) -> DocumentKnowledgeRepository:
    selected_backend = (backend or settings.document_knowledge_backend).strip().lower()
    if selected_backend == "postgres":
        return PostgresDocumentKnowledgeRepository()
    if selected_backend in {"elasticsearch", "elastic_cloud", "elasticcloud"}:
        return ElasticsearchDocumentKnowledgeRepository(client=client)
    raise DocumentKnowledgeBackendError(
        f"unsupported_document_knowledge_backend:{selected_backend}"
    )


def _create_elasticsearch_client() -> Any:
    if not settings.elasticsearch_cloud_id and not settings.elasticsearch_url:
        raise DocumentKnowledgeBackendError(
            "elasticsearch_cloud_id_or_url_required"
        )
    if not settings.elasticsearch_api_key and not (
        settings.elasticsearch_username and settings.elasticsearch_password
    ):
        raise DocumentKnowledgeBackendError(
            "elasticsearch_api_key_or_basic_auth_required"
        )

    try:
        from elasticsearch import Elasticsearch

        auth: dict[str, Any]
        if settings.elasticsearch_api_key:
            auth = {"api_key": settings.elasticsearch_api_key}
        else:
            auth = {
                "basic_auth": (
                    settings.elasticsearch_username,
                    settings.elasticsearch_password,
                )
            }
        connection: dict[str, Any] = {
            **auth,
            "request_timeout": settings.elasticsearch_request_timeout_seconds,
            "verify_certs": settings.elasticsearch_verify_certs,
        }
        if settings.elasticsearch_cloud_id:
            return Elasticsearch(cloud_id=settings.elasticsearch_cloud_id, **connection)
        return Elasticsearch(settings.elasticsearch_url, **connection)
    except DocumentKnowledgeBackendError:
        raise
    except Exception as error:  # noqa: BLE001 - translate SDK/config errors
        raise DocumentKnowledgeBackendError("elasticsearch_client_init_failed") from error


class _DocumentCandidate:
    def __init__(
        self,
        *,
        source_type: str,
        source_id: str,
        title: str,
        content: str,
        priority: int,
        document_id: str | None = None,
    ) -> None:
        self.source_type = source_type
        self.source_id = source_id
        self.title = title
        self.content = content
        self.search_text = f"{title} {content}"
        self.priority = priority
        self.document_id = document_id


def _collect_postgres_candidates(
    *,
    knowledge_id: str,
    tenant_id: str,
) -> list[_DocumentCandidate]:
    all_documents = {
        str(document.get("id")): document
        for document in store.list("documents", tenant_id)
        if document.get("knowledgeId") == knowledge_id
        and document.get("deletedAt") is None
    }
    documents = {
        document_id: document
        for document_id, document in all_documents.items()
        if document.get("ingestionStatus") in INDEXED_STATUSES
    }
    candidates: list[_DocumentCandidate] = []

    for table_name in DOCUMENT_CHUNK_TABLES:
        for chunk in store.list(table_name, tenant_id):
            if chunk.get("knowledgeId") != knowledge_id:
                continue
            if chunk.get("deletedAt") is not None:
                continue
            status = chunk.get("status") or chunk.get("ingestionStatus")
            if status not in INDEXED_STATUSES:
                continue
            document_id = str(chunk.get("documentId") or "").strip()
            if document_id:
                parent_document = all_documents.get(document_id)
                if (
                    parent_document is not None
                    and parent_document.get("ingestionStatus") not in INDEXED_STATUSES
                ):
                    continue
            content = _first_text(
                chunk,
                "text",
                "content",
                "chunkText",
                "body",
                "extractedText",
            )
            if not content:
                continue
            title = (
                _first_text(chunk, "title", "fileName")
                or _first_text(documents.get(document_id) or {}, "fileName")
                or "事前知識チャンク"
            )
            source_id = str(chunk.get("id") or "").strip()
            if not source_id:
                continue
            candidates.append(
                _DocumentCandidate(
                    source_type="document_chunk",
                    source_id=source_id,
                    title=title,
                    content=content,
                    priority=2,
                    document_id=document_id or None,
                )
            )

    for document_id, document in documents.items():
        content = _first_text(document, "text", "content", "extractedText", "body")
        file_name = _first_text(document, "fileName")
        if not content and not file_name:
            continue
        candidates.append(
            _DocumentCandidate(
                source_type="document",
                source_id=document_id,
                title=file_name or "事前知識文書",
                content=content or f"事前知識文書: {file_name}",
                priority=1,
                document_id=document_id,
            )
        )
    return candidates


def _document_is_in_scope(
    document: Mapping[str, Any] | None,
    document_id: str,
    knowledge_id: str,
    tenant_id: str,
) -> bool:
    if not document:
        return False
    return (
        str(document.get("id") or document_id) == document_id
        and str(document.get("tenantId") or "") == tenant_id
        and str(document.get("knowledgeId") or "") == knowledge_id
        and document.get("deletedAt") is None
        and document.get("ingestionStatus") in INDEXED_STATUSES
    )


def _collect_postgres_document_parts(
    *,
    document_id: str,
    knowledge_id: str,
    tenant_id: str,
) -> list[tuple[int, str, str]]:
    parts: list[tuple[int, str, str]] = []
    seen_ids: set[str] = set()
    for table_name in DOCUMENT_CHUNK_TABLES:
        for chunk in store.list(table_name, tenant_id):
            if str(chunk.get("documentId") or "") != document_id:
                continue
            if str(chunk.get("knowledgeId") or "") != knowledge_id:
                continue
            if chunk.get("deletedAt") is not None:
                continue
            status = chunk.get("status") or chunk.get("ingestionStatus")
            if status not in INDEXED_STATUSES:
                continue
            content = _raw_document_content(chunk)
            if not content:
                continue
            source_id = str(chunk.get("id") or "")
            if source_id and source_id in seen_ids:
                continue
            if source_id:
                seen_ids.add(source_id)
            try:
                sequence = int(chunk.get("sequence") or 0)
            except (TypeError, ValueError):
                sequence = 0
            parts.append((sequence, source_id, content))
    parts.sort(key=lambda item: (item[0], item[1]))
    return parts


def _raw_document_content(row: Mapping[str, Any] | None) -> str | None:
    if not row:
        return None
    for key in ("content", "text", "extractedText", "body", "chunkText"):
        value = row.get(key)
        if value is None:
            continue
        content = str(value).replace("\x00", "").strip()
        if content:
            return content
    return None


def _join_document_parts(parts: Sequence[tuple[int, str, str]]) -> str | None:
    content = "\n\n".join(part[2] for part in parts if part[2]).strip()
    return content or None


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


def _first_text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = _compact(str(value))
        if text:
            return text
    return ""


def _compact(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).strip()


def _normalize(value: str) -> str:
    return _compact(value).lower()


def _truncate(value: str) -> str:
    return _compact(value)[:MAX_CONTEXT_CONTENT_CHARS]


def _normalize_elasticsearch_score(score: float) -> float:
    if score <= 0:
        return 0.0
    return score / (1.0 + score)


document_knowledge_repository = create_document_knowledge_repository()
