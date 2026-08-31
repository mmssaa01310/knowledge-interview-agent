from __future__ import annotations

import json
from collections.abc import Mapping

import pytest
from botocore.credentials import Credentials

from ai_interviewer_api.agents.interview_knowledge.coordinator import (
    apply_structured_output,
    build_initial_structured_state,
    evaluate_completion,
    select_next_question_target,
)
from ai_interviewer_api.agents.interview_knowledge.provider import (
    BedrockResponsesStructuredProvider,
    _make_strict_schema,
)
from ai_interviewer_api.agents.interview_knowledge.schemas import (
    ApplicabilityUpdate,
    ProcessEdge,
    ProcessInteraction,
    ProcessNode,
    ProcessParticipant,
    ProcessPatch,
    QuestionGenerationOutput,
    RequirementUpdate,
    StructuredInterviewOutput,
)
from ai_interviewer_api.agents.interview_knowledge.service import (
    generate_structured_interview_result,
    get_structured_interview_state_snapshot,
    resolve_structured_model_id,
)
from ai_interviewer_api.auth.deps import DEV_TOKENS, UserContext
from ai_interviewer_api.repositories.store import store


class FakeStructuredProvider:
    def __init__(self, outputs: list[StructuredInterviewOutput]) -> None:
        self.outputs = iter(outputs)

    def interpret(self, **_: object) -> StructuredInterviewOutput:
        return next(self.outputs)

    def generate_question(self, *, target: Mapping[str, object], **_: object) -> QuestionGenerationOutput:
        return QuestionGenerationOutput(questionText=f"{target['label']}を教えてください。")


class FakeBedrockSession:
    def get_credentials(self) -> Credentials:
        return Credentials("access-key", "secret-key", "session-token")


class FakeHttpResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"output_text": '{"questionText":"利用者は誰ですか？"}'}


class CaptureHttpClient:
    def __init__(self, captured: dict[str, object]) -> None:
        self.captured = captured

    def __enter__(self) -> "CaptureHttpClient":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def post(self, url: str, **kwargs: object) -> FakeHttpResponse:
        self.captured["url"] = url
        self.captured.update(kwargs)
        return FakeHttpResponse()


class CaptureHttpClientFactory:
    def __init__(self, captured: dict[str, object]) -> None:
        self.captured = captured

    def __call__(self, **_: object) -> CaptureHttpClient:
        return CaptureHttpClient(self.captured)


class SequenceHttpResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload


class SequenceHttpClient:
    def __init__(self, responses: list[SequenceHttpResponse], captured: list[dict[str, object]]) -> None:
        self.responses = responses
        self.captured = captured

    def __enter__(self) -> "SequenceHttpClient":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def post(self, _url: str, **kwargs: object) -> SequenceHttpResponse:
        self.captured.append(json.loads(bytes(kwargs["content"]).decode("utf-8")))
        return self.responses.pop(0)


class SequenceHttpClientFactory:
    def __init__(self, responses: list[SequenceHttpResponse], captured: list[dict[str, object]]) -> None:
        self.responses = responses
        self.captured = captured

    def __call__(self, **_: object) -> SequenceHttpClient:
        return SequenceHttpClient(self.responses, self.captured)


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.tables.clear()


def test_structured_model_selection_uses_luna_when_configured() -> None:
    assert resolve_structured_model_id(
        {"interviewPlan": {"modelId": "global.openai.gpt-5.6-luna"}}
    ) == "global.openai.gpt-5.6-luna"


def test_bedrock_responses_provider_uses_global_profile_and_sigv4() -> None:
    captured: dict[str, object] = {}
    provider = BedrockResponsesStructuredProvider(
        model_id="global.openai.gpt-5.6-luna",
        region_name="us-east-1",
        session=FakeBedrockSession(),
        http_client_factory=CaptureHttpClientFactory(captured),
    )

    result = provider.generate_question(
        profile="system_requirement",
        context={"currentState": {}},
        target={"targetType": "requirement", "targetId": "requirement.users", "label": "利用者"},
        reasoning_effort="low",
    )

    request_body = json.loads(bytes(captured["content"]).decode("utf-8"))
    assert result.questionText == "利用者は誰ですか？"
    assert captured["url"] == "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1/responses"
    assert str(captured["headers"]["Authorization"]).startswith("AWS4-HMAC-SHA256")
    assert request_body["model"] == "global.openai.gpt-5.6-luna"
    assert request_body["reasoning"] == {"effort": "low"}
    assert request_body["max_output_tokens"] == 600
    assert request_body["text"]["format"]["type"] == "json_schema"
    assert request_body["text"]["format"]["strict"] is True


def test_bedrock_responses_provider_uses_process_model_edit_schema() -> None:
    captured: list[dict[str, object]] = []
    provider = BedrockResponsesStructuredProvider(
        model_id="global.openai.gpt-5.6-terra",
        region_name="us-east-1",
        session=FakeBedrockSession(),
        http_client_factory=SequenceHttpClientFactory(
            [SequenceHttpResponse({"output_text": '{"reply":"名称を変更しました。","processPatch":{}}'})],
            captured,
        ),
    )

    result = provider.edit_process_model(
        context={"instruction": "最初の処理名を変更する", "processModel": {"version": 0}},
        reasoning_effort="low",
    )

    request_body = captured[0]
    assert result.reply == "名称を変更しました。"
    assert request_body["model"] == "global.openai.gpt-5.6-terra"
    assert request_body["reasoning"] == {"effort": "low"}
    assert request_body["text"]["format"]["name"] == "process_model_edit_output"
    assert request_body["text"]["format"]["strict"] is True


def test_bedrock_responses_provider_retries_invalid_json_with_larger_budget() -> None:
    captured: list[dict[str, object]] = []
    provider = BedrockResponsesStructuredProvider(
        model_id="global.openai.gpt-5.6-terra",
        region_name="us-east-1",
        session=FakeBedrockSession(),
        http_client_factory=SequenceHttpClientFactory(
            [
                SequenceHttpResponse({"output_text": '{"dialogueAct":"ANSWER"'}),
                SequenceHttpResponse({"output_text": "{}"}),
            ],
            captured,
        ),
    )

    result = provider.interpret(
        profile="system_requirement",
        context={"currentState": {}},
        reasoning_effort="low",
    )

    assert result.dialogueAct == "ANSWER"
    assert captured[1]["max_output_tokens"] > captured[0]["max_output_tokens"]
    assert captured[1]["max_output_tokens"] == min(
        int(captured[0]["max_output_tokens"]) * 2,
        10_000,
    )


