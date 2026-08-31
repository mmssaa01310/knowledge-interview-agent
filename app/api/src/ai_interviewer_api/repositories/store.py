from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ai_interviewer_api.core.config import settings

POSTGRES_SCHEMA_STATEMENTS = (
    "CREATE SCHEMA IF NOT EXISTS kikiori",
    """
    CREATE TABLE IF NOT EXISTS kikiori.entity_store (
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (entity_type, entity_id),
        CONSTRAINT entity_store_type_check CHECK (
            entity_type IN (
                'audit_logs',
                'document_read_status',
                'documents',
                'document_chunks',
                'knowledge_chunks',
                'knowledge_document_chunks',
                'chunks',
                'guidance_drafts',
                'interview_prompt_profiles',
                'interview_states',
                'knowledge_dbs',
                'knowledge_fields',
                'knowledge_tags',
                'knowledges',
                'learning_analysis_drafts',
                'messages',
                'proposals',
                'records',
                'voice_assistant_events',
                'voice_connection_events',
                'voice_sessions',
                'voice_turns'
            )
        )
    )
    """,
    "ALTER TABLE kikiori.entity_store DROP CONSTRAINT IF EXISTS entity_store_type_check",
    """
    ALTER TABLE kikiori.entity_store
    ADD CONSTRAINT entity_store_type_check CHECK (
        entity_type IN (
            'audit_logs',
            'document_read_status',
            'documents',
            'document_chunks',
            'knowledge_chunks',
            'knowledge_document_chunks',
            'chunks',
            'guidance_drafts',
            'interview_prompt_profiles',
            'interview_states',
            'knowledge_dbs',
            'knowledge_fields',
            'knowledge_tags',
            'knowledges',
            'learning_analysis_drafts',
            'messages',
            'proposals',
            'records',
            'voice_assistant_events',
            'voice_connection_events',
            'voice_sessions',
            'voice_turns'
        )
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS entity_store_tenant_lookup_idx
        ON kikiori.entity_store (entity_type, tenant_id, created_at, entity_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS entity_store_knowledge_lookup_idx
        ON kikiori.entity_store (entity_type, tenant_id, ((payload ->> 'knowledgeId')))
    """,
    """
    CREATE INDEX IF NOT EXISTS entity_store_record_lookup_idx
        ON kikiori.entity_store (entity_type, tenant_id, ((payload ->> 'recordId')))
    """,
)


class InMemoryStore:
    """Small test double selected explicitly with a memory DATABASE_URL."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

    def ensure_schema(self) -> None:
        return None

    def list(self, table: str, tenant_id: str) -> list[dict[str, Any]]:
        return [row for row in self.tables[table].values() if row["tenantId"] == tenant_id]

    def get(self, table: str, item_id: str) -> dict[str, Any] | None:
        return self.tables[table].get(item_id)

    def upsert(self, table: str, item: dict[str, Any]) -> dict[str, Any]:
        self.tables[table][item["id"]] = item
        return item

    def delete(self, table: str, item_id: str) -> bool:
        return self.tables[table].pop(item_id, None) is not None

    def count(self, table: str) -> int:
        return len(self.tables[table])

    def health(self) -> dict[str, str]:
        return {"status": "ok", "backend": "memory"}


class PostgresStore:
    """PostgreSQL-backed implementation of the repository store contract.

    Domain services continue to work with dictionaries while PostgreSQL owns
    durability, tenant scoping metadata, and lookup indexes. JSONB keeps the
    existing API/domain shape stable while the schema evolves.
    """

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[Any]]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            yield connection

    def ensure_schema(self) -> None:
        with self._connection() as connection:
            for statement in POSTGRES_SCHEMA_STATEMENTS:
                connection.execute(statement)

    def list(self, table: str, tenant_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT payload
                FROM kikiori.entity_store
                WHERE entity_type = %s AND tenant_id = %s
                ORDER BY created_at, entity_id
                """,
                (table, tenant_id),
            ).fetchall()
        return [dict(row["payload"]) for row in rows]

    def get(self, table: str, item_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT payload
                FROM kikiori.entity_store
                WHERE entity_type = %s AND entity_id = %s
                """,
                (table, item_id),
            ).fetchone()
        return dict(row["payload"]) if row else None

    def upsert(self, table: str, item: dict[str, Any]) -> dict[str, Any]:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO kikiori.entity_store (entity_type, entity_id, tenant_id, payload)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (entity_type, entity_id)
                DO UPDATE SET
                    tenant_id = EXCLUDED.tenant_id,
                    payload = EXCLUDED.payload,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (table, item["id"], item["tenantId"], Jsonb(item)),
            )
        return item

    def delete(self, table: str, item_id: str) -> bool:
        with self._connection() as connection:
            result = connection.execute(
                """
                DELETE FROM kikiori.entity_store
                WHERE entity_type = %s AND entity_id = %s
                """,
                (table, item_id),
            )
        return result.rowcount > 0

    def count(self, table: str) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM kikiori.entity_store WHERE entity_type = %s",
                (table,),
            ).fetchone()
        return int(row["count"]) if row else 0

    def health(self) -> dict[str, str]:
        with self._connection() as connection:
            connection.execute("SELECT 1").fetchone()
        return {"status": "ok", "backend": "postgresql"}


def create_store() -> InMemoryStore | PostgresStore:
    if settings.database_url.startswith("memory://"):
        return InMemoryStore()
    return PostgresStore(settings.database_url)


store = create_store()
