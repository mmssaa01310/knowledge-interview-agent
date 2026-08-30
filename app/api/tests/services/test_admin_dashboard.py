import pytest
from fastapi import HTTPException

from ai_interviewer_api.auth.deps import DEV_TOKENS, UserContext
from ai_interviewer_api.models.interview_plan import InterviewPlan
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.knowledge_dbs import create_knowledge_db
from ai_interviewer_api.routers.knowledge_fields import create_field
from ai_interviewer_api.routers.knowledges import create_knowledge
from ai_interviewer_api.routers.records import create_record
from ai_interviewer_api.schemas.dashboard import (
    LearningAnalysisRequest,
    LearningAnalysisUpdateRequest,
)
from ai_interviewer_api.schemas.requests import KnowledgeDbCreate, KnowledgeFieldCreate, KnowledgeCreate, RecordCreate
from ai_interviewer_api.services.admin_dashboard import (
    GuidanceGenerationError,
    build_admin_dashboard,
    generate_learning_analysis,
    generate_guidance_draft,
    list_learning_analyses,
    list_guidance_for_record,
    publish_guidance_draft,
    review_learning_analysis,
    update_learning_analysis,
)


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.tables.clear()


def _create_knowledge(user: UserContext) -> dict:
    knowledge_db = create_knowledge_db(KnowledgeDbCreate(name="教育支援テストDB"), user)
    knowledge = create_knowledge(
        knowledge_db["id"],
        KnowledgeCreate(
            name="教育支援テスト",
            purpose="根拠付きレビューを確認する",
            interviewPlan=InterviewPlan(
                profile="fixed_form",
                modelId="global.openai.gpt-5.6-luna",
            ),
        ),
        user,
    )
    create_field(
        knowledge["id"],
        KnowledgeFieldCreate(name="設備の状態", required=True, displayOrder=1),
        user,
    )
    return knowledge


def _create_record(user: UserContext, knowledge: dict, owner_user_id: str | None = None) -> dict:
    return create_record(
        knowledge["id"],
        RecordCreate(
            title="確認対象の記録",
            ownerUserId=owner_user_id,
        ),
        user,
    )


def test_management_dashboard_is_scoped_to_management_roles_and_exposes_explainable_priority() -> None:
    manager = DEV_TOKENS["dev-manager"]
    admin = DEV_TOKENS["dev-admin"]
    knowledge = _create_knowledge(manager)
    record = _create_record(manager, knowledge)
    field_id = store.list("knowledge_fields", manager.tenant_id)[0]["id"]
    store.upsert(
        "interview_states",
        {
            "id": f"interview-state-{record['id']}",
            "tenantId": manager.tenant_id,
            "recordId": record["id"],
            "status": "in_progress",
            "fieldStates": {
                field_id: {
                    "fieldId": field_id,
                    "answerState": "UNANSWERED",
                }
            },
            "contradictions": [
                {
                    "contradictionId": "contradiction-1",
                    "topic": "設備の状態",
                    "evidenceTranscriptIds": ["message-1"],
                }
            ],
        },
    )

    dashboard = build_admin_dashboard(admin)

    assert dashboard.totals.knowledgeCount == 1
    assert dashboard.totals.recordCount == 1
    assert dashboard.totals.highPriorityCount == 1
    assert dashboard.reviewPriorityTotal == 1
    item = dashboard.reviewPriorities[0]
    assert item.level == "high"
    assert {reason.code for reason in item.reasons} == {
        "contradiction_detected",
        "required_item_unconfirmed",
    }
    assert any(point.createdCount == 1 for point in dashboard.timeSeries)
    assert dashboard.activityByUser[0].recordCount == 1
    assert dashboard.activityByUser[0].notEvidencedCount == 1

    manager_dashboard = build_admin_dashboard(manager)
    assert manager_dashboard.totals.recordCount == 1

    with pytest.raises(HTTPException) as viewer_error:
        build_admin_dashboard(DEV_TOKENS["dev-viewer"])
    assert viewer_error.value.status_code == 403