def test_priority_is_backend_controlled_and_only_one_candidate_is_confirmed() -> None:
    state = build_initial_structured_state("system_requirement", [])
    state["contradictions"] = [
        {
            "contradictionId": "c-1",
            "topic": "trigger",
            "description": "開始条件が一致しません。",
            "status": "open",
        }
    ]

    target = select_next_question_target(state, "system_requirement", [])

    assert target == {
        "targetType": "contradiction",
        "targetId": "c-1",
        "label": "trigger",
        "priority": 1,
    }


def test_unknown_applicability_is_not_treated_as_not_applicable() -> None:
    state = build_initial_structured_state("business_process", [])
    for requirement in state["requirementStates"].values():
        requirement["status"] = "CONFIRMED"
        requirement["value"] = "確認済み"
    state["applicabilityOverviewAsked"] = True

    target = select_next_question_target(state, "business_process", [])
    completion = evaluate_completion(state, "business_process", [])

    assert target == {
        "targetType": "applicability",
        "targetId": "branch",
        "label": "分岐",
        "priority": 4,
    }
    assert completion["complete"] is False
    assert "branch" in completion["unknownApplicabilityTopics"]


def test_completed_structured_interview_uses_clear_completion_message() -> None:
    user: UserContext = DEV_TOKENS["dev-manager"]
    record = {"id": "record-completed", "knowledgeId": "knowledge-completed", "title": "定型情報"}
    knowledge = {
        "id": "knowledge-completed",
        "name": "定型情報",
        "interviewPlan": {"profile": "fixed_form"},
    }
    store.upsert("records", {**record, "tenantId": user.tenant_id})
    store.upsert("knowledges", {**knowledge, "tenantId": user.tenant_id})
    state = build_initial_structured_state("fixed_form", [])
    state.update(
        {
            "id": f"interview-state-{record['id']}",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "status": "completed",
            "createdByUserId": user.user_id,
            "updatedByUserId": user.user_id,
            "createdAt": "2026-01-01T00:00:00+00:00",
            "updatedAt": "2026-01-01T00:00:00+00:00",
        }
    )
    store.upsert("interview_states", state)

    result = generate_structured_interview_result(record, knowledge, user)

    assert result["reply"] == "インタビューが完了しました。回答内容を確認してください。"


def test_structured_interview_extracts_candidate_then_requires_confirmation() -> None:
    user: UserContext = DEV_TOKENS["dev-manager"]
    record = {"id": "record-1", "knowledgeId": "knowledge-1", "title": "申請"}
    knowledge = {
        "id": "knowledge-1",
        "name": "申請要件",
        "interviewPlan": {"profile": "system_requirement"},
    }
    store.upsert("records", {**record, "tenantId": user.tenant_id})
    store.upsert("knowledges", {**knowledge, "tenantId": user.tenant_id})
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                dialogueAct="ANSWER",
                requirementUpdates=[
                    RequirementUpdate(
                        requirementId="requirement.purpose_problem",
                        value="申請処理を短縮する",
                        evidenceTranscriptIds=["voice-or-text-1"],
                    )
                ],
                processPatch=ProcessPatch(),
            ),
            StructuredInterviewOutput(dialogueAct="CONFIRMATION"),
        ]
    )

    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    first_question = first["question"]
    assert first_question["targetId"] == "requirement.purpose_problem"

    store.upsert(
        "messages",
        {
            "id": "voice-or-text-1",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "content": "申請処理を短縮したいです。",
            "isActualUtterance": True,
            "turnType": "ANSWER",
            "answerToQuestionId": first_question["questionId"],
        },
    )
    candidate = generate_structured_interview_result(record, knowledge, user, provider=provider)
    candidate_state = candidate["interviewState"]["requirementStates"]["requirement.purpose_problem"]
    assert candidate_state["status"] == "AWAITING_CONFIRMATION"
    assert candidate_state["value"] is None
    assert candidate["question"]["targetId"] == "requirement.purpose_problem"

    store.upsert(
        "messages",
        {
            "id": "voice-or-text-2",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "content": "はい。",
            "isActualUtterance": True,
            "turnType": "ANSWER",
            "answerToQuestionId": candidate["question"]["questionId"],
        },
    )
    confirmed = generate_structured_interview_result(record, knowledge, user, provider=provider)
    confirmed_state = confirmed["interviewState"]["requirementStates"]["requirement.purpose_problem"]
    assert confirmed_state["status"] == "CONFIRMED"
    assert confirmed_state["value"] == "申請処理を短縮する"
    assert confirmed["question"]["targetId"] == "requirement.users"


def test_structured_interview_confirms_explicit_affirmation_without_provider_retry() -> None:
    user: UserContext = DEV_TOKENS["dev-manager"]
    record = {
        "id": "record-explicit-confirm",
        "knowledgeId": "knowledge-explicit-confirm",
        "title": "申請",
        "interviewLocale": "en-US",
    }
    knowledge = {
        "id": "knowledge-explicit-confirm",
        "name": "申請要件",
        "interviewPlan": {"profile": "system_requirement"},
    }
    store.upsert("records", {**record, "tenantId": user.tenant_id})
    store.upsert("knowledges", {**knowledge, "tenantId": user.tenant_id})
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                dialogueAct="ANSWER",
                requirementUpdates=[
                    RequirementUpdate(
                        requirementId="requirement.purpose_problem",
                        value="申請処理を短縮する",
                        evidenceTranscriptIds=["explicit-answer"],
                    )
                ],
            )
        ]
    )

    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    first_question = first["question"]
    store.upsert(
        "messages",
        {
            "id": "explicit-answer",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "content": "申請処理を短縮したいです。",
            "isActualUtterance": True,
            "turnType": "ANSWER",
            "answerToQuestionId": first_question["questionId"],
        },
    )
    candidate = generate_structured_interview_result(record, knowledge, user, provider=provider)
    confirmation_question = candidate["question"]
    store.upsert(
        "messages",
        {
            "id": "explicit-confirmation",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "content": "Correct.",
            "isActualUtterance": True,
            "turnType": "ANSWER",
            "answerToQuestionId": confirmation_question["questionId"],
        },
    )

    confirmed = generate_structured_interview_result(record, knowledge, user, provider=provider)
    confirmed_state = confirmed["interviewState"]["requirementStates"]["requirement.purpose_problem"]
    assert confirmed_state["status"] == "CONFIRMED"
    assert confirmed_state["value"] == "申請処理を短縮する"
    assert confirmed["question"]["targetId"] == "requirement.users"


