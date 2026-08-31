from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

import pytest

from ai_interviewer_api.agents.interview.adapter import AdaptedInterviewTurnResult
from ai_interviewer_api.agents.interview_knowledge.schemas import (
    QuestionGenerationOutput,
    StructuredInterviewOutput,
)
from ai_interviewer_api.agents.interview_knowledge.service import (
    generate_structured_interview_result,
)
from ai_interviewer_api.auth.deps import DEV_TOKENS, UserContext
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.internal_voice import (
    create_internal_voice_turn,
    mark_internal_initial_reply_sent,
    process_internal_voice_turn,
)
from ai_interviewer_api.routers.voice_sessions import create_record_voice_session
from ai_interviewer_api.schemas.retrieval import (
    DocumentQuestionCandidate,
    RetrievedKnowledgeContext,
)
from ai_interviewer_api.schemas.voice import VoiceSessionCreate, VoiceTurnCreate
from ai_interviewer_api.services import ai_interview
from ai_interviewer_api.services import voice_interview as voice_interview_service
from ai_interviewer_api.services.dialogue_interpreter import DialogueInterpretation
from ai_interviewer_api.services.interview_document_retrieval import (
    retrieve_interview_document_context,
    validate_document_question_candidate,
)


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.tables.clear()


def _seed_interview_context(
    user: UserContext,
    *,
    profile: str = "fixed_form",
    field_count: int = 1,
    retrieval_policy: str = "required",
) -> tuple[dict, dict, list[dict]]:
    knowledge = {
        "id": "document-context-knowledge",
        "tenantId": user.tenant_id,
        "name": "設備保全インタビュー",
        "description": "設備の荷重ばらつきと発生条件を確認する",
        "purpose": "設備の発生条件を整理する",
        "targetEquipment": "med900",
        "interviewPlan": {"profile": profile, "modelId": "global.openai.gpt-5.6-terra"},
    }
    record = {
        "id": "document-context-record",
        "tenantId": user.tenant_id,
        "knowledgeId": knowledge["id"],
        "knowledgeName": knowledge["name"],
        "title": "med900 荷重ばらつき",
        "status": "in_progress",
        "ownerUserId": user.user_id,
    }
    fields = [
        {
            "id": f"document-context-field-{index}",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge["id"],
            "name": "発生条件" if index == 1 else f"確認項目{index}",
            "description": "荷重ばらつきが発生するタイミング",
            "inputType": "long_text",
            "required": True,
            "askByAi": True,
            "retrievalPolicy": retrieval_policy,
            "aiQuestionExamples": ["荷重のばらつきは、どんなタイミングで発生しますか？"],
            "displayOrder": index,
        }
        for index in range(1, field_count + 1)
    ]
    store.upsert("knowledges", knowledge)
    store.upsert("records", record)
    for field in fields:
        store.upsert("knowledge_fields", field)
    store.upsert(
        "documents",
        {
            "id": "document-context-document",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge["id"],
            "fileName": "med900保全手順.pdf",
            "contentType": "application/pdf",
            "ingestionStatus": "indexed",
            "progressPercent": 100,
        },
    )
    store.upsert(
        "document_chunks",
        {
            "id": "document-context-chunk",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge["id"],
            "documentId": "document-context-document",
            "status": "indexed",
            "title": "med900保全手順.pdf",
            "content": "med900の荷重ばらつきは朝一の暖機前と停止後に発生しやすい。",
        },
    )
    return record, knowledge, fields


def test_document_retrieval_uses_only_indexed_documents_and_scoped_chunks() -> None:
    user = DEV_TOKENS["dev-manager"]
    record, knowledge, fields = _seed_interview_context(user)
    store.upsert(
        "documents",
        {
            "id": "queued-document",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge["id"],
            "fileName": "queued.pdf",
            "ingestionStatus": "processing",
        },
    )
    store.upsert(
        "document_chunks",
        {
            "id": "queued-chunk",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge["id"],
            "documentId": "queued-document",
            "status": "indexed",
            "content": "med900の朝一の情報ではない混入データ。",
        },
    )
    store.upsert(
        "document_chunks",
        {
            "id": "other-tenant-chunk",
            "tenantId": "tenant-other",
            "knowledgeId": knowledge["id"],
            "status": "indexed",
            "content": "med900の別テナント情報。",
        },
    )

    contexts = retrieve_interview_document_context(
        record=record,
        knowledge=knowledge,
        user=user,
        current_field=fields[0],
        retrieval_policy="required",
    )

    assert [item.source_id for item in contexts] == ["document-context-chunk"]
    assert contexts[0].content.startswith("med900の荷重ばらつき")


