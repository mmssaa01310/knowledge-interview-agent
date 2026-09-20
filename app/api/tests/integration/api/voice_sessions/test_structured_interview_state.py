from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import pytest
from fastapi import HTTPException

from ai_interviewer_api.agents.interview_knowledge import service as structured_service
from ai_interviewer_api.agents.interview_knowledge.fast_interpreter.schemas import (
    FastAnswerAssessment,
)
from ai_interviewer_api.agents.interview_knowledge.schemas import (
    AnswerAssessment,
    FieldUpdate,
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
from ai_interviewer_api.services.interview_state_transition import (
    apply_background_state_proposal,
    commit_interview_state,
)

from .support import (
    FakeStructuredProvider,
    _create_record_with_field,
    _create_record_with_fields,
    _set_pending_voice_field_candidate,
)


def test_background_clarification_does_not_stale_the_next_voice_turn() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    state_id = f"interview-state-{record['id']}"
    state = store.get("interview_states", state_id)
    assert state is not None
    question = next(
        item for item in state["askedQuestions"]
        if item["questionId"] == state["currentQuestionId"]
    )
    conversation_version = int(state["stateVersion"])
    analysis_version = int(state.get("analysisVersion", 0))
    background_message_id = "background-message-voice-test"
    output = StructuredInterviewOutput(
        transcriptAssessment=TranscriptAssessment(
            rawTranscript="回答です。",
            normalizedTranscript="回答です。",
        ),
        answerAssessment=AnswerAssessment(sufficiency="SUFFICIENT"),
    )
    apply_background_state_proposal(
        record_id=record["id"],
        user=user,
        proposal={
            "sourceTurnId": "background-turn-voice-test",
            "sourceMessageId": background_message_id,
            "sourceTurnSequence": 1,
            "baseStateVersion": conversation_version,
            "baseAnalysisVersion": analysis_version,
            "sourceQuestion": question,
            "rawTranscript": "回答です。",
            "structuredOutput": output.model_dump(),
            "validEvidenceIds": [background_message_id],
            "clarificationProposal": {
                "requestId": "clarification-background-voice-test",
                "sourceTurnId": "background-turn-voice-test",
                "sourceMessageId": background_message_id,
                "sourceTarget": question,
                "reason": "補足確認が必要",
                "priority": 2,
            },
        },
        fields=structured_service._list_interview_fields(
            store.get("knowledges", record["knowledgeId"]),
            user,
        ),
        profile="fixed_form",
        source_question=question,
    )
    after_background = store.get("interview_states", state_id)
    assert after_background is not None
    assert after_background["stateVersion"] == conversation_version
    assert after_background["analysisVersion"] == analysis_version + 1
    assert after_background["clarificationQueue"]

    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(
            transcript="次のユーザー回答です。",
            turnType="ANSWER",
            answerToQuestionId=question["questionId"],
            expectedStateVersion=conversation_version,
            clientTurnId="after-background-clarification",
        ),
    )

    assert turn["expectedStateVersion"] == conversation_version
    assert turn["answerToQuestionId"] == question["questionId"]


def test_received_voice_turn_rebases_once_only_when_question_is_unchanged() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    state_id = f"interview-state-{record['id']}"
    state = store.get("interview_states", state_id)
    assert state is not None
    original_question_id = state["currentQuestionId"]
    original_version = int(state["stateVersion"])
    payload = VoiceTurnCreate(
        transcript="同一の回答です。",
        turnType="ANSWER",
        answerToQuestionId=original_question_id,
        expectedStateVersion=original_version,
        clientTurnId="rebase-same-source-turn",
    )

    first = create_internal_voice_turn(session["id"], payload)
    state["lastStructuredDialogueAct"] = "ANSWER"
    commit_interview_state(state, user, source="test_foreground_version_bump")
    latest = store.get("interview_states", state_id)
    assert latest is not None

    retried = create_internal_voice_turn(
        session["id"],
        payload.model_copy(update={"expectedStateVersion": latest["stateVersion"]}),
    )

    assert retried["id"] == first["id"]
    assert retried["expectedStateVersion"] == latest["stateVersion"]
    assert len(
        [
            turn
            for turn in store.list("voice_turns", user.tenant_id)
            if turn.get("clientTurnId") == "rebase-same-source-turn"
        ]
    ) == 1