def test_structured_interview_does_not_duplicate_current_question_on_retry() -> None:
    user: UserContext = DEV_TOKENS["dev-manager"]
    record = {"id": "record-retry", "knowledgeId": "knowledge-retry", "title": "申請"}
    knowledge = {
        "id": "knowledge-retry",
        "name": "申請要件",
        "interviewPlan": {"profile": "system_requirement"},
    }
    store.upsert("records", {**record, "tenantId": user.tenant_id})
    store.upsert("knowledges", {**knowledge, "tenantId": user.tenant_id})
    provider = FakeStructuredProvider([StructuredInterviewOutput()])

    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    second = generate_structured_interview_result(record, knowledge, user, provider=provider)

    assert first["question"]["questionId"] == second["question"]["questionId"]
    assert len(second["interviewState"]["askedQuestions"]) == 1


def test_confirmation_is_applied_to_the_current_question_target_not_pending_list_head() -> None:
    state = build_initial_structured_state("system_requirement", [])
    state["requirementStates"]["requirement.purpose_problem"].update(
        status="AWAITING_CONFIRMATION",
        candidateValue="目的の候補",
    )
    state["requirementStates"]["requirement.users"].update(
        status="AWAITING_CONFIRMATION",
        candidateValue="利用者の候補",
    )

    apply_structured_output(
        state,
        StructuredInterviewOutput(dialogueAct="CONFIRMATION"),
        latest_message_id="confirmation-users",
        fields=[],
        profile="system_requirement",
        valid_evidence_ids={"confirmation-users"},
        current_question={
            "questionId": "q-users",
            "targetType": "requirement",
            "targetId": "requirement.users",
        },
    )

    assert state["requirementStates"]["requirement.purpose_problem"]["status"] == "AWAITING_CONFIRMATION"
    assert state["requirementStates"]["requirement.users"]["status"] == "CONFIRMED"


def test_confirmation_does_not_reopen_field_when_provider_repeats_field_update() -> None:
    state = build_initial_structured_state("fixed_form", [{"id": "field-name"}])
    field_state = state["fieldStates"]["field-name"]
    field_state.update(
        {
            "answerState": "AWAITING_CONFIRMATION",
            "status": "asking",
            "candidateAnswer": "Masa Miyazaki",
            "candidateSource": "user_statement",
            "candidateItems": [
                {
                    "itemId": "field-name",
                    "value": "Masa Miyazaki",
                    "evidenceTranscriptIds": ["answer-1"],
                }
            ],
        }
    )

    apply_structured_output(
        state,
        StructuredInterviewOutput(
            dialogueAct="CONFIRMATION",
            fieldUpdates=[
                {
                    "fieldId": "field-name",
                    "value": "Masa Miyazaki",
                    "evidenceTranscriptIds": ["confirmation-1"],
                }
            ],
        ),
        latest_message_id="confirmation-1",
        fields=[{"id": "field-name"}],
        profile="fixed_form",
        valid_evidence_ids={"answer-1", "confirmation-1"},
        current_question={
            "questionId": "q-name-confirm",
            "targetType": "field",
            "targetId": "field-name",
        },
    )

    assert field_state["answerState"] == "CONFIRMED"
    assert field_state["status"] == "completed"
    assert field_state["recordAnswer"] == "Masa Miyazaki"
    assert field_state["candidateAnswer"] is None
    assert field_state["candidateItems"] == []
    assert state["completedFieldIds"] == ["field-name"]


def test_confirmation_of_field_contradiction_confirms_candidate_and_advances() -> None:
    user: UserContext = DEV_TOKENS["dev-manager"]
    record = {
        "id": "record-contradiction-confirm",
        "knowledgeId": "knowledge-contradiction-confirm",
        "title": "Profile",
        "interviewLocale": "en-US",
    }
    knowledge = {
        "id": "knowledge-contradiction-confirm",
        "name": "Profile",
        "interviewPlan": {"profile": "fixed_form"},
    }
    fields = [
        {
            "id": "field-name",
            "knowledgeId": knowledge["id"],
            "tenantId": user.tenant_id,
            "name": "Name",
            "required": True,
            "displayOrder": 1,
        },
        {
            "id": "field-role",
            "knowledgeId": knowledge["id"],
            "tenantId": user.tenant_id,
            "name": "Role",
            "required": True,
            "displayOrder": 2,
        },
    ]
    store.upsert("records", {**record, "tenantId": user.tenant_id})
    store.upsert("knowledges", {**knowledge, "tenantId": user.tenant_id})
    for field in fields:
        store.upsert("knowledge_fields", field)

    state = build_initial_structured_state("fixed_form", fields)
    state.update(
        {
            "id": f"interview-state-{record['id']}",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "createdByUserId": user.user_id,
            "updatedByUserId": user.user_id,
            "createdAt": "2026-01-01T00:00:00+00:00",
            "updatedAt": "2026-01-01T00:00:00+00:00",
            "currentQuestionId": "q-contradiction",
            "nextQuestionTarget": {
                "targetType": "contradiction",
                "targetId": "name-conflict",
                "label": "Name",
                "priority": 1,
            },
            "askedQuestions": [
                {
                    "questionId": "q-contradiction",
                    "questionType": "structured",
                    "fieldId": None,
                    "text": "To confirm, is your name Miyazaki Masashi rather than Masa Miyazaki?",
                    "targetType": "contradiction",
                    "targetId": "name-conflict",
                }
            ],
            "contradictions": [
                {
                    "contradictionId": "name-conflict",
                    "topic": "field",
                    "description": "The name differs from the earlier answer.",
                    "status": "open",
                    "evidenceTranscriptIds": ["name-answer"],
                }
            ],
        }
    )
    state["fieldStates"]["field-name"].update(
        {
            "answerState": "AWAITING_CONFIRMATION",
            "status": "asking",
            "candidateAnswer": "Miyazaki Masashi",
            "candidateSource": "user_statement",
            "candidateItems": [
                {
                    "itemId": "field-name",
                    "value": "Miyazaki Masashi",
                    "evidenceTranscriptIds": ["name-answer"],
                }
            ],
        }
    )
    store.upsert("interview_states", state)
    store.upsert(
        "messages",
        {
            "id": "name-confirmation",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "content": "Yes.",
            "isActualUtterance": True,
            "turnType": "ANSWER",
            "answerToQuestionId": "q-contradiction",
        },
    )

    result = generate_structured_interview_result(
        record,
        knowledge,
        user,
        provider=FakeStructuredProvider([]),
        persist_assistant_messages=False,
    )

    confirmed_state = result["interviewState"]["fieldStates"]["field-name"]
    assert confirmed_state["answerState"] == "CONFIRMED"
    assert confirmed_state["recordAnswer"] == "Miyazaki Masashi"
    assert confirmed_state["candidateAnswer"] is None
    assert result["interviewState"]["contradictions"][0]["status"] == "resolved"
    assert result["question"]["targetId"] == "field-role"


