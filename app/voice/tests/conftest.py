from pathlib import Path

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Assign exactly one test-level marker from the test directory."""

    tests_root = Path(__file__).parent
    for item in items:
        relative = Path(str(item.path)).relative_to(tests_root)
        parts = relative.parts
        if "e2e" in parts:
            item.add_marker(pytest.mark.e2e)
        elif "integration" in parts:
            item.add_marker(pytest.mark.integration)
        else:
            item.add_marker(pytest.mark.unit)
