import pytest
from fastapi import HTTPException

from ai_interviewer_api.agents.interview_knowledge.coordinator import build_initial_structured_state
from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.schemas.live import LiveStateUpdateCreate
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


def test_process_live_state_update_persists_semantics_without_generating_reply(
    monkeypatch,
    clean_interview_store,
) -> None:
    user = _user()
    record = _record()
    knowledge = {
        "id": "knowledge-live-test",
        "tenantId": user.tenant_id,
        "interviewPlan": {"profile": "fixed_form"},
    }
    field = {
        "id": "field-profile",
        "tenantId": user.tenant_id,
        "knowledgeId": knowledge["id"],
        "name": "経験",
        "description": "経験と変化を教えてください。",
        "required": True,
        "askByAi": True,
        "questionPlan": {
            "requiredItems": [
                {"itemId": "trigger_experience", "label": "きっかけとなった経験"},
                {"itemId": "impact_or_change", "label": "経験による変化"},
            ],
        },
    }
    store.upsert("knowledges", knowledge)
    store.upsert("records", record)
    store.upsert("knowledge_fields", field)
    state = build_initial_structured_state("fixed_form", [field])
    state.update(
        {
            "id": "interview-state-record-live-test",
            "recordId": record["id"],
            "currentQuestionId": "live-field-field-profile",
            "currentFieldId": "field-profile",
            "askedQuestions": [
                {
                    "questionId": "live-field-field-profile",
                    "questionType": "structured",
                    "fieldId": "field-profile",
                    "text": "経験と変化を教えてください。",
                    "targetType": "field",
                    "targetId": "field-profile",
                    "targetLabel": "経験",
                    "questionDefinition": {
                        "requiredItems": field["questionPlan"]["requiredItems"],
                        "optionalItems": [],
                    },
                },
            ],
        }
    )
    store.upsert("interview_states", state)
    payload = LiveStateUpdateCreate(
        record_id=record["id"],
        delegation_id="delegation-1",
        call_id="call-1",
        turn_id="turn-1",
        base_state_version=1,
        question_id="live-field-field-profile",
        answer_status="PARTIAL",
        covered_aspects=[
            {
                "aspect_id": "trigger_experience",
                "evidence": "顧客の要望を聞きながらシステム開発を進めた経験です。",
            },
        ],
        missing_aspects=["impact_or_change"],
        evidence=[
            {
                "aspect_id": "trigger_experience",
                "text": "顧客の要望を聞きながらシステム開発を進めた経験です。",
            },
        ],
        transcript="顧客の要望を聞きながらシステム開発を進めました。",
    )
    first = live_interview_module.process_live_state_update(
        payload,
        record=record,
        knowledge=knowledge,
        user=user,
    )
    second = live_interview_module.process_live_state_update(
        payload,
        record=record,
        knowledge=knowledge,
        user=user,
    )

    assert first["status"] == "applied"
    assert second["status"] == "duplicate"
    assert first["coveredAspects"] == ["trigger_experience"]
    assert first["missingAspects"] == ["impact_or_change"]
    assert first["persistedState"]["fieldStates"]["field-profile"]["capturedItems"]
    assert first["checklist"][0]["missing_required_items"] == ["経験による変化"]
    assert first["current"]["question_id"] == "live-field-field-profile"
    messages = store.list("messages", user.tenant_id)
    assert len(messages) == 1
    assert messages[0]["answerToQuestionId"] == "live-field-field-profile"
    persisted = store.get("interview_states", state["id"])
    assert persisted["lastAnswerStatus"] == "PARTIAL"
    assert persisted["lastAspectAssessments"][0]["aspectId"] == "trigger_experience"
    assert persisted["lastAspectAssessments"][1]["status"] == "MISSING"


