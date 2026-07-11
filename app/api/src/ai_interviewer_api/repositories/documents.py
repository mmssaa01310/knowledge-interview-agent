from ai_interviewer_api.repositories.store import store


def list_for_knowledge(tenant_id: str, knowledge_id: str) -> list[dict]:
    return [row for row in store.list("documents", tenant_id) if row["knowledgeId"] == knowledge_id]


def save(item: dict) -> dict:
    return store.upsert("documents", item)