def test_dashboard_does_not_raise_priority_from_activity_volume_alone() -> None:
    manager = DEV_TOKENS["dev-manager"]
    admin = DEV_TOKENS["dev-admin"]
    knowledge = _create_knowledge(manager)
    record = _create_record(manager, knowledge)
    for index in range(8):
        store.upsert(
            "messages",
            {
                "id": f"message-{index}",
                "tenantId": manager.tenant_id,
                "recordId": record["id"],
                "role": "user",
                "turnType": "ANSWER",
                "content": "確認済みの回答",
                "createdAt": f"2026-08-2{index + 1}T00:00:00+00:00",
            },
        )
    store.upsert(
        "messages",
        {
            "id": "synthetic-message",
            "tenantId": manager.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "turnType": "ANSWER",
            "isActualUtterance": False,
            "content": "内部処理用の発話ではないメッセージ",
        },
    )

    dashboard = build_admin_dashboard(admin)

    assert dashboard.activityByUser[0].answerCount == 8
    assert dashboard.totals.mediumPriorityCount == 1
    assert dashboard.totals.highPriorityCount == 0


class _FakeGuidanceProvider:
    def __init__(self, objective_id: str, evidence_id: str) -> None:
        self.objective_id = objective_id
        self.evidence_id = evidence_id

    def request_structured_output(self, **_: object) -> dict:
        return {
            "summary": "設備状態の確認内容を整理します。",
            "learnerGuidance": "設備状態を判断した根拠と確認手順を復習してください。",
            "instructorGuidance": "判断根拠を説明してもらい、確認手順を再確認します。",
            "assessments": [
                {
                    "objectiveId": self.objective_id,
                    "status": "confirmed",
                    "evidenceIds": [self.evidence_id],
                    "learnerGuidance": "根拠を示しながら説明してください。",
                    "instructorGuidance": "根拠の説明を再確認してください。",
                    "followUpQuestion": "判断の根拠を教えてください。",
                }
            ],
        }


class _FakeLearningAnalysisProvider:
    def __init__(self, objective_id: str, record_ids: list[str]) -> None:
        self.objective_id = objective_id
        self.record_ids = record_ids
        self.schema_names: list[str] = []

    def request_structured_output(self, **kwargs: object) -> dict:
        schema_name = kwargs.get("schema_name")
        if isinstance(schema_name, str):
            self.schema_names.append(schema_name)
        if schema_name == "learning_support_personal_advice_output":
            payload = kwargs.get("user_payload")
            respondents = payload.get("respondents", []) if isinstance(payload, dict) else []
            return {
                "advice": [
                    {
                        "respondentKey": respondent["respondentKey"],
                        "summary": "対象記録に基づく次の確認内容を整理します。",
                        "focusAreas": [
                            {
                                "title": "判断根拠の説明",
                                "summary": "設備状態を判断した根拠を確認します。",
                                "objectiveIds": [self.objective_id],
                                "evidenceRecordIds": [record["id"] for record in respondent["records"]],
                                "nextStep": "判断した理由を具体例で説明する練習をします。",
                                "followUpQuestion": "その判断の根拠は何ですか？",
                            }
                        ],
                        "nextSteps": ["判断の根拠を具体例で説明する"],
                        "followUpQuestions": ["判断の根拠を教えてください。"],
                    }
                    for respondent in respondents
                ]
            }
        return {
            "summary": "複数記録の確認状況を整理します。",
            "trendSummary": "設備状態の根拠確認にばらつきがあります。",
            "learnerGuidance": "設備状態と判断根拠を説明できるように復習してください。",
            "instructorGuidance": "判断根拠の説明を確認し、実例で再確認します。",
            "themes": [
                {
                    "themeId": "theme-1",
                    "title": "判断根拠の説明",
                    "summary": "設備状態の判断根拠を確認するテーマです。",
                    "objectiveIds": [self.objective_id],
                    "evidenceRecordIds": self.record_ids,
                    "learnerGuidance": "判断した理由を説明してください。",
                    "instructorGuidance": "理由と確認手順を聞き取ってください。",
                    "followUpQuestion": "その判断の根拠は何ですか？",
                }
            ],
        }


