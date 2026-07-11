from collections import defaultdict


class InMemoryStore:
    def __init__(self) -> None:
        self.tables: dict[str, dict[str, dict]] = defaultdict(dict)

    def list(self, table: str, tenant_id: str) -> list[dict]:
        return [row for row in self.tables[table].values() if row["tenantId"] == tenant_id]

    def get(self, table: str, item_id: str) -> dict | None:
        return self.tables[table].get(item_id)

    def upsert(self, table: str, item: dict) -> dict:
        self.tables[table][item["id"]] = item
        return item

    def delete(self, table: str, item_id: str) -> bool:
        return self.tables[table].pop(item_id, None) is not None


store = InMemoryStore()
