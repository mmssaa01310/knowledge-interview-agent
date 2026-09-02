from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy

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
    _interpreter_system_prompt,
)
from ai_interviewer_api.agents.interview_knowledge.schemas import (
    AnswerAssessment,
    ApplicabilityUpdate,
    FieldUpdate,
    ProcessEdge,
    ProcessInteraction,
    ProcessNode,
    ProcessParticipant,
    ProcessPatch,
    QuestionGenerationOutput,
    RequirementUpdate,
    StructuredInterviewOutput,
    TranscriptAssessment,
)
from ai_interviewer_api.agents.interview_knowledge.service import (
    generate_structured_interview_result,
    get_structured_interview_state_snapshot,
    resolve_structured_model_id,
)
from ai_interviewer_api.auth.deps import DEV_TOKENS, UserContext
from ai_interviewer_api.models.interview_plan import (
    InterviewPlanItem,
    InterviewQuestionPlan,
)
from ai_interviewer_api.repositories.store import store


class FakeStructuredProvider:
    def __init__(self, outputs: list[StructuredInterviewOutput]) -> None:
        self.outputs = iter(outputs)
        self.interpret_calls: list[dict[str, object]] = []
        self.question_calls: list[dict[str, object]] = []

    def interpret(self, **kwargs: object) -> StructuredInterviewOutput:
        self.interpret_calls.append(kwargs)
        return next(self.outputs)

    def generate_question(self, *, target: Mapping[str, object], **_: object) -> QuestionGenerationOutput:
        self.question_calls.append(dict(target))
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


def _seed_fixed_form_case(
    record_id: str,
    field_names: tuple[tuple[str, str], ...],
) -> tuple[UserContext, dict[str, object], dict[str, object]]:
    user: UserContext = DEV_TOKENS["dev-manager"]
    knowledge_id = f"knowledge-{record_id}"
    record = {"id": record_id, "knowledgeId": knowledge_id, "title": "プロフィール"}
    knowledge = {
        "id": knowledge_id,
        "name": "プロフィール",
        "interviewPlan": {"profile": "fixed_form"},
    }
    store.upsert("records", {**record, "tenantId": user.tenant_id})
    store.upsert("knowledges", {**knowledge, "tenantId": user.tenant_id})
    for index, (field_id, name) in enumerate(field_names, start=1):
        store.upsert(
            "knowledge_fields",
            {
                "id": field_id,
                "knowledgeId": knowledge_id,
                "tenantId": user.tenant_id,
                "name": name,
                "required": True,
                "displayOrder": index,
            },
        )
    return user, record, knowledge


def _add_structured_answer(
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


def test_structured_model_selection_uses_luna_when_configured() -> None:
    assert resolve_structured_model_id(
        {"interviewPlan": {"modelId": "global.openai.gpt-5.6-luna"}}
    ) == "global.openai.gpt-5.6-luna"


def test_interpreter_prompt_separates_question_help_from_answer_unavailability() -> None:
    prompt = _interpreter_system_prompt("fixed_form")

    assert "どういう意味ですか" in prompt
    assert "CLARIFICATION_REQUEST" in prompt
    assert "よく分からないですね" in prompt
    assert "UNANSWERABLE" in prompt
    assert "単に「よく分からない」とだけ言われた場合" in prompt
    assert "STTが不確実でない限りUNCERTAINにはしません" in prompt


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


def test_partial_question_plan_asks_only_missing_items_and_advances_after_completion() -> None:
    user, record, knowledge = _seed_fixed_form_case(
        "record-partial-question-plan",
        (("field-profile", "基本プロフィール"), ("field-next", "次の項目")),
    )
    profile_field = store.get("knowledge_fields", "field-profile")
    assert profile_field is not None
    profile_field.update(
        {
            "description": "氏名、所属部署、役職または担当領域を確認する。",
            "aiQuestionExamples": ["お名前、所属部署、役職または担当領域を教えてください。"],
            "questionPlan": InterviewQuestionPlan(
                requiredItems=[
                    InterviewPlanItem(itemId="name", label="お名前", description="氏名"),
                    InterviewPlanItem(itemId="department", label="所属部署", description="所属部署"),
                    InterviewPlanItem(
                        itemId="role_or_domain",
                        label="現在の役職または担当領域",
                        description="役職または担当領域",
                    ),
                ]
            ).model_dump(),
        }
    )
    store.upsert("knowledge_fields", profile_field)
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                answerAssessment=AnswerAssessment(sufficiency="PARTIAL", probeType="CLARIFY"),
                fieldUpdates=[
                    FieldUpdate(
                        fieldId="field-profile",
                        itemId="name",
                        value="宮崎",
                        evidenceTranscriptIds=["partial-profile-name"],
                        answerResolution="AUTO_CONFIRM",
                    )
                ],
            ),
            StructuredInterviewOutput(
                answerAssessment=AnswerAssessment(sufficiency="SUFFICIENT"),
                fieldUpdates=[
                    FieldUpdate(
                        fieldId="field-profile",
                        itemId="department",
                        value="営業部",
                        evidenceTranscriptIds=["partial-profile-rest"],
                        answerResolution="AUTO_CONFIRM",
                    ),
                    FieldUpdate(
                        fieldId="field-profile",
                        itemId="role_or_domain",
                        value="課長",
                        evidenceTranscriptIds=["partial-profile-rest"],
                        answerResolution="AUTO_CONFIRM",
                    ),
                ],
            ),
        ]
    )

    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    assert first["question"]["missingItemIds"] == ["name", "department", "role_or_domain"]
    _add_structured_answer(
        record,
        user,
        message_id="partial-profile-name",
        question=first["question"],
        content="宮崎です。",
    )

    partial = generate_structured_interview_result(record, knowledge, user, provider=provider)

    partial_state = partial["interviewState"]["fieldStates"]["field-profile"]
    assert partial_state["answerState"] == "CANDIDATE_PENDING"
    assert partial_state["capturedItemIds"] == ["name"]
    assert partial_state["missingRequiredItemIds"] == ["department", "role_or_domain"]
    assert partial["question"]["targetId"] == "field-profile"
    assert partial["question"]["missingItemIds"] == ["department", "role_or_domain"]
    assert partial["question"]["capturedItemIds"] == ["name"]
    assert partial["question"]["text"] == "所属部署と、現在の役職または担当領域を教えてください。"
    assert "お名前" not in partial["question"]["text"]

    _add_structured_answer(
        record,
        user,
        message_id="partial-profile-rest",
        question=partial["question"],
        content="所属部署は営業部で、現在の役職は課長です。",
    )

    completed = generate_structured_interview_result(record, knowledge, user, provider=provider)

    completed_state = completed["interviewState"]["fieldStates"]["field-profile"]
    assert completed_state["answerState"] == "CONFIRMED"
    assert completed_state["missingRequiredItemIds"] == []
    assert completed_state["capturedItemIds"] == ["name", "department", "role_or_domain"]
    assert [item["itemId"] for item in completed_state["confirmedItems"]] == [
        "name",
        "department",
        "role_or_domain",
    ]
    assert "field-profile" in completed["interviewState"]["completedFieldIds"]
    assert completed["question"]["targetId"] == "field-next"


