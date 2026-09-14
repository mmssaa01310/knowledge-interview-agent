import os
from uuid import uuid4

import pytest
from psycopg import OperationalError

from ai_interviewer_api.repositories.store import PostgresStore


@pytest.mark.integration
def test_postgres_store_persists_and_scopes_entities() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to run the PostgreSQL integration test.")

    repository = PostgresStore(database_url)
    try:
        repository.ensure_schema()
    except OperationalError as error:
        pytest.skip(f"PostgreSQL is unavailable: {error}")

    entity_id = f"postgres-store-test-{uuid4()}"
    tenant_id = f"tenant-{uuid4()}"
    item = {
        "id": entity_id,
        "tenantId": tenant_id,
        "createdByUserId": "test-user",
        "updatedByUserId": "test-user",
        "name": "PostgreSQL integration test",
    }
    try:
        assert repository.upsert("knowledges", item) == item
        assert repository.get("knowledges", entity_id) == item
        assert repository.list("knowledges", tenant_id) == [item]
        assert repository.list("knowledges", "another-tenant") == []
        assert repository.count("knowledges") >= 1
    finally:
        repository.delete("knowledges", entity_id)

    assert repository.get("knowledges", entity_id) is None
