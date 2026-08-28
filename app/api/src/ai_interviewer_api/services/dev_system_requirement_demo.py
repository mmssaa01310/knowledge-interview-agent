"""
Role:
    ローカル開発用のシステム要件インタビューデータを準備する。

Summary:
    システム要件ProfileのKnowledgeと、受注実績CSV出力要望のRecordを固定IDで冪等に作成する。

Relations:
    Uses domain models and the in-memory store. Used by API startup and dev tools.
"""

from __future__ import annotations

from ai_interviewer_api.models.domain import InterviewRecord, Knowledge, KnowledgeDb
from ai_interviewer_api.models.interview_plan import InterviewPlan
from ai_interviewer_api.repositories.store import store

DEV_TENANT_ID = "tenant-demo"
DEV_USER_ID = "user-manager"
DEV_INTERVIEWEE_USER_ID = "user-interviewer"
SYSTEM_REQUIREMENT_DEMO_DB_ID = "dev-system-requirement-demo-db"
SYSTEM_REQUIREMENT_DEMO_KNOWLEDGE_ID = "dev-system-requirement-demo-knowledge"
SYSTEM_REQUIREMENT_DEMO_RECORD_ID = "dev-system-requirement-demo-record"


def ensure_dev_system_requirement_demo() -> dict[str, str]:
    if store.get("knowledge_dbs", SYSTEM_REQUIREMENT_DEMO_DB_ID) is None:
        store.upsert(
            "knowledge_dbs",
            KnowledgeDb(
                id=SYSTEM_REQUIREMENT_DEMO_DB_ID,
                tenantId=DEV_TENANT_ID,
                createdByUserId=DEV_USER_ID,
                updatedByUserId=DEV_USER_ID,
                name="システム要件インタビューDB",
                description="業務上の要望からシステム要件と処理の流れを整理する",
            ).model_dump(),
        )

    if store.get("knowledges", SYSTEM_REQUIREMENT_DEMO_KNOWLEDGE_ID) is None:
        store.upsert(
            "knowledges",
            Knowledge(
                id=SYSTEM_REQUIREMENT_DEMO_KNOWLEDGE_ID,
                tenantId=DEV_TENANT_ID,
                createdByUserId=DEV_USER_ID,
                updatedByUserId=DEV_USER_ID,
                knowledgeDbId=SYSTEM_REQUIREMENT_DEMO_DB_ID,
                name="受注実績CSV出力要件",
                description="営業担当の受注実績検索・CSV出力要望を要件化する",
                purpose="システム開発要望のインタビュー動作確認",
                category="システム要件",
                targetBusiness="営業",
                language="ja",
                defaultModelId="apac.amazon.nova-pro-v1:0",
                interviewPlan=InterviewPlan(
                    profile="system_requirement",
                    modelId="global.openai.gpt-5.6-terra",
                ),
            ).model_dump(),
        )

    if store.get("records", SYSTEM_REQUIREMENT_DEMO_RECORD_ID) is None:
        store.upsert(
            "records",
            InterviewRecord(
                id=SYSTEM_REQUIREMENT_DEMO_RECORD_ID,
                tenantId=DEV_TENANT_ID,
                createdByUserId=DEV_USER_ID,
                updatedByUserId=DEV_USER_ID,
                knowledgeId=SYSTEM_REQUIREMENT_DEMO_KNOWLEDGE_ID,
                knowledgeName="受注実績CSV出力要件",
                title="受注実績をCSVで取得する機能",
                ownerUserId=DEV_INTERVIEWEE_USER_ID,
                status="in_progress",
                targetProcess="受注実績検索・CSV出力",
            ).model_dump(),
        )

    return _demo_identifiers()


def reset_dev_system_requirement_demo() -> dict[str, str]:
    identifiers = ensure_dev_system_requirement_demo()
    voice_session_ids = {
        item["id"]
        for item in store.list("voice_sessions", DEV_TENANT_ID)
        if item.get("recordId") == SYSTEM_REQUIREMENT_DEMO_RECORD_ID
    }
    scoped_tables = (
        "messages",
        "proposals",
        "interview_states",
        "voice_sessions",
        "voice_turns",
        "voice_assistant_events",
        "voice_connection_events",
    )
    for table_name in scoped_tables:
        for item in list(store.list(table_name, DEV_TENANT_ID)):
            if (
                item.get("recordId") == SYSTEM_REQUIREMENT_DEMO_RECORD_ID
                or item.get("voiceSessionId") in voice_session_ids
            ):
                store.delete(table_name, item["id"])
    return identifiers


def _demo_identifiers() -> dict[str, str]:
    return {
        "knowledgeDbId": SYSTEM_REQUIREMENT_DEMO_DB_ID,
        "knowledgeId": SYSTEM_REQUIREMENT_DEMO_KNOWLEDGE_ID,
        "recordId": SYSTEM_REQUIREMENT_DEMO_RECORD_ID,
    }