def test_state_snapshot_repairs_generic_question_for_pending_candidate() -> None:
    user: UserContext = DEV_TOKENS["dev-manager"]
    record = {
        "id": "record-repair-confirmation-question",
        "knowledgeId": "knowledge-repair-confirmation-question",
        "title": "Profile",
        "interviewLocale": "en-US",
    }
    knowledge = {
        "id": "knowledge-repair-confirmation-question",
        "name": "Profile",
        "interviewPlan": {"profile": "fixed_form"},
    }
    field = {
        "id": "field-name",
        "knowledgeId": knowledge["id"],
        "tenantId": user.tenant_id,
        "name": "Name",
        "required": True,
        "displayOrder": 1,
    }
    store.upsert("records", {**record, "tenantId": user.tenant_id})
    store.upsert("knowledges", {**knowledge, "tenantId": user.tenant_id})
    store.upsert("knowledge_fields", field)

    state = build_initial_structured_state("fixed_form", [field])
    state.update(
        {
            "id": f"interview-state-{record['id']}",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "createdByUserId": user.user_id,
            "updatedByUserId": user.user_id,
            "createdAt": "2026-01-01T00:00:00+00:00",
            "updatedAt": "2026-01-01T00:00:00+00:00",
            "currentQuestionId": "q-name",
            "askedQuestions": [
                {
                    "questionId": "q-name",
                    "questionType": "structured",
                    "fieldId": "field-name",
                    "targetType": "field",
                    "targetId": "field-name",
                    "text": "Please introduce yourself.",
                }
            ],
        }
    )
    state["fieldStates"]["field-name"].update(
        {
            "answerState": "AWAITING_CONFIRMATION",
            "candidateAnswer": "Miyazaki Masashi",
            "candidateSource": "user_statement",
        }
    )
    store.upsert("interview_states", state)

    snapshot = get_structured_interview_state_snapshot(record, knowledge, user)
    current_question = next(
        question
        for question in snapshot["interviewState"]["askedQuestions"]
        if question["questionId"] == "q-name"
    )

    assert current_question["text"] == 'To confirm, is your answer “Miyazaki Masashi”?'
    persisted_state = store.get("interview_states", state["id"])
    assert persisted_state["askedQuestions"][0]["text"] == current_question["text"]


def test_assistant_proposal_is_kept_as_candidate_until_explicit_acceptance() -> None:
    state = build_initial_structured_state("system_requirement", [])
    question = {
        "questionId": "q-constraints",
        "targetType": "requirement",
        "targetId": "requirement.constraints",
    }
    proposal_message_id = "proposal-request"

    apply_structured_output(
        state,
        StructuredInterviewOutput(
            dialogueAct="QUESTION_TO_ASSISTANT",
            requirementUpdates=[
                RequirementUpdate(
                    requirementId="requirement.constraints",
                    value="社内PCのブラウザから利用し、出力はUTF-8、最大1万件とする",
                    candidateSource="assistant_proposal",
                    evidenceTranscriptIds=[proposal_message_id],
                )
            ]
        ),
        latest_message_id=proposal_message_id,
        fields=[],
        profile="system_requirement",
        valid_evidence_ids={proposal_message_id},
        current_question=question,
    )

    candidate = state["requirementStates"]["requirement.constraints"]
    assert candidate["status"] == "AWAITING_CONFIRMATION"
    assert candidate["value"] is None
    assert candidate["candidateSource"] == "assistant_proposal"

    apply_structured_output(
        state,
        StructuredInterviewOutput(dialogueAct="CONFIRMATION"),
        latest_message_id="proposal-acceptance",
        fields=[],
        profile="system_requirement",
        valid_evidence_ids={"proposal-acceptance"},
        current_question=question,
    )

    confirmed = state["requirementStates"]["requirement.constraints"]
    assert confirmed["status"] == "CONFIRMED"
    assert confirmed["value"] == "社内PCのブラウザから利用し、出力はUTF-8、最大1万件とする"
    assert confirmed["confirmedSource"] == "assistant_proposal"
    assert confirmed["candidateSource"] is None


def test_proposal_request_can_use_conversational_dialogue_act_and_labels_question() -> None:
    user: UserContext = DEV_TOKENS["dev-manager"]
    record = {"id": "record-proposal-request", "knowledgeId": "knowledge-proposal-request", "title": "CSV出力"}
    knowledge = {
        "id": "knowledge-proposal-request",
        "name": "CSV出力要件",
        "interviewPlan": {"profile": "system_requirement"},
    }
    store.upsert("records", {**record, "tenantId": user.tenant_id})
    store.upsert("knowledges", {**knowledge, "tenantId": user.tenant_id})
    state = build_initial_structured_state("system_requirement", [])
    question = {
        "questionId": "q-constraints",
        "questionType": "structured",
        "fieldId": None,
        "text": "制約を教えてください。",
        "targetType": "requirement",
        "targetId": "requirement.constraints",
    }
    state.update(
        {
            "id": f"interview-state-{record['id']}",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "currentQuestionId": question["questionId"],
            "nextQuestionTarget": {
                "targetType": "requirement",
                "targetId": "requirement.constraints",
                "label": "制約",
                "priority": 3,
            },
            "askedQuestions": [question],
        }
    )
    store.upsert("interview_states", state)
    store.upsert(
        "messages",
        {
            "id": "proposal-request-message",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "content": "よくわからないので提案して",
            "isActualUtterance": True,
            "turnType": "ANSWER",
            "answerToQuestionId": question["questionId"],
        },
    )
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                dialogueAct="QUESTION_TO_ASSISTANT",
                requirementUpdates=[
                    RequirementUpdate(
                        requirementId="requirement.constraints",
                        value="社内PCのブラウザから利用し、CSVはUTF-8形式、最大1万件とする",
                        candidateSource="assistant_proposal",
                        evidenceTranscriptIds=["proposal-request-message"],
                    )
                ],
            )
        ]
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    candidate = result["interviewState"]["requirementStates"]["requirement.constraints"]
    assert result["question"]["candidateSource"] == "assistant_proposal"
    assert result["assistantMessage"]["candidateSource"] == "assistant_proposal"
    assert candidate["candidateSource"] == "assistant_proposal"
    assert candidate["candidateProposalMessageId"] == result["assistantMessage"]["id"]


