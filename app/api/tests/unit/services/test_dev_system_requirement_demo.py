from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.services.dev_system_requirement_demo import (
    DEV_TENANT_ID,
    SYSTEM_REQUIREMENT_DEMO_KNOWLEDGE_ID,
    SYSTEM_REQUIREMENT_DEMO_RECORD_ID,
    ensure_dev_system_requirement_demo,
    reset_dev_system_requirement_demo,
)


def setup_function() -> None:
    store.tables.clear()


def test_ensure_dev_system_requirement_demo_creates_browser_test_data() -> None:
    identifiers = ensure_dev_system_requirement_demo()

    assert identifiers["knowledgeId"] == SYSTEM_REQUIREMENT_DEMO_KNOWLEDGE_ID
    assert identifiers["recordId"] == SYSTEM_REQUIREMENT_DEMO_RECORD_ID
    knowledge = store.get("knowledges", SYSTEM_REQUIREMENT_DEMO_KNOWLEDGE_ID)
    assert knowledge["interviewPlan"] == {
        "version": 1,
        "purpose": None,
        "profile": "system_requirement",
        "modelId": "global.openai.gpt-5.6-terra",
        "interviewLocale": None,
    }
    assert store.get("records", SYSTEM_REQUIREMENT_DEMO_RECORD_ID)["targetProcess"] == "受注実績検索・CSV出力"
    assert [
        field
        for field in store.list("knowledge_fields", DEV_TENANT_ID)
        if field.get("knowledgeId") == SYSTEM_REQUIREMENT_DEMO_KNOWLEDGE_ID
    ] == []


def test_reset_dev_system_requirement_demo_removes_only_conversation_state() -> None:
    ensure_dev_system_requirement_demo()
    store.upsert(
        "interview_states",
        {
            "id": f"interview-state-{SYSTEM_REQUIREMENT_DEMO_RECORD_ID}",
            "tenantId": DEV_TENANT_ID,
            "recordId": SYSTEM_REQUIREMENT_DEMO_RECORD_ID,
        },
    )
    store.upsert(
        "messages",
        {
            "id": "system-requirement-demo-message",
            "tenantId": DEV_TENANT_ID,
            "recordId": SYSTEM_REQUIREMENT_DEMO_RECORD_ID,
        },
    )

    reset_dev_system_requirement_demo()

    assert store.get("records", SYSTEM_REQUIREMENT_DEMO_RECORD_ID) is not None
    assert store.get(
        "knowledges",
        SYSTEM_REQUIREMENT_DEMO_KNOWLEDGE_ID,
    ) is not None
    assert store.get(
        "interview_states",
        f"interview-state-{SYSTEM_REQUIREMENT_DEMO_RECORD_ID}",
    ) is None
    assert store.get("messages", "system-requirement-demo-message") is None
