import pytest
from fastapi import HTTPException

from ai_interviewer_api.agents.interview_knowledge.schemas import StructuredInterviewOutput
from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.schemas.live import LiveCaptureCreate
from ai_interviewer_api.services import live_capture as service


@pytest.fixture
def capture(monkeypatch, clean_interview_store):
    user = UserContext(user_id="u", tenant_id="t", role="interviewer", display_name="test")
    record = {"id": "r", "tenantId": "t", "knowledgeId": "k"}
    knowledge = {"id": "k", "tenantId": "t", "interviewPlan": {"profile": "fixed_form"}}
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
            return StructuredInterviewOutput.model_validate({**result, "fieldUpdates": [
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
