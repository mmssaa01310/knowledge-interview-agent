from __future__ import annotations

from collections.abc import Mapping
from threading import Event

import pytest

from ai_interviewer_api.agents.interview_knowledge.coordinator import (
    build_initial_structured_state,
    select_next_question_target,
)
from ai_interviewer_api.agents.interview_knowledge.fast_interpreter.schemas import (
    FastAnswerAssessment,
)
from ai_interviewer_api.agents.interview_knowledge.fast_interpreter.provider import (
    BedrockFastInterpreterProvider,
)
from ai_interviewer_api.agents.interview_knowledge.fast_interpreter.service import (
    build_fast_interpreter_context,
    can_proceed,
)
from ai_interviewer_api.agents.interview_knowledge.schemas import (
    AnswerAssessment,
    FieldUpdate,
    QuestionGenerationOutput,
    StructuredInterviewOutput,
    TranscriptAssessment,
)
from ai_interviewer_api.agents.interview_knowledge.service import start_fast_interview_turn
from ai_interviewer_api.auth.deps import DEV_TOKENS
from ai_interviewer_api.repositories.store import store


class FakeFastProvider:
    def __init__(
        self,
        assessment: FastAnswerAssessment,
        *,
        background_started: Event | None = None,
    ) -> None:
        self.assessment = assessment
        self.background_started = background_started
        self.contexts: list[dict[str, object]] = []

    def assess(self, *, context: Mapping[str, object]) -> FastAnswerAssessment:
        self.contexts.append(dict(context))
        if self.background_started is not None:
            assert self.background_started.wait(2)
        return self.assessment


class FakeStructuredProvider:
    def __init__(
        self,
        output: StructuredInterviewOutput,
        *,
        background_started: Event | None = None,
    ) -> None:
        self.output = output
        self.background_started = background_started
        self.interpret_calls: list[dict[str, object]] = []
        self.question_calls: list[dict[str, object]] = []

    def interpret(self, **kwargs: object) -> StructuredInterviewOutput:
        self.interpret_calls.append(dict(kwargs))
        if self.background_started is not None:
            self.background_started.set()
        return self.output

    def generate_question(
        self,
        *,
        target: Mapping[str, object],
        **_: object,
    ) -> QuestionGenerationOutput:
        self.question_calls.append(dict(target))
        return QuestionGenerationOutput(
            questionText=f"{target.get('label') or '次の項目'}を教えてください。"
        )


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.tables.clear()


def test_fast_assessment_only_allows_a_minimum_screen() -> None:
    cases = [
        (True, True, False, True),
        (True, True, True, False),
        (False, True, False, False),
        (True, False, False, False),
    ]
    for minimum, understandable, incomplete, expected in cases:
        assessment = FastAnswerAssessment(
            minimumInformationPresent=minimum,
            understandable=understandable,
            clearlyIncomplete=incomplete,
            reason="short",
        )
        assert can_proceed(assessment) is expected


@pytest.mark.parametrize(
    ("case", "assessment", "expected"),
    [
        (
            "担当業務への短い回答",
            FastAnswerAssessment(
                minimumInformationPresent=True,
                understandable=True,
                clearlyIncomplete=False,
            ),
            True,
        ),
        (
            "詳細不足だが会話を進められる回答",
            FastAnswerAssessment(
                minimumInformationPresent=True,
                understandable=True,
                clearlyIncomplete=False,
            ),
            True,
        ),
        (
            "はいだけの回答",
            FastAnswerAssessment(
                minimumInformationPresent=False,
                understandable=True,
                clearlyIncomplete=False,
            ),
            False,
        ),
        (
            "無関係な回答",
            FastAnswerAssessment(
                minimumInformationPresent=False,
                understandable=True,
                clearlyIncomplete=False,
            ),
            False,
        ),
        (
            "意味不明な回答",
            FastAnswerAssessment(
                minimumInformationPresent=False,
                understandable=False,
                clearlyIncomplete=False,
            ),
            False,
        ),
        (
            "明らかな発話途中",
            FastAnswerAssessment(
                minimumInformationPresent=True,
                understandable=True,
                clearlyIncomplete=True,
            ),
            False,
        ),
    ],
)
def test_fast_examples_use_only_the_three_minimum_gate_fields(
    case: str,
    assessment: FastAnswerAssessment,
    expected: bool,
) -> None:
    del case
    assert can_proceed(assessment) is expected


