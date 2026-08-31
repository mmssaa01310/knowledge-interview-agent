from __future__ import annotations

import logging
from typing import Any

import pytest

from ai_interviewer_api.agents.interview.adapter import AdaptedInterviewTurnResult
from ai_interviewer_api.auth.deps import DEV_TOKENS
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers import records as records_router
from ai_interviewer_api.schemas.requests import ChatMessageCreate
from ai_interviewer_api.services import ai_interview


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.tables.clear()


@pytest.fixture(autouse=True)
def stub_dialogue_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ai_interview,
        "interpret_dialogue_act",
        lambda **_: ai_interview.DialogueInterpretation(act="ANSWER"),
    )


def _seed_stream_context() -> tuple[dict, object]:
    user = DEV_TOKENS["dev-manager"]
    knowledge = {
        "id": "knowledge-1",
        "tenantId": user.tenant_id,
        "name": "保全ノウハウ",
        "description": "圧入工程のインタビュー",
        "systemPrompt": "停止判断を優先して確認してください。",
        "interviewPlan": {
            "version": 1,
            "profile": "fixed_form",
            "modelId": "global.openai.gpt-5.6-terra",
        },
    }
    record = {
        "id": "record-1",
        "tenantId": user.tenant_id,
        "knowledgeId": "knowledge-1",
        "knowledgeName": "保全ノウハウ",
        "title": "圧入機A 朝一の荷重ばらつき",
    }
    field_1 = {
        "id": "field-1",
        "tenantId": user.tenant_id,
        "knowledgeId": "knowledge-1",
        "name": "現象",
        "description": "発生している症状",
        "inputType": "long_text",
        "required": True,
        "askByAi": True,
        "displayOrder": 1,
        "aiQuestionExamples": ["どのような現象が起きていますか。"],
    }
    field_2 = {
        "id": "field-2",
        "tenantId": user.tenant_id,
        "knowledgeId": "knowledge-1",
        "name": "原因",
        "description": "疑った原因",
        "inputType": "long_text",
        "required": True,
        "askByAi": True,
        "displayOrder": 2,
        "aiQuestionExamples": ["最初に何を原因候補として疑いましたか。"],
    }

    store.upsert("knowledges", knowledge)
    store.upsert("records", record)
    store.upsert("knowledge_fields", field_1)
    store.upsert("knowledge_fields", field_2)
    return record, user


def test_control_text_turn_is_not_scoped_to_the_current_answer() -> None:
    record, user = _seed_stream_context()
    first = ai_interview.generate_interview_reply(record, user)
    current_question_id = first.metadata["question"]["questionId"]

    control = records_router.create_record_message(
        record["id"],
        ChatMessageCreate(content="インタビュー開始して"),
        user,
    )

    assert control["proposalId"] is None
    assert control["recordMessage"]["turnType"] == "CONTROL"
    assert control["recordMessage"]["answerToQuestionId"] is None

    ai_interview.generate_interview_reply(record, user)
    state = store.get("interview_states", f"interview-state-{record['id']}")
    field_state = state["fieldStates"]["field-1"]
    assert state["currentQuestionId"] == current_question_id
    assert field_state["rawAnswerHistory"] == []
    assert field_state["capturedItems"] == []


