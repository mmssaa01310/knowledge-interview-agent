import pytest
from fastapi import HTTPException

from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.schemas.live import LiveCaptureCreate
from ai_interviewer_api.services import live_capture as service


@pytest.fixture
def capture_context(clean_interview_store):
    user = UserContext(user_id="u", tenant_id="t", role="interviewer", display_name="test")
    record = {
        "id": "r",
        "tenantId": "t",
        "knowledgeId": "k",
        "ownerUserId": "u",
        "status": "in_progress",
    }
    knowledge = {
        "id": "k",
        "tenantId": "t",
        "knowledgeDbId": "db",
        "interviewPlan": {"profile": "fixed_form"},
    }
    store.upsert("knowledge_dbs", {"id": "db", "tenantId": "t", "status": "active"})
    store.upsert("knowledges", knowledge)
    store.upsert("records", record)
    store.upsert(
        "knowledge_fields",
        {
            "id": "profile",
            "tenantId": "t",
            "knowledgeId": "k",
            "name": "profile",
            "required": True,
            "askByAi": True,
            "questionPlan": {
                "requiredItems": [{"itemId": "name", "label": "名前"}],
            },
        },
    )
    return user, record, knowledge


def _payload(revision: int, text: str = "田中です。", capture_id: str = "session"):
    return LiveCaptureCreate(
        record_id="r",
        capture_id=capture_id,
        revision=revision,
        fragments=[{"role": "user", "text": text}],
    )


def test_live_capture_is_receipt_only_and_does_not_run_backend_llm(capture_context, monkeypatch):
    user, record, knowledge = capture_context
    monkeypatch.setattr(service, "get_interview_state_snapshot", lambda *_args, **_kwargs: {
        "interviewState": {"status": "in_progress", "stateVersion": 0},
        "structuredDraft": {},
    })
    monkeypatch.setattr(service, "build_live_interview_context", lambda *_args, **_kwargs: {"fields": []})
    monkeypatch.setattr(service, "evaluate_completion", lambda *_args, **_kwargs: {
        "complete": False,
        "closingRequired": False,
        "missingRequiredTargets": [],
        "pendingConfirmationTargets": [],
        "unknownApplicabilityTopics": [],
        "unresolvedContradictionIds": [],
    })

    assert service.receive_live_capture(
        _payload(1), record=record, knowledge=knowledge, user=user,
    )["status"] == "accepted"
    result = service.process_live_capture(
        _payload(1), record=record, knowledge=knowledge, user=user,
    )

    assert result["status"] == "updated"
    assert result["semanticSource"] == "responses_delegation"
    message = store.list("messages", user.tenant_id)[0]
    assert message["liveCaptureApplied"] is True
    assert message["rawTranscript"] == "田中です。"


def test_live_capture_coalesces_receipts_without_changing_interview_state(capture_context, monkeypatch):
    user, record, knowledge = capture_context
    before = {"status": "in_progress", "stateVersion": 4}
    monkeypatch.setattr(service, "get_interview_state_snapshot", lambda *_args, **_kwargs: {
        "interviewState": dict(before),
        "structuredDraft": {},
    })
    monkeypatch.setattr(service, "build_live_interview_context", lambda *_args, **_kwargs: {"fields": []})
    monkeypatch.setattr(service, "evaluate_completion", lambda *_args, **_kwargs: {
        "complete": False,
        "closingRequired": False,
        "missingRequiredTargets": [],
        "pendingConfirmationTargets": [],
        "unknownApplicabilityTopics": [],
        "unresolvedContradictionIds": [],
    })
    store.upsert(
        "interview_states",
        {
            "id": "interview-state-r",
            "recordId": "r",
            "tenantId": "t",
            "stateVersion": before["stateVersion"],
            "status": before["status"],
        },
    )
    service.receive_live_capture(_payload(1), record=record, knowledge=knowledge, user=user)
    service.receive_live_capture(
        _payload(2, "追加です。"), record=record, knowledge=knowledge, user=user,
    )

    result = service.process_live_capture(
        _payload(2, "追加です。"),
        record=record,
        knowledge=knowledge,
        user=user,
        coalesce=True,
    )

    assert result["status"] == "updated"
    assert result["interviewState"]["stateVersion"] == before["stateVersion"]
    assert len([row for row in store.list("messages", "t") if row.get("liveCaptureApplied")]) == 2


def test_live_capture_revision_conflict_and_out_of_order_are_rejected(capture_context):
    user, record, knowledge = capture_context
    service.receive_live_capture(_payload(1), record=record, knowledge=knowledge, user=user)
    with pytest.raises(HTTPException) as conflict:
        service.receive_live_capture(
            _payload(1, "別の回答"), record=record, knowledge=knowledge, user=user,
        )
    assert conflict.value.detail == "live_capture_revision_conflict"
    with pytest.raises(HTTPException) as out_of_order:
        service.receive_live_capture(
            _payload(3), record=record, knowledge=knowledge, user=user,
        )
    assert out_of_order.value.detail == "live_capture_out_of_order"


def test_live_capture_status_does_not_expose_backend_next_action(capture_context):
    user, record, knowledge = capture_context
    service.receive_live_capture(_payload(1), record=record, knowledge=knowledge, user=user)
    result = service.process_live_capture(
        _payload(1), record=record, knowledge=knowledge, user=user,
    )

    assert "nextAction" not in result
    assert "next_action" not in result
