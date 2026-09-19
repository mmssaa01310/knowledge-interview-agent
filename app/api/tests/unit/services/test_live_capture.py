import pytest
from fastapi import HTTPException

from ai_interviewer_api.agents.interview_knowledge.schemas import LiveObservationOutput
from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.schemas.live import LiveCaptureCreate
from ai_interviewer_api.services import live_capture as service


@pytest.fixture
def capture(monkeypatch, clean_interview_store):
    user = UserContext(user_id="u", tenant_id="t", role="interviewer", display_name="test")
    record = {"id": "r", "tenantId": "t", "knowledgeId": "k", "ownerUserId": "u", "status": "in_progress"}
    knowledge = {"id": "k", "tenantId": "t", "knowledgeDbId": "db", "interviewPlan": {"profile": "fixed_form"}}
    store.upsert("knowledge_dbs", {"id": "db", "tenantId": "t", "status": "active"})
    store.upsert("knowledges", knowledge)
    store.upsert("records", record)
    for field_id, items in [("profile", ["name", "department", "role"]), ("work", ["task"])]:
        store.upsert("knowledge_fields", {
            "id": field_id, "tenantId": "t", "knowledgeId": "k", "name": field_id,
            "required": True, "askByAi": True,
            "questionPlan": {"requiredItems": [{"itemId": item, "label": item} for item in items]},
        })
    calls = []
    outputs = []

    class Provider:
        def __init__(self, **kwargs):
            pass

        def interpret(self, **kwargs):
            calls.append(kwargs["context"])
            result = outputs.pop(0)
            if isinstance(result, Exception):
                raise result
            evidence = [item["id"] for item in kwargs["context"]["conversation"] if item["role"] == "user"]
            return LiveObservationOutput.model_validate({**result, "fieldUpdates": [
                {**update, "evidenceTranscriptIds": update.get("evidenceTranscriptIds", evidence)}
                for update in result.get("fieldUpdates", [])
            ]})

    monkeypatch.setattr(service, "BedrockResponsesStructuredProvider", Provider)

    def run(revision, text="回答です。", capture_id="session", role="user"):
        return service.process_live_capture(LiveCaptureCreate(
            record_id="r", capture_id=capture_id, revision=revision,
            fragments=[{"role": role, "text": text}],
        ), record=record, knowledge=knowledge, user=user)

    return run, outputs, calls


def update(item, value, field="profile", **kwargs):
    return {"fieldId": field, "itemId": item, "value": value,
            "answerResolution": "AUTO_CONFIRM", **kwargs}


def test_partial_followup_all_fields_and_later_correction(capture):
    run, outputs, calls = capture
    outputs.append({"fieldUpdates": [update("name", "田中")]})
    first = run(1, "田中です。")
    profile = first["interviewState"]["fieldStates"]["profile"]
    assert profile["answerState"] == "CANDIDATE_PENDING"
    assert "田中" in profile["candidateAnswer"]
    assert first["checklist"][0]["missing_required_items"] == ["department", "role"]
    outputs.append({"fieldUpdates": [update("department", "開発部"), update("role", "課長"),
                                     update("task", "品質管理", "work")]})
    second = run(2, "開発部の課長です。品質管理を担当します。")
    assert set(second["interviewState"]["completedFieldIds"]) == {"profile", "work"}
    assert all(field["answerState"] == "CONFIRMED" for field in second["interviewState"]["fieldStates"].values())
    assert "田中" in second["structuredDraft"]["profile"]
    outputs.append({"fieldUpdates": [update("role", "部長")]})
    third = run(3, "訂正します。課長ではなく部長です。")
    assert "部長" in third["structuredDraft"]["profile"]
    assert "課長" not in third["structuredDraft"]["profile"]
    assert "田中" in third["structuredDraft"]["profile"]
    assert len(calls[-1]["conversation"]) == 3
    assert third["interviewState"]["currentQuestionId"] is None


def test_idempotency_order_and_failure_retains_raw_evidence(capture):
    run, outputs, calls = capture
    outputs.append(RuntimeError("provider unavailable"))
    with pytest.raises(RuntimeError):
        run(1)
    messages = store.list("messages", "t")
    assert len(messages) == 1 and messages[0]["rawTranscript"] == "回答です。"
    assert messages[0]["createdByUserId"] == "u"
    with pytest.raises(HTTPException):
        run(2)
    outputs.append({"fieldUpdates": [update("name", "田中")]})
    run(1)
    assert run(1)["status"] == "duplicate"
    assert len(calls) == 2
    with pytest.raises(HTTPException):
        run(1, "別の内容")
    with pytest.raises(HTTPException):
        run(4)


@pytest.mark.parametrize("assessment", [
    {"utteranceCompleteness": "INCOMPLETE"},
    {"transcriptAssessment": {"correctionStatus": "UNCERTAIN"}},
])
def test_uncertain_observation_cannot_confirm(capture, assessment):
    run, outputs, _ = capture
    outputs.append({**assessment, "fieldUpdates": [update("task", "推測", "work")]})
    assert run(1)["interviewState"]["fieldStates"]["work"]["answerState"] == "UNANSWERED"


