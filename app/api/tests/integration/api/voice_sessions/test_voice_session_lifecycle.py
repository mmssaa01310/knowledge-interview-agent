from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from ai_interviewer_api.auth.deps import DEV_TOKENS
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.internal_voice import (
    claim_internal_initial_reply,
    mark_internal_initial_reply_sent,
)
from ai_interviewer_api.routers.voice_sessions import (
    create_record_voice_session,
    get_record_voice_session,
    stop_record_voice_session,
)
from ai_interviewer_api.schemas.voice import VoiceSessionCreate
from ai_interviewer_api.services import voice_interview as voice_interview_service
from ai_interviewer_api.services.interview_state_transition import commit_interview_state

from .support import _create_record_with_field, _create_record_with_fields


def test_create_get_stop_and_atomically_claim_initial_reply() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)

    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    assert session["provider"] == "transcribe_polly"
    assert session["currentQuestionId"] == "q-001"
    state = store.get("interview_states", f"interview-state-{record['id']}")
    assert state is not None
    assert session["stateVersion"] == state["stateVersion"]
    assert session["initialReplyStatus"] == "pending"
    assert "現象について教えてください。" in session["initialReplyText"]

    fetched = get_record_voice_session(session["id"], user)
    assert fetched["initialReplyText"] == session["initialReplyText"]

    claimed = claim_internal_initial_reply(session["id"])
    assert claimed["claimed"] is True
    assert claim_internal_initial_reply(session["id"])["reason"] == "already_sending"
    marked = mark_internal_initial_reply_sent(session["id"])
    assert marked["initialReplyStatus"] == "sent"
    assert claim_internal_initial_reply(session["id"])["reason"] == "already_sent"

    stopped = stop_record_voice_session(session["id"], user)
    assert stopped["status"] == "stopped"
    assert stopped["connectionStatus"] == "closed"


def test_voice_session_reads_current_question_and_version_from_canonical_state() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_fields(user, [("氏名", "short_text"), ("担当", "short_text")])
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    state_id = f"interview-state-{record['id']}"
    state = store.get("interview_states", state_id)
    assert state is not None
    mirror_question_id = session["currentQuestionId"]
    state["currentQuestionId"] = "q-canonical-after-reconnect"
    state["stateVersion"] = int(state["stateVersion"]) + 1
    commit_interview_state(state, user, source="test_canonical_state_read")

    fetched = get_record_voice_session(session["id"], user)

    assert fetched["currentQuestionId"] == "q-canonical-after-reconnect"
    assert fetched["stateVersion"] == state["stateVersion"]
    persisted_session = store.get("voice_sessions", session["id"])
    assert persisted_session is not None
    assert persisted_session["currentQuestionId"] == mirror_question_id


def test_reconnect_race_has_only_one_initial_reply_claim_winner() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)

    def claim(_: int) -> dict:
        return claim_internal_initial_reply(session["id"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, (1, 2)))

    assert sum(bool(item["claimed"]) for item in claims) == 1
    assert sorted(item.get("reason") for item in claims if not item["claimed"]) == [
        "already_sending"
    ]


def test_voice_session_uses_record_interview_locale() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user, interview_locale="en-US")

    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)

    assert session["interviewLocale"] == "en-US"
    assert (session["initialReplyText"] or "").startswith("We are about to start the interview.")
    assert "Please tell me about 現象." in session["initialReplyText"]


def test_initial_question_is_not_saved_before_it_is_spoken() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_fields(user, [("氏名", "short_text"), ("担当", "short_text")])

    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)

    assert session["initialReplyText"] == "これからインタビューを開始します。氏名について教えてください。"
    assert [row for row in store.list("messages", user.tenant_id) if row.get("recordId") == record["id"]] == []


def test_initial_question_reuses_canonical_question_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_fields(user, [("氏名", "short_text"), ("担当", "short_text")])
    create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    state = store.get("interview_states", f"interview-state-{record['id']}")
    assert state is not None
    current_question = next(
        item for item in state["askedQuestions"] if item["questionId"] == state["currentQuestionId"]
    )
    current_question["text"] = "お名前、所属部署、現在の役職または担当領域を教えてください。"
    store.upsert("interview_states", state)

    def fail_if_called(*_: object, **__: object) -> None:
        raise AssertionError("canonical initial question must not call the LLM")

    monkeypatch.setattr(voice_interview_service, "generate_interview_reply", fail_if_called)

    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)

    assert session["initialReplyText"] == "これからインタビューを開始します。お名前、所属部署、現在の役職または担当領域を教えてください。"
    assert session["initialQuestionId"] == session["currentQuestionId"]
