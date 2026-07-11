from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from ai_interviewer_api.agents.interview.adapter import AdaptedInterviewTurnResult
from ai_interviewer_api.auth.deps import DEV_TOKENS
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers import records as records_router
from ai_interviewer_api.services import ai_interview


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.tables.clear()


def _seed_stream_context() -> tuple[dict, object]:
    user = DEV_TOKENS["dev-manager"]
    knowledge = {
        "id": "knowledge-1",
        "tenantId": user.tenant_id,
        "name": "保全ノウハウ",
        "description": "圧入工程のインタビュー",
        "targetBusiness": "保全",
        "targetEquipment": "圧入機A",
        "systemPrompt": "停止判断を優先して確認してください。",
    }
    record = {
        "id": "record-1",
        "tenantId": user.tenant_id,
        "knowledgeId": "knowledge-1",
        "knowledgeName": "保全ノウハウ",
        "title": "圧入機A 朝一の荷重ばらつき",
        "targetEquipment": "圧入機A",
    }
    messages = [
        {
            "id": "msg-1",
            "tenantId": user.tenant_id,
            "recordId": "record-1",
            "role": "assistant",
            "content": "どの現象から始まりましたか。",
        },
        {
            "id": "msg-2",
            "tenantId": user.tenant_id,
            "recordId": "record-1",
            "role": "user",
            "content": "朝一だけ圧入荷重が不安定です。",
        },
    ]
    field = {
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

    store.upsert("knowledges", knowledge)
    store.upsert("records", record)
    for message in messages:
        store.upsert("messages", message)
    store.upsert("knowledge_fields", field)
    return record, user


def _seed_greeting_context() -> tuple[dict, object]:
    user = DEV_TOKENS["dev-manager"]
    knowledge = {
        "id": "knowledge-greeting",
        "tenantId": user.tenant_id,
        "name": "汎用ヒアリング",
        "description": "挨拶のみの確認",
        "systemPrompt": "",
    }
    record = {
        "id": "record-greeting",
        "tenantId": user.tenant_id,
        "knowledgeId": "knowledge-greeting",
        "knowledgeName": "汎用ヒアリング",
        "title": "挨拶確認",
    }

    store.upsert("knowledges", knowledge)
    store.upsert("records", record)
    return record, user


async def _no_sleep(_: float) -> None:
    return None


async def _collect_stream_text(response) -> str:
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk.decode("utf-8"))
        else:
            chunks.append(chunk)
    return "".join(chunks)


def test_generate_interview_reply_uses_strands_adapter_and_returns_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    record, user = _seed_stream_context()
    captured: dict[str, Any] = {}

    def fake_run_adapted_interview_turn(record_data, knowledge_data, messages, knowledge_fields, **kwargs):
        captured["record_id"] = record_data["id"]
        captured["knowledge_id"] = knowledge_data["id"]
        captured["messages"] = [message["content"] for message in messages]
        captured["knowledge_fields"] = [field["name"] for field in knowledge_fields]
        return AdaptedInterviewTurnResult(
            reply_text="strands intro\nstrands question",
            reply_chunks=["strands intro", "strands question"],
            next_questions=["朝一のどのタイミングで発生しますか。"],
            draft_updates={"symptom": "朝一の荷重ばらつき"},
            used_tools=["search_existing_fields"],
            answer_status="answered",
            reask_question=None,
        )

    monkeypatch.setattr(ai_interview, "run_adapted_interview_turn", fake_run_adapted_interview_turn)

    result = ai_interview.generate_interview_reply(record, user)

    assert captured == {
        "record_id": "record-1",
        "knowledge_id": "knowledge-1",
        "messages": ["どの現象から始まりましたか。", "朝一だけ圧入荷重が不安定です。"],
        "knowledge_fields": ["現象"],
    }
    assert result.reply_chunks == ["strands intro", "strands question"]
    assert result.metadata == {
        "answer_status": "answered",
        "reask_question": None,
        "next_questions": ["朝一のどのタイミングで発生しますか。"],
        "draft_updates": {"symptom": "朝一の荷重ばらつき"},
        "used_tools": ["search_existing_fields"],
    }


