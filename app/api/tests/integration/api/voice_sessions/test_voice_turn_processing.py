from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi import HTTPException

from ai_interviewer_api.agents.interview_knowledge.schemas import StructuredInterviewOutput
from ai_interviewer_api.auth.deps import DEV_TOKENS
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.internal_voice import (
    cancel_internal_voice_turn,
    classify_internal_voice_turn_intent,
    create_internal_assistant_event,
    create_internal_voice_turn,
    mark_internal_initial_reply_sent,
    process_internal_voice_turn,
)
from ai_interviewer_api.routers.voice_sessions import (
    create_record_voice_session,
    get_record_voice_session,
    stop_record_voice_session,
)
from ai_interviewer_api.schemas.voice import (
    AssistantEventCreate,
    SpeculativeTurnCreate,
    VoiceSessionCreate,
    VoiceTurnCancel,
    VoiceTurnCreate,
    VoiceTurnIntentCreate,
)
from ai_interviewer_api.services import voice_interview as voice_interview_service
from ai_interviewer_api.services.conversation_policy import CanonicalIntentDecision
from ai_interviewer_api.services.interview_state_transition import commit_interview_state

from .support import _create_record_with_field


def test_process_voice_turn_is_idempotent() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="荷重が朝一に不安定です", clientTurnId="client-1"),
    )

    first = process_internal_voice_turn(session["id"], turn["id"])
    second = process_internal_voice_turn(session["id"], turn["id"])
    assistant_messages = [
        message
        for message in store.list("messages", user.tenant_id)
        if message.get("recordId") == record["id"] and message.get("role") == "assistant"
    ]
    user_messages = [
        message
        for message in store.list("messages", user.tenant_id)
        if message.get("recordId") == record["id"] and message.get("role") == "user"
    ]
    voice_user_messages = [
        message
        for message in user_messages
        if message.get("voiceClientTurnId") == "client-1"
    ]

    assert second["responseId"] == first["responseId"]
    assert second["text"] == first["text"]
    assert len(assistant_messages) == 1
    assert len(voice_user_messages) == 1


def test_speculative_turn_does_not_create_voice_turn_message_or_state_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    state_before = store.get("interview_states", f"interview-state-{record['id']}")
    messages_before = [
        message
        for message in store.list("messages", user.tenant_id)
        if message.get("recordId") == record["id"]
    ]
    completed = Event()

    def preview(**_: object) -> StructuredInterviewOutput:
        completed.set()
        return StructuredInterviewOutput()

    monkeypatch.setattr(
        voice_interview_service,
        "preview_structured_interview_output",
        preview,
    )
    try:
        accepted = voice_interview_service.start_speculative_voice_turn(
            session["id"],
            SpeculativeTurnCreate(
                speculativeId="spec-no-commit",
                transcript="山田です",
                answerToQuestionId=session["currentQuestionId"],
                expectedStateVersion=session["stateVersion"],
            ),
        )
        assert accepted["accepted"] is True
        assert completed.wait(1)
        assert not [
            turn
            for turn in store.list("voice_turns", user.tenant_id)
            if turn.get("voiceSessionId") == session["id"]
        ]
        assert [
            message
            for message in store.list("messages", user.tenant_id)
            if message.get("recordId") == record["id"]
        ] == messages_before
        assert store.get("interview_states", f"interview-state-{record['id']}") == state_before
    finally:
        with voice_interview_service._SPECULATIVE_PREVIEWS_GUARD:
            voice_interview_service._SPECULATIVE_PREVIEWS.clear()


