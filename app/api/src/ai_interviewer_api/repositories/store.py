from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from threading import RLock
from time import time

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
                'users',
                'auth_sessions',
                'auth_attempts',
                'application_settings',
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
            'users',
            'auth_sessions',
            'auth_attempts',
            'application_settings',
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
    """
    CREATE UNIQUE INDEX IF NOT EXISTS active_user_login_unique_idx
        ON kikiori.entity_store (lower(payload ->> 'loginId'))
        WHERE entity_type = 'users' AND payload ->> 'active' = 'true'
    """,
    """
    CREATE INDEX IF NOT EXISTS active_user_display_page_idx
        ON kikiori.entity_store (tenant_id, lower(payload ->> 'displayName'), entity_id)
        WHERE entity_type = 'users' AND payload ->> 'active' = 'true'
    """,
    """
    CREATE INDEX IF NOT EXISTS user_audit_page_idx
        ON kikiori.entity_store (tenant_id, created_at DESC, entity_id DESC)
        WHERE entity_type = 'audit_logs' AND payload ->> 'resourceType' = 'user'
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS voice_turn_session_client_id_unique_idx
        ON kikiori.entity_store (
            tenant_id,
            ((payload ->> 'voiceSessionId')),
            ((payload ->> 'clientTurnId'))
        )
        WHERE entity_type = 'voice_turns'
          AND NULLIF(payload ->> 'voiceSessionId', '') IS NOT NULL
          AND NULLIF(payload ->> 'clientTurnId', '') IS NOT NULL
    """,
)


