from fastapi import HTTPException
import pytest

from ai_interviewer_api.auth.deps import DEV_TOKENS
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.records import update_record_interview_answer
from ai_interviewer_api.schemas.requests import InterviewAnswerUpdate


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.tables.clear()


def _seed_answer(*, answer_state: str = "CONFIRMED") -> None:
    user = DEV_TOKENS["dev-manager"]
    store.upsert(
        "records",
        {
            "id": "record-1",
            "tenantId": user.tenant_id,
            "knowledgeId": "knowledge-1",
            "title": "インタビュー記録",
        },
    )
    store.upsert(
        "interview_states",
        {
            "id": "interview-state-record-1",
            "tenantId": user.tenant_id,
            "recordId": "record-1",
            "fieldStates": {
                "field-1": {
                    "fieldId": "field-1",
                    "status": "completed" if answer_state == "CONFIRMED" else "asking",
                    "answerState": answer_state,
                    "answerSummary": "変更前の回答" if answer_state == "CONFIRMED" else None,
                },
            },
        },
    )
    store.upsert(
        "messages",
        {
            "id": "confirmed-answer-1",
            "tenantId": user.tenant_id,
            "recordId": "record-1",
            "role": "user",
            "isActualUtterance": False,
            "messageType": "confirmed_answer",
            "answerToQuestionId": "question-1",
            "answerToFieldId": "field-1",
            "content": "変更前の回答",
        },
    )


def test_update_confirmed_interview_answer() -> None:
    user = DEV_TOKENS["dev-manager"]
    _seed_answer()

    result = update_record_interview_answer(
        "record-1",
        "field-1",
        InterviewAnswerUpdate(answerSummary="  キーボードで修正した回答  "),
        user,
    )

    state = store.get("interview_states", "interview-state-record-1")
    message = store.get("messages", "confirmed-answer-1")
    assert result["answerSummary"] == "キーボードで修正した回答"
    assert state["fieldStates"]["field-1"]["answerSummary"] == "キーボードで修正した回答"
    assert state["fieldStates"]["field-1"]["answerState"] == "CONFIRMED"
    assert message["content"] == "キーボードで修正した回答"


def test_update_rejects_unconfirmed_interview_answer() -> None:
    user = DEV_TOKENS["dev-manager"]
    _seed_answer(answer_state="AWAITING_CONFIRMATION")

    with pytest.raises(HTTPException) as exc_info:
        update_record_interview_answer(
            "record-1",
            "field-1",
            InterviewAnswerUpdate(answerSummary="未確認の回答"),
            user,
        )

    assert exc_info.value.status_code == 409
    state = store.get("interview_states", "interview-state-record-1")
    assert state["fieldStates"]["field-1"]["answerSummary"] is None
