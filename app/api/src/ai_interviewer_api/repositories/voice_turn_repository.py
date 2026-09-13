from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.repositories.store import PostgresStore, store


TABLE = "voice_turns"


def get(turn_id: str) -> dict | None:
    return store.get(TABLE, turn_id)


def save(item: dict) -> dict:
    return store.upsert(TABLE, item)


def list_for_session(tenant_id: str, voice_session_id: str) -> list[dict]:
    return [
        row
        for row in store.list(TABLE, tenant_id)
        if row.get("voiceSessionId") == voice_session_id
    ]


def find_by_client_turn_id(
    tenant_id: str,
    voice_session_id: str,
    client_turn_id: str,
) -> dict | None:
    """Find the durable canonical turn for one provider source turn.

    The PostgreSQL unique index is the race-safe boundary.  This lookup is
    intentionally repository-owned so reconnects do not need the runtime's
    in-memory processed-item set to recover an existing turn.
    """

    normalized_client_turn_id = str(client_turn_id or "").strip()
    if not normalized_client_turn_id:
        return None
    if isinstance(store, PostgresStore):
        with store._connection() as connection:
            row = connection.execute(
                """
                SELECT payload
                FROM kikiori.entity_store
                WHERE entity_type = %s
                  AND tenant_id = %s
                  AND payload ->> 'voiceSessionId' = %s
                  AND payload ->> 'clientTurnId' = %s
                ORDER BY created_at, entity_id
                LIMIT 1
                """,
                (TABLE, tenant_id, voice_session_id, normalized_client_turn_id),
            ).fetchone()
        return dict(row["payload"]) if row else None
    return next(
        (
            row
            for row in store.list(TABLE, tenant_id)
            if row.get("voiceSessionId") == voice_session_id
            and row.get("clientTurnId") == normalized_client_turn_id
        ),
        None,
    )


def rebase_received_turn_state_version(
    turn_id: str,
    state_version: int,
    updated_at: str,
) -> dict | None:
    """Update a received turn's optimistic version without reclaiming it.

    This is used only after the caller has verified that the same durable
    client turn still targets the current question. PostgreSQL applies the
    lifecycle predicate atomically so a concurrent processor cannot be
    overwritten back to RECEIVED.
    """

    if isinstance(store, PostgresStore):
        with store._connection() as connection:
            row = connection.execute(
                """
                UPDATE kikiori.entity_store
                SET payload = jsonb_set(
                    jsonb_set(
                        payload,
                        '{expectedStateVersion}',
                        to_jsonb(CAST(%s AS integer)),
                        true
                    ),
                    '{updatedAt}',
                    to_jsonb(CAST(%s AS text)),
                    true
                ), updated_at = CURRENT_TIMESTAMP
                WHERE entity_type = %s
                  AND entity_id = %s
                  AND COALESCE(payload ->> 'lifecycleStatus', 'RECEIVED') = 'RECEIVED'
                RETURNING payload
                """,
                (state_version, updated_at, TABLE, turn_id),
            ).fetchone()
        return dict(row["payload"]) if row else None

    turn = store.get(TABLE, turn_id)
    if turn is None:
        return None
    if turn.get("lifecycleStatus") not in (None, "RECEIVED"):
        return None
    if turn.get("processingStatus") == "processing":
        return None
    turn["expectedStateVersion"] = state_version
    turn["updatedAt"] = updated_at
    return store.upsert(TABLE, turn)


def claim_processing(turn_id: str, processing_id: str) -> dict | None:
    """Atomically claim a received turn for processing.

    PostgreSQL performs the lifecycle transition in one conditional UPDATE so
    two API workers cannot both run the same turn. The in-memory store is
    protected by the service's per-turn lock and follows the same contract.
    """

    if isinstance(store, PostgresStore):
        with store._connection() as connection:
            row = connection.execute(
                """
                UPDATE kikiori.entity_store
                SET payload = jsonb_set(
                    jsonb_set(
                        jsonb_set(
                            payload,
                            '{processingStatus}',
                            '"processing"'::jsonb,
                            true
                        ),
                        '{lifecycleStatus}',
                        '"EVALUATING"'::jsonb,
                        true
                    ),
                    '{processingId}',
                    to_jsonb(CAST(%s AS text)),
                    true
                ),
                    updated_at = CURRENT_TIMESTAMP
                WHERE entity_type = %s
                  AND entity_id = %s
                  AND COALESCE(payload ->> 'lifecycleStatus', 'RECEIVED') = 'RECEIVED'
                RETURNING payload
                """,
                (processing_id, TABLE, turn_id),
            ).fetchone()
        return dict(row["payload"]) if row else None

    turn = store.get(TABLE, turn_id)
    if turn is None or turn.get("lifecycleStatus") not in (None, "RECEIVED"):
        return None
    turn["processingStatus"] = "processing"
    turn["lifecycleStatus"] = "EVALUATING"
    turn["processingId"] = processing_id
    turn["updatedAt"] = utc_now()
    return store.upsert(TABLE, turn)