def test_fixed_interview_passes_document_context_to_agent_and_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = DEV_TOKENS["dev-manager"]
    record, _knowledge, fields = _seed_interview_context(user)
    monkeypatch.setattr(ai_interview, "_generate_document_candidate_question", lambda **_: None)
    first = ai_interview.generate_interview_reply(record, user, persist_assistant_messages=False)
    first_question = first.metadata["question"]
    assert first_question["retrievedSources"][0]["sourceId"] == "document-context-chunk"

    store.upsert(
        "messages",
        {
            "id": "document-context-user-answer",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "content": "朝一です",
            "isActualUtterance": True,
            "turnType": "ANSWER",
            "answerToQuestionId": first_question["questionId"],
            "answerToFieldId": fields[0]["id"],
        },
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        ai_interview,
        "interpret_dialogue_act",
        lambda **_: DialogueInterpretation(act="ANSWER"),
    )

    def fake_agent(*_args: object, **kwargs: object) -> AdaptedInterviewTurnResult:
        captured["retrieved_context"] = kwargs["retrieved_context"]
        return AdaptedInterviewTurnResult(
            reply_text="もう少し確認します。",
            field_evaluation={
                "fieldId": fields[0]["id"],
                "isComplete": False,
                "answerSummary": "朝一に発生する",
                "missingInformation": ["暖機前かどうか"],
                "nextAction": "follow_up",
            },
            follow_up_question="朝一の暖機前に発生しますか？",
            used_tools=[],
        )

    monkeypatch.setattr(ai_interview, "run_adapted_interview_turn", fake_agent)
    result = ai_interview.generate_interview_reply(record, user)

    retrieved_context = captured["retrieved_context"]
    assert isinstance(retrieved_context, list)
    assert retrieved_context[0].source_id == "document-context-chunk"
    assert result.metadata["question"]["retrievedSources"][0]["sourceId"] == "document-context-chunk"


def test_fixed_interview_uses_document_value_as_confirmation_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = DEV_TOKENS["dev-manager"]
    record, _, fields = _seed_interview_context(user)
    fields[0]["name"] = "設備名"
    store.upsert("knowledge_fields", fields[0])
    monkeypatch.setattr(
        ai_interview,
        "_generate_document_candidate_question",
        lambda **_: DocumentQuestionCandidate(
            value="med900",
            source_ids=("document-context-chunk",),
        ),
    )

    result = ai_interview.generate_interview_reply(record, user, persist_assistant_messages=False)

    question = result.metadata["question"]
    field_state = result.metadata["interviewState"]["fieldStates"][fields[0]["id"]]
    assert question["text"] == "事前知識では設備名は「med900」となっています。この内容で合っていますか？"
    assert question["candidateSource"] == "document_reference"
    assert question["candidateSourceIds"] == ["document-context-chunk"]
    assert field_state["answerState"] == "AWAITING_CONFIRMATION"
    assert field_state["candidateAnswer"] == "med900"


class _CapturingStructuredProvider:
    def __init__(self) -> None:
        self.question_context: Mapping[str, object] | None = None

    def interpret(self, **_: object) -> StructuredInterviewOutput:
        return StructuredInterviewOutput()

    def generate_question(
        self,
        *,
        context: Mapping[str, object],
        **_: object,
    ) -> QuestionGenerationOutput:
        self.question_context = context
        return QuestionGenerationOutput(questionText="発生条件を詳しく教えてください。")


def test_structured_question_generation_receives_document_context() -> None:
    user = DEV_TOKENS["dev-manager"]
    record, knowledge, _ = _seed_interview_context(
        user,
        profile="business_process",
    )
    provider = _CapturingStructuredProvider()

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    assert provider.question_context is not None
    retrieved = provider.question_context["retrieved_knowledge"]
    assert isinstance(retrieved, list)
    assert retrieved[0]["source_id"] == "document-context-chunk"
    assert "暖機前" in retrieved[0]["content"]
    assert result["question"]["retrievedSources"][0]["sourceId"] == "document-context-chunk"
    assert result["retrievalExecuted"] is True


def test_voice_initial_question_exposes_the_same_document_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = DEV_TOKENS["dev-manager"]
    record, _, _ = _seed_interview_context(user, field_count=2)
    monkeypatch.setattr(ai_interview, "_generate_document_candidate_question", lambda **_: None)

    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    state = store.get("interview_states", f"interview-state-{record['id']}")

    assert session["currentQuestionId"] == "q-001"
    assert state["askedQuestions"][0]["retrievedSources"][0]["sourceId"] == "document-context-chunk"