def test_assistant_only_is_not_evidence_and_missing_items_cannot_be_fabricated(capture):
    run, outputs, calls = capture
    run(1, "お名前は田中さんですか？", role="assistant")
    assert calls == []
    outputs.append({"fieldUpdates": [update("task", "assistant assertion", "work", evidenceTranscriptIds=["unknown"])]})
    result = run(2, "いいえ")
    assert result["interviewState"]["fieldStates"]["work"]["answerState"] == "UNANSWERED"
    assert calls[0]["conversation"][0]["role"] == "assistant"


def test_ai_proposal_cannot_become_a_user_answer(capture):
    run, outputs, _ = capture
    outputs.append({"fieldUpdates": [update("task", "提案", "work", candidateSource="assistant_proposal")]})
    assert run(1)["interviewState"]["fieldStates"]["work"]["answerState"] == "UNANSWERED"


def receive(revision, text="回答です。"):
    return service.receive_live_capture(
        LiveCaptureCreate(record_id="r", capture_id="session", revision=revision,
                          fragments=[{"role": "user", "text": text}]),
        record=store.get("records", "r"), knowledge=store.get("knowledges", "k"),
        user=UserContext(user_id="u", tenant_id="t", role="interviewer", display_name="test"),
    )


def test_receipt_is_durable_without_llm_and_coalesces_after_browser_leaves(capture):
    from ai_interviewer_api.services.live_capture_consumer import consume_pending_captures

    _, outputs, calls = capture
    assert receive(1)["status"] == "accepted"
    receive(2, "追加回答")
    assert not calls
    assert len(store.pending_live_captures()) == 2
    # A new consumer discovers saved receipts; no browser callback is involved.
    outputs.append({"fieldUpdates": [update("name", "田中")]})
    consume_pending_captures()
    assert len(calls) == 1
    assert len(calls[0]["conversation"]) == 2
    assert not store.pending_live_captures()
    assert all(row["liveCaptureApplied"] for row in store.list("messages", "t"))


def test_server_retries_failure_without_browser_and_does_not_duplicate(capture):
    from ai_interviewer_api.services.live_capture_consumer import consume_pending_captures

    _, outputs, calls = capture
    receive(1)
    receive(1)
    outputs.append(RuntimeError("unavailable"))
    consume_pending_captures()
    receipt = store.list("messages", "t")[0]
    assert not receipt["liveCaptureApplied"]
    assert receipt["liveCaptureAttempts"] == 1
    receipt["liveCaptureRetryAt"] = 0
    store.upsert("messages", receipt)
    outputs.append({"fieldUpdates": [update("name", "田中")]})
    consume_pending_captures()
    assert len(calls) == 2
    assert len(store.list("messages", "t")) == 1
    assert store.list("messages", "t")[0]["liveCaptureApplied"]


@pytest.mark.parametrize("valid_evidence", [True, False])
def test_complete_only_after_all_required_items_and_grounded_closing(capture, valid_evidence):
    run, outputs, calls = capture
    outputs.append({"fieldUpdates": [update("name", "田中"), update("department", "開発"),
                                     update("role", "課長"), update("task", "検査", "work")]})
    result = run(1)
    assert result["interviewState"]["status"] != "completed"
    outputs.append({})
    run(2, "最後に、ここまで触れていない大切なことはありますか？", role="assistant")
    question_id = calls[-1]["conversation"][-1]["id"]
    user = UserContext("u", "t", "interviewer", "test")
    payload = LiveCaptureCreate(record_id="r", capture_id="session", revision=3,
                               fragments=[{"role": "user", "text": "特にありません。"}])
    answer_id = service._fragments(payload, service._message_id(payload, user))[0]["id"]
    outputs.append({"closingQuestionEvidenceIds": [question_id],
                    "closingAnswerEvidenceIds": [answer_id if valid_evidence else "fabricated"]})
    result = run(3, "特にありません。")
    assert (result["interviewState"]["status"] == "completed") is valid_evidence
    assert (store.get("records", "r")["status"] == "submitted") is valid_evidence


def test_closing_answer_does_not_confirm_when_a_required_candidate_is_pending(capture):
    run, outputs, calls = capture
    outputs.append({"fieldUpdates": [update("name", "田中"), update("department", "開発部"),
                                     update("role", "課長", answerResolution="TENTATIVE"),
                                     update("task", "検査", "work")]})
    run(1, "基本項目への回答です。")
    outputs.append({"closingQuestionEvidenceIds": ["assistant-closing"],
                    "closingAnswerEvidenceIds": ["user-closing"]})
    result = run(2, "特にないです。", capture_id="session")
    state = result["interviewState"]
    assert state["closingState"] == "UNANSWERED"
    assert state["status"] == "in_progress"
    assert result["completion"]["closingRequired"] is True