def test_concurrent_process_voice_turn_is_idempotent() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="同じ処理を同時に受けた回答", clientTurnId="process-race-1"),
    )

    def process_duplicate(_: int) -> dict:
        return process_internal_voice_turn(session["id"], turn["id"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(process_duplicate, range(2)))

    assistant_messages = [
        message
        for message in store.list("messages", user.tenant_id)
        if message.get("recordId") == record["id"] and message.get("role") == "assistant"
    ]
    voice_user_messages = [
        message
        for message in store.list("messages", user.tenant_id)
        if message.get("voiceTurnId") == turn["id"] and message.get("role") == "user"
    ]

    assert results[0]["responseId"] == results[1]["responseId"]
    assert len(assistant_messages) == 1
    assert len(voice_user_messages) == 1


def test_voice_session_requires_owner_match() -> None:
    owner = DEV_TOKENS["dev-manager"]
    other = DEV_TOKENS["dev-interviewer"]
    record = _create_record_with_field(owner)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), owner)

    with pytest.raises(HTTPException) as exc_info:
        get_record_voice_session(session["id"], other)

    assert exc_info.value.status_code == 403


def test_control_turn_keeps_current_question_without_answer_scope() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="一時停止してください", turnType="CONTROL"),
    )

    result = process_internal_voice_turn(session["id"], turn["id"])
    stored_turn = store.get("voice_turns", turn["id"])

    assert result["action"] == "ask_structured"
    assert stored_turn["answerToQuestionId"] is None
    assert stored_turn["answerToFieldId"] is None


def test_canonical_control_intent_is_applied_before_message_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    monkeypatch.setattr(
        voice_interview_service,
        "resolve_canonical_intent",
        lambda **_: CanonicalIntentDecision(
            dialogueAct="CONVERSATION_REQUEST",
            latency_ms=0.1,
            provider="test",
        ),
    )
    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="インタビューを一時停止してください"),
    )

    result = process_internal_voice_turn(session["id"], turn["id"])
    stored_turn = store.get("voice_turns", turn["id"])
    user_messages = [
        row
        for row in store.list("messages", user.tenant_id)
        if row.get("voiceTurnId") == turn["id"] and row.get("role") == "user"
    ]

    assert result["voiceTurn"]["canonicalIntent"] == "CONVERSATION_REQUEST"
    assert result["voiceTurn"]["canonicalAction"] == "HANDLE_CONTROL"
    assert stored_turn["turnType"] == "CONTROL"
    assert stored_turn["answerToQuestionId"] is None
    assert len(user_messages) == 1
    assert user_messages[0]["turnType"] == "CONTROL"


def test_legacy_voice_turn_intent_response_uses_canonical_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)

    monkeypatch.setattr(
        voice_interview_service,
        "resolve_canonical_intent",
        lambda **_: CanonicalIntentDecision(
            dialogueAct="CONVERSATION_REQUEST",
            latency_ms=0.1,
            provider="test",
        ),
    )

    result = classify_internal_voice_turn_intent(
        session["id"],
        VoiceTurnIntentCreate(transcript="インタビューを終了してください"),
    )

    assert result == {"turnType": "CONTROL"}


def test_finish_and_duplicate_assistant_events_do_not_create_duplicate_message() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    payload = AssistantEventCreate(
        eventType="assistant_transcript_final",
        responseId="response-same",
        transcript="次の質問です。",
        detail={"action": "ask_structured", "questionId": "q-001"},
    )

    first_event = create_internal_assistant_event(session["id"], payload)
    second_event = create_internal_assistant_event(session["id"], payload)
    messages = [
        row
        for row in store.list("messages", user.tenant_id)
        if row.get("recordId") == record["id"] and row.get("voiceResponseId") == "response-same"
    ]

    events = [
        row
        for row in store.list("voice_assistant_events", user.tenant_id)
        if row.get("voiceSessionId") == session["id"]
    ]

    assert len(messages) == 1
    assert len(events) == 1
    assert second_event["id"] == first_event["id"]


