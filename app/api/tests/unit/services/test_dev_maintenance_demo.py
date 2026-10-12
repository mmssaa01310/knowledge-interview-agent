from ai_interviewer_api.agents.interview_knowledge.coordinator import (
    apply_structured_output,
    build_initial_structured_state,
    select_next_question_target,
)
from ai_interviewer_api.agents.interview_knowledge.schemas import (
    AnswerAssessment,
    FieldUpdate,
    StructuredInterviewOutput,
)
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
    equipment_field = next(item for item in fields if item["id"].endswith("-equipment"))
    assert [
        (item["itemId"], item["label"])
        for item in equipment_field["questionPlan"]["requiredItems"]
    ] == [("equipment_name", "設備名"), ("unit_number", "号機")]


def test_existing_demo_equipment_field_gets_missing_question_plan_backfilled() -> None:
    ensure_dev_maintenance_demo()
    field_id = "dev-maintenance-demo-field-equipment"
    equipment_field = store.get("knowledge_fields", field_id)
    assert equipment_field is not None
    equipment_field.pop("questionPlan", None)
    store.upsert("knowledge_fields", equipment_field)

    ensure_dev_maintenance_demo()

    updated_field = store.get("knowledge_fields", field_id)
    assert updated_field is not None
    assert [
        item["itemId"] for item in updated_field["questionPlan"]["requiredItems"]
    ] == ["equipment_name", "unit_number"]


def test_equipment_field_stays_open_until_name_and_unit_are_both_answered() -> None:
    ensure_dev_maintenance_demo()
    field_id = "dev-maintenance-demo-field-equipment"
    equipment_field = store.get("knowledge_fields", field_id)
    assert equipment_field is not None
    state = build_initial_structured_state("fixed_form", [equipment_field])

    apply_structured_output(
        state,
        StructuredInterviewOutput(
            answerAssessment=AnswerAssessment(sufficiency="SUFFICIENT"),
            fieldUpdates=[
                FieldUpdate(
                    fieldId=field_id,
                    itemId="equipment_name",
                    value="圧入機A",
                    evidenceTranscriptIds=["equipment-name-answer"],
                    answerResolution="AUTO_CONFIRM",
                )
            ],
        ),
        latest_message_id="equipment-name-answer",
        fields=[equipment_field],
        profile="fixed_form",
        valid_evidence_ids={"equipment-name-answer"},
        current_question={
            "targetType": "field",
            "targetId": field_id,
            "targetLabel": "設備名",
        },
    )

    field_state = state["fieldStates"][field_id]
    assert field_state["answerState"] == "CANDIDATE_PENDING"
    assert field_state["missingRequiredItemIds"] == ["unit_number"]
    assert field_id not in state["completedFieldIds"]
    next_target = select_next_question_target(state, "fixed_form", [equipment_field])
    assert next_target is not None
    assert next_target["targetId"] == field_id
    assert [item["label"] for item in next_target["missingItems"]] == ["号機"]
