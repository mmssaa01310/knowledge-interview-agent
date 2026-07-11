from ai_interviewer_api.repositories.store import store


def save(item: dict) -> dict:
    return store.upsert("audit_logs", item)
