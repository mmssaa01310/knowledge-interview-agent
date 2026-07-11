from ai_interviewer_api.repositories.store import store


def get_item(table: str, item_id: str) -> dict | None:
    return store.get(table, item_id)
