from ai_interviewer_api.repositories.store import store


def list_for_tenant(tenant_id: str) -> list[dict]:
    return store.list("knowledge_dbs", tenant_id)


def save(item: dict) -> dict:
    return store.upsert("knowledge_dbs", item)


def delete(knowledge_db_id: str) -> bool:
    return store.delete("knowledge_dbs", knowledge_db_id)