def test_purpose_proposal_waits_for_users_and_request_context() -> None:
    user: UserContext = DEV_TOKENS["dev-manager"]
    record = {"id": "record-purpose-context", "knowledgeId": "knowledge-purpose-context", "title": "CSV出力"}
    knowledge = {
        "id": "knowledge-purpose-context",
        "name": "CSV出力要件",
        "interviewPlan": {
            "profile": "system_requirement",
            "modelId": "global.openai.gpt-5.6-luna",
        },
    }
    store.upsert("records", {**record, "tenantId": user.tenant_id})
    store.upsert("knowledges", {**knowledge, "tenantId": user.tenant_id})
    state = build_initial_structured_state("system_requirement", [])
    question = {
        "questionId": "q-purpose",
        "questionType": "structured",
        "fieldId": None,
        "text": "この機能で解決したい目的・課題は何ですか？",
        "targetType": "requirement",
        "targetId": "requirement.purpose_problem",
    }
    state.update(
        {
            "id": f"interview-state-{record['id']}",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "currentQuestionId": question["questionId"],
            "nextQuestionTarget": {
                "targetType": "requirement",
                "targetId": "requirement.purpose_problem",
                "label": "目的・課題",
                "priority": 3,
            },
            "askedQuestions": [question],
        }
    )
    store.upsert("interview_states", state)
    store.upsert(
        "messages",
        {
            "id": "purpose-proposal-request",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "content": "考えて",
            "isActualUtterance": True,
            "turnType": "ANSWER",
            "answerToQuestionId": question["questionId"],
        },
    )
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                dialogueAct="QUESTION_TO_ASSISTANT",
                requirementUpdates=[
                    RequirementUpdate(
                        requirementId="requirement.purpose_problem",
                        value="営業担当が受注実績を検索し、CSVで取得できるようにする",
                        candidateSource="assistant_proposal",
                        evidenceTranscriptIds=["purpose-proposal-request"],
                    )
                ],
            )
        ]
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    purpose = result["interviewState"]["requirementStates"]["requirement.purpose_problem"]
    assert purpose["status"] == "UNANSWERED"
    assert purpose["candidateValue"] is None
    assert result["interviewState"]["deferredProposalTarget"] == "requirement.purpose_problem"
    assert result["question"]["targetId"] == "requirement.users"
    assert result["question"]["targetType"] == "requirement"

    deferred_state = result["interviewState"]
    deferred_state["requirementStates"]["requirement.users"]["status"] = "CONFIRMED"
    assert select_next_question_target(deferred_state, "system_requirement", []) == {
        "targetType": "requirement",
        "targetId": "requirement.request",
        "label": "要求内容",
        "priority": 3,
    }
    deferred_state["requirementStates"]["requirement.request"]["status"] = "CONFIRMED"
    assert select_next_question_target(deferred_state, "system_requirement", []) == {
        "targetType": "requirement",
        "targetId": "requirement.purpose_problem",
        "label": "目的・課題",
        "priority": 3,
    }


def test_strict_schema_forbids_extra_properties_and_requires_object_properties() -> None:
    schema = _make_strict_schema(StructuredInterviewOutput.model_json_schema())

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["$defs"]["ProcessPatch"]["additionalProperties"] is False
    assert set(schema["$defs"]["ProcessPatch"]["required"]) == set(
        schema["$defs"]["ProcessPatch"]["properties"]
    )
    assert all(
        "default" not in definition
        for definition in schema["$defs"].values()
        if isinstance(definition, dict)
    )


def test_process_patch_is_atomic_and_removals_keep_history() -> None:
    state = build_initial_structured_state("business_process", [])
    evidence = {"message-1"}
    patch = ProcessPatch(
        baseProcessVersion=0,
        addParticipants=[
            ProcessParticipant(
                participantId="applicant",
                name="申請者",
                kind="person",
                evidenceTranscriptIds=["message-1"],
            ),
            ProcessParticipant(
                participantId="web",
                name="Web画面",
                kind="system",
                evidenceTranscriptIds=["message-1"],
            ),
        ],
        addNodes=[
            ProcessNode(
                nodeId="submit",
                label="申請を送信する",
                participantIds=["applicant", "web"],
                evidenceTranscriptIds=["message-1"],
            ),
            ProcessNode(
                nodeId="finish",
                label="完了",
                nodeType="end",
                evidenceTranscriptIds=["message-1"],
            ),
        ],
        addEdges=[
            ProcessEdge(
                edgeId="submit-finish",
                sourceNodeId="submit",
                targetNodeId="finish",
                evidenceTranscriptIds=["message-1"],
            )
        ],
        addInteractions=[
            ProcessInteraction(
                interactionId="submit-request",
                sequence=1,
                sourceParticipantId="applicant",
                targetParticipantId="web",
                action="申請を送信する",
                evidenceTranscriptIds=["message-1"],
            )
        ],
    )

    apply_structured_output(
        state,
        StructuredInterviewOutput(processPatch=patch),
        latest_message_id="message-1",
        fields=[],
        profile="business_process",
        valid_evidence_ids=evidence,
    )

    assert state["processState"]["version"] == 1
    assert state["processState"]["edges"][0]["lifecycle"] == "active"
    assert state["processState"]["interactions"][0]["lifecycle"] == "active"

    before_invalid = state["processState"].copy()
    invalid_patch = ProcessPatch(
        baseProcessVersion=1,
        addNodes=[
            ProcessNode(
                nodeId="invalid",
                label="不正な処理",
                evidenceTranscriptIds=["message-1"],
            )
        ],
        addEdges=[
            ProcessEdge(
                edgeId="invalid-edge",
                sourceNodeId="invalid",
                targetNodeId="missing",
                evidenceTranscriptIds=["message-1"],
            )
        ],
    )
    apply_structured_output(
        state,
        StructuredInterviewOutput(processPatch=invalid_patch),
        latest_message_id="message-1",
        fields=[],
        profile="business_process",
        valid_evidence_ids=evidence,
    )
    assert state["processState"] == before_invalid

    apply_structured_output(
        state,
        StructuredInterviewOutput(
            processPatch=ProcessPatch(
                baseProcessVersion=1,
                removeEdges=["submit-finish"],
                removeInteractions=["submit-request"],
            )
        ),
        latest_message_id="message-1",
        fields=[],
        profile="business_process",
        valid_evidence_ids=evidence,
    )
    assert state["processState"]["version"] == 2
    assert state["processState"]["edges"][0]["lifecycle"] == "superseded"
    assert state["processState"]["interactions"][0]["lifecycle"] == "superseded"