def test_capture_status_processing_is_scoped_to_the_current_live_session(capture):
    _, _, _ = capture
    user = UserContext(user_id="u", tenant_id="t", role="interviewer", display_name="test")
    record = store.get("records", "r")
    knowledge = store.get("knowledges", "k")
    assert record is not None and knowledge is not None

    receive(1, "現在のセッション")
    service.receive_live_capture(
        LiveCaptureCreate(
            record_id="r", capture_id="previous-session", revision=1,
            fragments=[{"role": "user", "text": "過去のセッション"}],
        ),
        record=record,
        knowledge=knowledge,
        user=user,
    )
    current = next(row for row in store.list("messages", "t") if row["liveCaptureId"] == "session")
    current["liveCaptureApplied"] = True
    store.upsert("messages", current)

    current_status = service._response(record, knowledge, user, "updated", capture_id="session")
    previous_status = service._response(record, knowledge, user, "updated", capture_id="previous-session")
    assert current_status["processing"] is False
    assert previous_status["processing"] is True


@pytest.mark.parametrize("changes", [{"tenantId": "other"}, {"status": "approved"}, {"ownerUserId": "other"}])
def test_consumer_does_not_write_other_tenant_or_approved_record(capture, changes):
    from ai_interviewer_api.services.live_capture_consumer import consume_pending_captures

    _, _, calls = capture
    receive(1)
    record = dict(store.get("records", "r"), **changes)
    store.upsert("records", record)
    consume_pending_captures()
    assert not calls
    assert not store.list("messages", "t")[0]["liveCaptureApplied"]


def test_receipt_conflict_and_out_of_order_do_not_overwrite_evidence(capture):
    receive(1, "原文")
    with pytest.raises(HTTPException) as error:
        receive(1, "別の文")
    assert error.value.status_code == 409
    with pytest.raises(HTTPException):
        receive(3)
    assert store.list("messages", "t")[0]["rawTranscript"] == "原文"


def test_receipts_arriving_during_interpretation_are_not_marked_applied(capture, monkeypatch):
    from ai_interviewer_api.services.live_capture_consumer import consume_pending_captures

    _, outputs, calls = capture
    receive(1)
    original = service.BedrockResponsesStructuredProvider.interpret

    def interpret(self, **kwargs):
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(receive, 2, "処理中に届いた追加回答").result(timeout=2)
        return original(self, **kwargs)

    monkeypatch.setattr(service.BedrockResponsesStructuredProvider, "interpret", interpret)
    outputs.append({"fieldUpdates": [update("name", "田中")]})
    consume_pending_captures()
    receipts = sorted(store.list("messages", "t"), key=lambda row: row["liveCaptureRevision"])
    assert receipts[0]["liveCaptureApplied"]
    assert not receipts[1]["liveCaptureApplied"]
    assert len(calls[0]["conversation"]) == 1


def test_http_receipt_then_processing_after_client_closes(capture, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from ai_interviewer_api.auth.deps import get_current_user
    from ai_interviewer_api.routers import live_sessions
    from ai_interviewer_api.services.live_capture_consumer import consume_pending_captures

    _, outputs, calls = capture
    app = FastAPI()
    app.include_router(live_sessions.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext("u", "t", "interviewer", "test")
    monkeypatch.setattr(live_sessions, "require_interview_configuration", lambda _: None)
    with TestClient(app) as client:
        response = client.post("/api/live/captures", json={
            "record_id": "r", "capture_id": "session", "revision": 1,
            "fragments": [{"role": "user", "text": "田中です。"}],
        })
        assert response.status_code == 202
        assert response.json() == {"status": "accepted", "revision": 1}
        assert client.get("/api/live/captures/r").json()["processing"]
        assert not calls
    outputs.append({"fieldUpdates": [update("name", "田中")]})
    consume_pending_captures()
    with TestClient(app) as client:
        result = client.get("/api/live/captures/r").json()
        assert not result["processing"]
        assert "田中" in result["interviewState"]["fieldStates"]["profile"]["candidateAnswer"]


@pytest.mark.parametrize("completed_at, allowed", [(980, True), (900, False)])
def test_submitted_record_accepts_only_recent_trailing_capture(capture, monkeypatch, completed_at, allowed):
    from ai_interviewer_api.routers import live_sessions

    receive(1)
    receipt = dict(store.list("messages", "t")[0], liveCaptureApplied=True,
                   liveCaptureCompletedAt=completed_at)
    store.upsert("messages", receipt)
    store.upsert("records", dict(store.get("records", "r"), status="submitted"))
    monkeypatch.setattr(live_sessions, "time", lambda: 1000)
    monkeypatch.setattr(live_sessions, "require_interview_configuration", lambda _: None)
    payload = LiveCaptureCreate(record_id="r", capture_id="session", revision=2,
                               fragments=[{"role": "assistant", "text": "ありがとうございました。"}])
    user = UserContext("u", "t", "interviewer", "test")
    if allowed:
        assert live_sessions.capture_live_transcript(payload, user)["status"] == "accepted"
    else:
        with pytest.raises(HTTPException) as error:
            live_sessions.capture_live_transcript(payload, user)
        assert error.value.status_code == 409
