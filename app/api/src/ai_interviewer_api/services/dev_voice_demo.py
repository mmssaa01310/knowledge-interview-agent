"""
Role:
    ローカル開発用の音声インタビューデータを準備する。

Summary:
    固定IDの質問項目と記録を冪等に作成し、会話状態だけを初期化する。

Relations:
    Uses domain models and the in-memory store. Used by API startup and dev tools.
"""

from __future__ import annotations

from ai_interviewer_api.models.domain import (
    InterviewRecord,
    Knowledge,
    KnowledgeDb,
    KnowledgeField,
)
from ai_interviewer_api.models.interview_plan import InterviewPlan
from ai_interviewer_api.repositories.store import store

DEV_TENANT_ID = "tenant-demo"
DEV_USER_ID = "user-manager"
VOICE_DEMO_DB_ID = "dev-voice-demo-db"
VOICE_DEMO_KNOWLEDGE_ID = "dev-voice-demo-knowledge"
VOICE_DEMO_RECORD_ID = "dev-voice-demo-record"


def ensure_dev_voice_demo() -> dict[str, str]:
    if store.get("knowledge_dbs", VOICE_DEMO_DB_ID) is None:
        store.upsert(
            "knowledge_dbs",
            KnowledgeDb(
                id=VOICE_DEMO_DB_ID,
                tenantId=DEV_TENANT_ID,
                createdByUserId=DEV_USER_ID,
                updatedByUserId=DEV_USER_ID,
                name="音声インタビュー動作確認",
                description="ブラウザからすぐに会話フローを確認するための開発用データ",
            ).model_dump(),
        )

    if store.get("knowledges", VOICE_DEMO_KNOWLEDGE_ID) is None:
        store.upsert(
            "knowledges",
            Knowledge(
                id=VOICE_DEMO_KNOWLEDGE_ID,
                tenantId=DEV_TENANT_ID,
                createdByUserId=DEV_USER_ID,
                updatedByUserId=DEV_USER_ID,
                knowledgeDbId=VOICE_DEMO_DB_ID,
                name="人物ヒアリング",
                description="自己紹介、趣味、担当業務を順番に確認する",
                purpose="リアルタイム音声インタビューの動作確認",
                language="ja",
                defaultModelId="apac.amazon.nova-pro-v1:0",
                interviewPlan=InterviewPlan(
                    profile="fixed_form",
                    modelId="global.openai.gpt-5.6-terra",
                ),
            ).model_dump(),
        )

    fields = [
        KnowledgeField(
            id="dev-voice-demo-field-self-introduction",
            tenantId=DEV_TENANT_ID,
            createdByUserId=DEV_USER_ID,
            updatedByUserId=DEV_USER_ID,
            knowledgeId=VOICE_DEMO_KNOWLEDGE_ID,
            name="自己紹介",
            description="氏名が回答されれば十分とする。",
            inputType="short_text",
            required=True,
            askByAi=True,
            retrievalPolicy="never",
            aiQuestionExamples=["自己紹介をお願いします。"],
            displayOrder=1,
        ),
        KnowledgeField(
            id="dev-voice-demo-field-hobby",
            tenantId=DEV_TENANT_ID,
            createdByUserId=DEV_USER_ID,
            updatedByUserId=DEV_USER_ID,
            knowledgeId=VOICE_DEMO_KNOWLEDGE_ID,
            name="趣味",
            description="具体的な趣味が1つ回答されれば十分とする。",
            inputType="short_text",
            required=True,
            askByAi=True,
            retrievalPolicy="never",
            aiQuestionExamples=["具体的な趣味を教えてください。"],
            displayOrder=2,
        ),
        KnowledgeField(
            id="dev-voice-demo-field-role",
            tenantId=DEV_TENANT_ID,
            createdByUserId=DEV_USER_ID,
            updatedByUserId=DEV_USER_ID,
            knowledgeId=VOICE_DEMO_KNOWLEDGE_ID,
            name="担当業務",
            description="現在の担当業務が回答されれば十分とする。",
            inputType="short_text",
            required=True,
            askByAi=True,
            retrievalPolicy="never",
            aiQuestionExamples=["現在の担当業務を教えてください。"],
            displayOrder=3,
        ),
    ]
    for field in fields:
        if store.get("knowledge_fields", field.id) is None:
            store.upsert("knowledge_fields", field.model_dump())

    if store.get("records", VOICE_DEMO_RECORD_ID) is None:
        store.upsert(
            "records",
            InterviewRecord(
                id=VOICE_DEMO_RECORD_ID,
                tenantId=DEV_TENANT_ID,
                createdByUserId=DEV_USER_ID,
                updatedByUserId=DEV_USER_ID,
                knowledgeId=VOICE_DEMO_KNOWLEDGE_ID,
                knowledgeName="人物ヒアリング",
                title="音声会話テスト",
            ).model_dump(),
        )

    return _demo_identifiers()


def reset_dev_voice_demo() -> dict[str, str]:
    identifiers = ensure_dev_voice_demo()
    voice_session_ids = {
        item["id"]
        for item in store.list("voice_sessions", DEV_TENANT_ID)
        if item.get("recordId") == VOICE_DEMO_RECORD_ID
    }
    scoped_tables = (
        "messages",
        "proposals",
        "voice_sessions",
        "voice_turns",
        "voice_assistant_events",
        "voice_connection_events",
    )
    for table_name in scoped_tables:
        for item in list(store.list(table_name, DEV_TENANT_ID)):
            if (
                item.get("recordId") == VOICE_DEMO_RECORD_ID
                or item.get("voiceSessionId") in voice_session_ids
            ):
                store.delete(table_name, item["id"])
    store.delete("interview_states", f"interview-state-{VOICE_DEMO_RECORD_ID}")
    return identifiers


def _demo_identifiers() -> dict[str, str]:
    return {
        "knowledgeDbId": VOICE_DEMO_DB_ID,
        "knowledgeId": VOICE_DEMO_KNOWLEDGE_ID,
        "recordId": VOICE_DEMO_RECORD_ID,
    }