def test_system_requirement_does_not_force_process_when_process_is_not_present() -> None:
    state = build_initial_structured_state("system_requirement", [])
    patch = ProcessPatch(
        baseProcessVersion=0,
        addNodes=[
            ProcessNode(
                nodeId="download",
                label="CSVをダウンロードする",
                evidenceTranscriptIds=["message-1"],
            )
        ],
    )

    apply_structured_output(
        state,
        StructuredInterviewOutput(processPatch=patch),
        latest_message_id="message-1",
        fields=[],
        profile="system_requirement",
        valid_evidence_ids={"message-1"},
    )
    assert state["processState"]["nodes"] == []

    apply_structured_output(
        state,
        StructuredInterviewOutput(
            applicability=[
                ApplicabilityUpdate(
                    topic="process",
                    status="not_applicable",
                    evidenceTranscriptIds=["message-1"],
                )
            ],
            processPatch=patch,
        ),
        latest_message_id="message-1",
        fields=[],
        profile="system_requirement",
        valid_evidence_ids={"message-1"},
    )
    assert state["applicabilityState"]["process"]["status"] == "not_applicable"
    assert state["processState"]["nodes"] == []


def test_system_requirement_skips_process_details_when_process_is_not_applicable() -> None:
    state = build_initial_structured_state("system_requirement", [])
    for requirement in state["requirementStates"].values():
        if requirement["kind"] == "requirement":
            requirement["status"] = "CONFIRMED"
            requirement["value"] = "確認済み"
    apply_structured_output(
        state,
        StructuredInterviewOutput(
            applicability=[
                ApplicabilityUpdate(
                    topic="process",
                    status="not_applicable",
                    evidenceTranscriptIds=["message-1"],
                )
            ]
        ),
        latest_message_id="message-1",
        fields=[],
        profile="system_requirement",
        valid_evidence_ids={"message-1"},
    )

    completion = evaluate_completion(state, "system_requirement", [])
    assert completion["complete"] is True
    assert select_next_question_target(state, "system_requirement", []) is None


def test_system_requirement_rejects_process_details_when_process_is_not_applicable() -> None:
    state = build_initial_structured_state("system_requirement", [])
    apply_structured_output(
        state,
        StructuredInterviewOutput(
            applicability=[
                ApplicabilityUpdate(
                    topic="process",
                    status="not_applicable",
                    evidenceTranscriptIds=["message-1"],
                )
            ],
            requirementUpdates=[
                RequirementUpdate(
                    requirementId="process.trigger",
                    value="画面を開いたとき",
                    evidenceTranscriptIds=["message-1"],
                )
            ],
        ),
        latest_message_id="message-1",
        fields=[],
        profile="system_requirement",
        valid_evidence_ids={"message-1"},
    )

    assert state["requirementStates"]["process.trigger"]["status"] == "UNANSWERED"


def test_system_requirement_single_answer_captures_requirements_and_process_model() -> None:
    """受注実績のCSV出力要望から要求と処理モデルを同時に候補化する。"""

    # 前提条件:
    # - Profileはsystem_requirement。
    # - 利用者は営業担当で、Web画面から受注実績を検索してCSV出力する。
    # - 現状はExcelを手作業で作成しており、月末の集計に時間がかかる。
    # 想定質問:「今回、どのような課題を解決したいですか？」
    # 想定回答: 要求項目と、検索→受注管理システム→CSV出力の処理を1回答に含める。
    state = build_initial_structured_state("system_requirement", [])
    message_id = "system-requirement-answer-1"
    output = StructuredInterviewOutput(
        requirementUpdates=[
            RequirementUpdate(
                requirementId="requirement.purpose_problem",
                value="月末の受注実績集計にかかる時間を短縮する",
                evidenceTranscriptIds=[message_id],
            ),
            RequirementUpdate(
                requirementId="requirement.users",
                value="営業担当",
                evidenceTranscriptIds=[message_id],
            ),
            RequirementUpdate(
                requirementId="requirement.request",
                value="検索条件を入力して受注実績をCSVでダウンロードできる機能",
                evidenceTranscriptIds=[message_id],
            ),
            RequirementUpdate(
                requirementId="requirement.expected_result",
                value="営業担当が必要な受注実績を短時間で取得できる",
                evidenceTranscriptIds=[message_id],
            ),
            RequirementUpdate(
                requirementId="requirement.constraints",
                value="既存の受注管理システムを利用し、権限のないデータは表示しない",
                evidenceTranscriptIds=[message_id],
            ),
        ],
        applicability=[
            ApplicabilityUpdate(
                topic="process",
                status="present",
                evidenceTranscriptIds=[message_id],
            )
        ],
        processPatch=ProcessPatch(
            baseProcessVersion=0,
            addParticipants=[
                ProcessParticipant(
                    participantId="sales-user",
                    name="営業担当",
                    kind="person",
                    evidenceTranscriptIds=[message_id],
                ),
                ProcessParticipant(
                    participantId="web-portal",
                    name="営業Web画面",
                    kind="system",
                    evidenceTranscriptIds=[message_id],
                ),
                ProcessParticipant(
                    participantId="order-system",
                    name="受注管理システム",
                    kind="system",
                    evidenceTranscriptIds=[message_id],
                ),
            ],
            addNodes=[
                ProcessNode(
                    nodeId="search-orders",
                    label="検索条件を入力して受注実績を検索する",
                    participantIds=["sales-user", "web-portal"],
                    evidenceTranscriptIds=[message_id],
                ),
                ProcessNode(
                    nodeId="create-csv",
                    label="受注管理システムから受注実績を取得してCSVを作成する",
                    participantIds=["web-portal", "order-system"],
                    evidenceTranscriptIds=[message_id],
                ),
                ProcessNode(
                    nodeId="download-csv",
                    label="CSVをダウンロードする",
                    nodeType="end",
                    participantIds=["sales-user", "web-portal"],
                    evidenceTranscriptIds=[message_id],
                ),
            ],
            addEdges=[
                ProcessEdge(
                    edgeId="search-to-create",
                    sourceNodeId="search-orders",
                    targetNodeId="create-csv",
                    evidenceTranscriptIds=[message_id],
                ),
                ProcessEdge(
                    edgeId="create-to-download",
                    sourceNodeId="create-csv",
                    targetNodeId="download-csv",
                    evidenceTranscriptIds=[message_id],
                ),
            ],
            addInteractions=[
                ProcessInteraction(
                    interactionId="sales-to-web-search",
                    sequence=1,
                    sourceParticipantId="sales-user",
                    targetParticipantId="web-portal",
                    action="検索条件を入力して検索を実行する",
                    data="検索条件",
                    evidenceTranscriptIds=[message_id],
                ),
                ProcessInteraction(
                    interactionId="web-to-order-system",
                    sequence=2,
                    sourceParticipantId="web-portal",
                    targetParticipantId="order-system",
                    action="受注実績を取得する",
                    data="検索条件、受注実績",
                    evidenceTranscriptIds=[message_id],
                ),
                ProcessInteraction(
                    interactionId="web-to-sales-download",
                    sequence=3,
                    sourceParticipantId="web-portal",
                    targetParticipantId="sales-user",
                    action="CSVをダウンロード可能にする",
                    data="CSV",
                    evidenceTranscriptIds=[message_id],
                ),
            ],
        ),
    )

    changed_topics = apply_structured_output(
        state,
        output,
        latest_message_id=message_id,
        fields=[],
        profile="system_requirement",
        valid_evidence_ids={message_id},
    )

    assert set(changed_topics) == {
        "requirement.purpose_problem",
        "requirement.users",
        "requirement.request",
        "requirement.expected_result",
        "requirement.constraints",
        "applicability:process",
        "process_model",
    }
    assert state["applicabilityState"]["process"]["status"] == "present"
    assert state["processState"]["version"] == 1
    assert [node["nodeId"] for node in state["processState"]["nodes"]] == [
        "search-orders",
        "create-csv",
        "download-csv",
    ]
    assert len(state["processState"]["interactions"]) == 3
    assert all(
        entity["confirmationStatus"] == "candidate"
        for collection in ("participants", "nodes", "edges", "interactions")
        for entity in state["processState"][collection]
    )
    assert state["requirementStates"]["requirement.purpose_problem"]["status"] == "AWAITING_CONFIRMATION"
    assert all(
        state["requirementStates"][requirement_id]["status"] == "CANDIDATE_PENDING"
        for requirement_id in (
            "requirement.users",
            "requirement.request",
            "requirement.expected_result",
            "requirement.constraints",
        )
    )
    assert select_next_question_target(state, "system_requirement", []) == {
        "targetType": "requirement",
        "targetId": "requirement.purpose_problem",
        "label": "目的・課題",
        "priority": 2,
    }


