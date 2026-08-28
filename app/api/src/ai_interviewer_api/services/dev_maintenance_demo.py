"""
Role:
    ローカル開発用の保全ナレッジ蓄積データを準備する。

Summary:
    保全ヒアリングの標準項目と動作確認用記録を固定IDで冪等に作成する。

Relations:
    Uses domain models and the in-memory store. Used by API startup.
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
MAINTENANCE_DEMO_DB_ID = "dev-maintenance-demo-db"
MAINTENANCE_DEMO_KNOWLEDGE_ID = "dev-maintenance-demo-knowledge"
MAINTENANCE_DEMO_RECORD_ID = "dev-maintenance-demo-record"


def ensure_dev_maintenance_demo() -> dict[str, str]:
    if store.get("knowledge_dbs", MAINTENANCE_DEMO_DB_ID) is None:
        store.upsert(
            "knowledge_dbs",
            KnowledgeDb(
                id=MAINTENANCE_DEMO_DB_ID,
                tenantId=DEV_TENANT_ID,
                createdByUserId=DEV_USER_ID,
                updatedByUserId=DEV_USER_ID,
                name="保全ナレッジDB",
                description="設備トラブルの現象、原因、対処、判断基準を蓄積する",
            ).model_dump(),
        )

    if store.get("knowledges", MAINTENANCE_DEMO_KNOWLEDGE_ID) is None:
        store.upsert(
            "knowledges",
            Knowledge(
                id=MAINTENANCE_DEMO_KNOWLEDGE_ID,
                tenantId=DEV_TENANT_ID,
                createdByUserId=DEV_USER_ID,
                updatedByUserId=DEV_USER_ID,
                knowledgeDbId=MAINTENANCE_DEMO_DB_ID,
                name="圧入設備の保全ノウハウ",
                description="圧入設備で発生する異常と復旧判断を構造化して蓄積する",
                purpose="保全担当者の判断根拠と再発防止策を引き継ぐ",
                category="設備保全",
                targetBusiness="保全",
                targetEquipment="圧入機A",
                language="ja",
                defaultModelId="apac.amazon.nova-pro-v1:0",
                interviewPlan=InterviewPlan(
                    profile="fixed_form",
                    modelId="global.openai.gpt-5.6-terra",
                ),
            ).model_dump(),
        )

    field_definitions = [
        (
            "equipment",
            "設備名",
            "対象設備と号機を特定する。",
            "short_text",
            "対象の設備名と号機を教えてください。",
        ),
        (
            "symptom",
            "現象・症状",
            "観測した異常を、正常時との差が分かるように記録する。",
            "long_text",
            "どのような現象や症状が起きましたか。",
        ),
        (
            "conditions",
            "発生条件",
            "発生時刻、運転状態、品種、頻度などの条件を記録する。",
            "long_text",
            "いつ、どの運転条件で発生しましたか。",
        ),
        (
            "cause",
            "原因",
            "確認できた原因と、その原因だと判断した根拠を記録する。",
            "long_text",
            "原因は何で、どの確認結果からそう判断しましたか。",
        ),
        (
            "action",
            "対処方法",
            "実施した処置を作業順序と注意点が分かる形で記録する。",
            "long_text",
            "どのような手順で対処しましたか。",
        ),
        (
            "criteria",
            "復旧判断基準",
            "正常復帰を判断した測定値、状態、確認方法を記録する。",
            "long_text",
            "復旧したと判断した基準と確認方法を教えてください。",
        ),
        (
            "prevention",
            "再発防止",
            "恒久対策、点検周期、監視項目などの再発防止策を記録する。",
            "long_text",
            "再発を防ぐために、今後どのような対応が必要ですか。",
        ),
    ]
    for display_order, definition in enumerate(field_definitions, start=1):
        suffix, name, description, input_type, question = definition
        field_id = f"dev-maintenance-demo-field-{suffix}"
        if store.get("knowledge_fields", field_id) is not None:
            continue
        store.upsert(
            "knowledge_fields",
            KnowledgeField(
                id=field_id,
                tenantId=DEV_TENANT_ID,
                createdByUserId=DEV_USER_ID,
                updatedByUserId=DEV_USER_ID,
                knowledgeId=MAINTENANCE_DEMO_KNOWLEDGE_ID,
                name=name,
                description=description,
                inputType=input_type,
                required=name not in {"再発防止"},
                askByAi=True,
                retrievalPolicy="never",
                aiQuestionExamples=[question],
                displayOrder=display_order,
            ).model_dump(),
        )

    if store.get("records", MAINTENANCE_DEMO_RECORD_ID) is None:
        store.upsert(
            "records",
            InterviewRecord(
                id=MAINTENANCE_DEMO_RECORD_ID,
                tenantId=DEV_TENANT_ID,
                createdByUserId=DEV_USER_ID,
                updatedByUserId=DEV_USER_ID,
                knowledgeId=MAINTENANCE_DEMO_KNOWLEDGE_ID,
                knowledgeName="圧入設備の保全ノウハウ",
                title="圧入機A 朝一の荷重ばらつき",
                targetEquipment="圧入機A",
                targetProcess="圧入工程",
            ).model_dump(),
        )

    return {
        "knowledgeDbId": MAINTENANCE_DEMO_DB_ID,
        "knowledgeId": MAINTENANCE_DEMO_KNOWLEDGE_ID,
        "recordId": MAINTENANCE_DEMO_RECORD_ID,
    }
