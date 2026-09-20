from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import pytest

from ai_interviewer_api.agents.interview_knowledge import service as structured_service
from ai_interviewer_api.agents.interview_knowledge.fast_interpreter.schemas import (
    FastAnswerAssessment,
)
from ai_interviewer_api.agents.interview_knowledge.schemas import (
    AnswerAssessment,
    FieldUpdate,
    QuestionGenerationOutput,
    StructuredInterviewOutput,
    TranscriptAssessment,
)
from ai_interviewer_api.auth.deps import DEV_TOKENS
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.internal_voice import (
    create_internal_voice_turn,
    mark_internal_initial_reply_sent,
    process_internal_voice_turn,
)
from ai_interviewer_api.routers.voice_sessions import create_record_voice_session
from ai_interviewer_api.schemas.voice import VoiceSessionCreate, VoiceTurnCreate
from ai_interviewer_api.services import voice_interview as voice_interview_service

from .support import FakeStructuredProvider, _create_record_with_field, _create_record_with_fields


def test_voice_turn_uses_structured_interpreter_and_advances_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_fields(user, [("氏名", "short_text"), ("担当", "short_text")])
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    monkeypatch.setattr(
        voice_interview_service,
        "settings",
        replace(voice_interview_service.settings, structured_interview_fast_path_enabled=False),
    )
    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="山田です。", sttConfidence=0.96),
    )

    result = process_internal_voice_turn(session["id"], turn["id"])
    state = store.get("interview_states", f"interview-state-{record['id']}")
    first_field_id = state["askedQuestions"][0]["fieldId"]

    assert result["action"] == "ask_structured"
    assert result["questionId"] == "q-002"
    assert state["fieldStates"][first_field_id]["answerState"] == "CONFIRMED"
    assert state["fieldStates"][first_field_id]["recordAnswer"] == "山田"
    assert store.get("voice_turns", turn["id"])["processingMode"] == "structured_interpretation"
    assert result["voiceTurn"]["canonicalIntent"] == "ANSWER"
    assert result["voiceTurn"]["canonicalAction"] == "PROCESS_ANSWER"
    assert result["voiceTurn"]["fastCheckExecuted"] is False


@pytest.mark.parametrize("fast_path_requested", [False, True], ids=["off", "requested-fail-closed"])
def test_answer_is_saved_before_question_progression_even_if_fast_is_requested(
    monkeypatch: pytest.MonkeyPatch,
    stub_structured_provider: FakeStructuredProvider,
    fast_path_requested: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING")
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_fields(user, [("氏名", "short_text"), ("担当", "short_text")])
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    monkeypatch.setattr(
        voice_interview_service,
        "settings",
        replace(
            voice_interview_service.settings,
            structured_interview_fast_path_enabled=fast_path_requested,
        ),
    )
    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="山田です。", sttConfidence=0.96),
    )
    field_id = turn["answerToFieldId"]
    generate_question = stub_structured_provider.generate_question

    def assert_answer_is_durable_before_render(
        *, target: Mapping[str, object], context: Mapping[str, object], **kwargs: object
    ) -> QuestionGenerationOutput:
        state = store.get("interview_states", f"interview-state-{record['id']}")
        assert state is not None
        assert state["fieldStates"][field_id]["recordAnswer"] == "山田"
        assert state["fieldStates"][field_id]["answerState"] == "CONFIRMED"
        return generate_question(target=target, context=context, **kwargs)

    monkeypatch.setattr(
        stub_structured_provider,
        "generate_question",
        assert_answer_is_durable_before_render,
    )

    result = process_internal_voice_turn(session["id"], turn["id"])
    persisted_state = store.get("interview_states", f"interview-state-{record['id']}")
    assert persisted_state is not None
    assert result["questionId"] == "q-002"
    assert persisted_state["fieldStates"][field_id]["recordAnswer"] == "山田"
    assert result["voiceTurn"]["canonicalIntent"] == "ANSWER"
    assert result["voiceTurn"]["canonicalAction"] == "PROCESS_ANSWER"
    assert result["voiceTurn"]["fastCheckExecuted"] is False
    if fast_path_requested:
        assert "structured_fast_path_deferred" in caplog.text