def test_received_voice_turn_is_not_rebased_to_a_new_question() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    state_id = f"interview-state-{record['id']}"
    state = store.get("interview_states", state_id)
    assert state is not None
    old_question_id = state["currentQuestionId"]
    old_version = int(state["stateVersion"])
    payload = VoiceTurnCreate(
        transcript="古い質問への回答です。",
        turnType="ANSWER",
        answerToQuestionId=old_question_id,
        expectedStateVersion=old_version,
        clientTurnId="rebase-question-changed",
    )
    first = create_internal_voice_turn(session["id"], payload)

    state["currentQuestionId"] = "question-after-concurrent-advance"
    commit_interview_state(state, user, source="test_question_advance")
    latest = store.get("interview_states", state_id)
    assert latest is not None
    with pytest.raises(HTTPException) as exc_info:
        create_internal_voice_turn(
            session["id"],
            payload.model_copy(update={"expectedStateVersion": latest["stateVersion"]}),
        )

    assert exc_info.value.detail == "turn_question_conflict"
    unchanged = store.get("voice_turns", first["id"])
    assert unchanged is not None
    assert unchanged["expectedStateVersion"] == old_version


def test_incomplete_final_transcript_stays_on_current_question() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    before = store.get("interview_states", f"interview-state-{record['id']}")
    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="主に社内システムの開発を担当し"),
    )

    result = process_internal_voice_turn(session["id"], turn["id"])
    state = store.get("interview_states", f"interview-state-{record['id']}")
    field_id = state["currentFieldId"]

    assert result["text"] == "続き、お願いします。"
    assert result["questionId"] == before["currentQuestionId"]
    assert state["currentQuestionId"] == before["currentQuestionId"]
    assert state["fieldStates"][field_id]["answerState"] == "UNANSWERED"
    assert store.get("voice_turns", turn["id"])["lifecycleStatus"] == "COMMITTED"


def test_transcribe_polly_retry_names_only_the_unheard_profile_item(
    stub_structured_provider: FakeStructuredProvider,
) -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_fields(
        user,
        [
            ("基本プロフィール", "long_text"),
            ("氏名", "short_text"),
            ("部署", "short_text"),
            ("担当領域", "long_text"),
        ],
    )
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])

    def uncertain_provider(
        *, context: Mapping[str, object], **_: object
    ) -> StructuredInterviewOutput:
        latest = context["latestUtterance"]
        assert isinstance(latest, Mapping)
        message_id = str(latest["messageId"])
        fields = context["fields"]
        assert isinstance(fields, list)
        updates = [
            FieldUpdate(
                fieldId=str(field["id"]),
                value=value,
                evidenceTranscriptIds=[message_id],
                answerResolution="AUTO_CONFIRM",
            )
            for field in fields
            if isinstance(field, Mapping)
            for value in (
                "山田太郎"
                if field.get("name") == "氏名"
                else "社内システムの開発"
                if field.get("name") == "担当領域"
                else None,
            )
            if value is not None
        ]
        return StructuredInterviewOutput(
            transcriptAssessment=TranscriptAssessment(
                correctionStatus="UNCERTAIN",
                correctionReason="所属部分が不自然",
            ),
            answerAssessment=AnswerAssessment(sufficiency="SUFFICIENT"),
            fieldUpdates=updates,
        )

    stub_structured_provider.interpret = uncertain_provider  # type: ignore[method-assign]
    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(
            transcript="え山田太郎でかい家族部に所属しており、主に社内システムの開発を担当しています。"
        ),
    )

    result = process_internal_voice_turn(session["id"], turn["id"])

    assert "山田太郎" in result["text"]
    assert "社内システムの開発" in result["text"]
    assert "所属だけもう一度お願いします" in result["text"]
    assert "家族部" not in result["text"]
    assert result["questionId"] == turn["answerToQuestionId"]


