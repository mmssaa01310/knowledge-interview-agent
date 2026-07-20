from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.services.dev_maintenance_demo import (
    DEV_TENANT_ID,
    MAINTENANCE_DEMO_KNOWLEDGE_ID,
    MAINTENANCE_DEMO_RECORD_ID,
    ensure_dev_maintenance_demo,
)


def setup_function() -> None:
    store.tables.clear()


def test_ensure_dev_maintenance_demo_creates_interview_ready_data() -> None:
    identifiers = ensure_dev_maintenance_demo()

    assert identifiers["recordId"] == MAINTENANCE_DEMO_RECORD_ID
    assert store.get("records", MAINTENANCE_DEMO_RECORD_ID)["targetEquipment"] == "圧入機A"
    fields = [
        item
        for item in store.list("knowledge_fields", DEV_TENANT_ID)
        if item.get("knowledgeId") == MAINTENANCE_DEMO_KNOWLEDGE_ID
    ]
    assert [item["name"] for item in fields] == [
        "設備名",
        "現象・症状",
        "発生条件",
        "原因",
        "対処方法",
        "復旧判断基準",
        "再発防止",
    ]
    assert all(item["askByAi"] for item in fields)
    assert all(item["aiQuestionExamples"] for item in fields)


def test_ensure_dev_maintenance_demo_does_not_overwrite_existing_record() -> None:
    ensure_dev_maintenance_demo()
    record = store.get("records", MAINTENANCE_DEMO_RECORD_ID)
    record["summary"] = "入力済みの保全記録"
    store.upsert("records", record)

    ensure_dev_maintenance_demo()

    assert store.get("records", MAINTENANCE_DEMO_RECORD_ID)["summary"] == "入力済みの保全記録"