def test_dialogue_question_to_assistant_does_not_enter_text_answer_processor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record, user = _seed_stream_context()
    first = ai_interview.generate_interview_reply(record, user)
    question = first.metadata["question"]
    state = store.get("interview_states", f"interview-state-{record['id']}")
    field_id = question["fieldId"]
    field_state = state["fieldStates"][field_id]
    field_state["answerState"] = "AWAITING_CONFIRMATION"
    field_state["candidateAnswer"] = "清掃員"
    field_state["pendingQuestionId"] = question["questionId"]
    field_state["pendingFieldId"] = field_id
    store.upsert("interview_states", state)
    store.upsert(
        "messages",
        {
            "id": "msg-user-question",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "content": "この内容とは？",
            "isActualUtterance": True,
            "turnType": "ANSWER",
            "answerToQuestionId": question["questionId"],
            "answerToFieldId": field_id,
            "createdAt": "2026-01-01T00:00:00+00:00",
        },
    )

    def fail_evaluation(*args: Any, **kwargs: Any):
        raise AssertionError("dialogue questions must not enter answer evaluation")

    monkeypatch.setattr(ai_interview, "run_adapted_interview_turn", fail_evaluation)
    monkeypatch.setattr(
        ai_interview,
        "interpret_dialogue_act",
        lambda **_: ai_interview.DialogueInterpretation(
            act="QUESTION_TO_ASSISTANT",
            response_text="先ほどの「清掃員」という回答のことです。",
        ),
    )

    result = ai_interview.generate_interview_reply(record, user)
    updated_state = result.metadata["interviewState"]
    updated_field_state = updated_state["fieldStates"][field_id]
    saved_message = store.get("messages", "msg-user-question")

    assert result.metadata["reply"] == "先ほどの「清掃員」という回答のことです。"
    assert result.metadata["action"] == "ask_follow_up"
    assert result.metadata["question"]["questionId"] == question["questionId"]
    assert updated_field_state["candidateAnswer"] == "清掃員"
    assert updated_field_state["rawAnswerHistory"] == []
    assert updated_field_state["capturedItems"] == []
    assert updated_state["lastProcessedUserMessageId"] == "msg-user-question"
    assert saved_message["dialogueAct"] == "QUESTION_TO_ASSISTANT"


async def _no_sleep(_: float) -> None:
    return None


async def _collect_stream_text(response) -> str:
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
    return "".join(chunks)


def test_generate_interview_reply_first_turn_adds_configured_question_metadata() -> None:
    record, user = _seed_stream_context()

    result = ai_interview.generate_interview_reply(record, user)

    assert result.metadata is not None
    assert result.metadata["action"] == "ask_configured_field"
    assert result.metadata["question"]["questionType"] == "configured_field"
    assert result.metadata["question"]["fieldId"] == "field-1"
    assert result.metadata["interviewState"]["currentFieldId"] == "field-1"
    assert result.metadata["assistantMessage"]["questionId"] == result.metadata["question"]["questionId"]
    saved_messages = list(store.tables["messages"].values())
    assert any(message.get("questionType") == "configured_field" for message in saved_messages)


def test_legacy_initial_question_uses_record_interview_locale() -> None:
    record, user = _seed_stream_context()
    record["interviewLocale"] = "en-US"

    result = ai_interview.generate_interview_reply(record, user)

    assert result.metadata is not None
    assert result.metadata["reply"] == "Please tell me about 現象."
    assert "どのような現象" not in result.metadata["reply"]


def test_legacy_summary_is_migrated_from_actual_user_utterance() -> None:
    record, user = _seed_stream_context()
    store.upsert(
        "interview_states",
        {
            "id": f"interview-state-{record['id']}",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "status": "completed",
            "currentFieldId": None,
            "currentQuestionId": None,
            "completedFieldIds": ["field-1"],
            "pendingFieldIds": ["field-2"],
            "fieldStates": {
                "field-1": {
                    "fieldId": "field-1",
                    "status": "completed",
                    "answerState": "CONFIRMED",
                    "answerSummary": "自己紹介として、宮崎という名前が回答されました。",
                },
            },
        },
    )
    store.upsert(
        "messages",
        {
            "id": "actual-answer-1",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "isActualUtterance": True,
            "answerToFieldId": "field-1",
            "content": "宮崎です",
        },
    )
    store.upsert(
        "messages",
        {
            "id": "confirmed-answer-1",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "isActualUtterance": False,
            "messageType": "confirmed_answer",
            "answerToFieldId": "field-1",
            "content": "自己紹介として、宮崎という名前が回答されました。",
        },
    )

    snapshot = ai_interview.get_interview_state_snapshot(record, user)
    field_state = snapshot["interviewState"]["fieldStates"]["field-1"]

    assert field_state["answerSummary"] is None
    assert field_state["recordAnswer"] == "宮崎です"
    assert snapshot["structuredDraft"]["現象"] == "宮崎です"
    assert store.get("messages", "confirmed-answer-1")["content"] == "宮崎です"