def test_corrected_transcript_is_confirmed_before_field_commit() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    def corrected_provider(*, context: Mapping[str, object], **_: object) -> StructuredInterviewOutput:
        latest = context["latestUtterance"]
        assert isinstance(latest, Mapping)
        raw = str(latest["rawTranscript"])
        message_id = str(latest["messageId"])
        if raw == "特にありません。":
            return StructuredInterviewOutput()
        return StructuredInterviewOutput(
            transcriptAssessment=TranscriptAssessment(
                rawTranscript=raw,
                normalizedTranscript="実装から運用後の改善まで関わっています",
                correctionStatus="CORRECTED",
                correctionCandidates=["実装から運用後の改善まで関わっています"],
            ),
            answerAssessment=AnswerAssessment(sufficiency="SUFFICIENT"),
            fieldUpdates=[
                FieldUpdate(
                    fieldId=str(context["currentQuestion"]["targetId"]),
                    value="実装から運用後の改善まで関わっています",
                    evidenceTranscriptIds=[message_id],
                    answerResolution="AUTO_CONFIRM",
                )
            ],
        )

    provider = FakeStructuredProvider()
    provider.interpret = corrected_provider  # type: ignore[method-assign]
    # The fixture patches the provider factory; replace the returned instance's
    # interpreter only for this scenario.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(structured_service, "_get_structured_provider", lambda *_a, **_k: provider)
    try:
        turn = create_internal_voice_turn(
            session["id"],
            VoiceTurnCreate(transcript="要件整理から実装を輸送ス後の星星まで関わっ"),
        )
        result = process_internal_voice_turn(session["id"], turn["id"])
        state = store.get("interview_states", f"interview-state-{record['id']}")
        field_id = turn["answerToFieldId"]
        assert "実装から運用後の改善まで関わっています" in result["text"]
        assert result["questionId"] == "q-002"
        assert result["voiceTurn"]["questionId"] == "q-002"
        assert state["fieldStates"][field_id]["answerState"] == "AWAITING_CONFIRMATION"
        assert state["fieldStates"][field_id]["recordAnswer"] is None
        assert store.get("voice_turns", turn["id"])["rawTranscript"].endswith("関わっ")

        confirmation = create_internal_voice_turn(
            session["id"],
            VoiceTurnCreate(transcript="はい"),
        )
        confirmation_result = process_internal_voice_turn(session["id"], confirmation["id"])
        assert confirmation_result["action"] == "ask_structured"
        assert confirmation_result["voiceTurn"]["questionId"] is not None
        assert confirmation_result["voiceTurn"]["canonicalIntent"] == "CONFIRMATION"
        assert confirmation_result["voiceTurn"]["canonicalAction"] == "CONFIRM_PENDING_CANDIDATE"
        assert confirmation_result["voiceTurn"]["fastCheckExecuted"] is False
        confirmation_state = store.get("interview_states", f"interview-state-{record['id']}")
        assert confirmation_state is not None
        assert confirmation_state["fieldStates"][field_id]["answerState"] == "CONFIRMED"
        assert confirmation_state["currentQuestionId"] != confirmation["answerToQuestionId"]
        closing = create_internal_voice_turn(
            session["id"],
            VoiceTurnCreate(
                transcript="特にありません。",
                answerToQuestionId=confirmation_result["voiceTurn"]["questionId"],
            ),
        )
        confirmed = process_internal_voice_turn(session["id"], closing["id"])
        state = store.get("interview_states", f"interview-state-{record['id']}")
        assert confirmed["action"] == "finish"
        assert state["fieldStates"][field_id]["answerState"] == "CONFIRMED"
        assert state["fieldStates"][field_id]["recordAnswer"] == "実装から運用後の改善まで関わっています"
    finally:
        monkeypatch.undo()


def test_canonical_rejection_rejects_candidate_without_running_fast_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    _set_pending_voice_field_candidate(record["id"], "候補の回答")
    monkeypatch.setattr(
        voice_interview_service,
        "settings",
        replace(voice_interview_service.settings, structured_interview_fast_path_enabled=True),
    )
    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="違います。"),
    )

    result = process_internal_voice_turn(session["id"], turn["id"])
    state = store.get("interview_states", f"interview-state-{record['id']}")
    stored_turn = store.get("voice_turns", turn["id"])

    assert result["voiceTurn"]["canonicalIntent"] == "REJECTION"
    assert result["voiceTurn"]["canonicalAction"] == "REJECT_PENDING_CANDIDATE"
    assert result["voiceTurn"]["fastCheckExecuted"] is False
    assert state["fieldStates"][turn["answerToFieldId"]]["answerState"] == "UNANSWERED"
    assert state["fieldStates"][turn["answerToFieldId"]]["candidateAnswer"] is None
    assert stored_turn["canonicalAction"] == "REJECT_PENDING_CANDIDATE"


def test_canonical_clarification_keeps_target_and_skips_fast_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    monkeypatch.setattr(
        voice_interview_service,
        "settings",
        replace(voice_interview_service.settings, structured_interview_fast_path_enabled=True),
    )

    class UnexpectedFastProvider:
        def assess(self, **_: object) -> FastAnswerAssessment:
            raise AssertionError("clarification must not enter Fast Answer Check")

    monkeypatch.setattr(structured_service, "BedrockFastInterpreterProvider", UnexpectedFastProvider)
    before = voice_interview_service.get_internal_voice_session(session["id"])
    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="担当領域ってどの範囲のこと？"),
    )

    result = process_internal_voice_turn(session["id"], turn["id"])

    assert result["voiceTurn"]["canonicalIntent"] == "QUESTION_TO_ASSISTANT"
    assert result["voiceTurn"]["canonicalAction"] == "EXPLAIN_CURRENT_QUESTION"
    assert result["voiceTurn"]["fastCheckExecuted"] is False
    assert result["questionId"] == before["currentQuestionId"]
    assert "この質問では" in result["text"]