@pytest.mark.anyio
async def test_stream_record_uses_strands_path_and_propagates_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    record, user = _seed_stream_context()

    monkeypatch.setattr(records_router.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(
        ai_interview,
        "settings",
        SimpleNamespace(strands_interview_agent_enabled=False, bedrock_enabled=False),
    )
    monkeypatch.setattr(
        ai_interview,
        "run_adapted_interview_turn",
        lambda *args, **kwargs: AdaptedInterviewTurnResult(
            reply_text="strands intro\nstrands question",
            reply_chunks=["strands intro", "strands question"],
            next_questions=["朝一のどのタイミングで発生しますか。"],
            draft_updates={"symptom": "朝一の荷重ばらつき"},
            used_tools=["search_existing_fields"],
            answer_status="answered",
            reask_question=None,
        ),
    )

    response = await records_router.stream_record(record["id"], user)
    body = await _collect_stream_text(response)

    assert body.count("event: delta") == 2
    assert 'data: {"text": "strands intro"}' in body
    assert 'data: {"text": "strands question"}' in body
    assert (
        'event: stream_end\ndata: {"metadata": {"answer_status": "answered", "reask_question": null, '
        '"next_questions": ["朝一のどのタイミングで発生しますか。"], '
        '"draft_updates": {"symptom": "朝一の荷重ばらつき"}, "used_tools": ["search_existing_fields"]}}'
    ) in body
    assert body.count("event: stream_start") == 1
    assert body.count("event: stream_end") == 1


@pytest.mark.anyio
async def test_stream_record_returns_safe_error_when_strands_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    record, user = _seed_stream_context()

    monkeypatch.setattr(records_router.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(
        ai_interview,
        "run_adapted_interview_turn",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("strands failure")),
    )

    response = await records_router.stream_record(record["id"], user)
    body = await _collect_stream_text(response)

    assert "一時的にAI応答を生成できませんでした。少し時間をおいて再度送信してください。" in body
    assert 'event: stream_end\ndata: {"metadata": {"error": "strands_interview_failed"}}' in body
    assert "legacy" not in body


@pytest.mark.anyio
async def test_stream_record_logs_strands_usage_without_prompt_or_message_body(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    record, _user = _seed_stream_context()

    monkeypatch.setattr(records_router.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(
        ai_interview,
        "run_adapted_interview_turn",
        lambda *args, **kwargs: AdaptedInterviewTurnResult(
            reply_text="strands only",
            reply_chunks=["strands only"],
            next_questions=[],
            draft_updates={},
            used_tools=[],
            answer_status="answered",
            reask_question=None,
        ),
    )

    caplog.set_level(logging.INFO)

    response = await records_router.stream_record(record["id"], DEV_TOKENS["dev-manager"])
    _ = await _collect_stream_text(response)

    log_text = caplog.text
    assert "Using Strands interview agent record_id=record-1 knowledge_id=knowledge-1" in log_text
    assert "朝一だけ圧入荷重が不安定です。" not in log_text
    assert "停止判断を優先して確認してください。" not in log_text
    assert "knowledge_context:" not in log_text


@pytest.mark.anyio
async def test_stream_record_does_not_save_proposals_or_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    record, user = _seed_stream_context()
    before_proposals = dict(store.tables["proposals"])
    before_fields = dict(store.tables["knowledge_fields"])
    before_messages = dict(store.tables["messages"])

    monkeypatch.setattr(records_router.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(
        ai_interview,
        "run_adapted_interview_turn",
        lambda *args, **kwargs: AdaptedInterviewTurnResult(
            reply_text="strands only",
            reply_chunks=["strands only"],
            next_questions=["次の確認ポイントは何ですか。"],
            draft_updates={"action": "接点確認"},
            used_tools=[],
            answer_status="answered",
            reask_question=None,
        ),
    )

    response = await records_router.stream_record(record["id"], user)
    _ = await _collect_stream_text(response)

    assert store.tables["proposals"] == before_proposals
    assert store.tables["knowledge_fields"] == before_fields
    assert store.tables["messages"] == before_messages
    assert store.tables["records"][record["id"]]["title"] == "圧入機A 朝一の荷重ばらつき"
    assert store.tables["records"][record["id"]].get("summary") is None


def test_generate_interview_reply_does_not_reference_legacy_prompt_helpers() -> None:
    from ai_interviewer_api.services.prompts import loader as prompts_loader

    assert not hasattr(ai_interview, "_generate_interview_reply_with_bedrock")
    assert not hasattr(prompts_loader, "get_interview_base_system_prompt")
    assert not hasattr(prompts_loader, "build_interview_system_prompt")


@pytest.mark.anyio
async def test_stream_record_for_greeting_does_not_add_legacy_business_terms(monkeypatch: pytest.MonkeyPatch) -> None:
    record, user = _seed_greeting_context()

    monkeypatch.setattr(records_router.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(
        ai_interview,
        "run_adapted_interview_turn",
        lambda *args, **kwargs: AdaptedInterviewTurnResult(
            reply_text="こんにちは。まず状況を確認します。",
            reply_chunks=["こんにちは。まず状況を確認します。"],
            next_questions=[],
            draft_updates={},
            used_tools=[],
            answer_status="answered",
            reask_question=None,
        ),
    )

    response = await records_router.stream_record(record["id"], user)
    body = await _collect_stream_text(response)

    assert "こんにちは。まず状況を確認します。" in body
    assert "対象業務" not in body
    assert "対象設備" not in body
    assert "設備" not in body
    assert "保全" not in body
    assert "製造" not in body