def test_retrieval_never_still_evaluates_and_requires_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    record, user = _seed_stream_context()
    field = store.get("knowledge_fields", "field-1")
    field["retrievalPolicy"] = "never"
    store.upsert("knowledge_fields", field)
    first = ai_interview.generate_interview_reply(record, user)
    question_id = first.metadata["question"]["questionId"]
    store.upsert(
        "messages",
        {
            "id": "msg-user-1",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "content": "圧入機Bです。違う、圧入機Aです。",
            "answerToQuestionId": question_id,
            "answerToFieldId": "field-1",
        },
    )
    calls: list[str] = []

    def fake_run_adapted_interview_turn(*args: Any, **kwargs: Any):
        calls.append("evaluated")
        return AdaptedInterviewTurnResult(
            reply_text="",
            field_evaluation={
                "fieldId": "field-1",
                "decision": "CONFIRMABLE",
                "isComplete": True,
                "isRelevant": True,
                "isSufficient": True,
                "answerSummary": "圧入機A",
                "recordAnswer": "圧入機Aです。",
                "confirmationOutcome": (
                    "CONFIRM"
                    if kwargs.get("interview_state", {}).get("fieldStates", {})
                    .get("field-1", {})
                    .get("answerState")
                    == "AWAITING_CONFIRMATION"
                    else None
                ),
                "missingInformation": [],
                "nextAction": "next_field",
            },
            follow_up_question=None,
            used_tools=[],
        )

    monkeypatch.setattr(ai_interview, "run_adapted_interview_turn", fake_run_adapted_interview_turn)
    evaluated = ai_interview.generate_interview_reply(record, user)
    state = evaluated.metadata["interviewState"]
    field_state = state["fieldStates"]["field-1"]

    assert calls == ["evaluated"]
    assert evaluated.metadata["retrievalPolicy"] == "never"
    assert field_state["answerState"] == "AWAITING_CONFIRMATION"
    assert field_state["candidateAnswer"] == "圧入機Aです。"
    assert field_state["answerSummary"] is None
    assert state["completedFieldIds"] == []

    confirmation_question_id = evaluated.metadata["question"]["questionId"]
    store.upsert(
        "messages",
        {
            "id": "msg-user-2",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "content": "はい、そうです",
            "answerToQuestionId": confirmation_question_id,
            "answerToFieldId": "field-1",
        },
    )
    confirmed = ai_interview.generate_interview_reply(record, user)
    confirmed_state = confirmed.metadata["interviewState"]

    assert confirmed_state["fieldStates"]["field-1"]["answerState"] == "CONFIRMED"
    assert confirmed_state["fieldStates"]["field-1"]["answerSummary"] is None
    assert confirmed_state["fieldStates"]["field-1"]["recordAnswer"] == "圧入機Aです。"
    assert [
        message["content"]
        for message in store.tables["messages"].values()
        if message.get("messageType") == "confirmed_answer"
    ] == ["圧入機Aです。"]
    assert confirmed_state["completedFieldIds"] == ["field-1"]
    assert confirmed.metadata["question"]["fieldId"] == "field-2"


@pytest.mark.anyio
async def test_stream_record_uses_metadata_from_saved_question(monkeypatch: pytest.MonkeyPatch) -> None:
    record, user = _seed_stream_context()
    monkeypatch.setattr(records_router.asyncio, "sleep", _no_sleep)

    response = await records_router.stream_record(record["id"], user)
    body = await _collect_stream_text(response)

    assert "event: delta" in body
    assert '"action": "ask_configured_field"' in body
    assert '"questionType": "configured_field"' in body
    assert '"fieldId": "field-1"' in body