def test_voice_initial_question_reuses_document_confirmation_from_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = DEV_TOKENS["dev-manager"]
    record, _, fields = _seed_interview_context(user)
    fields[0]["name"] = "設備名"
    store.upsert("knowledge_fields", fields[0])
    monkeypatch.setattr(
        ai_interview,
        "_generate_document_candidate_question",
        lambda **_: DocumentQuestionCandidate(
            value="med900",
            source_ids=("document-context-chunk",),
        ),
    )

    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)

    assert session["initialReplyText"] == (
        "これからインタビューを開始します。"
        "事前知識では設備名は「med900」となっています。この内容で合っていますか？"
    )
    state = store.get("interview_states", f"interview-state-{record['id']}")
    assert state["fieldStates"][fields[0]["id"]]["candidateSource"] == "document_reference"


def test_voice_document_candidate_is_confirmed_without_reasking_the_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = DEV_TOKENS["dev-manager"]
    record, _, fields = _seed_interview_context(user)
    fields[0]["name"] = "設備名"
    store.upsert("knowledge_fields", fields[0])
    monkeypatch.setattr(
        ai_interview,
        "_generate_document_candidate_question",
        lambda **_: DocumentQuestionCandidate(
            value="med900",
            source_ids=("document-context-chunk",),
        ),
    )
    monkeypatch.setattr(
        voice_interview_service,
        "_evaluate_confirmation_response",
        lambda **kwargs: voice_interview_service.VoiceConfirmationEvaluation(
            outcome="CONFIRM",
            record_answer=kwargs["candidate_answer"],
        ),
    )

    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="はい"),
    )

    result = process_internal_voice_turn(session["id"], turn["id"])
    state = store.get("interview_states", f"interview-state-{record['id']}")
    field_state = state["fieldStates"][fields[0]["id"]]

    assert result["action"] == "finish"
    assert field_state["answerState"] == "CONFIRMED"
    assert field_state["recordAnswer"] == "med900"
    assert field_state["confirmedSource"] == "document_reference"
    assert field_state["confirmedSourceIds"] == ["document-context-chunk"]


def test_never_policy_does_not_read_document_context() -> None:
    user = DEV_TOKENS["dev-manager"]
    record, knowledge, fields = _seed_interview_context(
        user,
        retrieval_policy="never",
    )

    contexts = retrieve_interview_document_context(
        record=record,
        knowledge=knowledge,
        user=user,
        current_field=fields[0],
        retrieval_policy="never",
    )

    assert contexts == []


class _DocumentCandidateProvider:
    def __init__(self, candidate: str, source_id: str) -> None:
        self.candidate = candidate
        self.source_id = source_id

    def interpret(self, **_: object) -> StructuredInterviewOutput:
        return StructuredInterviewOutput()

    def generate_question(
        self,
        *,
        context: Mapping[str, object],
        **_: object,
    ) -> QuestionGenerationOutput:
        retrieved = context["retrieved_knowledge"]
        assert isinstance(retrieved, list)
        return QuestionGenerationOutput(
            questionText="設備名を教えてください。",
            documentCandidateValue=self.candidate,
            documentCandidateSourceIds=[self.source_id],
        )


class _MultipleDocumentCandidateProvider:
    candidates: ClassVar[dict[str, str]] = {
        "設備名": "圧入機A",
        "発生条件": "朝一",
    }

    def interpret(self, **_: object) -> StructuredInterviewOutput:
        return StructuredInterviewOutput()

    def generate_question(
        self,
        *,
        context: Mapping[str, object],
        target: Mapping[str, object],
        **_: object,
    ) -> QuestionGenerationOutput:
        retrieved = context["retrieved_knowledge"]
        assert isinstance(retrieved, list)
        label = str(target.get("label") or "")
        candidate = self.candidates[label]
        return QuestionGenerationOutput(
            questionText=f"{label}を教えてください。",
            documentCandidateValue=candidate,
            documentCandidateSourceIds=["document-context-chunk"],
        )


