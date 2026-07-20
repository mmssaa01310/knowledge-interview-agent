from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.services.dev_voice_demo import (
    DEV_TENANT_ID,
    VOICE_DEMO_KNOWLEDGE_ID,
    VOICE_DEMO_RECORD_ID,
    ensure_dev_voice_demo,
    reset_dev_voice_demo,
)


def setup_function() -> None:
    store.tables.clear()


def test_ensure_dev_voice_demo_is_idempotent() -> None:
    first = ensure_dev_voice_demo()
    record = store.get("records", VOICE_DEMO_RECORD_ID)
    assert record is not None
    record["title"] = "変更後のタイトル"
    store.upsert("records", record)

    second = ensure_dev_voice_demo()

    assert first == second
    assert store.get("records", VOICE_DEMO_RECORD_ID)["title"] == "変更後のタイトル"
    fields = [
        item
        for item in store.list("knowledge_fields", DEV_TENANT_ID)
        if item.get("knowledgeId") == VOICE_DEMO_KNOWLEDGE_ID
    ]
    assert [item["name"] for item in fields] == ["自己紹介", "趣味", "担当業務"]


def test_reset_dev_voice_demo_removes_only_conversation_state() -> None:
    ensure_dev_voice_demo()
    store.upsert(
        "interview_states",
        {
            "id": f"interview-state-{VOICE_DEMO_RECORD_ID}",
            "tenantId": DEV_TENANT_ID,
            "recordId": VOICE_DEMO_RECORD_ID,
        },
    )
    store.upsert(
        "messages",
        {
            "id": "demo-message",
            "tenantId": DEV_TENANT_ID,
            "recordId": VOICE_DEMO_RECORD_ID,
        },
    )
    store.upsert(
        "voice_sessions",
        {
            "id": "demo-session",
            "tenantId": DEV_TENANT_ID,
            "recordId": VOICE_DEMO_RECORD_ID,
        },
    )
    store.upsert(
        "voice_turns",
        {
            "id": "demo-turn",
            "tenantId": DEV_TENANT_ID,
            "voiceSessionId": "demo-session",
            "recordId": VOICE_DEMO_RECORD_ID,
        },
    )

    reset_dev_voice_demo()

    assert store.get("records", VOICE_DEMO_RECORD_ID) is not None
    assert store.get("interview_states", f"interview-state-{VOICE_DEMO_RECORD_ID}") is None
    assert store.get("messages", "demo-message") is None
    assert store.get("voice_sessions", "demo-session") is None
    assert store.get("voice_turns", "demo-turn") is None