def test_system_requirement_conversation_routes_one_target_and_records_terra() -> None:
    """複数項目を含む回答を受け、確認を1件ずつ行ってから処理詳細へ進む。"""

    # 前提条件:
    # - KnowledgeのProfileはsystem_requirement、モデルはGPT-5.6 Terra。
    # - 要望は「営業担当が受注実績を検索し、CSVで取得したい」。
    # - 初回回答には5つの要求項目を含め、業務フローの有無はまだ回答しない。
    # 質問順:
    # 1. 目的・課題 → 2〜6. 各候補の確認 → 7. 業務上の処理の流れの有無
    user: UserContext = DEV_TOKENS["dev-manager"]
    record = {
        "id": "record-system-conversation",
        "knowledgeId": "knowledge-system-conversation",
        "title": "受注実績CSV出力",
    }
    knowledge = {
        "id": "knowledge-system-conversation",
        "name": "受注実績CSV出力要件",
        "interviewPlan": {
            "profile": "system_requirement",
            "modelId": "global.openai.gpt-5.6-terra",
        },
    }
    store.upsert("records", {**record, "tenantId": user.tenant_id})
    store.upsert("knowledges", {**knowledge, "tenantId": user.tenant_id})

    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                requirementUpdates=[
                    RequirementUpdate(
                        requirementId="requirement.purpose_problem",
                        value="月末の受注実績集計にかかる時間を短縮する",
                        evidenceTranscriptIds=["system-answer-1"],
                    ),
                    RequirementUpdate(
                        requirementId="requirement.users",
                        value="営業担当",
                        evidenceTranscriptIds=["system-answer-1"],
                    ),
                    RequirementUpdate(
                        requirementId="requirement.request",
                        value="検索条件を入力して受注実績をCSVでダウンロードできる機能",
                        evidenceTranscriptIds=["system-answer-1"],
                    ),
                    RequirementUpdate(
                        requirementId="requirement.expected_result",
                        value="営業担当が必要な受注実績を短時間で取得できる",
                        evidenceTranscriptIds=["system-answer-1"],
                    ),
                    RequirementUpdate(
                        requirementId="requirement.constraints",
                        value="既存の受注管理システムを利用し、権限のないデータは表示しない",
                        evidenceTranscriptIds=["system-answer-1"],
                    ),
                ]
            ),
            StructuredInterviewOutput(
                applicability=[
                    ApplicabilityUpdate(
                        topic="process",
                        status="present",
                        evidenceTranscriptIds=["system-process-answer"],
                    )
                ],
                processPatch=ProcessPatch(
                    baseProcessVersion=0,
                    addParticipants=[
                        ProcessParticipant(
                            participantId="sales-user",
                            name="営業担当",
                            kind="person",
                            evidenceTranscriptIds=["system-process-answer"],
                        ),
                        ProcessParticipant(
                            participantId="web-portal",
                            name="営業Web画面",
                            kind="system",
                            evidenceTranscriptIds=["system-process-answer"],
                        ),
                    ],
                    addNodes=[
                        ProcessNode(
                            nodeId="search-orders",
                            label="検索条件を入力して受注実績を検索する",
                            participantIds=["sales-user", "web-portal"],
                            evidenceTranscriptIds=["system-process-answer"],
                        ),
                        ProcessNode(
                            nodeId="download-csv",
                            label="CSVをダウンロードする",
                            nodeType="end",
                            participantIds=["sales-user", "web-portal"],
                            evidenceTranscriptIds=["system-process-answer"],
                        ),
                    ],
                    addEdges=[
                        ProcessEdge(
                            edgeId="search-to-download",
                            sourceNodeId="search-orders",
                            targetNodeId="download-csv",
                            evidenceTranscriptIds=["system-process-answer"],
                        )
                    ],
                    addInteractions=[
                        ProcessInteraction(
                            interactionId="sales-to-web-search",
                            sequence=1,
                            sourceParticipantId="sales-user",
                            targetParticipantId="web-portal",
                            action="検索条件を入力して検索を実行する",
                            data="検索条件",
                            evidenceTranscriptIds=["system-process-answer"],
                        )
                    ],
                ),
            ),
        ]
    )

    def add_answer(message_id: str, content: str, question: Mapping[str, object]) -> None:
        store.upsert(
            "messages",
            {
                "id": message_id,
                "tenantId": user.tenant_id,
                "recordId": record["id"],
                "role": "user",
                "content": content,
                "isActualUtterance": True,
                "turnType": "ANSWER",
                "answerToQuestionId": question["questionId"],
            },
        )

    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    assert first["question"]["targetId"] == "requirement.purpose_problem"
    assert first["question"]["text"] == "目的・課題を教えてください。"

    add_answer(
        "system-answer-1",
        "営業担当がWeb画面で受注実績を検索し、CSVでダウンロードしたいです。現状はExcelを手作業で作成しているため、月末の集計に時間がかかっています。既存の受注管理システムを使い、権限外のデータは表示しないでください。",
        first["question"],
    )
    candidate = generate_structured_interview_result(record, knowledge, user, provider=provider)
    assert candidate["question"]["targetId"] == "requirement.purpose_problem"
    assert candidate["interviewState"]["lastStructuredModelId"] == "global.openai.gpt-5.6-terra"

    current = candidate
    for index, expected_target_id in enumerate(
        (
            "requirement.users",
            "requirement.request",
            "requirement.expected_result",
            "requirement.constraints",
            "process",
        ),
        start=2,
    ):
        add_answer(
            f"system-confirmation-{index}",
            "はい、大丈夫です。",
            current["question"],
        )
        current = generate_structured_interview_result(record, knowledge, user, provider=provider)
        assert current["question"]["targetId"] == expected_target_id

    assert current["question"]["targetType"] == "applicability"
    add_answer(
        "system-process-answer",
        "あります。営業担当が検索条件を入力して検索し、Web画面からCSVをダウンロードします。",
        current["question"],
    )
    after_process = generate_structured_interview_result(record, knowledge, user, provider=provider)

    assert after_process["question"]["targetType"] == "process"
    assert after_process["question"]["targetId"] == "process.trigger"
    assert after_process["interviewState"]["applicabilityState"]["process"]["status"] == "present"
    assert after_process["interviewState"]["processState"]["version"] == 1
    assert after_process["interviewState"]["lastStructuredModelId"] == "global.openai.gpt-5.6-terra"
    assert after_process["interviewState"]["lastQuestionModelId"] == "global.openai.gpt-5.6-terra"


