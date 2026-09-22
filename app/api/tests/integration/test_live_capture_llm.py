"""GPT-Live capture integration contract.

The file name is kept for compatibility with the previous opt-in smoke test.
GPT-Live semantic evaluation now belongs to Responses Delegation, so this
integration test verifies that the capture consumer only durabilizes receipts.
"""

from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.schemas.live import LiveCaptureCreate
from ai_interviewer_api.services import live_capture as capture_service
from ai_interviewer_api.services.live_capture_consumer import consume_pending_captures


def test_gpt_live_capture_consumer_does_not_run_backend_interpreter(
    clean_interview_store,
    monkeypatch,
) -> None:
    user = UserContext(
        user_id="capture-integration-user",
        tenant_id="capture-integration",
        role="interviewer",
        display_name="test",
    )
    record = {
        "id": "capture-integration-record",
        "tenantId": user.tenant_id,
        "knowledgeId": "capture-integration-knowledge",
        "ownerUserId": user.user_id,
        "status": "in_progress",
    }
    knowledge = {
        "id": record["knowledgeId"],
        "tenantId": user.tenant_id,
        "knowledgeDbId": "capture-integration-db",
        "interviewPlan": {"profile": "fixed_form"},
    }
    store.upsert("knowledge_dbs", {
        "id": knowledge["knowledgeDbId"],
        "tenantId": user.tenant_id,
        "status": "active",
    })
    store.upsert("knowledges", knowledge)
    store.upsert("records", record)
    store.upsert("knowledge_fields", {
        "id": "capture-integration-field",
        "tenantId": user.tenant_id,
        "knowledgeId": knowledge["id"],
        "name": "プロフィール",
        "required": True,
        "askByAi": True,
        "questionPlan": {
            "requiredItems": [{"itemId": "name", "label": "名前"}],
        },
    })
    monkeypatch.setattr(capture_service, "evaluate_completion", lambda *_args, **_kwargs: {
        "complete": False,
        "closingRequired": False,
        "missingRequiredTargets": [],
        "pendingConfirmationTargets": [],
        "unknownApplicabilityTopics": [],
        "unresolvedContradictionIds": [],
    })

    payload = LiveCaptureCreate(
        record_id=record["id"],
        capture_id="capture-integration-session",
        revision=1,
        fragments=[{"role": "user", "text": "田中です。"}],
    )
    capture_service.receive_live_capture(
        payload,
        record=record,
        knowledge=knowledge,
        user=user,
    )

    consume_pending_captures()

    receipt = store.list("messages", user.tenant_id)[0]
    assert receipt["liveCaptureApplied"] is True
    assert receipt["liveSemanticSource"] == "responses_delegation"
    state = store.get(
        "interview_states",
        "interview-state-capture-integration-record",
    )
    assert state is not None
    assert state["lastAnswerStatus"] is None
    assert state["lastAspectAssessments"] == []
    assert state["questionAnswers"] == {}