def test_completed_field_gets_one_useful_deepening_then_moves_on_without_persistence() -> None:
    user, record, knowledge = _seed_fixed_form_case(
        "record-optional-deepening",
        (("field-role", "現在の役割"), ("field-next", "次の項目")),
    )
    role_field = store.get("knowledge_fields", "field-role")
    assert role_field is not None
    role_field.update(
        {
            "description": "現在の役割と責任範囲を確認する。",
            "aiQuestionExamples": ["現在の役割と、日々どのような責任を担っているかを教えてください。"],
            "questionPlan": InterviewQuestionPlan(
                requiredItems=[
                    InterviewPlanItem(itemId="role", label="現在の役割", description="現在担っている役割"),
                    InterviewPlanItem(itemId="responsibilities", label="日々の責任", description="日々担っている責任範囲"),
                ],
                optionalItems=[
                    InterviewPlanItem(
                        itemId="role_example",
                        label="具体的な業務上の工夫や事例",
                        description="役割や責任が分かる具体的な業務上の工夫や事例",
                    )
                ],
            ).model_dump(),
        }
    )
    store.upsert("knowledge_fields", role_field)
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                answerAssessment=AnswerAssessment(
                    sufficiency="EXAMPLE_MISSING",
                    probeType="EXAMPLE",
                ),
                fieldUpdates=[
                    FieldUpdate(
                        fieldId="field-role",
                        itemId="role",
                        value="チームの進行管理を担当しています",
                        evidenceTranscriptIds=["optional-role-answer"],
                        answerResolution="AUTO_CONFIRM",
                    ),
                    FieldUpdate(
                        fieldId="field-role",
                        itemId="responsibilities",
                        value="日々の進捗確認とメンバー支援を担っています",
                        evidenceTranscriptIds=["optional-role-answer"],
                        answerResolution="AUTO_CONFIRM",
                    ),
                ],
            ),
            StructuredInterviewOutput(
                answerAssessment=AnswerAssessment(sufficiency="REFUSAL", probeType="CLARIFY"),
            ),
        ]
    )

    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_structured_answer(
        record,
        user,
        message_id="optional-role-answer",
        question=first["question"],
        content="チームの進行管理を担当し、日々は進捗確認とメンバー支援をしています。",
    )

    deepening = generate_structured_interview_result(record, knowledge, user, provider=provider)

    assert deepening["question"]["targetId"] == "field-role"
    assert deepening["question"]["optionalDeepening"] is True
    assert deepening["question"]["deepeningItemIds"] == ["role_example"]
    assert deepening["question"]["text"] == "具体的な業務上の工夫や事例を教えてください。"
    assert deepening["interviewState"]["fieldStates"]["field-role"]["missingRequiredItemIds"] == []

    _add_structured_answer(
        record,
        user,
        message_id="optional-role-refusal",
        question=deepening["question"],
        content="特にありません。",
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    role_state = result["interviewState"]["fieldStates"]["field-role"]
    assert role_state["answerState"] == "CONFIRMED"
    assert role_state["capturedItemIds"] == ["role", "responsibilities"]
    assert result["question"]["targetId"] == "field-next"
    assert all(call.get("optionalDeepening") is not True for call in provider.question_calls[2:])


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
    assistant_messages = [
        message
        for message in store.list("messages", user.tenant_id)
        if message.get("recordId") == record["id"] and message.get("role") == "assistant"
    ]
    assert len(assistant_messages) == 1


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


def test_assistant_proposal_uses_standard_prompt_and_advances_after_acceptance() -> None:
    user: UserContext = DEV_TOKENS["dev-manager"]
    record = {"id": "record-standard-proposal", "knowledgeId": "knowledge-standard-proposal", "title": "受注実績CSV出力"}
    knowledge = {
        "id": "knowledge-standard-proposal",
        "name": "受注実績CSV出力要件",
        "interviewPlan": {"profile": "system_requirement"},
    }
    store.upsert("records", {**record, "tenantId": user.tenant_id})
    store.upsert("knowledges", {**knowledge, "tenantId": user.tenant_id})
    question = {
        "questionId": "q-purpose",
        "questionType": "structured",
        "fieldId": None,
        "text": "受注実績CSV出力を必要とする目的、または現在解決したい課題を教えてください。",
        "targetType": "requirement",
        "targetId": "requirement.purpose_problem",
        "targetLabel": "目的・課題",
    }
    state = build_initial_structured_state("system_requirement", [])
    for requirement_id in ("requirement.users", "requirement.request"):
        state["requirementStates"][requirement_id].update(status="CONFIRMED", value="確認済み")
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
            "id": "standard-proposal-request",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "content": "考えて",
            "isActualUtterance": True,
            "turnType": "ANSWER",
            "answerToQuestionId": question["questionId"],
        },
    )
    proposal_value = "受注実績をCSV形式で出力し、集計・分析や他システムでの利用をしやすくすることを目的とする"
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                dialogueAct="QUESTION_TO_ASSISTANT",
                requirementUpdates=[
                    RequirementUpdate(
                        requirementId="requirement.purpose_problem",
                        value=proposal_value,
                        candidateSource="assistant_proposal",
                        evidenceTranscriptIds=["standard-proposal-request"],
                    )
                ],
            )
        ]
    )

    first = generate_structured_interview_result(record, knowledge, user, provider=provider)

    expected_prompt = f"AIの案です。{proposal_value}という内容でよいですか。修正や拒否もできます。"
    assert first["reply"] == expected_prompt
    assert first["question"]["candidateSource"] == "assistant_proposal"
    assert first["assistantMessage"]["content"] == expected_prompt
    assert first["question"]["targetId"] == "requirement.purpose_problem"

    store.upsert(
        "messages",
        {
            "id": "standard-proposal-acceptance",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "content": "はい",
            "isActualUtterance": True,
            "turnType": "ANSWER",
            "answerToQuestionId": first["question"]["questionId"],
            "targetType": "requirement",
            "targetId": "requirement.purpose_problem",
        },
    )

    second = generate_structured_interview_result(record, knowledge, user, provider=provider)

    assert len(provider.interpret_calls) == 1
    assert second["interviewState"]["requirementStates"]["requirement.purpose_problem"]["status"] == "CONFIRMED"
    assert second["question"]["targetId"] == "requirement.expected_result"
    assert second["question"]["text"] != expected_prompt


def test_persisted_assistant_proposal_is_normalized_before_replay() -> None:
    user: UserContext = DEV_TOKENS["dev-manager"]
    record = {
        "id": "record-persisted-standard-proposal",
        "knowledgeId": "knowledge-persisted-standard-proposal",
        "title": "受注実績CSV出力",
    }
    knowledge = {
        "id": "knowledge-persisted-standard-proposal",
        "name": "受注実績CSV出力要件",
        "interviewPlan": {"profile": "system_requirement"},
    }
    proposal_value = "営業が受注実績を検索し、検索結果をCSV形式で出力できるようにする"
    question = {
        "questionId": "q-persisted-proposal",
        "questionType": "structured",
        "fieldId": None,
        "text": f'「{proposal_value}」でよろしいですか？',
        "targetType": "requirement",
        "targetId": "requirement.request",
        "targetLabel": "要求内容",
        "candidateSource": "assistant_proposal",
        "candidateValue": proposal_value,
    }
    state = build_initial_structured_state("system_requirement", [])
    state["requirementStates"]["requirement.request"].update(
        {
            "status": "AWAITING_CONFIRMATION",
            "answerResolution": "CONFIRM_REQUIRED",
            "candidateValue": proposal_value,
            "candidateSource": "assistant_proposal",
        }
    )
    state.update(
        {
            "id": f"interview-state-{record['id']}",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "currentQuestionId": question["questionId"],
            "nextQuestionTarget": {
                "targetType": "requirement",
                "targetId": "requirement.request",
                "label": "要求内容",
                "priority": 2,
                "candidateSource": "assistant_proposal",
            },
            "askedQuestions": [question],
        }
    )
    store.upsert("records", {**record, "tenantId": user.tenant_id})
    store.upsert("knowledges", {**knowledge, "tenantId": user.tenant_id})
    store.upsert("interview_states", state)

    result = generate_structured_interview_result(
        record,
        knowledge,
        user,
        provider=FakeStructuredProvider([]),
    )

    expected_prompt = f"AIの案です。{proposal_value}という内容でよいですか。修正や拒否もできます。"
    assert result["reply"] == expected_prompt
    assert result["question"]["text"] == expected_prompt
    assert store.get("interview_states", state["id"])["askedQuestions"][0]["text"] == expected_prompt


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