def test_process_live_state_update_confirms_complete_field_for_sidebar(
    clean_interview_store,
) -> None:
    user = _user()
    record = _record()
    knowledge = {
        "id": "knowledge-live-test",
        "tenantId": user.tenant_id,
        "interviewPlan": {"profile": "fixed_form"},
    }
    field = {
        "id": "field-role",
        "tenantId": user.tenant_id,
        "knowledgeId": knowledge["id"],
        "name": "役割",
        "description": "現在の役割を教えてください。",
        "required": True,
        "askByAi": True,
        "questionPlan": {
            "requiredItems": [{"itemId": "role", "label": "役割"}],
        },
    }
    store.upsert("knowledges", knowledge)
    store.upsert("records", record)
    store.upsert("knowledge_fields", field)
    state = build_initial_structured_state("fixed_form", [field])
    state.update(
        {
            "id": "interview-state-record-live-test",
            "recordId": record["id"],
            "currentQuestionId": "live-field-field-role",
            "currentFieldId": field["id"],
            "askedQuestions": [
                {
                    "questionId": "live-field-field-role",
                    "questionType": "structured",
                    "fieldId": field["id"],
                    "text": field["description"],
                    "targetType": "field",
                    "targetId": field["id"],
                    "targetLabel": field["name"],
                    "questionDefinition": {
                        "requiredItems": field["questionPlan"]["requiredItems"],
                        "optionalItems": [],
                    },
                },
            ],
        }
    )
    store.upsert("interview_states", state)
    payload = LiveStateUpdateCreate(
        record_id=record["id"],
        delegation_id="delegation-role",
        call_id="call-role",
        turn_id="turn-role",
        base_state_version=1,
        question_id="live-field-field-role",
        answer_status="ANSWERED",
        covered_aspects=[{"aspect_id": "role", "evidence": "プロジェクトリーダー"}],
        evidence=[{"aspect_id": "role", "text": "プロジェクトリーダー"}],
        transcript="プロジェクトリーダーです。",
    )

    result = live_interview_module.process_live_state_update(
        payload,
        record=record,
        knowledge=knowledge,
        user=user,
    )

    persisted = store.get("interview_states", state["id"])
    field_state = persisted["fieldStates"][field["id"]]
    assert result["status"] == "applied"
    assert field_state["answerState"] == "CONFIRMED"
    assert field_state["recordAnswer"] == "プロジェクトリーダー"
    assert result["checklist"][0]["answer_state"] == "CONFIRMED"


def test_process_live_state_update_accepts_the_next_unasked_live_question(
    clean_interview_store,
) -> None:
    user = _user()
    record = _record()
    knowledge = {
        "id": "knowledge-live-test",
        "tenantId": user.tenant_id,
        "interviewPlan": {"profile": "fixed_form"},
    }
    profile_field = {
        "id": "field-profile",
        "tenantId": user.tenant_id,
        "knowledgeId": knowledge["id"],
        "name": "所属",
        "required": True,
        "askByAi": True,
        "questionPlan": {"requiredItems": [{"itemId": "department", "label": "所属"}]},
    }
    role_field = {
        "id": "field-role",
        "tenantId": user.tenant_id,
        "knowledgeId": knowledge["id"],
        "name": "役職",
        "required": True,
        "askByAi": True,
        "questionPlan": {"requiredItems": [{"itemId": "role", "label": "役職"}]},
    }
    store.upsert("knowledges", knowledge)
    store.upsert("records", record)
    store.upsert("knowledge_fields", profile_field)
    store.upsert("knowledge_fields", role_field)
    state = build_initial_structured_state("fixed_form", [profile_field, role_field])
    state.update(
        {
            "id": "interview-state-record-live-test",
            "recordId": record["id"],
            "tenantId": user.tenant_id,
            "stateVersion": 0,
            "currentQuestionId": "live-field-field-profile",
            "currentFieldId": profile_field["id"],
            "askedQuestions": [
                {
                    "questionId": "live-field-field-profile",
                    "questionType": "structured",
                    "fieldId": profile_field["id"],
                    "targetType": "field",
                    "targetId": profile_field["id"],
                    "targetLabel": profile_field["name"],
                    "questionDefinition": {
                        "requiredItems": profile_field["questionPlan"]["requiredItems"],
                        "optionalItems": [],
                    },
                },
            ],
        }
    )
    store.upsert("interview_states", state)

    first = live_interview_module.process_live_state_update(
        LiveStateUpdateCreate(
            record_id=record["id"],
            delegation_id="delegation-profile",
            call_id="call-profile",
            turn_id="turn-profile",
            base_state_version=1,
            question_id="live-field-field-profile",
            answer_status="ANSWERED",
            covered_aspects=[{"aspect_id": "department", "evidence": "スマート技術開発部"}],
            evidence=[{"aspect_id": "department", "text": "スマート技術開発部"}],
            transcript="スマート技術開発部です。",
        ),
        record=record,
        knowledge=knowledge,
        user=user,
    )
    second = live_interview_module.process_live_state_update(
        LiveStateUpdateCreate(
            record_id=record["id"],
            delegation_id="delegation-role",
            call_id="call-role",
            turn_id="turn-role",
            base_state_version=first["stateVersion"],
            question_id="live-field-field-role",
            answer_status="ANSWERED",
            covered_aspects=[{"aspect_id": "role", "evidence": "シニアエンジニア"}],
            evidence=[{"aspect_id": "role", "text": "シニアエンジニア"}],
            transcript="シニアエンジニアです。",
        ),
        record=record,
        knowledge=knowledge,
        user=user,
    )

    persisted = store.get("interview_states", state["id"])
    assert first["status"] == "applied"
    assert second["status"] == "applied"
    assert persisted["currentQuestionId"] == "live-field-field-role"
    assert persisted["fieldStates"]["field-profile"]["recordAnswer"] == "スマート技術開発部"
    assert persisted["fieldStates"]["field-role"]["recordAnswer"] == "シニアエンジニア"
    assert len(store.list("messages", user.tenant_id)) == 2


