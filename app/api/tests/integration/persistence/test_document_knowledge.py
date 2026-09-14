from __future__ import annotations

from typing import Any

from ai_interviewer_api.repositories.document_knowledge import (
    ElasticsearchDocumentKnowledgeRepository,
    PostgresDocumentKnowledgeRepository,
)
from ai_interviewer_api.repositories.store import store


class _FakeIndices:
    def __init__(self) -> None:
        self.existing: set[str] = set()
        self.created: list[dict[str, Any]] = []

    def exists(self, *, index: str) -> bool:
        return index in self.existing

    def create(self, *, index: str, settings: dict, mappings: dict) -> None:
        self.existing.add(index)
        self.created.append({"index": index, "settings": settings, "mappings": mappings})


class _FakeElasticsearch:
    def __init__(self) -> None:
        self.indices = _FakeIndices()
        self.info_calls = 0
        self.deleted_queries: list[dict[str, Any]] = []
        self.indexed: list[dict[str, Any]] = []
        self.search_args: dict[str, Any] | None = None
        self.search_response: dict[str, Any] = {"hits": {"hits": []}}

    def info(self) -> dict[str, str]:
        self.info_calls += 1
        return {"cluster_name": "fake"}

    def delete_by_query(self, **kwargs: Any) -> dict[str, int]:
        self.deleted_queries.append(kwargs)
        return {"deleted": 0}

    def index(self, **kwargs: Any) -> dict[str, str]:
        self.indexed.append(kwargs)
        return {"result": "created"}

    def search(self, **kwargs: Any) -> dict[str, Any]:
        self.search_args = kwargs
        return self.search_response


def _seed_document() -> None:
    store.upsert(
        "documents",
        {
            "id": "elastic-document",
            "tenantId": "tenant-demo",
            "knowledgeId": "elastic-knowledge",
            "fileName": "保全手順.md",
            "ingestionStatus": "indexed",
            "deletedAt": None,
        },
    )


def test_postgres_document_repository_replaces_indexed_chunks() -> None:
    store.tables.clear()
    repository = PostgresDocumentKnowledgeRepository()
    _seed_document()

    repository.replace_document(
        store.get("documents", "elastic-document") or {},
        content="本文",
        chunks=[
            {
                "id": "elastic-document:chunk:1",
                "tenantId": "tenant-demo",
                "knowledgeId": "elastic-knowledge",
                "documentId": "elastic-document",
                "status": "indexed",
                "content": "朝一の暖機前に荷重がばらつく",
            }
        ],
    )

    contexts = repository.search(
        query="荷重の発生条件",
        knowledge_id="elastic-knowledge",
        tenant_id="tenant-demo",
        limit=3,
    )

    assert [context.source_id for context in contexts] == ["elastic-document:chunk:1"]
    assert contexts[0].title == "保全手順.md"


def test_postgres_document_repository_reads_and_deletes_document_content() -> None:
    store.tables.clear()
    repository = PostgresDocumentKnowledgeRepository()
    _seed_document()
    repository.replace_document(
        store.get("documents", "elastic-document") or {},
        content="本文全体",
        chunks=[
            {
                "id": "elastic-document:chunk:2",
                "tenantId": "tenant-demo",
                "knowledgeId": "elastic-knowledge",
                "documentId": "elastic-document",
                "sequence": 2,
                "status": "indexed",
                "content": "後半",
            },
            {
                "id": "elastic-document:chunk:1",
                "tenantId": "tenant-demo",
                "knowledgeId": "elastic-knowledge",
                "documentId": "elastic-document",
                "sequence": 1,
                "status": "indexed",
                "content": "前半",
            },
        ],
    )

    assert repository.get_document_content(
        document_id="elastic-document",
        knowledge_id="elastic-knowledge",
        tenant_id="tenant-demo",
    ) == "前半\n\n後半"

    repository.delete_document(
        document_id="elastic-document",
        knowledge_id="elastic-knowledge",
        tenant_id="tenant-demo",
    )
    assert store.list("document_chunks", "tenant-demo") == []


def test_elasticsearch_repository_creates_explicit_indexes_and_scopes_search() -> None:
    store.tables.clear()
    _seed_document()
    client = _FakeElasticsearch()
    repository = ElasticsearchDocumentKnowledgeRepository(
        client=client,
        document_index="documents-test",
        document_chunk_index="document-chunks-test",
    )

    repository.ensure_ready()

    assert client.info_calls == 1
    assert [item["index"] for item in client.indices.created] == [
        "documents-test",
        "document-chunks-test",
    ]
    assert client.indices.created[0]["mappings"]["properties"]["tenantId"] == {"type": "keyword"}

    client.search_response = {
        "hits": {
            "hits": [
                {
                    "_id": "elastic-document:chunk:1",
                    "_score": 2.0,
                    "_source": {
                        "tenantId": "tenant-demo",
                        "knowledgeId": "elastic-knowledge",
                        "documentId": "elastic-document",
                        "sourceType": "document_chunk",
                        "sourceId": "elastic-document:chunk:1",
                        "title": "保全手順.md",
                        "status": "indexed",
                        "content": "朝一の暖機前に荷重がばらつく",
                    },
                }
            ]
        }
    }
    contexts = repository.search(
        query="荷重の発生条件",
        knowledge_id="elastic-knowledge",
        tenant_id="tenant-demo",
        limit=3,
    )

    assert contexts[0].source_id == "elastic-document:chunk:1"
    assert contexts[0].score == 0.6667
    assert client.search_args is not None
    filters = client.search_args["query"]["bool"]["filter"]
    assert {"term": {"tenantId": "tenant-demo"}} in filters
    assert {"term": {"knowledgeId": "elastic-knowledge"}} in filters
    assert {"terms": {"documentId": ["elastic-document"]}} in filters
    assert {"terms": {"status": ["completed", "indexed"]}} in filters

    client.search_response = {
        "hits": {
            "hits": [
                {
                    "_id": "elastic-document:chunk:1",
                    "_source": {
                        "tenantId": "tenant-demo",
                        "knowledgeId": "elastic-knowledge",
                        "documentId": "elastic-document",
                        "sourceType": "document_chunk",
                        "status": "indexed",
                        "sequence": 1,
                        "content": "本文",
                    },
                }
            ]
        }
    }
    assert repository.get_document_content(
        document_id="elastic-document",
        knowledge_id="elastic-knowledge",
        tenant_id="tenant-demo",
    ) == "本文"
    repository.delete_document(
        document_id="elastic-document",
        knowledge_id="elastic-knowledge",
        tenant_id="tenant-demo",
    )
    assert len(client.deleted_queries) == 2


def test_elasticcloud_backend_alias_uses_elasticsearch_repository() -> None:
    from ai_interviewer_api.repositories.document_knowledge import (
        create_document_knowledge_repository,
    )

    repository = create_document_knowledge_repository(
        backend="elasticcloud",
        client=_FakeElasticsearch(),
    )

    assert isinstance(repository, ElasticsearchDocumentKnowledgeRepository)