def test_sufficient_interpretation_without_applied_update_keeps_current_question(
    monkeypatch: pytest.MonkeyPatch,
    stub_structured_provider: FakeStructuredProvider,
) -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    before = store.get("interview_states", f"interview-state-{record['id']}")
    assert before is not None

    def no_extraction(*, context: Mapping[str, object], **_: object) -> StructuredInterviewOutput:
        latest = context["latestUtterance"]
        assert isinstance(latest, Mapping)
        transcript = str(latest.get("rawTranscript") or "")
        return StructuredInterviewOutput(
            transcriptAssessment=TranscriptAssessment(
                rawTranscript=transcript,
                normalizedTranscript=transcript,
            ),
            answerAssessment=AnswerAssessment(sufficiency="SUFFICIENT"),
        )

    monkeypatch.setattr(stub_structured_provider, "interpret", no_extraction)
    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="具体的な回答ですが抽出結果は空です。"),
    )

    result = process_internal_voice_turn(session["id"], turn["id"])
    after = store.get("interview_states", f"interview-state-{record['id']}")

    assert after is not None
    assert result["questionId"] == before["currentQuestionId"]
    assert after["currentQuestionId"] == before["currentQuestionId"]
    assert after["fieldStates"][turn["answerToFieldId"]]["answerState"] == "UNANSWERED"
    assert result["text"] == before["askedQuestions"][0]["text"]


def test_short_ack_after_project_lead_does_not_erase_confirmed_role() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_fields(
        user,
        [("役割", "short_text"), ("担当領域", "long_text")],
    )
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    role_field_id = store.get("interview_states", f"interview-state-{record['id']}")[
        "currentFieldId"
    ]

    first = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="プロジェクトリードです。"),
    )
    first_result = process_internal_voice_turn(session["id"], first["id"])
    state_after_role = store.get("interview_states", f"interview-state-{record['id']}")
    assert state_after_role is not None
    assert state_after_role["fieldStates"][role_field_id]["answerState"] == "CONFIRMED"
    assert state_after_role["fieldStates"][role_field_id]["recordAnswer"] == "プロジェクトリード"

    second = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(
            transcript="はい、そうそう。",
            answerToQuestionId=first_result["voiceTurn"]["questionId"],
        ),
    )
    process_internal_voice_turn(session["id"], second["id"])
    final_state = store.get("interview_states", f"interview-state-{record['id']}")

    assert final_state is not None
    assert final_state["fieldStates"][role_field_id]["answerState"] == "CONFIRMED"
    assert final_state["fieldStates"][role_field_id]["recordAnswer"] == "プロジェクトリード"