def test_process_live_state_update_rejects_out_of_order_state(clean_interview_store) -> None:
    user = _user()
    record = _record()
    knowledge = {
        "id": "knowledge-live-test",
        "tenantId": user.tenant_id,
        "interviewPlan": {"profile": "fixed_form"},
    }
    field = {
        "id": "field-profile",
        "tenantId": user.tenant_id,
        "knowledgeId": knowledge["id"],
        "name": "役割",
        "questionPlan": {"requiredItems": [{"itemId": "role", "label": "役割"}]},
    }
    store.upsert("knowledges", knowledge)
    store.upsert("records", record)
    store.upsert("knowledge_fields", field)
    state = build_initial_structured_state("fixed_form", [field])
    state.update({"id": "interview-state-record-live-test", "recordId": record["id"], "stateVersion": 2})
    store.upsert("interview_states", state)
    payload = LiveStateUpdateCreate(
        record_id=record["id"], delegation_id="delegation-old", call_id="call-old",
        turn_id="turn-old", base_state_version=1, question_id="live-field-field-profile",
        answer_status="ANSWERED",
        covered_aspects=[{"aspect_id": "role", "evidence": "リーダー"}],
    )

    result = live_interview_module.process_live_state_update(
        payload, record=record, knowledge=knowledge, user=user,
    )

    assert result["status"] == "stale"
    assert store.list("messages", user.tenant_id) == []


def test_process_live_state_update_rejects_an_old_question_at_the_current_version(
    clean_interview_store,
) -> None:
    user = _user()
    record = _record()
    knowledge = {
        "id": "knowledge-live-test",
        "tenantId": user.tenant_id,
        "interviewPlan": {"profile": "fixed_form"},
    }
    field = {
        "id": "field-profile",
        "tenantId": user.tenant_id,
        "knowledgeId": knowledge["id"],
        "name": "役割",
        "description": "現在の役割を教えてください。",
        "required": True,
        "askByAi": True,
        "questionPlan": {"requiredItems": [{"itemId": "role", "label": "役割"}]},
    }
    store.upsert("knowledges", knowledge)
    store.upsert("records", record)
    store.upsert("knowledge_fields", field)
    state = build_initial_structured_state("fixed_form", [field])
    state.update(
        {
            "id": "interview-state-record-live-test",
            "recordId": record["id"],
            "tenantId": user.tenant_id,
            "stateVersion": 5,
            "currentQuestionId": "question-current",
            "currentFieldId": field["id"],
            "askedQuestions": [
                {
                    "questionId": "question-old",
                    "questionType": "structured",
                    "fieldId": field["id"],
                    "targetType": "field",
                    "targetId": field["id"],
                    "targetLabel": field["name"],
                    "questionDefinition": {"requiredItems": field["questionPlan"]["requiredItems"]},
                },
                {
                    "questionId": "question-current",
                    "questionType": "structured",
                    "fieldId": field["id"],
                    "targetType": "field",
                    "targetId": field["id"],
                    "targetLabel": field["name"],
                    "questionDefinition": {"requiredItems": field["questionPlan"]["requiredItems"]},
                },
            ],
        }
    )
    store.upsert("interview_states", state)
    payload = LiveStateUpdateCreate(
        record_id=record["id"],
        delegation_id="delegation-old-question",
        call_id="call-old-question",
        turn_id="turn-old-question",
        base_state_version=6,
        question_id="question-old",
        answer_status="ANSWERED",
        covered_aspects=[{"aspect_id": "role", "evidence": "プロジェクトリーダー"}],
    )

    with pytest.raises(HTTPException) as error:
        live_interview_module.process_live_state_update(
            payload,
            record=record,
            knowledge=knowledge,
            user=user,
        )

    assert error.value.status_code == 409
    assert error.value.detail == "live_question_not_current"
    assert store.get("interview_states", state["id"])["currentQuestionId"] == "question-current"
    assert store.list("messages", user.tenant_id) == []