def test_invalid_process_patch_logs_the_rejection_reason(caplog: pytest.LogCaptureFixture) -> None:
    state = build_initial_structured_state("business_process", [])
    state["recordId"] = "record-invalid-process-patch"
    patch = ProcessPatch(
        addNodes=[
            ProcessNode(
                nodeId="search",
                label="検索する",
                evidenceTranscriptIds=["message-1"],
            )
        ],
        addEdges=[
            ProcessEdge(
                edgeId="search-to-missing",
                sourceNodeId="search",
                targetNodeId="missing",
                evidenceTranscriptIds=["message-1"],
            )
        ],
    )

    with caplog.at_level("WARNING"):
        apply_structured_output(
            state,
            StructuredInterviewOutput(processPatch=patch),
            latest_message_id="message-1",
            fields=[],
            profile="business_process",
            valid_evidence_ids={"message-1"},
        )

    assert state["processState"]["nodes"] == []
    assert "structured_process_patch_rejected" in caplog.text
    assert "unknown_target_node_reference:edge:search-to-missing:missing" in caplog.text


def test_invalid_process_patch_is_repaired_before_apply() -> None:
    user: UserContext = DEV_TOKENS["dev-manager"]
    record = {
        "id": "record-process-patch-repair",
        "knowledgeId": "knowledge-process-patch-repair",
        "title": "CSV出力",
    }
    knowledge = {
        "id": "knowledge-process-patch-repair",
        "name": "CSV出力要件",
        "interviewPlan": {"profile": "system_requirement"},
    }
    question = {
        "questionId": "q-process",
        "questionType": "structured",
        "fieldId": None,
        "text": "処理の流れを教えてください。",
        "targetType": "applicability",
        "targetId": "process",
        "targetLabel": "処理の流れがあるか",
    }
    store.upsert("records", {**record, "tenantId": user.tenant_id})
    store.upsert("knowledges", {**knowledge, "tenantId": user.tenant_id})
    state = build_initial_structured_state("system_requirement", [])
    state.update(
        {
            "id": f"interview-state-{record['id']}",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "currentQuestionId": question["questionId"],
            "nextQuestionTarget": {
                "targetType": "applicability",
                "targetId": "process",
                "label": "処理の流れがあるか",
                "priority": 4,
            },
            "askedQuestions": [question],
        }
    )
    store.upsert("interview_states", state)
    store.upsert(
        "messages",
        {
            "id": "process-patch-answer",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "content": "あります。検索してCSVを出力します。",
            "isActualUtterance": True,
            "turnType": "ANSWER",
            "answerToQuestionId": question["questionId"],
        },
    )
    invalid_patch = ProcessPatch(
        addNodes=[
            ProcessNode(
                nodeId="search",
                label="検索する",
                evidenceTranscriptIds=["process-patch-answer"],
            )
        ],
        addEdges=[
            ProcessEdge(
                edgeId="search-to-missing",
                sourceNodeId="search",
                targetNodeId="missing",
                evidenceTranscriptIds=["process-patch-answer"],
            )
        ],
    )
    repaired_patch = ProcessPatch(
        addNodes=[
            ProcessNode(
                nodeId="search",
                label="検索する",
                evidenceTranscriptIds=["process-patch-answer"],
            ),
            ProcessNode(
                nodeId="download",
                label="CSVを出力する",
                nodeType="end",
                evidenceTranscriptIds=["process-patch-answer"],
            ),
        ],
        addEdges=[
            ProcessEdge(
                edgeId="search-to-download",
                sourceNodeId="search",
                targetNodeId="download",
                evidenceTranscriptIds=["process-patch-answer"],
            )
        ],
    )
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                applicability=[
                    ApplicabilityUpdate(
                        topic="process",
                        status="present",
                        evidenceTranscriptIds=["process-patch-answer"],
                    )
                ],
                processPatch=invalid_patch,
            ),
            StructuredInterviewOutput(processPatch=repaired_patch),
        ]
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    assert len(provider.interpret_calls) == 2
    assert provider.interpret_calls[1]["reasoning_effort"] == "medium"
    assert provider.interpret_calls[1]["context"]["processPatchRepair"]["validationErrors"]
    assert result["interviewState"]["processState"]["version"] == 1
    assert [node["nodeId"] for node in result["interviewState"]["processState"]["nodes"]] == [
        "search",
        "download",
    ]
    assert result["interviewState"]["openIssues"] == []


def test_failed_process_patch_does_not_complete_interview() -> None:
    user: UserContext = DEV_TOKENS["dev-manager"]
    record = {
        "id": "record-process-patch-failed",
        "knowledgeId": "knowledge-process-patch-failed",
        "title": "CSV出力",
    }
    knowledge = {
        "id": "knowledge-process-patch-failed",
        "name": "CSV出力要件",
        "interviewPlan": {"profile": "system_requirement"},
    }
    question = {
        "questionId": "q-main-flow",
        "questionType": "structured",
        "fieldId": None,
        "text": "処理の流れを教えてください。",
        "targetType": "process",
        "targetId": "process.main_flow",
        "targetLabel": "処理の流れ",
    }
    store.upsert("records", {**record, "tenantId": user.tenant_id})
    store.upsert("knowledges", {**knowledge, "tenantId": user.tenant_id})
    state = build_initial_structured_state("system_requirement", [])
    for requirement_state in state["requirementStates"].values():
        requirement_state.update(status="CONFIRMED", value="確認済み")
    for topic, applicability_state in state["applicabilityState"].items():
        applicability_state.update(
            status="present" if topic == "process" else "not_applicable",
            evidenceTranscriptIds=["process-retry-answer"],
        )
    state.update(
        {
            "id": f"interview-state-{record['id']}",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "currentQuestionId": question["questionId"],
            "nextQuestionTarget": {
                "targetType": "process",
                "targetId": "process.main_flow",
                "label": "処理の流れ",
                "priority": 3,
            },
            "askedQuestions": [question],
        }
    )
    store.upsert("interview_states", state)
    store.upsert(
        "messages",
        {
            "id": "process-retry-answer",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "content": "検索してCSVを出力します。",
            "isActualUtterance": True,
            "turnType": "ANSWER",
            "answerToQuestionId": question["questionId"],
        },
    )
    invalid_patch = ProcessPatch(
        addNodes=[
            ProcessNode(
                nodeId="search",
                label="検索する",
                evidenceTranscriptIds=["process-retry-answer"],
            )
        ],
        addEdges=[
            ProcessEdge(
                edgeId="invalid-edge",
                sourceNodeId="search",
                targetNodeId="missing",
                evidenceTranscriptIds=["process-retry-answer"],
            )
        ],
    )
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(processPatch=invalid_patch),
            StructuredInterviewOutput(processPatch=invalid_patch),
        ]
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    assert result["status"] == "in_progress"
    assert result["interviewState"]["status"] == "in_progress"
    assert result["question"]["targetId"] == "process.main_flow"
    assert result["interviewState"]["processState"]["nodes"] == []


