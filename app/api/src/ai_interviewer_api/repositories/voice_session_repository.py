from ai_interviewer_api.repositories.store import store


TABLE = "voice_sessions"


def get(session_id: str) -> dict | None:
    return store.get(TABLE, session_id)


def save(item: dict) -> dict:
    return store.upsert(TABLE, item)


def list_for_record(tenant_id: str, record_id: str) -> list[dict]:
    return [row for row in store.list(TABLE, tenant_id) if row.get("recordId") == record_id]