def test_learning_analysis_aggregates_same_knowledge_and_requires_human_review() -> None:
    manager = DEV_TOKENS["dev-manager"]
    knowledge = _create_knowledge(manager)
    first_record = _create_record(manager, knowledge)
    second_record = _create_record(manager, knowledge)
    field_id = store.list("knowledge_fields", manager.tenant_id)[0]["id"]
    for record, answer_state in ((first_record, "CONFIRMED"), (second_record, "UNANSWERED")):
        store.upsert(
            "interview_states",
            {
                "id": f"interview-state-{record['id']}",
                "tenantId": manager.tenant_id,
                "recordId": record["id"],
                "status": "completed",
                "fieldStates": {
                    field_id: {
                        "fieldId": field_id,
                        "answerState": answer_state,
                    }
                },
            },
        )

    objective_id = f"field:{field_id}"
    request = LearningAnalysisRequest(knowledgeId=knowledge["id"])
    fake_provider = _FakeLearningAnalysisProvider(
        objective_id,
        [first_record["id"], second_record["id"]],
    )
    draft = generate_learning_analysis(
        manager,
        request,
        provider=fake_provider,
    )

    assert draft.status == "draft"
    assert draft.scope.recordCount == 2
    assert draft.objectiveTrends[0].confirmedCount == 1
    assert draft.objectiveTrends[0].notEvidencedCount == 1
    assert draft.themes[0].evidenceRecordIds == [first_record["id"], second_record["id"]]
    assert len(draft.personalAdvice) == 1
    assert draft.personalAdvice[0].recordIds == [first_record["id"], second_record["id"]]
    assert fake_provider.schema_names == [
        "learning_support_analysis_output",
        "learning_support_personal_advice_output",
    ]
    assert len(list_learning_analyses(manager, knowledge_id=knowledge["id"])) == 1

    updated = update_learning_analysis(
        draft.id,
        LearningAnalysisUpdateRequest(summary="管理者が確認した要約です。"),
        manager,
    )
    assert updated.status == "draft"
    assert updated.summary == "管理者が確認した要約です。"

    reviewed = review_learning_analysis(draft.id, manager)
    assert reviewed.status == "reviewed"
    assert reviewed.reviewedByUserId == manager.user_id

    with pytest.raises(HTTPException) as viewer_error:
        generate_learning_analysis(
            DEV_TOKENS["dev-viewer"],
            request,
            provider=_FakeLearningAnalysisProvider(objective_id, [first_record["id"]]),
        )
    assert viewer_error.value.status_code == 403


def test_guidance_requires_valid_evidence_and_human_publication() -> None:
    manager = DEV_TOKENS["dev-manager"]
    interviewer = DEV_TOKENS["dev-interviewer"]
    knowledge = _create_knowledge(manager)
    record = _create_record(manager, knowledge, owner_user_id=interviewer.user_id)
    field_id = store.list("knowledge_fields", manager.tenant_id)[0]["id"]
    store.upsert(
        "interview_states",
        {
            "id": f"interview-state-{record['id']}",
            "tenantId": manager.tenant_id,
            "recordId": record["id"],
            "status": "completed",
            "stateVersion": 2,
            "fieldStates": {
                field_id: {
                    "fieldId": field_id,
                    "answerState": "CONFIRMED",
                    "confirmationEvidenceTranscriptIds": ["evidence-1"],
                }
            },
        },
    )
    store.upsert(
        "messages",
        {
            "id": "evidence-1",
            "tenantId": manager.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "turnType": "ANSWER",
            "content": "正常です",
            "isActualUtterance": True,
        },
    )

    fake_provider = _FakeGuidanceProvider(f"field:{field_id}", "evidence-1")
    draft = generate_guidance_draft(record["id"], manager, provider=fake_provider)

    assert draft.status == "draft"
    assert draft.assessments[0].status == "confirmed"
    assert draft.assessments[0].evidenceIds == ["evidence-1"]
    assert list_guidance_for_record(record["id"], interviewer, public=True) == []

    published = publish_guidance_draft(draft.id, manager)
    assert published.status == "published"
    public_guidance = list_guidance_for_record(record["id"], interviewer, public=True)
    assert len(public_guidance) == 1
    assert public_guidance[0].instructorGuidance is None
    assert public_guidance[0].publishedByUserId is None
    assert public_guidance[0].assessments[0].suggestedStatus is None

    viewer = DEV_TOKENS["dev-viewer"]
    with pytest.raises(HTTPException) as viewer_error:
        list_guidance_for_record(record["id"], viewer, public=True)
    assert viewer_error.value.status_code == 403

    other_interviewer = UserContext(
        user_id="other-interviewer",
        tenant_id=interviewer.tenant_id,
        role="interviewer",
        display_name="別の対象者",
    )
    with pytest.raises(HTTPException) as owner_error:
        list_guidance_for_record(record["id"], other_interviewer, public=True)
    assert owner_error.value.status_code == 403

    bad_provider = _FakeGuidanceProvider(f"field:{field_id}", "not-in-record")
    with pytest.raises(GuidanceGenerationError):
        generate_guidance_draft(record["id"], manager, provider=bad_provider)