def test_completed_state_with_missing_process_model_is_reopened() -> None:
    user: UserContext = DEV_TOKENS["dev-manager"]
    record = {
        "id": "record-completed-without-process-model",
        "knowledgeId": "knowledge-completed-without-process-model",
        "title": "CSV出力",
    }
    knowledge = {
        "id": "knowledge-completed-without-process-model",
        "name": "CSV出力要件",
        "interviewPlan": {"profile": "system_requirement"},
    }
    store.upsert("records", {**record, "tenantId": user.tenant_id})
    store.upsert("knowledges", {**knowledge, "tenantId": user.tenant_id})
    state = build_initial_structured_state("system_requirement", [])
    for requirement_state in state["requirementStates"].values():
        requirement_state.update(status="CONFIRMED", value="確認済み")
    for topic, applicability_state in state["applicabilityState"].items():
        applicability_state.update(
            status="present" if topic == "process" else "not_applicable",
            evidenceTranscriptIds=["previous-answer"],
        )
    state.update(
        {
            "id": f"interview-state-{record['id']}",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "status": "completed",
        }
    )
    store.upsert("interview_states", state)

    result = generate_structured_interview_result(
        record,
        knowledge,
        user,
        provider=FakeStructuredProvider([]),
    )

    assert result["status"] == "in_progress"
    assert result["question"]["targetId"] == "process.main_flow"
    assert result["interviewState"]["status"] == "in_progress"


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
    state["closingState"] = "CONFIRMED"
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


