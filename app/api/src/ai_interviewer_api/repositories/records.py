from ai_interviewer_api.repositories.store import store


def list_for_knowledge(tenant_id: str, knowledge_id: str) -> list[dict]:
    return [row for row in store.list("records", tenant_id) if row["knowledgeId"] == knowledge_id]


def save(item: dict) -> dict:
    return store.upsert("records", item)


def delete(record_id: str) -> bool:
    return store.delete("records", record_id)