def test_all_document_backed_fixed_fields_start_as_confirmation_targets() -> None:
    user = DEV_TOKENS["dev-manager"]
    record, knowledge, fields = _seed_interview_context(
        user,
        field_count=2,
        retrieval_policy="auto",
    )
    fields[0]["name"] = "設備名"
    fields[0]["description"] = "対象設備を特定する"
    fields[1]["name"] = "発生条件"
    fields[1]["description"] = "荷重ばらつきが発生するタイミング"
    for field in fields:
        store.upsert("knowledge_fields", field)
    store.upsert(
        "document_chunks",
        {
            "id": "document-context-chunk",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge["id"],
            "documentId": "document-context-document",
            "status": "indexed",
            "title": "med900保全手順.pdf",
            "content": "対象設備は圧入機Aです。荷重ばらつきは朝一に発生しやすい。",
        },
    )
    provider = _MultipleDocumentCandidateProvider()

    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    first_question = first["question"]
    assert first_question["candidateValue"] == "圧入機A"
    assert first_question["candidateSource"] == "document_reference"

    store.upsert(
        "messages",
        {
            "id": "document-context-confirmation",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "content": "はい",
            "isActualUtterance": True,
            "turnType": "ANSWER",
            "answerToQuestionId": first_question["questionId"],
            "answerToFieldId": fields[0]["id"],
        },
    )

    second = generate_structured_interview_result(record, knowledge, user, provider=provider)
    second_question = second["question"]
    assert second_question["targetLabel"] == "発生条件"
    assert second_question["candidateValue"] == "朝一"
    assert second_question["candidateSource"] == "document_reference"
    assert second_question["text"] == "事前知識では発生条件は「朝一」となっています。この内容で合っていますか？"


def test_document_candidate_becomes_confirmation_target_and_is_confirmed() -> None:
    user = DEV_TOKENS["dev-manager"]
    knowledge = {
        "id": "document-candidate-knowledge",
        "tenantId": user.tenant_id,
        "name": "設備保全インタビュー",
        "interviewPlan": {"profile": "fixed_form"},
    }
    record = {
        "id": "document-candidate-record",
        "tenantId": user.tenant_id,
        "knowledgeId": knowledge["id"],
        "title": "設備確認",
        "status": "in_progress",
        "ownerUserId": user.user_id,
    }
    field = {
        "id": "document-candidate-equipment",
        "tenantId": user.tenant_id,
        "knowledgeId": knowledge["id"],
        "name": "設備名",
        "description": "対象設備を特定する",
        "inputType": "short_text",
        "required": True,
        "retrievalPolicy": "auto",
        "displayOrder": 1,
    }
    store.upsert("knowledges", knowledge)
    store.upsert("records", record)
    store.upsert("knowledge_fields", field)
    store.upsert(
        "documents",
        {
            "id": "document-candidate-document",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge["id"],
            "fileName": "設備手順.md",
            "ingestionStatus": "indexed",
        },
    )
    store.upsert(
        "document_chunks",
        {
            "id": "document-candidate-chunk",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge["id"],
            "documentId": "document-candidate-document",
            "status": "indexed",
            "title": "設備手順.md",
            "content": "対象設備は圧入機Aです。med900を使用します。",
        },
    )
    provider = _DocumentCandidateProvider("圧入機A", "document-candidate-chunk")

    first = generate_structured_interview_result(record, knowledge, user, provider=provider)

    question = first["question"]
    field_state = first["interviewState"]["fieldStates"][field["id"]]
    assert question["text"] == "事前知識では設備名は「圧入機A」となっています。この内容で合っていますか？"
    assert question["candidateSource"] == "document_reference"
    assert question["candidateValue"] == "圧入機A"
    assert question["candidateSourceIds"] == ["document-candidate-chunk"]
    assert field_state["answerState"] == "AWAITING_CONFIRMATION"
    assert field_state["candidateAnswer"] == "圧入機A"
    assert field_state["recordAnswer"] is None
    assert field_state["candidateSource"] == "document_reference"
    assert field_state["candidateSourceIds"] == ["document-candidate-chunk"]

    store.upsert(
        "messages",
        {
            "id": "document-candidate-confirmation",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "content": "はい",
            "isActualUtterance": True,
            "turnType": "ANSWER",
            "answerToQuestionId": question["questionId"],
            "answerToFieldId": field["id"],
        },
    )
    confirmed = generate_structured_interview_result(record, knowledge, user, provider=provider)

    confirmed_state = confirmed["interviewState"]["fieldStates"][field["id"]]
    assert confirmed["status"] == "completed"
    assert confirmed_state["answerState"] == "CONFIRMED"
    assert confirmed_state["recordAnswer"] == "圧入機A"
    assert confirmed_state["confirmedSource"] == "document_reference"
    assert confirmed_state["confirmedSourceIds"] == ["document-candidate-chunk"]


def test_document_candidate_is_ignored_when_backend_source_does_not_support_value() -> None:
    contexts = [
        RetrievedKnowledgeContext(
            source_type="document_chunk",
            source_id="chunk-1",
            title="設備手順.md",
            content="対象設備は圧入機Aです。",
        )
    ]

    candidate = validate_document_question_candidate(
        value="圧入機B",
        source_ids=["chunk-1"],
        contexts=contexts,
    )

    assert candidate is None