@pytest.mark.parametrize("transcript", ["主に社内システムの開発を担当し", "要件整理から実装まで関わっ"])
def test_incomplete_utterance_does_not_commit_or_advance(transcript: str) -> None:
    user, record, knowledge = _seed_fixed_form_case(
        f"record-incomplete-{len(transcript)}",
        (("field-role", "役割"), ("field-department", "部署")),
    )
    provider = FakeStructuredProvider([StructuredInterviewOutput()])

    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_structured_answer(
        record,
        user,
        message_id="incomplete-answer",
        question=first["question"],
        content=transcript,
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    assert result["reply"] == "続き、お願いします。"
    assert result["question"]["questionId"] == first["question"]["questionId"]
    assert len(result["interviewState"]["askedQuestions"]) == 1
    field_state = result["interviewState"]["fieldStates"]["field-role"]
    assert field_state["answerState"] == "UNANSWERED"
    assert field_state["candidateAnswer"] is None
    assert result["interviewState"]["lastUtteranceCompleteness"] == "INCOMPLETE"


def test_interpreter_can_mark_an_utterance_incomplete_without_backend_keyword_guessing() -> None:
    user, record, knowledge = _seed_fixed_form_case(
        "record-explicit-incomplete",
        (("field-role", "役割"), ("field-department", "部署")),
    )
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                utteranceCompleteness="INCOMPLETE",
                fieldUpdates=[
                    FieldUpdate(
                        fieldId="field-role",
                        value="社内システムの開発を担当し",
                        evidenceTranscriptIds=["explicit-incomplete-answer"],
                        answerResolution="AUTO_CONFIRM",
                    )
                ],
            )
        ]
    )
    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_structured_answer(
        record,
        user,
        message_id="explicit-incomplete-answer",
        question=first["question"],
        content="社内システムの開発を担当し",
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    assert result["reply"] == "続き、お願いします。"
    assert result["interviewState"]["fieldStates"]["field-role"]["answerState"] == "UNANSWERED"
    assert result["interviewState"]["lastStructuredOutput"]["utteranceCompleteness"] == "INCOMPLETE"


def test_complete_utterance_commits_and_advances_to_the_next_field() -> None:
    user, record, knowledge = _seed_fixed_form_case(
        "record-complete-utterance",
        (("field-role", "役割"), ("field-department", "部署")),
    )
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                fieldUpdates=[
                    FieldUpdate(
                        fieldId="field-role",
                        value="ウェブアプリケーションの設計・開発を担当しています。",
                        evidenceTranscriptIds=["complete-answer"],
                        answerResolution="AUTO_CONFIRM",
                    )
                ]
            )
        ]
    )
    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_structured_answer(
        record,
        user,
        message_id="complete-answer",
        question=first["question"],
        content="ウェブアプリケーションの設計・開発を担当しています。",
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    assert result["interviewState"]["fieldStates"]["field-role"]["answerState"] == "CONFIRMED"
    assert result["interviewState"]["fieldStates"]["field-role"]["recordAnswer"] == (
        "ウェブアプリケーションの設計・開発を担当しています。"
    )
    assert result["interviewState"]["lastStructuredOutput"]["dialogueAct"] == "ANSWER"
    assert result["question"]["questionId"] != first["question"]["questionId"]
    assert result["question"]["targetId"] == "field-department"
    assert result["reply"] == "部署を教えてください。"
    assert len(provider.question_calls) == 2
    assert result["latencyMetrics"]["interpreter_calls"] == 1
    assert result["latencyMetrics"]["question_generation_calls"] == 1
    assert result["latencyMetrics"]["retrieval_calls"] == 1


@pytest.mark.parametrize(
    ("transcript", "dialogue_act"),
    [
        ("うーん", "HESITATION"),
        ("へえ", "BACKCHANNEL"),
        ("先ほど回答しました", "OTHER"),
    ],
    ids=["GEN-009", "GEN-010", "GEN-012"],
)
def test_non_answer_dialogue_acts_keep_the_current_question_without_regeneration(
    transcript: str,
    dialogue_act: str,
) -> None:
    user, record, knowledge = _seed_fixed_form_case(
        f"record-non-answer-{dialogue_act}",
        (("field-role", "役割"), ("field-department", "部署")),
    )
    provider = FakeStructuredProvider(
        [StructuredInterviewOutput(dialogueAct=dialogue_act)]
    )
    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    state_before = store.get("interview_states", f"interview-state-{record['id']}")
    field_state_before = deepcopy(state_before["fieldStates"]["field-role"])
    _add_structured_answer(
        record,
        user,
        message_id=f"non-answer-{dialogue_act}",
        question=first["question"],
        content=transcript,
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    assert result["question"]["questionId"] == first["question"]["questionId"]
    assert result["question"]["targetId"] == "field-role"
    assert result["interviewState"]["fieldStates"]["field-role"] == field_state_before
    assert result["interviewState"]["lastStructuredOutput"]["dialogueAct"] == dialogue_act
    assert result["reply"] == "急がなくて大丈夫です。続けられるところからお願いします。"
    assert len(provider.question_calls) == 1


def test_GEN_005_question_clarification_is_not_reframed_as_an_stt_retry() -> None:
    user, record, knowledge = _seed_fixed_form_case(
        "record-question-clarification",
        (("field-role", "役割"),),
    )
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                dialogueAct="CLARIFICATION_REQUEST",
                utteranceCompleteness="UNCERTAIN",
            )
        ]
    )
    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_structured_answer(
        record,
        user,
        message_id="clarification-request",
        question=first["question"],
        content="どういう意味ですか？",
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    assert result["question"]["questionId"] == first["question"]["questionId"]
    assert result["interviewState"]["fieldStates"]["field-role"]["answerState"] == "UNANSWERED"
    assert "聞き取" not in result["reply"]
    assert "たとえば" in result["reply"]
    assert result["interviewState"]["lastStructuredOutput"]["dialogueAct"] == "CLARIFICATION_REQUEST"
    assert len(provider.question_calls) == 1


@pytest.mark.parametrize(
    "transcript",
    ["よくわからない", "よく分からないですね", "答えが思いつかない"],
    ids=["GEN-006-bare", "GEN-006-polite", "answer-not-coming-to-mind"],
)
def test_unanswerable_language_is_not_treated_as_stt_failure(transcript: str) -> None:
    user, record, knowledge = _seed_fixed_form_case(
        f"record-unanswerable-language-{transcript}",
        (("field-role", "強み"), ("field-department", "部署")),
    )
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                dialogueAct="ANSWER",
                answerAssessment=AnswerAssessment(
                    sufficiency="UNANSWERABLE",
                    probeType="REFRAME",
                ),
            )
        ]
    )
    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_structured_answer(
        record,
        user,
        message_id="unanswerable-language",
        question=first["question"],
        content=transcript,
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    assert result["question"]["questionId"] == first["question"]["questionId"]
    assert result["interviewState"]["fieldStates"]["field-role"]["answerState"] == "UNANSWERED"
    assert result["interviewState"]["lastStructuredOutput"]["answerAssessment"]["sufficiency"] == "UNANSWERABLE"
    assert "聞き取" not in result["reply"]
    assert "具体的な出来事" in result["reply"]
    assert result["latencyMetrics"]["question_generation_calls"] == 0
    assert result["latencyMetrics"]["retrieval_calls"] == 0


def test_GEN_014_qualified_confirmation_keeps_candidate_until_the_difference_is_stated() -> None:
    user, record, knowledge = _seed_fixed_form_case(
        "record-qualified-confirmation",
        (("field-role", "役割"),),
    )
    provider = FakeStructuredProvider([StructuredInterviewOutput(dialogueAct="CONFIRMATION")])
    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    state = store.get("interview_states", f"interview-state-{record['id']}")
    state["fieldStates"]["field-role"].update(
        {
            "answerState": "AWAITING_CONFIRMATION",
            "status": "asking",
            "candidateAnswer": "設計を担当する",
            "candidateSource": "user_statement",
        }
    )
    store.upsert("interview_states", state)
    _add_structured_answer(
        record,
        user,
        message_id="qualified-confirmation",
        question=first["question"],
        content="ちょっと違うけど、いいよ",
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    field_state = result["interviewState"]["fieldStates"]["field-role"]
    assert result["question"]["questionId"] == first["question"]["questionId"]
    assert field_state["answerState"] == "AWAITING_CONFIRMATION"
    assert field_state["candidateAnswer"] == "設計を担当する"
    assert result["reply"] == "少し違う部分があれば、その点だけ教えてください。"
    assert len(provider.question_calls) == 1


def test_unanswerable_uses_one_static_reframe_before_advancing() -> None:
    user, record, knowledge = _seed_fixed_form_case(
        "record-unanswerable-static-reframe",
        (("field-role", "役割"), ("field-department", "部署")),
    )
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                dialogueAct="ANSWER",
                answerAssessment=AnswerAssessment(
                    sufficiency="UNANSWERABLE",
                    probeType="REFRAME",
                ),
            )
        ]
    )
    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_structured_answer(
        record,
        user,
        message_id="unanswerable-answer",
        question=first["question"],
        content="答えが思いつかない",
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    assert result["question"]["questionId"] == first["question"]["questionId"]
    assert result["interviewState"]["fieldStates"]["field-role"]["answerState"] == "UNANSWERED"
    assert result["interviewState"]["activeProbeTarget"]["probeType"] == "REFRAME"
    assert "具体的な出来事" in result["reply"]
    assert "聞き取" not in result["reply"]
    assert len(provider.question_calls) == 1
    assert result["latencyMetrics"]["interpreter_calls"] == 1
    assert result["latencyMetrics"]["question_generation_calls"] == 0
    assert result["latencyMetrics"]["retrieval_calls"] == 0


def test_corrected_transcript_is_confirmed_before_becoming_a_formal_answer() -> None:
    user, record, knowledge = _seed_fixed_form_case(
        "record-transcript-correction",
        (("field-role", "担当領域"), ("field-department", "部署")),
    )
    raw = "要件整理から実装を輸送ス後の星星まで関わっ"
    corrected = "要件整理から実装後の運用まで関わっています。"
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                transcriptAssessment=TranscriptAssessment(
                    rawTranscript=raw,
                    normalizedTranscript=corrected,
                    correctionStatus="CORRECTED",
                    correctionCandidates=[corrected],
                    correctionReason="明らかな音声認識誤り",
                ),
                fieldUpdates=[
                    FieldUpdate(
                        fieldId="field-role",
                        value=corrected,
                        evidenceTranscriptIds=["correction-answer"],
                        answerResolution="AUTO_CONFIRM",
                    )
                ],
            ),
            StructuredInterviewOutput(dialogueAct="CONFIRMATION"),
        ]
    )
    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_structured_answer(
        record,
        user,
        message_id="correction-answer",
        question=first["question"],
        content=raw,
    )

    candidate = generate_structured_interview_result(record, knowledge, user, provider=provider)

    candidate_state = candidate["interviewState"]["fieldStates"]["field-role"]
    assert candidate["question"]["targetType"] == "transcript_confirmation"
    assert corrected in candidate["question"]["text"]
    assert raw not in candidate["question"]["text"]
    assert candidate_state["answerState"] == "AWAITING_CONFIRMATION"
    assert candidate_state["recordAnswer"] is None
    assert candidate_state["candidateAnswer"] == corrected
    stored_answer = store.get("messages", "correction-answer")
    assert stored_answer["content"] == raw
    assert stored_answer["rawTranscript"] == raw
    assert stored_answer["normalizedTranscript"] == corrected
    assert stored_answer["correctionStatus"] == "CORRECTED"

    _add_structured_answer(
        record,
        user,
        message_id="correction-confirmation",
        question=candidate["question"],
        content="はい",
    )
    confirmed = generate_structured_interview_result(record, knowledge, user, provider=provider)

    confirmed_state = confirmed["interviewState"]["fieldStates"]["field-role"]
    assert confirmed_state["answerState"] == "CONFIRMED"
    assert confirmed_state["recordAnswer"] == corrected
    assert confirmed["interviewState"]["pendingTranscriptConfirmation"] is None
    assert confirmed["question"]["targetId"] == "field-department"


def test_GEN_017_numeric_self_correction_keeps_the_latest_value_only() -> None:
    user, record, knowledge = _seed_fixed_form_case(
        "record-gen-017-numeric-correction",
        (("field-age", "年齢"), ("field-department", "部署")),
    )
    raw = "31……いや、32歳です"
    corrected = "32歳です"
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                transcriptAssessment=TranscriptAssessment(
                    rawTranscript=raw,
                    normalizedTranscript=corrected,
                    correctionStatus="CORRECTED",
                    correctionCandidates=[corrected],
                    correctionReason="数字の言い直し",
                ),
                fieldUpdates=[
                    FieldUpdate(
                        fieldId="field-age",
                        value=corrected,
                        evidenceTranscriptIds=["gen-017-answer"],
                        answerResolution="AUTO_CONFIRM",
                    )
                ],
            ),
            StructuredInterviewOutput(dialogueAct="CONFIRMATION"),
        ]
    )
    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_structured_answer(
        record,
        user,
        message_id="gen-017-answer",
        question=first["question"],
        content=raw,
    )

    candidate = generate_structured_interview_result(record, knowledge, user, provider=provider)
    candidate_state = candidate["interviewState"]["fieldStates"]["field-age"]

    assert candidate["question"]["targetType"] == "transcript_confirmation"
    assert candidate["question"]["questionId"] != first["question"]["questionId"]
    assert corrected in candidate["question"]["text"]
    assert raw not in candidate["question"]["text"]
    assert candidate_state["answerState"] == "AWAITING_CONFIRMATION"
    assert candidate_state["recordAnswer"] is None
    assert candidate_state["candidateAnswer"] == corrected

    _add_structured_answer(
        record,
        user,
        message_id="gen-017-confirmation",
        question=candidate["question"],
        content="はい、それで合っています",
    )
    confirmed = generate_structured_interview_result(record, knowledge, user, provider=provider)
    confirmed_state = confirmed["interviewState"]["fieldStates"]["field-age"]

    assert confirmed_state["answerState"] == "CONFIRMED"
    assert confirmed_state["recordAnswer"] == corrected
    assert "31" not in str(confirmed_state["recordAnswer"])
    assert confirmed["question"]["targetId"] == "field-department"


