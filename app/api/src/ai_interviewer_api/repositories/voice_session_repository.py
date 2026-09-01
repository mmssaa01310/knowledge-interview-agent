from threading import Lock

from ai_interviewer_api.repositories.store import store


TABLE = "voice_sessions"
_INITIAL_REPLY_CLAIM_LOCK = Lock()


def get(session_id: str) -> dict | None:
    return store.get(TABLE, session_id)


def save(item: dict) -> dict:
    return store.upsert(TABLE, item)


def claim_initial_reply(session_id: str) -> dict | None:
    """Atomically claim an initial reply for one session.

    Reconnects can reach separate API requests at the same time. The
    PostgreSQL path uses a conditional UPDATE; the in-memory test store uses
    the same critical section semantics locally.
    """

    connection_factory = getattr(store, "_connection", None)
    if callable(connection_factory):
        with connection_factory() as connection:
            row = connection.execute(
                """
                UPDATE kikiori.entity_store
                SET payload = jsonb_set(
                    payload,
                    '{initialReplyStatus}',
                    '"sending"'::jsonb,
                    true
                ),
                    updated_at = CURRENT_TIMESTAMP
                WHERE entity_type = %s
                  AND entity_id = %s
                  AND payload ? 'initialReplyText'
                  AND NULLIF(payload ->> 'initialReplyText', '') IS NOT NULL
                  AND payload ->> 'initialReplyStatus' IN ('pending', 'failed_retryable')
                  AND payload ->> 'initialQuestionId' = payload ->> 'currentQuestionId'
                  AND COALESCE(payload ->> 'status', 'active') NOT IN ('stopped', 'completed')
                RETURNING payload
                """,
                (TABLE, session_id),
            ).fetchone()
        return dict(row["payload"]) if row else None

    with _INITIAL_REPLY_CLAIM_LOCK:
        session = get(session_id)
        if not session:
            return None
        if (
            session.get("initialReplyText")
            and session.get("initialReplyStatus") in {"pending", "failed_retryable"}
            and session.get("initialQuestionId") == session.get("currentQuestionId")
            and session.get("status") not in {"stopped", "completed"}
        ):
            session["initialReplyStatus"] = "sending"
            save(session)
            return session
    return None


def list_for_record(tenant_id: str, record_id: str) -> list[dict]:
    return [row for row in store.list(TABLE, tenant_id) if row.get("recordId") == record_id]
