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


def test_live_receipts_are_recoverable_from_a_new_repository_instance() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to run the PostgreSQL integration test.")
    repository = PostgresStore(database_url)
    repository.ensure_schema()
    receipt_id = f"live-receipt-test-{uuid4()}"
    receipt = {
        "id": receipt_id, "tenantId": f"tenant-{uuid4()}", "recordId": "test-record",
        "liveCaptureId": "test-capture", "liveCaptureRevision": 1,
        "liveCaptureApplied": False, "liveCaptureUser": {"user_id": "test-user"},
    }
    try:
        with repository.live_capture_lock(receipt_id):
            repository.upsert("messages", receipt)
        restarted = PostgresStore(database_url)
        assert restarted.get("messages", receipt_id) == receipt
        assert any(row["id"] == receipt_id for row in restarted.pending_live_captures())
        restarted.upsert("messages", {**receipt, "liveCaptureApplied": True})
        assert not any(row["id"] == receipt_id for row in restarted.pending_live_captures())
    finally:
        repository.delete("messages", receipt_id)