def test_ambiguous_transcript_correction_requests_repetition_without_committing() -> None:
    user, record, knowledge = _seed_fixed_form_case(
        "record-ambiguous-transcript",
        (("field-role", "担当領域"), ("field-department", "部署")),
    )
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                transcriptAssessment=TranscriptAssessment(
                    normalizedTranscript="",
                    correctionStatus="UNCERTAIN",
                    correctionCandidates=["運用", "輸送"],
                    correctionReason="候補が一意でない",
                ),
            )
        ]
    )
    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_structured_answer(
        record,
        user,
        message_id="ambiguous-answer",
        question=first["question"],
        content="音声認識が曖昧な発話です",
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    assert result["reply"] == "この部分をもう一度お願いします。"
    assert result["question"]["questionId"] == first["question"]["questionId"]
    assert result["interviewState"]["pendingTranscriptConfirmation"] is None
    assert result["interviewState"]["fieldStates"]["field-role"]["candidateAnswer"] is None
    assert result["interviewState"]["lastTranscriptAssessment"]["correctionStatus"] == "UNCERTAIN"


def test_multiple_corrected_candidates_are_not_confirmed_automatically() -> None:
    user, record, knowledge = _seed_fixed_form_case(
        "record-multiple-correction-candidates",
        (("field-role", "担当領域"), ("field-department", "部署")),
    )
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                transcriptAssessment=TranscriptAssessment(
                    normalizedTranscript="運用まで関わっています。",
                    correctionStatus="CORRECTED",
                    correctionCandidates=["運用まで関わっています。", "輸送まで関わっています。"],
                ),
                fieldUpdates=[
                    FieldUpdate(
                        fieldId="field-role",
                        value="運用まで関わっています。",
                        evidenceTranscriptIds=["multiple-correction-answer"],
                        answerResolution="AUTO_CONFIRM",
                    )
                ],
            )
        ]
    )
    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_structured_answer(
        record,
        user,
        message_id="multiple-correction-answer",
        question=first["question"],
        content="認識候補が複数ある発話です",
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    field_state = result["interviewState"]["fieldStates"]["field-role"]
    assert result["reply"] == "この部分をもう一度お願いします。"
    assert result["question"]["questionId"] == first["question"]["questionId"]
    assert field_state["answerState"] == "UNANSWERED"
    assert field_state["recordAnswer"] is None
    assert result["interviewState"]["lastTranscriptAssessment"]["correctionStatus"] == "UNCERTAIN"


def test_empty_corrected_candidate_is_treated_as_uncertain_without_committing() -> None:
    user, record, knowledge = _seed_fixed_form_case(
        "record-empty-correction",
        (("field-role", "担当領域"), ("field-department", "部署")),
    )
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                transcriptAssessment=TranscriptAssessment(
                    correctionStatus="CORRECTED",
                    normalizedTranscript="",
                    correctionCandidates=[],
                    correctionReason="補正候補が空",
                ),
                fieldUpdates=[
                    FieldUpdate(
                        fieldId="field-role",
                        value="存在しない補正",
                        evidenceTranscriptIds=["empty-correction-answer"],
                        answerResolution="AUTO_CONFIRM",
                    )
                ],
            )
        ]
    )
    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_structured_answer(
        record,
        user,
        message_id="empty-correction-answer",
        question=first["question"],
        content="音声認識が不明瞭です",
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    field_state = result["interviewState"]["fieldStates"]["field-role"]
    assert result["reply"] == "この部分をもう一度お願いします。"
    assert result["question"]["questionId"] == first["question"]["questionId"]
    assert field_state["answerState"] == "UNANSWERED"
    assert field_state["candidateAnswer"] is None
    assert result["interviewState"]["lastTranscriptAssessment"]["correctionStatus"] == "UNCERTAIN"


def test_rejected_transcript_correction_is_discarded_and_original_question_is_restored() -> None:
    user, record, knowledge = _seed_fixed_form_case(
        "record-rejected-transcript",
        (("field-role", "担当領域"), ("field-department", "部署")),
    )
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                transcriptAssessment=TranscriptAssessment(
                    normalizedTranscript="運用まで関わっています。",
                    correctionStatus="CORRECTED",
                    correctionCandidates=["運用まで関わっています。"],
                ),
                fieldUpdates=[
                    FieldUpdate(
                        fieldId="field-role",
                        value="運用まで関わっています。",
                        evidenceTranscriptIds=["rejected-answer"],
                    )
                ],
            ),
            StructuredInterviewOutput(dialogueAct="REJECTION"),
        ]
    )
    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_structured_answer(
        record,
        user,
        message_id="rejected-answer",
        question=first["question"],
        content="運用まで関わっ",
    )
    candidate = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_structured_answer(
        record,
        user,
        message_id="rejected-confirmation",
        question=candidate["question"],
        content="違います",
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    field_state = result["interviewState"]["fieldStates"]["field-role"]
    assert result["reply"] == "この部分をもう一度お願いします。"
    assert result["question"]["targetId"] == "field-role"
    assert result["question"]["questionId"] == first["question"]["questionId"]
    assert result["interviewState"]["pendingTranscriptConfirmation"] is None
    assert field_state["answerState"] == "UNANSWERED"
    assert field_state["candidateAnswer"] is None


def test_generated_question_is_reduced_to_one_question_without_a_thematic_preamble() -> None:
    user, record, knowledge = _seed_fixed_form_case(
        "record-single-question",
        (("field-role", "現在の役割"), ("field-department", "部署")),
    )

    class DuplicateQuestionProvider(FakeStructuredProvider):
        def generate_question(self, *, target: Mapping[str, object], **_: object) -> QuestionGenerationOutput:
            return QuestionGenerationOutput(
                questionText=(
                    "では、現在の役割について教えてください。"
                    "現在の役割について、担当している業務や責任を教えてください。"
                )
            )

    result = generate_structured_interview_result(
        record,
        knowledge,
        user,
        provider=DuplicateQuestionProvider([]),
    )

    assert result["reply"] == "現在の役割について、担当している業務や責任を教えてください。"
    assert result["reply"].count("教えてください") == 1


def test_generated_reply_does_not_echo_a_long_answer_with_a_fixed_reaction() -> None:
    user, record, knowledge = _seed_fixed_form_case(
        "record-no-long-echo",
        (("field-role", "現在の役割"), ("field-department", "部署")),
    )
    long_answer = (
        "山田太郎です。開発部に所属しており、社内システムの設計と開発を担当しています。"
    )

    class EchoingQuestionProvider(FakeStructuredProvider):
        def generate_question(self, **_: object) -> QuestionGenerationOutput:
            return QuestionGenerationOutput(
                questionText=(
                    f"{long_answer}なんですね。"
                    "現在の役割で、特に工夫していることを教えてください。"
                )
            )

    result = generate_structured_interview_result(
        record,
        knowledge,
        user,
        provider=EchoingQuestionProvider([]),
    )

    assert result["reply"] == "現在の役割で、特に工夫していることを教えてください。"
    assert long_answer not in result["reply"]
    assert "なんですね" not in result["reply"]
    assert result["reply"].count("教えてください") == 1


def test_unanswerable_target_gets_one_neutral_probe_then_accepts_explicit_no_detail() -> None:
    user: UserContext = DEV_TOKENS["dev-manager"]
    record = {
        "id": "record-probe-no-detail",
        "knowledgeId": "knowledge-probe-no-detail",
        "title": "キャリアインタビュー",
    }
    knowledge = {
        "id": "knowledge-probe-no-detail",
        "name": "キャリアインタビュー",
        "interviewPlan": {"profile": "system_requirement"},
    }
    store.upsert("records", {**record, "tenantId": user.tenant_id})
    store.upsert("knowledges", {**knowledge, "tenantId": user.tenant_id})
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                answerAssessment=AnswerAssessment(
                    sufficiency="UNANSWERABLE",
                    probeType="REFRAME",
                )
            ),
            StructuredInterviewOutput(
                answerAssessment=AnswerAssessment(
                    sufficiency="REFUSAL",
                    probeType="CLARIFY",
                )
            ),
        ]
    )

    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    first_target_id = first["question"]["targetId"]
    _add_structured_answer(
        record,
        user,
        message_id="probe-first-answer",
        question=first["question"],
        content="あまり覚えていません。特に大きな転機はなかったと思います。",
    )
    probe = generate_structured_interview_result(record, knowledge, user, provider=provider)

    assert probe["question"]["targetId"] == first_target_id
    assert probe["interviewState"]["activeProbeTarget"]["targetId"] == first_target_id
    assert probe["question"]["questionId"] == first["question"]["questionId"]

    _add_structured_answer(
        record,
        user,
        message_id="probe-second-answer",
        question=probe["question"],
        content="特にありません。",
    )
    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    target_state = result["interviewState"]["requirementStates"][first_target_id]
    assert target_state["status"] == "CONFIRMED"
    assert target_state["answerDisposition"] == "NO_DETAIL"
    assert result["interviewState"]["activeProbeTarget"] is None
    assert result["question"]["targetId"] != first_target_id


