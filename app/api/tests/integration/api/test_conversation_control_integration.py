from __future__ import annotations

from collections.abc import Mapping

import pytest

from ai_interviewer_api.agents.interview_knowledge.schemas import (
    AnswerAssessment,
    FieldUpdate,
    QuestionGenerationOutput,
    StructuredInterviewOutput,
)
from ai_interviewer_api.agents.interview_knowledge.service import (
    generate_structured_interview_result,
)
from ai_interviewer_api.auth.deps import DEV_TOKENS, UserContext
from ai_interviewer_api.models.interview_plan import InterviewPlanItem, InterviewQuestionPlan
from ai_interviewer_api.repositories.store import store


pytestmark = pytest.mark.integration


class CapturingProvider:
    def __init__(self, output: StructuredInterviewOutput) -> None:
        self.output = output
        self.question_contexts: list[dict[str, object]] = []

    def interpret(self, **_: object) -> StructuredInterviewOutput:
        return self.output

    def generate_question(
        self,
        *,
        context: Mapping[str, object],
        target: Mapping[str, object],
        **_: object,
    ) -> QuestionGenerationOutput:
        self.question_contexts.append(dict(context))
        return QuestionGenerationOutput(
            questionText=f"{target.get('label') or target.get('targetId')}について教えてください。"
        )


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.tables.clear()


def _seed_case(record_id: str) -> tuple[UserContext, dict[str, object], dict[str, object]]:
    user: UserContext = DEV_TOKENS["dev-manager"]
    knowledge_id = f"knowledge-{record_id}"
    record = {"id": record_id, "knowledgeId": knowledge_id, "title": "業務インタビュー"}
    knowledge = {
        "id": knowledge_id,
        "name": "業務インタビュー",
        "interviewPlan": {"profile": "fixed_form"},
    }
    field = {
        "id": "field-role",
        "knowledgeId": knowledge_id,
        "tenantId": user.tenant_id,
        "name": "役割と責任",
        "description": "現在の役割と責任範囲を確認する",
        "questionPlan": InterviewQuestionPlan(
            purpose="現在の役割と責任範囲を確認する",
            requiredItems=[
                InterviewPlanItem(itemId="role", label="役割"),
                InterviewPlanItem(itemId="scope", label="責任範囲"),
            ],
        ).model_dump(),
        "required": True,
        "displayOrder": 1,
    }
    store.upsert("records", {**record, "tenantId": user.tenant_id})
    store.upsert("knowledges", {**knowledge, "tenantId": user.tenant_id})
    store.upsert("knowledge_fields", field)
    return user, record, knowledge


def _add_answer(
    record: Mapping[str, object],
    user: UserContext,
    *,
    message_id: str,
    question: Mapping[str, object],
    content: str,
) -> None:
    store.upsert(
        "messages",
        {
            "id": message_id,
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "content": content,
            "rawTranscript": content,
            "isActualUtterance": True,
            "turnType": "ANSWER",
            "answerToQuestionId": question["questionId"],
        },
    )


def test_partial_answer_asks_only_for_missing_aspect() -> None:
    user, record, knowledge = _seed_case("record-partial-control")
    provider = CapturingProvider(
        StructuredInterviewOutput(
            answerAssessment=AnswerAssessment(sufficiency="PARTIAL", probeType="CRITERIA"),
            missingInformation=["責任範囲"],
            fieldUpdates=[
                FieldUpdate(
                    fieldId="field-role",
                    itemId="role",
                    value="プロジェクトリーダー",
                    answerResolution="TENTATIVE",
                    evidenceTranscriptIds=["partial-answer"],
                )
            ],
        )
    )

    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_answer(
        record,
        user,
        message_id="partial-answer",
        question=first["question"],
        content="プロジェクトリーダーです。",
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)
    field_state = result["interviewState"]["fieldStates"]["field-role"]

    assert result["answerEvaluation"] == "PARTIALLY_ANSWERED"
    assert result["nextAction"] == "ASK_MISSING_INFORMATION"
    assert field_state["capturedItemIds"] == ["role"]
    assert field_state["missingRequiredItemIds"] == ["scope"]
    assert result["nextQuestionTarget"]["targetId"] == "field-role"
    assert "聞き取れ" not in result["reply"]
    assert provider.question_contexts[-1]["missingInformation"]


def test_complete_answer_moves_to_another_target_without_reasking() -> None:
    user, record, knowledge = _seed_case("record-complete-control")
    # Use a single required item for the complete-answer case.
    store.upsert(
        "knowledge_fields",
        {
            "id": "field-role",
            "knowledgeId": knowledge["id"],
            "tenantId": user.tenant_id,
            "name": "役割",
            "questionPlan": InterviewQuestionPlan(
                purpose="役割を確認する",
                requiredItems=[InterviewPlanItem(itemId="role", label="役割")],
            ).model_dump(),
            "required": True,
            "displayOrder": 1,
        },
    )
    provider = CapturingProvider(
        StructuredInterviewOutput(
            answerAssessment=AnswerAssessment(sufficiency="SUFFICIENT"),
            fieldUpdates=[
                FieldUpdate(
                    fieldId="field-role",
                    itemId="role",
                    value="プロジェクトリーダー",
                    answerResolution="AUTO_CONFIRM",
                    evidenceTranscriptIds=["complete-answer"],
                )
            ],
        )
    )

    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_answer(
        record,
        user,
        message_id="complete-answer",
        question=first["question"],
        content="プロジェクトリーダーです。",
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    assert result["answerEvaluation"] == "ANSWERED"
    assert result["nextAction"] == "MOVE_NEXT"
    assert result["interviewState"]["fieldStates"]["field-role"]["answerState"] == "CONFIRMED"
    assert result["nextQuestionTarget"]["targetId"] != "field-role"
    assert "続き、お願いします" not in result["reply"]