class InMemoryStore:
    """Small test double selected explicitly with a memory DATABASE_URL."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self._live_lock_guard = RLock()
        self._live_locks: dict[str, RLock] = {}

    @contextmanager
    def live_capture_lock(self, key: str) -> Iterator[None]:
        with self._live_lock_guard:
            lock = self._live_locks.setdefault(key, RLock())
        with lock:
            yield

    def pending_live_captures(self) -> list[dict[str, Any]]:
        return sorted([
            row for row in list(self.tables["messages"].values())
            if row.get("liveCaptureUser") and not row.get("liveCaptureApplied")
            and row.get("liveCaptureRetryAt", 0) <= time()
        ], key=lambda row: (row["createdAt"], row["liveCaptureRevision"]))[:100]

    def ensure_schema(self) -> None:
        return None

    def ensure_initial_application_settings_admin(self) -> None:
        with self.live_capture_lock("application-settings-admin-bootstrap"):
            users = list(self.tables["users"].values())
            if any(
                row.get("active")
                and row.get("role") == "admin"
                and row.get("applicationSettingsAdmin")
                for row in users
            ):
                return
            first_admin = next(
                (
                    row for row in users
                    if row.get("active") and row.get("role") == "admin"
                ),
                None,
            )
            if first_admin:
                first_admin["applicationSettingsAdmin"] = True
                self.upsert("users", first_admin)

    def list(self, table: str, tenant_id: str) -> list[dict[str, Any]]:
        return [row for row in self.tables[table].values() if row["tenantId"] == tenant_id]

    def list_all_users(self) -> list[dict[str, Any]]:
        return list(self.tables["users"].values())

    def list_users_page(self, tenant_id: str, search: str, role: str, limit: int, offset: int) -> dict[str, Any]:
        active = [row for row in self.list("users", tenant_id) if row.get("active")]
        term = search.casefold()
        matched = [row for row in active if (not role or row.get("role") == role) and (not term or term in str(row.get("loginId", "")).casefold() or term in str(row.get("displayName", "")).casefold())]
        matched.sort(key=lambda row: (str(row.get("displayName", "")).casefold(), row["id"]))
        return {"items": matched[offset:offset + limit], "total": len(matched), "activeCount": len(active), "adminCount": sum(row.get("role") == "admin" for row in active)}

    def list_user_audit_page(self, tenant_id: str, action: str, limit: int, offset: int) -> dict[str, Any]:
        rows = [row for row in self.list("audit_logs", tenant_id) if row.get("resourceType") == "user" and (not action or row.get("action") == action)]
        rows.sort(key=lambda row: (str(row.get("createdAt", "")), row["id"]), reverse=True)
        return {"items": rows[offset:offset + limit], "total": len(rows)}

    def get(self, table: str, item_id: str) -> dict[str, Any] | None:
        return self.tables[table].get(item_id)

    def upsert(self, table: str, item: dict[str, Any]) -> dict[str, Any]:
        self.tables[table][item["id"]] = item
        return item

    def compare_and_swap_interview_state(
        self,
        item: dict[str, Any],
        expected_state_version: int,
    ) -> bool:
        existing = self.tables["interview_states"].get(item["id"])
        if existing is None:
            return False
        current_version = int(existing.get("stateVersion", 0) or 0)
        if current_version != expected_state_version:
            return False
        self.tables["interview_states"][item["id"]] = item
        return True

    def compare_and_swap_interview_state_with_record_status_reset(
        self,
        item: dict[str, Any],
        expected_state_version: int,
        record_id: str,
        actor_user_id: str,
        updated_at: str,
    ) -> bool:
        existing = self.tables["interview_states"].get(item["id"])
        record = self.tables["records"].get(record_id)
        if existing is None or not record or record.get("status") != "approved":
            return False
        current_version = int(existing.get("stateVersion", 0) or 0)
        if current_version != expected_state_version:
            return False
        item_record = self.tables["records"][record_id]
        item_record["status"] = "submitted"
        item_record["updatedByUserId"] = actor_user_id
        item_record["updatedAt"] = updated_at
        self.tables["interview_states"][item["id"]] = item
        return True

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
    def live_capture_lock(self, key: str) -> Iterator[None]:
        # Session lock spans independent store transactions and API replicas.
        with self._connection() as connection:
            connection.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (key,))
            try:
                yield
            finally:
                connection.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))

    def pending_live_captures(self) -> list[dict[str, Any]]:
        """Internal recovery scan; tenant/user authorization is rechecked by the consumer."""
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT payload FROM kikiori.entity_store
                WHERE entity_type = 'messages' AND payload ? 'liveCaptureUser'
                  AND payload ->> 'liveCaptureApplied' = 'false'
                  AND COALESCE((payload ->> 'liveCaptureRetryAt')::double precision, 0) <= %s
                ORDER BY created_at, (payload ->> 'liveCaptureRevision')::integer
                LIMIT 100""", (time(),),
            ).fetchall()
        return [dict(row["payload"]) for row in rows]

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[Any]]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            yield connection

    def ensure_schema(self) -> None:
        with self._connection() as connection:
            for statement in POSTGRES_SCHEMA_STATEMENTS:
                connection.execute(statement)

    def ensure_initial_application_settings_admin(self) -> None:
        with self._connection() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("application-settings-admin-bootstrap",),
            )
            rows = connection.execute(
                """SELECT entity_id, payload FROM kikiori.entity_store
                WHERE entity_type = 'users' AND payload ->> 'active' = 'true'
                  AND payload ->> 'role' = 'admin'
                ORDER BY created_at, entity_id FOR UPDATE"""
            ).fetchall()
            if any(dict(row["payload"]).get("applicationSettingsAdmin") for row in rows):
                return
            if not rows:
                return
            row = rows[0]
            item = dict(row["payload"])
            item["applicationSettingsAdmin"] = True
            connection.execute(
                """UPDATE kikiori.entity_store SET payload = %s, updated_at = CURRENT_TIMESTAMP
                WHERE entity_type = 'users' AND entity_id = %s""",
                (Jsonb(item), row["entity_id"]),
            )

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

    def list_all_users(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT payload FROM kikiori.entity_store
                WHERE entity_type = 'users' ORDER BY created_at, entity_id"""
            ).fetchall()
        return [dict(row["payload"]) for row in rows]

    def list_users_page(self, tenant_id: str, search: str, role: str, limit: int, offset: int) -> dict[str, Any]:
        where = """entity_type = 'users' AND tenant_id = %s AND payload ->> 'active' = 'true'
            AND (%s = '' OR payload ->> 'role' = %s)
            AND (%s = '' OR strpos(lower(COALESCE(payload ->> 'loginId', '')), lower(%s)) > 0
                OR strpos(lower(COALESCE(payload ->> 'displayName', '')), lower(%s)) > 0)"""
        params = (tenant_id, role, role, search, search, search)
        with self._connection() as connection:
            total = connection.execute(f"SELECT count(*) AS count FROM kikiori.entity_store WHERE {where}", params).fetchone()["count"]
            counts = connection.execute("""SELECT count(*) AS active_count, count(*) FILTER (WHERE payload ->> 'role' = 'admin') AS admin_count
                FROM kikiori.entity_store WHERE entity_type = 'users' AND tenant_id = %s AND payload ->> 'active' = 'true'""", (tenant_id,)).fetchone()
            rows = connection.execute(f"SELECT payload FROM kikiori.entity_store WHERE {where} ORDER BY lower(payload ->> 'displayName'), entity_id LIMIT %s OFFSET %s", (*params, limit, offset)).fetchall()
        return {"items": [dict(row["payload"]) for row in rows], "total": int(total), "activeCount": int(counts["active_count"]), "adminCount": int(counts["admin_count"])}

    def list_user_audit_page(self, tenant_id: str, action: str, limit: int, offset: int) -> dict[str, Any]:
        with self._connection() as connection:
            total = connection.execute("SELECT count(*) AS count FROM kikiori.entity_store WHERE entity_type = 'audit_logs' AND tenant_id = %s AND payload ->> 'resourceType' = 'user' AND (%s = '' OR payload ->> 'action' = %s)", (tenant_id, action, action)).fetchone()["count"]
            rows = connection.execute("""SELECT payload FROM kikiori.entity_store WHERE entity_type = 'audit_logs' AND tenant_id = %s AND payload ->> 'resourceType' = 'user'
                AND (%s = '' OR payload ->> 'action' = %s) ORDER BY created_at DESC, entity_id DESC LIMIT %s OFFSET %s""", (tenant_id, action, action, limit, offset)).fetchall()
        return {"items": [dict(row["payload"]) for row in rows], "total": int(total)}

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

    def compare_and_swap_interview_state(
        self,
        item: dict[str, Any],
        expected_state_version: int,
    ) -> bool:
        with self._connection() as connection:
            result = connection.execute(
                """
                UPDATE kikiori.entity_store
                SET tenant_id = %s,
                    payload = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE entity_type = 'interview_states'
                  AND entity_id = %s
                  AND COALESCE((payload ->> 'stateVersion')::integer, 0) = %s
                """,
                (
                    item["tenantId"],
                    Jsonb(item),
                    item["id"],
                    expected_state_version,
                ),
            )
        return result.rowcount == 1

    def compare_and_swap_interview_state_with_record_status_reset(
        self,
        item: dict[str, Any],
        expected_state_version: int,
        record_id: str,
        actor_user_id: str,
        updated_at: str,
    ) -> bool:
        with self._connection() as connection:
            result = connection.execute(
                """UPDATE kikiori.entity_store
                SET tenant_id = %s, payload = %s, updated_at = CURRENT_TIMESTAMP
                WHERE entity_type = 'interview_states' AND entity_id = %s
                  AND COALESCE((payload ->> 'stateVersion')::integer, 0) = %s""",
                (item["tenantId"], Jsonb(item), item["id"], expected_state_version),
            )
            if result.rowcount != 1:
                connection.rollback()
                return False
            record_result = connection.execute(
                """UPDATE kikiori.entity_store
                SET payload = jsonb_set(
                    jsonb_set(
                        jsonb_set(payload, '{status}', to_jsonb('submitted'::text)),
                        '{updatedByUserId}', to_jsonb(%s::text)
                    ),
                    '{updatedAt}', to_jsonb(%s::text)
                ), updated_at = CURRENT_TIMESTAMP
                WHERE entity_type = 'records' AND entity_id = %s
                  AND payload ->> 'status' = 'approved'""",
                (actor_user_id, updated_at, record_id),
            )
            if record_result.rowcount != 1:
                connection.rollback()
                return False
        return True

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