def test_GEN_025_explicit_refusal_is_not_repeated_beyond_one_neutral_probe() -> None:
    user, record, knowledge = _seed_fixed_form_case(
        "record-gen-025-refusal",
        (("field-age", "年齢"), ("field-department", "部署")),
    )
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                dialogueAct="ANSWER",
                answerAssessment=AnswerAssessment(
                    sufficiency="REFUSAL",
                    probeType="CLARIFY",
                ),
            ),
            StructuredInterviewOutput(
                dialogueAct="ANSWER",
                answerAssessment=AnswerAssessment(
                    sufficiency="REFUSAL",
                    probeType="CLARIFY",
                ),
            ),
        ]
    )
    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_structured_answer(
        record,
        user,
        message_id="gen-025-refusal-1",
        question=first["question"],
        content="それは答えたくないです",
    )

    probe = generate_structured_interview_result(record, knowledge, user, provider=provider)
    first_field_state = probe["interviewState"]["fieldStates"]["field-age"]
    assert probe["question"]["questionId"] == first["question"]["questionId"]
    assert first_field_state["answerState"] == "UNANSWERED"
    assert probe["interviewState"]["activeProbeTarget"]["targetId"] == "field-age"
    assert "答えたくないです" not in probe["reply"]

    _add_structured_answer(
        record,
        user,
        message_id="gen-025-refusal-2",
        question=probe["question"],
        content="やはり答えたくないです",
    )
    result = generate_structured_interview_result(record, knowledge, user, provider=provider)
    final_field_state = result["interviewState"]["fieldStates"]["field-age"]

    assert final_field_state["answerState"] == "CONFIRMED"
    assert final_field_state["answerDisposition"] == "NO_DETAIL"
    assert result["interviewState"]["activeProbeTarget"] is None
    assert result["question"]["targetId"] == "field-department"
    assert result["question"]["questionId"] != first["question"]["questionId"]


def test_one_answer_can_confirm_multiple_fields_without_reasking_them() -> None:
    user, record, knowledge = _seed_fixed_form_case(
        "record-multiple-field-answer",
        (
            ("field-name", "氏名"),
            ("field-department", "部署"),
            ("field-role", "役職"),
        ),
    )
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                fieldUpdates=[
                    FieldUpdate(
                        fieldId="field-name",
                        value="山田太郎",
                        evidenceTranscriptIds=["multiple-answer"],
                        answerResolution="AUTO_CONFIRM",
                    ),
                    FieldUpdate(
                        fieldId="field-department",
                        value="開発部",
                        evidenceTranscriptIds=["multiple-answer"],
                        answerResolution="AUTO_CONFIRM",
                    ),
                    FieldUpdate(
                        fieldId="field-role",
                        value="主任",
                        evidenceTranscriptIds=["multiple-answer"],
                        answerResolution="AUTO_CONFIRM",
                    ),
                ]
            )
        ]
    )
    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_structured_answer(
        record,
        user,
        message_id="multiple-answer",
        question=first["question"],
        content="山田太郎です。開発部の主任で、社内システムの設計と開発を担当しています。",
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    states = result["interviewState"]["fieldStates"]
    assert states["field-name"]["answerState"] == "CONFIRMED"
    assert states["field-department"]["answerState"] == "CONFIRMED"
    assert states["field-role"]["answerState"] == "CONFIRMED"
    assert result["question"]["targetType"] == "closing"
    assert result["interviewState"]["closingState"] == "ASKING"


@pytest.mark.parametrize(
    "transcript",
    [
        "主に社内システムの開発を担当し",
        "要件整理から実装まで関わっ",
        "私が担当しているのは、主に",
        "例えば",
        "そうですね、私の場合は",
        "開発と",
        "それから",
        "担当しているのは",
        "基本的には再起動するのですが",
        "えーっと……",
    ],
)
def test_obvious_fragment_or_hesitation_never_advances_the_current_target(
    transcript: str,
) -> None:
    user, record, knowledge = _seed_fixed_form_case(
        f"record-fragment-{abs(hash(transcript))}",
        (("field-role", "役割"), ("field-department", "部署")),
    )
    provider = FakeStructuredProvider([StructuredInterviewOutput()])
    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_structured_answer(
        record,
        user,
        message_id="fragment-answer",
        question=first["question"],
        content=transcript,
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    assert result["question"]["questionId"] == first["question"]["questionId"]
    assert result["question"]["targetId"] == first["question"]["targetId"]
    assert result["interviewState"]["lastUtteranceCompleteness"] == "INCOMPLETE"
    assert result["interviewState"]["fieldStates"]["field-role"]["answerState"] == "UNANSWERED"
    assert result["interviewState"]["fieldStates"]["field-role"]["candidateAnswer"] is None
    assert result["reply"] == "続き、お願いします。"


def test_safe_transcript_normalization_keeps_raw_text_and_only_changes_formatting() -> None:
    user, record, knowledge = _seed_fixed_form_case(
        "record-safe-transcript-normalization",
        (("field-name", "氏名"), ("field-department", "部署")),
    )
    raw = " 山田  太郎 です。 "
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                transcriptAssessment=TranscriptAssessment(
                    normalizedTranscript="ignored by backend for NONE",
                    correctionStatus="NONE",
                ),
                fieldUpdates=[
                    FieldUpdate(
                        fieldId="field-name",
                        value="山田太郎",
                        evidenceTranscriptIds=["safe-normalization-answer"],
                        answerResolution="AUTO_CONFIRM",
                    )
                ],
            )
        ]
    )
    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_structured_answer(
        record,
        user,
        message_id="safe-normalization-answer",
        question=first["question"],
        content=raw,
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)
    stored_message = store.get("messages", "safe-normalization-answer")

    assert stored_message["rawTranscript"] == raw.strip()
    assert stored_message["normalizedTranscript"] == "山田太郎です。"
    assert stored_message["correctionStatus"] == "NONE"
    assert result["interviewState"]["fieldStates"]["field-name"]["recordAnswer"] == "山田太郎"