@pytest.mark.anyio
async def test_stream_record_follow_up_is_saved_as_follow_up(monkeypatch: pytest.MonkeyPatch) -> None:
    record, user = _seed_stream_context()
    first = ai_interview.generate_interview_reply(record, user)
    question_id = first.metadata["question"]["questionId"]
    store.upsert(
        "messages",
        {
            "id": "msg-user-1",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "role": "user",
            "content": "朝一だけ荷重が不安定です。",
            "answerToQuestionId": question_id,
            "answerToFieldId": "field-1",
        },
    )

    monkeypatch.setattr(records_router.asyncio, "sleep", _no_sleep)

    def fake_run_adapted_interview_turn(*args: Any, **kwargs: Any):
        return AdaptedInterviewTurnResult(
            reply_text="確認します。",
            field_evaluation={
                "fieldId": "field-1",
                "isComplete": False,
                "answerSummary": "朝一だけ荷重が不安定。",
                "missingInformation": ["発生タイミング"],
                "nextAction": "follow_up",
            },
            follow_up_question="朝一のどのタイミングで発生しますか。",
            used_tools=["search_existing_fields"],
        )

    monkeypatch.setattr(ai_interview, "run_adapted_interview_turn", fake_run_adapted_interview_turn)
    response = await records_router.stream_record(record["id"], user)
    body = await _collect_stream_text(response)

    assert '"action": "ask_follow_up"' in body
    assert '"questionType": "follow_up"' in body
    assert '"fieldId": "field-1"' in body
    saved_messages = list(store.tables["messages"].values())
    assert any(message.get("questionType") == "follow_up" for message in saved_messages)


@pytest.mark.anyio
async def test_stream_record_completed_state_returns_finish_without_question(monkeypatch: pytest.MonkeyPatch) -> None:
    record, user = _seed_stream_context()
    state = {
        "id": f"interview-state-{record['id']}",
        "tenantId": user.tenant_id,
        "recordId": record["id"],
        "status": "completed",
        "currentFieldId": None,
        "currentQuestionId": None,
        "completedFieldIds": ["field-1", "field-2"],
        "pendingFieldIds": [],
        "askedQuestions": [],
        "followUpCounts": {"field-1": 1, "field-2": 0},
        "fieldStates": {
            "field-1": {
                "fieldId": "field-1",
                "status": "completed",
                "answerState": "CONFIRMED",
                "answerSummary": "荷重が不安定。",
                "missingInformation": [],
            },
            "field-2": {
                "fieldId": "field-2",
                "status": "completed",
                "answerState": "CONFIRMED",
                "answerSummary": "接点不良を疑った。",
                "missingInformation": [],
            },
        },
        "lastProcessedUserMessageId": "msg-user-1",
        "createdByUserId": user.user_id,
        "updatedByUserId": user.user_id,
        "createdAt": "2026-01-01T00:00:00+00:00",
        "updatedAt": "2026-01-01T00:00:00+00:00",
    }
    store.upsert("interview_states", state)
    monkeypatch.setattr(records_router.asyncio, "sleep", _no_sleep)

    response = await records_router.stream_record(record["id"], user)
    body = await _collect_stream_text(response)

    assert '"status": "completed"' in body
    assert '"action": "finish"' in body
    assert '"question": null' in body
    assert "ご協力ありがとうございました" in body


@pytest.mark.anyio
async def test_stream_record_returns_safe_error_when_strands_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    record, user = _seed_stream_context()
    monkeypatch.setattr(records_router.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(
        ai_interview,
        "_generate_interview_stream_result_with_strands",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("strands failure")),
    )

    response = await records_router.stream_record(record["id"], user)
    body = await _collect_stream_text(response)

    assert "一時的にAI応答を生成できませんでした。少し時間をおいて再度送信してください。" in body
    assert '"error": "strands_interview_failed"' in body


@pytest.mark.anyio
async def test_stream_record_logs_without_prompt_or_message_body(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    record, user = _seed_stream_context()
    monkeypatch.setattr(records_router.asyncio, "sleep", _no_sleep)
    caplog.set_level(logging.INFO)

    response = await records_router.stream_record(record["id"], user)
    _ = await _collect_stream_text(response)

    log_text = caplog.text
    assert "Using Strands interview agent record_id=record-1 knowledge_id=knowledge-1" in log_text
    assert "どのような現象が起きていますか。" not in log_text
    assert "停止判断を優先して確認してください。" not in log_text
