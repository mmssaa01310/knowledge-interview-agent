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
    store.upsert(
        "knowledge_fields",
        {
            "id": "dev-voice-demo-field-hobby",
            "tenantId": DEV_TENANT_ID,
            "knowledgeId": VOICE_DEMO_KNOWLEDGE_ID,
            "name": "趣味",
        },
    )
    store.upsert(
        "knowledge_fields",
        {
            "id": "dev-voice-demo-field-role",
            "tenantId": DEV_TENANT_ID,
            "knowledgeId": VOICE_DEMO_KNOWLEDGE_ID,
            "name": "担当業務",
        },
    )
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
    assert [item["name"] for item in fields] == [
        "基本プロフィール",
        "現在の役割",
        "経験・転機",
        "強み・成果",
        "課題・改善",
        "今後の目標",
    ]
    assert "趣味" not in [item["name"] for item in fields]
    assert "担当業務" not in [item["name"] for item in fields]
    assert all(item["inputType"] == "long_text" for item in fields)
    assert [item["aiQuestionExamples"][0] for item in fields] == [
        "お名前、所属部署、役職または担当領域を教えてください。",
        "現在の役割と、日々どのような責任を担っているかを具体的に教えてください。",
        "これまでの経験の中で、現在の仕事の進め方や専門性に大きく影響した出来事・転機を教えてください。",
        "ご自身の強みや専門性が実際の業務で発揮され、成果につながった具体的な事例を教えてください。",
        "現在の仕事で課題と感じていることと、その課題に対して実施している改善を教えてください。",
        "今後取り組みたいテーマや身につけたい能力、実現に向けて必要な支援を教えてください。",
    ]


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
