import os
from pathlib import Path

import pytest

# API tests use the deterministic in-memory Store by default. PostgreSQL is
# covered by the opt-in persistence integration test using TEST_DATABASE_URL.
os.environ["DATABASE_URL"] = "memory://test"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Assign exactly one test-level marker from the test directory."""

    tests_root = Path(__file__).parent
    for item in items:
        path = Path(str(item.path))
        relative = path.relative_to(tests_root)
        parts = relative.parts
        if "e2e" in parts:
            item.add_marker(pytest.mark.e2e)
        elif "integration" in parts:
            item.add_marker(pytest.mark.integration)
        else:
            item.add_marker(pytest.mark.unit)


@pytest.fixture
def clean_interview_store() -> None:
    """Reset the configured test store for cross-component scenarios."""

    from ai_interviewer_api.repositories.store import store

    store.tables.clear()