def test_semantically_partial_answer_is_a_probe_candidate_not_a_confirmed_answer() -> None:
    user, record, knowledge = _seed_fixed_form_case(
        "record-semantic-partial-answer",
        (("field-role", "現在の役割"), ("field-department", "部署")),
    )

    class ProbeProvider(FakeStructuredProvider):
        def __init__(self) -> None:
            super().__init__(
                [
                    StructuredInterviewOutput(
                        answerAssessment=AnswerAssessment(
                            sufficiency="EXAMPLE_MISSING",
                            probeType="EXAMPLE",
                        ),
                        fieldUpdates=[
                            FieldUpdate(
                                fieldId="field-role",
                                value="トラブル対応が得意です",
                                evidenceTranscriptIds=["partial-answer"],
                                answerResolution="AUTO_CONFIRM",
                            )
                        ],
                    )
                ]
            )

        def generate_question(
            self,
            *,
            target: Mapping[str, object],
            **_: object,
        ) -> QuestionGenerationOutput:
            return QuestionGenerationOutput(questionText="最近対応した具体的な事例を一つ教えてください。")

    provider = ProbeProvider()
    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_structured_answer(
        record,
        user,
        message_id="partial-answer",
        question=first["question"],
        content="トラブル対応が得意です。",
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)
    field_state = result["interviewState"]["fieldStates"]["field-role"]

    assert field_state["answerState"] == "CANDIDATE_PENDING"
    assert field_state["answerResolution"] == "TENTATIVE"
    assert field_state["recordAnswer"] is None
    assert result["interviewState"]["activeProbeTarget"] == {
        "targetType": "field",
        "targetId": "field-role",
        "label": "現在の役割",
        "probeType": "EXAMPLE",
        "probeCount": 1,
    }
    assert result["question"]["targetId"] == "field-role"
    assert result["question"]["text"] == "最近対応した具体的な事例を一つ教えてください。"


def test_user_question_explains_the_current_target_without_answering_or_advancing() -> None:
    user, record, knowledge = _seed_fixed_form_case(
        "record-question-help",
        (("field-role", "現在の役割"), ("field-department", "部署")),
    )
    provider = FakeStructuredProvider(
        [StructuredInterviewOutput(dialogueAct="QUESTION_TO_ASSISTANT")]
    )
    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_structured_answer(
        record,
        user,
        message_id="question-help-answer",
        question=first["question"],
        content="具体的には何を答えればいいですか？",
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    assert result["action"] == "ask_follow_up"
    assert result["question"]["questionId"] == first["question"]["questionId"]
    assert len(result["interviewState"]["askedQuestions"]) == 1
    assert result["interviewState"]["fieldStates"]["field-role"]["answerState"] == "UNANSWERED"
    assert "この質問では" in result["reply"]


def test_invalid_evidence_cannot_commit_a_hallucinated_field_value() -> None:
    user, record, knowledge = _seed_fixed_form_case(
        "record-invalid-evidence",
        (("field-role", "役割"), ("field-department", "部署")),
    )
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                fieldUpdates=[
                    FieldUpdate(
                        fieldId="field-role",
                        value="ユーザーが言っていない役職",
                        evidenceTranscriptIds=["missing-message"],
                        answerResolution="AUTO_CONFIRM",
                    )
                ]
            )
        ]
    )
    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_structured_answer(
        record,
        user,
        message_id="grounded-answer",
        question=first["question"],
        content="開発部です。",
    )

    result = generate_structured_interview_result(record, knowledge, user, provider=provider)

    field_state = result["interviewState"]["fieldStates"]["field-role"]
    assert field_state["answerState"] == "UNANSWERED"
    assert field_state["recordAnswer"] is None
    assert field_state["candidateAnswer"] is None
    assert result["question"]["targetId"] == "field-role"


def test_explicit_correction_updates_the_latest_confirmed_value_without_merging_old_value() -> None:
    state = build_initial_structured_state(
        "fixed_form",
        [{"id": "field-department", "required": True}],
    )
    apply_structured_output(
        state,
        StructuredInterviewOutput(
            fieldUpdates=[
                FieldUpdate(
                    fieldId="field-department",
                    value="開発部",
                    evidenceTranscriptIds=["department-1"],
                    answerResolution="AUTO_CONFIRM",
                )
            ]
        ),
        latest_message_id="department-1",
        fields=[{"id": "field-department", "required": True}],
        profile="fixed_form",
        valid_evidence_ids={"department-1"},
    )
    apply_structured_output(
        state,
        StructuredInterviewOutput(
            dialogueAct="CORRECTION",
            fieldUpdates=[
                FieldUpdate(
                    fieldId="field-department",
                    value="DX推進部",
                    evidenceTranscriptIds=["department-2"],
                    answerResolution="AUTO_CONFIRM",
                )
            ]
        ),
        latest_message_id="department-2",
        fields=[{"id": "field-department", "required": True}],
        profile="fixed_form",
        valid_evidence_ids={"department-1", "department-2"},
    )

    field_state = state["fieldStates"]["field-department"]
    assert field_state["answerState"] == "CONFIRMED"
    assert field_state["recordAnswer"] == "DX推進部"
    assert field_state["rawAnswerHistory"] == ["開発部", "DX推進部"]


def test_all_required_targets_are_not_complete_until_one_closing_answer_is_recorded() -> None:
    user, record, knowledge = _seed_fixed_form_case(
        "record-open-ended-closing",
        (("field-role", "役割"),),
    )
    provider = FakeStructuredProvider(
        [
            StructuredInterviewOutput(
                fieldUpdates=[
                    FieldUpdate(
                        fieldId="field-role",
                        value="主任",
                        evidenceTranscriptIds=["closing-role-answer"],
                        answerResolution="AUTO_CONFIRM",
                    )
                ]
            ),
            StructuredInterviewOutput(answerAssessment=AnswerAssessment(sufficiency="REFUSAL")),
        ]
    )
    first = generate_structured_interview_result(record, knowledge, user, provider=provider)
    _add_structured_answer(
        record,
        user,
        message_id="closing-role-answer",
        question=first["question"],
        content="主任です。",
    )
    before_closing = generate_structured_interview_result(record, knowledge, user, provider=provider)

    assert before_closing["status"] == "in_progress"
    assert before_closing["question"]["targetType"] == "closing"
    assert before_closing["interviewState"]["closingState"] == "ASKING"
    assert before_closing["interviewState"]["closingAnswer"] is None

    _add_structured_answer(
        record,
        user,
        message_id="closing-answer",
        question=before_closing["question"],
        content="特にありません。",
    )
    finished = generate_structured_interview_result(record, knowledge, user, provider=provider)

    assert finished["status"] == "completed"
    assert finished["action"] == "finish"
    assert finished["interviewState"]["closingState"] == "CONFIRMED"
    assert finished["interviewState"]["closingAnswer"]["rawTranscript"] == "特にありません。"
