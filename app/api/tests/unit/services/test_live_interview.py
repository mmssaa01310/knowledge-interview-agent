from types import SimpleNamespace

from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.schemas.live import LiveDelegationCreate
import ai_interviewer_api.services.live_interview as live_interview_module


def _user() -> UserContext:
    return UserContext(
        user_id="user-interviewer",
        tenant_id="tenant-demo",
        role="interviewer",
        display_name="インタビュー対象者",
    )


def _record() -> dict:
    return {
        "id": "record-live-test",
        "tenantId": "tenant-demo",
        "knowledgeId": "knowledge-live-test",
    }


def _snapshot(state: dict) -> dict:
    return {
        "status": state.get("status", "in_progress"),
        "interviewState": state,
        "structuredDraft": {},
    }


def test_build_live_context_includes_required_question_plan_items(
    monkeypatch,
    clean_interview_store,
) -> None:
    user = _user()
    knowledge = {
        "id": "knowledge-live-test",
        "interviewPlan": {"profile": "fixed_form", "purpose": "人物インタビュー"},
    }
    state = {
        "status": "in_progress",
        "interviewProfile": "fixed_form",
        "fieldStates": {
            "field-profile": {
                "answerState": "UNANSWERED",
                "capturedItems": [],
            },
        },
    }
    monkeypatch.setattr(
        live_interview_module,
        "get_interview_state_snapshot",
        lambda *_args, **_kwargs: _snapshot(state),
    )
    store.upsert(
        "knowledge_fields",
        {
            "id": "field-profile",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge["id"],
            "name": "基本プロフィール",
            "description": "氏名、所属、役職を確認する。",
            "required": True,
            "askByAi": True,
            "displayOrder": 1,
            "questionPlan": {
                "requiredItems": [
                    {"itemId": "name", "label": "お名前", "description": "氏名"},
                    {"itemId": "department", "label": "所属", "description": "所属"},
                    {"itemId": "role", "label": "役職", "description": "役職"},
                ],
            },
        },
    )

    context = live_interview_module.build_live_interview_context(_record(), knowledge, user)

    field = context["fields"][0]
    assert [item["label"] for item in field["required_items"]] == ["お名前", "所属", "役職"]
    assert field["missing_required_items"] == ["お名前", "所属", "役職"]
    assert field["needs_confirmation"] is True


def test_build_live_context_exposes_candidate_confirmation_state(
    monkeypatch,
    clean_interview_store,
) -> None:
    user = _user()
    knowledge = {
        "id": "knowledge-live-test",
        "knowledgeDbId": "knowledge-db-live-test",
        "interviewPlan": {"profile": "fixed_form"},
    }
    state = {
        "status": "in_progress",
        "interviewProfile": "fixed_form",
        "fieldStates": {
            "field-profile": {
                "answerState": "CANDIDATE_PENDING",
                "answerResolution": "TENTATIVE",
                "candidateAnswer": "生成AIを活用して設計を効率化したい",
                "capturedItems": [{"itemId": "goal"}],
            },
        },
    }
    monkeypatch.setattr(
        live_interview_module,
        "get_interview_state_snapshot",
        lambda *_args, **_kwargs: _snapshot(state),
    )
    store.upsert("knowledge_dbs", {
        "id": "knowledge-db-live-test", "tenantId": user.tenant_id, "status": "active",
    })
    store.upsert("knowledge_fields", {
        "id": "field-profile", "tenantId": user.tenant_id,
        "knowledgeId": knowledge["id"], "name": "方針", "required": True, "askByAi": True,
        "questionPlan": {"requiredItems": [{"itemId": "goal", "label": "方針"}]},
    })

    field = live_interview_module.build_live_interview_context(
        _record(), knowledge, user,
    )["fields"][0]

    assert field["answer_state"] == "CANDIDATE_PENDING"
    assert field["answer_resolution"] == "TENTATIVE"
    assert field["candidate_answer"] == "生成AIを活用して設計を効率化したい"
    assert field["needs_confirmation"] is True


def test_build_live_context_includes_direct_prior_knowledge(
    monkeypatch,
    clean_interview_store,
) -> None:
    user = _user()
    knowledge = {
        "id": "knowledge-live-test",
        "interviewPlan": {"profile": "fixed_form", "purpose": "人物インタビュー"},
    }
    monkeypatch.setattr(
        live_interview_module,
        "get_interview_state_snapshot",
        lambda *_args, **_kwargs: _snapshot({"status": "in_progress", "fieldStates": {}}),
    )
    store.upsert(
        "documents",
        {
            "id": "live-prior-knowledge",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge["id"],
            "sourceType": "prior_knowledge",
            "title": "PLM用語",
            "fileName": "PLM用語",
            "contentFormat": "markdown",
            "knowledgeType": "glossary",
            "contentType": "text/markdown",
            "ingestionStatus": "indexed",
        },
    )
    store.upsert(
        "document_chunks",
        {
            "id": "live-prior-knowledge:chunk:1",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge["id"],
            "documentId": "live-prior-knowledge",
            "status": "indexed",
            "title": "PLM用語",
            "content": "PLMは製品ライフサイクル管理を指す。",
        },
    )

    context = live_interview_module.build_live_interview_context(_record(), knowledge, user)

    assert context["prior_knowledge"][0]["knowledge_type"] == "glossary"
    assert "製品ライフサイクル管理" in context["prior_knowledge"][0]["content"]


def test_process_live_delegation_is_idempotent(monkeypatch, clean_interview_store) -> None:
    user = _user()
    record = _record()
    state = {
        "id": "interview-state-record-live-test",
        "recordId": record["id"],
        "status": "in_progress",
        "stateVersion": 3,
        "currentQuestionId": "question-1",
        "currentFieldId": "field-profile",
        "askedQuestions": [
            {
                "questionId": "question-1",
                "questionType": "structured",
                "fieldId": "field-profile",
                "text": "基本プロフィールについて教えてください。",
                "targetType": "field",
                "targetId": "field-profile",
            },
        ],
    }
    snapshots = [_snapshot(state), _snapshot({**state, "stateVersion": 4})]
    monkeypatch.setattr(
        live_interview_module,
        "get_interview_state_snapshot",
        lambda *_args, **_kwargs: snapshots.pop(0) if snapshots else _snapshot(state),
    )
    generated: list[str] = []
    monkeypatch.setattr(
        live_interview_module,
        "generate_interview_reply",
        lambda record, _user, **_kwargs: (
            generated.append(record["id"])
            or SimpleNamespace(metadata={"completionStatus": "in_progress"})
        ),
    )

    payload = LiveDelegationCreate(
        record_id=record["id"],
        delegation_id="delegation-1",
        transcript="田中です。開発部のマネージャーです。",
    )
    first = live_interview_module.process_live_delegation(payload, record=record, user=user)
    second = live_interview_module.process_live_delegation(payload, record=record, user=user)

    assert first["status"] == "updated"
    assert second["status"] == "duplicate"
    assert generated == [record["id"]]
    messages = store.list("messages", user.tenant_id)
    assert len(messages) == 1
    assert messages[0]["answerToQuestionId"] == "question-1"