def test_multi_field_answer_keeps_extracted_items_and_asks_only_missing_field(
    monkeypatch: pytest.MonkeyPatch,
    stub_structured_provider: FakeStructuredProvider,
) -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_fields(
        user,
        [("氏名", "short_text"), ("部署", "short_text"), ("役職", "short_text")],
    )
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])

    def extract_only_name_and_department(
        *, context: Mapping[str, object], **_: object
    ) -> StructuredInterviewOutput:
        latest = context["latestUtterance"]
        fields = context["fields"]
        assert isinstance(latest, Mapping)
        assert isinstance(fields, list)
        evidence_id = str(latest.get("messageId") or "")
        field_ids = {
            str(field.get("name")): str(field.get("id"))
            for field in fields
            if isinstance(field, Mapping)
        }
        return StructuredInterviewOutput(
            transcriptAssessment=TranscriptAssessment(
                rawTranscript="宮崎です。開発部です。",
                normalizedTranscript="宮崎です。開発部です。",
            ),
            answerAssessment=AnswerAssessment(sufficiency="SUFFICIENT"),
            fieldUpdates=[
                FieldUpdate(
                    fieldId=field_ids[label],
                    value=value,
                    evidenceTranscriptIds=[evidence_id],
                    answerResolution="AUTO_CONFIRM",
                )
                for label, value in (("氏名", "宮崎"), ("部署", "開発部"))
            ],
        )

    monkeypatch.setattr(stub_structured_provider, "interpret", extract_only_name_and_department)
    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="宮崎です。開発部です。"),
    )

    result = process_internal_voice_turn(session["id"], turn["id"])
    state = store.get("interview_states", f"interview-state-{record['id']}")
    assert state is not None
    fields = state["fieldStates"]
    field_ids_by_name = {
        str(field["name"]): str(field["id"])
        for field in structured_service._list_interview_fields(
            store.get("knowledges", record["knowledgeId"]),
            user,
        )
    }

    assert fields[field_ids_by_name["氏名"]]["recordAnswer"] == "宮崎"
    assert fields[field_ids_by_name["部署"]]["recordAnswer"] == "開発部"
    assert fields[field_ids_by_name["氏名"]]["answerState"] == "CONFIRMED"
    assert fields[field_ids_by_name["部署"]]["answerState"] == "CONFIRMED"
    assert fields[field_ids_by_name["役職"]]["answerState"] == "UNANSWERED"
    assert result["questionId"] is not None
    assert state["currentQuestionId"] == result["questionId"]
    assert state["askedQuestions"][-1]["fieldId"] == field_ids_by_name["役職"]
def test_no_answer_gets_one_neutral_probe_then_advances() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_fields(user, [("転機", "long_text"), ("強み", "long_text")])
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])

    first = create_internal_voice_turn(session["id"], VoiceTurnCreate(transcript="あまり覚えていません。特に大きな転機はなかったと思います。"))
    first_result = process_internal_voice_turn(session["id"], first["id"])
    assert first_result["action"] == "ask_follow_up"
    assert first_result["questionId"] == "q-001"
    assert "具体的な出来事" in first_result["text"]
    assert first_result["latencyMetrics"]["question_generation_calls"] == 0
    assert first_result["latencyMetrics"]["retrieval_calls"] == 0
    assert "api_total_ms" in first_result["latencyMetrics"]
    assert store.get("voice_turns", first["id"])["latencyMetrics"] == first_result["latencyMetrics"]
    assert {
        "interpreter_ms",
        "medium_retry_ms",
        "patch_repair_ms",
        "state_transition_ms",
        "retrieval_ms",
        "question_generation_ms",
        "api_total_ms",
    } <= set(first_result["latencyMetrics"])

    second = create_internal_voice_turn(session["id"], VoiceTurnCreate(transcript="特にありません。"))
    second_result = process_internal_voice_turn(session["id"], second["id"])
    state = store.get("interview_states", f"interview-state-{record['id']}")
    first_field_id = state["askedQuestions"][0]["fieldId"]
    assert second_result["questionId"] == "q-002"
    assert state["fieldStates"][first_field_id]["answerDisposition"] == "NO_DETAIL"
    assert state["askedQuestions"][-1]["fieldId"] != first_field_id


def test_multiple_fields_in_one_answer_are_not_reasked() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_fields(
        user,
        [("氏名", "short_text"), ("部署", "short_text"), ("役職", "short_text"), ("担当領域", "long_text")],
    )
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="山田太郎です。開発部の主任で、社内システムの設計と開発を担当しています。"),
    )

    result = process_internal_voice_turn(session["id"], turn["id"])
    state = store.get("interview_states", f"interview-state-{record['id']}")
    states = state["fieldStates"]

    assert result["action"] == "ask_structured"
    assert result["voiceTurn"]["questionId"] is not None
    assert all(item["answerState"] == "CONFIRMED" for item in states.values())
    assert [item["targetId"] for item in state["askedQuestions"]] == [
        state["askedQuestions"][0]["targetId"],
        "open_ended",
    ]

    closing = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(
            transcript="特にありません。",
            answerToQuestionId=result["voiceTurn"]["questionId"],
        ),
    )
    finished = process_internal_voice_turn(session["id"], closing["id"])
    assert finished["action"] == "finish"
