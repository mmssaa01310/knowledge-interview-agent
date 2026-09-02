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
from ai_interviewer_api.models.interview_plan import (
    InterviewPlan,
    InterviewPlanItem,
    InterviewQuestionPlan,
)
from ai_interviewer_api.repositories.store import store

DEV_TENANT_ID = "tenant-demo"
DEV_USER_ID = "user-manager"
DEV_INTERVIEWEE_USER_ID = "user-interviewer"
VOICE_DEMO_DB_ID = "dev-voice-demo-db"
VOICE_DEMO_KNOWLEDGE_ID = "dev-voice-demo-knowledge"
VOICE_DEMO_RECORD_ID = "dev-voice-demo-record"
LEGACY_VOICE_DEMO_FIELD_IDS = {
    "dev-voice-demo-field-hobby",
    "dev-voice-demo-field-role",
}


def _question_plan(*items: tuple[str, str, str]) -> InterviewQuestionPlan:
    return InterviewQuestionPlan(
        requiredItems=[
            InterviewPlanItem(itemId=item_id, label=label, description=description)
            for item_id, label, description in items
        ]
    )


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
                name="人物インタビュー",
                description="人物の業務経験、強み、課題、今後の目標を順番に確認する",
                purpose="人物インタビューの質問設計とリアルタイム音声会話の動作確認",
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
            name="基本プロフィール",
            description="氏名、所属、役職または担当領域を確認する。",
            inputType="long_text",
            required=True,
            askByAi=True,
            retrievalPolicy="never",
            aiQuestionExamples=["お名前、所属部署、役職または担当領域を教えてください。"],
            questionPlan=_question_plan(
                ("name", "お名前", "氏名"),
                ("department", "所属部署", "所属部署"),
                ("role_or_domain", "現在の役職または担当領域", "役職または担当領域"),
            ),
            displayOrder=1,
        ),
        KnowledgeField(
            id="dev-voice-demo-field-current-role",
            tenantId=DEV_TENANT_ID,
            createdByUserId=DEV_USER_ID,
            updatedByUserId=DEV_USER_ID,
            knowledgeId=VOICE_DEMO_KNOWLEDGE_ID,
            name="現在の役割",
            description="現在担っている役割と責任範囲を具体的に確認する。",
            inputType="long_text",
            required=True,
            askByAi=True,
            retrievalPolicy="never",
            aiQuestionExamples=["現在の役割と、日々どのような責任を担っているかを具体的に教えてください。"],
            questionPlan=_question_plan(
                ("role", "現在の役割", "現在担っている役割"),
                ("responsibilities", "日々の責任", "日々担っている責任範囲"),
            ),
            displayOrder=2,
        ),
        KnowledgeField(
            id="dev-voice-demo-field-experience",
            tenantId=DEV_TENANT_ID,
            createdByUserId=DEV_USER_ID,
            updatedByUserId=DEV_USER_ID,
            knowledgeId=VOICE_DEMO_KNOWLEDGE_ID,
            name="経験・転機",
            description="現在の専門性につながる主な経験や転機を確認する。",
            inputType="long_text",
            required=True,
            askByAi=True,
            retrievalPolicy="never",
            aiQuestionExamples=["これまでの経験の中で、現在の仕事の進め方や専門性に大きく影響した出来事・転機を教えてください。"],
            questionPlan=_question_plan(
                ("experience", "現在の専門性につながる主な経験", "現在の専門性につながる主な経験"),
                ("turning_point", "大きく影響した出来事・転機", "現在の仕事の進め方や専門性に影響した出来事・転機"),
            ),
            displayOrder=3,
        ),
        KnowledgeField(
            id="dev-voice-demo-field-strengths-results",
            tenantId=DEV_TENANT_ID,
            createdByUserId=DEV_USER_ID,
            updatedByUserId=DEV_USER_ID,
            knowledgeId=VOICE_DEMO_KNOWLEDGE_ID,
            name="強み・成果",
            description="本人の強みが成果につながった具体的な事例を確認する。",
            inputType="long_text",
            required=True,
            askByAi=True,
            retrievalPolicy="never",
            aiQuestionExamples=["ご自身の強みや専門性が実際の業務で発揮され、成果につながった具体的な事例を教えてください。"],
            questionPlan=_question_plan(
                ("strength", "強みや専門性", "業務で発揮された強みや専門性"),
                ("result_example", "成果につながった具体的な事例", "強みや専門性が成果につながった具体的な事例"),
            ),
            displayOrder=4,
        ),
        KnowledgeField(
            id="dev-voice-demo-field-challenge-improvement",
            tenantId=DEV_TENANT_ID,
            createdByUserId=DEV_USER_ID,
            updatedByUserId=DEV_USER_ID,
            knowledgeId=VOICE_DEMO_KNOWLEDGE_ID,
            name="課題・改善",
            description="現在の課題と、それに対して実施している改善を確認する。",
            inputType="long_text",
            required=True,
            askByAi=True,
            retrievalPolicy="never",
            aiQuestionExamples=["現在の仕事で課題と感じていることと、その課題に対して実施している改善を教えてください。"],
            questionPlan=_question_plan(
                ("challenge", "現在の課題", "現在の仕事で課題と感じていること"),
                ("improvement", "実施している改善", "課題に対して実施している改善"),
            ),
            displayOrder=5,
        ),
        KnowledgeField(
            id="dev-voice-demo-field-future-goals",
            tenantId=DEV_TENANT_ID,
            createdByUserId=DEV_USER_ID,
            updatedByUserId=DEV_USER_ID,
            knowledgeId=VOICE_DEMO_KNOWLEDGE_ID,
            name="今後の目標",
            description="今後取り組みたいテーマや身につけたい能力を確認する。",
            inputType="long_text",
            required=True,
            askByAi=True,
            retrievalPolicy="never",
            aiQuestionExamples=["今後取り組みたいテーマや身につけたい能力、実現に向けて必要な支援を教えてください。"],
            questionPlan=_question_plan(
                ("future_theme", "今後取り組みたいテーマ", "今後取り組みたいテーマ"),
                ("skill", "身につけたい能力", "今後身につけたい能力"),
                ("support", "実現に向けて必要な支援", "目標の実現に向けて必要な支援"),
            ),
            displayOrder=6,
        ),
    ]
    for field_id in LEGACY_VOICE_DEMO_FIELD_IDS:
        legacy_field = store.get("knowledge_fields", field_id)
        if legacy_field and legacy_field.get("knowledgeId") == VOICE_DEMO_KNOWLEDGE_ID:
            store.delete("knowledge_fields", field_id)
    for field in fields:
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
                knowledgeName="人物インタビュー",
                title="音声会話テスト",
                ownerUserId=DEV_INTERVIEWEE_USER_ID,
                status="in_progress",
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
