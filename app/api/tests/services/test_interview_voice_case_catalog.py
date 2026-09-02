from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
CASE_CATALOG = (
    REPO_ROOT
    / "app"
    / "api"
    / "tests"
    / "fixtures"
    / "interview_voice_critical_cases.json"
)


def test_voice_critical_case_catalog_is_machine_readable_and_grounded() -> None:
    catalog = json.loads(CASE_CATALOG.read_text(encoding="utf-8"))
    assert catalog["schema_version"] == 1
    cases = catalog["cases"]
    assert cases
    assert len({case["id"] for case in cases}) == len(cases)

    source_document = REPO_ROOT / catalog["source_document"]
    source_text = source_document.read_text(encoding="utf-8")
    for document in catalog["suite_documents"]:
        assert (REPO_ROOT / document).is_file()
    allowed_profiles = {"fixed_form", "business_process", "system_requirement"}

    for case in cases:
        assert case["profile"] in allowed_profiles
        assert case["execution"] == "deterministic_api"
        assert case["audio"]["file"] is None
        assert case["id"] in source_text
        assert case["transcript"] in source_text
        assert case["expected"]


def test_voice_case_catalog_covers_all_critical_general_cases() -> None:
    catalog = json.loads(CASE_CATALOG.read_text(encoding="utf-8"))
    case_ids = {case["id"] for case in catalog["cases"]}
    assert case_ids == {
        "GEN-005",
        "GEN-006",
        "GEN-009",
        "GEN-012",
        "GEN-014",
        "GEN-017",
        "GEN-025",
    }
