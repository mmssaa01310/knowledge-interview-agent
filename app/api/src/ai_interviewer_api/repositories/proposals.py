from ai_interviewer_api.repositories.store import store


def list_for_record(tenant_id: str, record_id: str) -> list[dict]:
    return [row for row in store.list("proposals", tenant_id) if row["recordId"] == record_id]


def save(item: dict) -> dict:
    return store.upsert("proposals", item)