def test_system_requirement_confirms_process_presence_before_process_details() -> None:
    """処理の有無を先に質問し、present確定後に処理詳細を質問対象にする。"""

    # 前提条件:
    # - 5つの要求項目は確認済み。
    # - 処理の有無は未確認。
    # 想定質問:「この要望には、利用者の操作やシステム間連携など、業務上の処理の流れがありますか？」
    state = build_initial_structured_state("system_requirement", [])
    for requirement_id in (
        "requirement.purpose_problem",
        "requirement.users",
        "requirement.request",
        "requirement.expected_result",
        "requirement.constraints",
    ):
        state["requirementStates"][requirement_id].update(
            status="CONFIRMED",
            value="確認済み",
        )

    presence_target = select_next_question_target(state, "system_requirement", [])
    assert presence_target == {
        "targetType": "applicability",
        "targetId": "process",
        "label": "処理の流れがあるか",
        "priority": 4,
    }

    message_id = "system-requirement-process-presence-1"
    apply_structured_output(
        state,
        StructuredInterviewOutput(
            applicability=[
                ApplicabilityUpdate(
                    topic="process",
                    status="present",
                    evidenceTranscriptIds=[message_id],
                )
            ]
        ),
        latest_message_id=message_id,
        fields=[],
        profile="system_requirement",
        valid_evidence_ids={message_id},
    )

    assert select_next_question_target(state, "system_requirement", []) == {
        "targetType": "process",
        "targetId": "process.trigger",
        "label": "処理の開始条件",
        "priority": 3,
    }
    assert evaluate_completion(state, "system_requirement", [])["complete"] is False


def test_system_requirement_keeps_unmentioned_optional_cases_unknown_after_overview() -> None:
    """存在確認で言及されなかった任意ケースを未確認のまま次の質問へ送る。"""

    # 前提条件:
    # - 要求5項目と、業務フローの必須5項目は確認済み。
    # - processはpresent。分岐、例外、外部連携、エラー処理などはunknown。
    # 想定質問:「通常と異なるケースや条件による処理変更はありますか？」
    # 想定回答: 分岐はある、例外はない。外部連携とエラー処理には言及しない。
    state = build_initial_structured_state("system_requirement", [])
    required_ids = {
        "requirement.purpose_problem",
        "requirement.users",
        "requirement.request",
        "requirement.expected_result",
        "requirement.constraints",
        "process.trigger",
        "process.actors",
        "process.main_flow",
        "process.end",
        "process.interaction",
    }
    for requirement_id in required_ids:
        state["requirementStates"][requirement_id].update(
            status="CONFIRMED",
            value="確認済み",
        )
    state["applicabilityState"]["process"].update(
        status="present",
        evidenceTranscriptIds=["process-presence"],
    )

    overview_target = select_next_question_target(state, "system_requirement", [])
    assert overview_target == {
        "targetType": "applicability_overview",
        "targetId": "optional_cases",
        "label": "通常と異なるケースや条件による処理変更の有無",
        "priority": 4,
    }

    state["applicabilityOverviewAsked"] = True
    message_id = "system-requirement-optional-overview-1"
    apply_structured_output(
        state,
        StructuredInterviewOutput(
            applicability=[
                ApplicabilityUpdate(
                    topic="branch",
                    status="present",
                    reason="承認金額によって承認者が変わる",
                    evidenceTranscriptIds=[message_id],
                ),
                ApplicabilityUpdate(
                    topic="exception",
                    status="not_applicable",
                    reason="通常と異なる処理はない",
                    evidenceTranscriptIds=[message_id],
                ),
            ]
        ),
        latest_message_id=message_id,
        fields=[],
        profile="system_requirement",
        valid_evidence_ids={message_id},
    )

    completion = evaluate_completion(state, "system_requirement", [])
    assert completion["complete"] is False
    assert completion["unknownApplicabilityTopics"] == [
        "external_system",
        "error_handling",
        "handoff",
        "input_output",
    ]
    assert state["applicabilityState"]["branch"]["status"] == "present"
    assert state["applicabilityState"]["exception"]["status"] == "not_applicable"
    assert state["applicabilityState"]["external_system"]["status"] == "unknown"
    assert select_next_question_target(state, "system_requirement", []) == {
        "targetType": "process",
        "targetId": "process.branch",
        "label": "条件による分岐",
        "priority": 3,
    }

    # presentと判定された対象は、残りの存在確認より先に詳細を確認する。
    state["requirementStates"]["process.branch"].update(
        status="CONFIRMED",
        value="承認金額が100万円以上の場合は部長承認にする",
    )
    assert select_next_question_target(state, "system_requirement", []) == {
        "targetType": "applicability",
        "targetId": "external_system",
        "label": "外部システム連携",
        "priority": 4,
    }
