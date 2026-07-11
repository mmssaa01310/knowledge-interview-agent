from ai_interviewer_api.repositories.store import store


def create_user_message(tenant_id: str, record_id: str, content: str) -> dict:
    message = {
        "id": f"msg-{len(store.tables['messages']) + 1}",
        "tenantId": tenant_id,
        "recordId": record_id,
        "content": content,
        "role": "user",
    }
    return store.upsert("messages", message)