def test_assistant_events_with_same_source_turn_are_idempotent_across_response_ids() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    first = create_internal_assistant_event(
        session["id"],
        AssistantEventCreate(
            eventType="assistant_transcript_final",
            responseId="response-source-1",
            transcript="次の質問です。",
            detail={"action": "ask_structured", "turnId": "turn-source-1", "questionId": "q-002"},
        ),
    )
    second = create_internal_assistant_event(
        session["id"],
        AssistantEventCreate(
            eventType="assistant_transcript_final",
            responseId="response-source-2",
            transcript="次の質問です。",
            detail={"action": "ask_structured", "turnId": "turn-source-1", "questionId": "q-002"},
        ),
    )

    messages = [
        row
        for row in store.list("messages", user.tenant_id)
        if row.get("recordId") == record["id"] and row.get("voiceTurnId") == "turn-source-1"
    ]
    events = [
        row
        for row in store.list("voice_assistant_events", user.tenant_id)
        if row.get("voiceSessionId") == session["id"]
    ]

    assert first["id"] == second["id"]
    assert len(messages) == 1
    assert len(events) == 1


def test_stopped_voice_session_rejects_new_turn() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    stop_record_voice_session(session["id"], user)

    with pytest.raises(HTTPException) as exc_info:
        create_internal_voice_turn(session["id"], VoiceTurnCreate(transcript="回答"))

    assert exc_info.value.status_code == 409


def test_client_turn_id_is_idempotent_and_rejects_different_payload() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    payload = VoiceTurnCreate(transcript="回答", clientTurnId="client-1")
    first = create_internal_voice_turn(session["id"], payload)
    second = create_internal_voice_turn(session["id"], payload)
    assert second["id"] == first["id"]

    with pytest.raises(HTTPException) as exc_info:
        create_internal_voice_turn(
            session["id"],
            VoiceTurnCreate(transcript="別の回答", clientTurnId="client-1"),
        )
    assert exc_info.value.status_code == 409


def test_replayed_client_turn_reuses_identity_after_state_snapshot_changes() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    initial_state_version = session["stateVersion"]
    first = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(
            transcript="同じ発話",
            clientTurnId="reconnect-item-1",
            expectedStateVersion=initial_state_version,
        ),
    )
    state_id = f"interview-state-{record['id']}"
    state = store.get("interview_states", state_id)
    assert state is not None
    original_question_id = state["currentQuestionId"]
    state["lastStructuredDialogueAct"] = "ANSWER"
    commit_interview_state(state, user, source="test_background_snapshot_update")
    latest_state = store.get("interview_states", state_id)
    assert latest_state is not None

    replay = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(
            transcript="同じ発話",
            clientTurnId="reconnect-item-1",
            answerToQuestionId=original_question_id,
            expectedStateVersion=latest_state["stateVersion"],
        ),
    )

    assert replay["id"] == first["id"]
    assert replay["answerToQuestionId"] == first["answerToQuestionId"]
    assert replay["expectedStateVersion"] == latest_state["stateVersion"]


def test_concurrent_client_turn_duplicate_is_durable_idempotent() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    payload = VoiceTurnCreate(transcript="同じ回答", clientTurnId="concurrent-client-1")

    def create_duplicate(_: int) -> dict:
        return create_internal_voice_turn(session["id"], payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create_duplicate, range(2)))

    assert results[0]["id"] == results[1]["id"]
    turns = [
        row
        for row in store.list("voice_turns", user.tenant_id)
        if row.get("voiceSessionId") == session["id"]
    ]
    assert len(turns) == 1


def test_cancel_before_processing_prevents_late_commit() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    initial_state_version = session["stateVersion"]
    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(
            transcript="回答",
            clientTurnId="client-1",
            expectedStateVersion=initial_state_version,
        ),
    )

    cancelled = cancel_internal_voice_turn(
        session["id"],
        VoiceTurnCancel(
            clientTurnId="client-1",
            expectedStateVersion=initial_state_version,
        ),
    )

    assert cancelled["cancelled"] is True
    with pytest.raises(HTTPException) as exc_info:
        process_internal_voice_turn(session["id"], turn["id"])
    assert exc_info.value.status_code == 409