def test_fast_provider_retries_only_a_technical_schema_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    from ai_interviewer_api.agents.interview_knowledge.fast_interpreter import (
        provider as fast_provider_module,
    )

    calls: list[dict[str, object]] = []

    class StubProvider:
        def request_structured_output(self, **kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            if len(calls) == 1:
                return {"minimumInformationPresent": True}
            return {
                "minimumInformationPresent": True,
                "understandable": True,
                "clearlyIncomplete": False,
                "reason": "短い回答",
            }

    fast_provider = object.__new__(BedrockFastInterpreterProvider)
    fast_provider._provider = StubProvider()
    monkeypatch.setattr(
        fast_provider_module,
        "settings",
        replace(
            fast_provider_module.settings,
            structured_interview_fast_reasoning_effort="none",
            structured_interview_fast_max_output_tokens=160,
        ),
    )

    result = fast_provider.assess(context={"currentQuestion": {}, "latestUserAnswer": {}})

    assert result.minimumInformationPresent is True
    assert len(calls) == 2
    assert calls[0]["schema_name"] == "fast_answer_assessment"
    assert calls[0]["reasoning_effort"] == "none"
    assert calls[0]["max_output_tokens"] == 160
    assert calls[1]["max_output_tokens"] == 320
    assert set(calls[0]["schema"]["properties"]) == {
        "minimumInformationPresent",
        "understandable",
        "clearlyIncomplete",
        "needsQuestionExplanation",
        "reason",
    }


def test_fast_context_excludes_full_state_and_long_history() -> None:
    context = build_fast_interpreter_context(
        current_question={
            "questionId": "q-001",
            "text": "現在の担当業務は？",
            "targetType": "field",
            "targetId": "field-1",
            "targetLabel": "担当業務",
            "sourceDescription": "最低限、担当している業務を知る",
            "questionPlan": {
                "requiredItems": [
                    {"itemId": "field-1", "label": "担当業務", "description": "業務"}
                ]
            },
        },
        latest_answer={"id": "m-1", "content": "社内システムの開発です。"},
        messages=[
            {"id": f"m-{index}", "role": "user", "content": f"turn {index}"}
            for index in range(10)
        ],
    )

    assert set(context) == {
        "currentQuestion",
        "prior_knowledge",
        "latestUserAnswer",
        "previousTurns",
    }
    assert "interviewState" not in context
    assert "fields" not in context
    assert context["prior_knowledge"] == []
    assert len(context["previousTurns"]) <= 4
    assert context["latestUserAnswer"]["text"] == "社内システムの開発です。"


def _seed_case() -> tuple[object, dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
    user = DEV_TOKENS["dev-manager"]
    record = {
        "id": "record-fast-1",
        "tenantId": user.tenant_id,
        "knowledgeId": "knowledge-fast-1",
        "title": "Fast test",
    }
    knowledge = {
        "id": "knowledge-fast-1",
        "tenantId": user.tenant_id,
        "name": "Fast test",
        "interviewPlan": {"profile": "fixed_form"},
    }
    fields = [
        {
            "id": "field-1",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge["id"],
            "name": "担当業務",
            "description": "担当している業務",
            "required": True,
            "displayOrder": 1,
            "retrievalPolicy": "never",
        },
        {
            "id": "field-2",
            "tenantId": user.tenant_id,
            "knowledgeId": knowledge["id"],
            "name": "所属部署",
            "description": "所属部署",
            "required": True,
            "displayOrder": 2,
            "retrievalPolicy": "never",
        },
    ]
    state = build_initial_structured_state("fixed_form", fields)
    question = {
        "questionId": "q-001",
        "questionType": "structured",
        "fieldId": "field-1",
        "text": "現在の担当業務は？",
        "targetType": "field",
        "targetId": "field-1",
        "targetLabel": "担当業務",
        "retrievalPolicy": "never",
    }
    state.update(
        {
            "id": "interview-state-record-fast-1",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "currentFieldId": "field-1",
            "currentQuestionId": "q-001",
            "nextQuestionTarget": {
                "targetType": "field",
                "targetId": "field-1",
                "label": "担当業務",
            },
            "askedQuestions": [question],
        }
    )
    message = {
        "id": "message-fast-1",
        "tenantId": user.tenant_id,
        "recordId": record["id"],
        "role": "user",
        "content": "社内システムの開発です。",
        "rawTranscript": "社内システムの開発です。",
        "isActualUtterance": True,
        "answerToQuestionId": "q-001",
        "answerToFieldId": "field-1",
        "voiceTurnId": "turn-fast-1",
        "createdAt": "2026-01-01T00:00:00+00:00",
    }
    store.upsert("records", record)
    store.upsert("knowledges", knowledge)
    for field in fields:
        store.upsert("knowledge_fields", field)
    store.upsert("interview_states", state)
    store.upsert("messages", message)
    return user, record, knowledge, state, message


def _structured_output(message_id: str, *, sufficiency: str = "SUFFICIENT") -> StructuredInterviewOutput:
    updates = []
    if sufficiency == "SUFFICIENT":
        updates = [
            FieldUpdate(
                fieldId="field-1",
                value="社内システムの開発",
                evidenceTranscriptIds=[message_id],
                answerResolution="AUTO_CONFIRM",
            )
        ]
    return StructuredInterviewOutput(
        transcriptAssessment=TranscriptAssessment(
            rawTranscript="社内システムの開発です。",
            normalizedTranscript="社内システムの開発です。",
        ),
        answerAssessment=AnswerAssessment(sufficiency=sufficiency),
        fieldUpdates=updates,
    )


def test_fast_pass_runs_background_before_return_and_keeps_formal_update_in_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, record, knowledge, state, message = _seed_case()
    background_started = Event()
    background_finished = Event()
    fast_provider = FakeFastProvider(
        FastAnswerAssessment(
            minimumInformationPresent=True,
            understandable=True,
            clearlyIncomplete=False,
            reason="回答として通る",
        ),
        background_started=background_started,
    )
    structured_provider = FakeStructuredProvider(
        _structured_output(str(message["id"])),
        background_started=background_started,
    )
    monkeypatch.setattr(
        "ai_interviewer_api.agents.interview_knowledge.service.start_speculative_interview_document_retrieval",
        lambda **_: None,
    )

    result = start_fast_interview_turn(
        record,
        knowledge,
        user,
        state=state,
        current_question=state["askedQuestions"][0],
        latest_user_message=message,
        provider=structured_provider,
        fast_provider=fast_provider,
        on_background_validation=lambda _: background_finished.set(),
    )

    assert result.can_proceed is True
    assert result.question is not None
    assert result.question["questionId"] == "q-002"
    assert fast_provider.contexts[0].keys() == {
        "currentQuestion",
        "prior_knowledge",
        "latestUserAnswer",
        "previousTurns",
    }
    assert fast_provider.contexts[0]["prior_knowledge"] == []
    assert background_finished.wait(2)
    stored_state = store.get("interview_states", state["id"])
    assert stored_state is not None
    assert stored_state["fieldStates"]["field-1"]["answerState"] == "CONFIRMED"
    assert stored_state["currentQuestionId"] == "q-002"
    assert structured_provider.interpret_calls
    assert result.latency_metrics["fast_interpreter_latency_ms"] >= 0
    background_metrics = stored_state.get("pendingBackgroundValidations")
    assert background_metrics == []


def test_fast_fail_keeps_current_question_while_background_still_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, record, knowledge, state, message = _seed_case()
    background_finished = Event()
    structured_provider = FakeStructuredProvider(
        _structured_output(str(message["id"])),
    )
    monkeypatch.setattr(
        "ai_interviewer_api.agents.interview_knowledge.service.start_speculative_interview_document_retrieval",
        lambda **_: None,
    )
    result = start_fast_interview_turn(
        record,
        knowledge,
        user,
        state=state,
        current_question=state["askedQuestions"][0],
        latest_user_message=message,
        provider=structured_provider,
        fast_provider=FakeFastProvider(
            FastAnswerAssessment(
                minimumInformationPresent=False,
                understandable=True,
                clearlyIncomplete=False,
                reason="情報不足",
            )
        ),
        on_background_validation=lambda _: background_finished.set(),
    )

    assert result.can_proceed is False
    assert result.question["questionId"] == "q-001"
    assert "具体的" in result.reply
    assert background_finished.wait(2)


def test_fast_legacy_question_explanation_field_does_not_route_the_turn() -> None:
    user, record, knowledge, state, message = _seed_case()
    background_finished = Event()
    result = start_fast_interview_turn(
        record,
        knowledge,
        user,
        state=state,
        current_question=state["askedQuestions"][0],
        latest_user_message=message,
        provider=FakeStructuredProvider(_structured_output(str(message["id"]))),
        fast_provider=FakeFastProvider(
            FastAnswerAssessment(
                minimumInformationPresent=False,
                understandable=True,
                clearlyIncomplete=False,
                needsQuestionExplanation=True,
                reason="質問の意味を確認している",
            )
        ),
        on_background_validation=lambda _: background_finished.set(),
    )

    assert result.can_proceed is False
    assert result.needs_question_explanation is False
    assert "担当業務" in result.reply
    assert "関わった相手" not in result.reply
    assert "行った作業" not in result.reply
    assert background_finished.wait(2)


def test_criteria_missing_is_queued_for_later_clarification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, record, knowledge, state, message = _seed_case()
    background_finished = Event()
    structured_provider = FakeStructuredProvider(
        _structured_output(str(message["id"]), sufficiency="CRITERIA_MISSING"),
    )
    monkeypatch.setattr(
        "ai_interviewer_api.agents.interview_knowledge.service.start_speculative_interview_document_retrieval",
        lambda **_: None,
    )
    result = start_fast_interview_turn(
        record,
        knowledge,
        user,
        state=state,
        current_question=state["askedQuestions"][0],
        latest_user_message=message,
        provider=structured_provider,
        fast_provider=FakeFastProvider(
            FastAnswerAssessment(
                minimumInformationPresent=True,
                understandable=True,
                clearlyIncomplete=False,
            )
        ),
        on_background_validation=lambda _: background_finished.set(),
    )

    assert result.can_proceed is True
    assert background_finished.wait(2)
    stored_state = store.get("interview_states", state["id"])
    assert stored_state is not None
    queue = stored_state["clarificationQueue"]
    assert len(queue) == 1
    assert queue[0]["priority"] == 2
    assert stored_state["currentQuestionId"] == "q-002"

    target = select_next_question_target(
        stored_state,
        "fixed_form",
        [
            store.get("knowledge_fields", "field-1"),
            store.get("knowledge_fields", "field-2"),
        ],
    )
    assert target is not None
    assert target["targetType"] == "field"
    assert target["targetId"] == "field-1"
