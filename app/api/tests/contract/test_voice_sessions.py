import time
from copy import deepcopy
from threading import Event

import pytest
from fastapi import HTTPException

from ai_interviewer_api.auth.deps import DEV_TOKENS, UserContext
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.internal_voice import (
    cancel_internal_voice_turn,
    claim_internal_initial_reply,
    classify_internal_voice_turn_intent,
    create_internal_assistant_event,
    create_internal_voice_turn,
    mark_internal_initial_reply_sent,
    process_internal_voice_turn,
)
from ai_interviewer_api.routers.knowledge_dbs import create_knowledge_db
from ai_interviewer_api.routers.knowledge_fields import create_field
from ai_interviewer_api.routers.knowledges import create_knowledge
from ai_interviewer_api.routers.records import create_record
from ai_interviewer_api.routers.voice_sessions import (
    create_record_voice_session,
    get_record_voice_session,
    stop_record_voice_session,
)
from ai_interviewer_api.schemas.requests import (
    KnowledgeCreate,
    KnowledgeDbCreate,
    KnowledgeFieldCreate,
    RecordCreate,
)
from ai_interviewer_api.schemas.voice import (
    AssistantEventCreate,
    VoiceSessionCreate,
    VoiceTurnCancel,
    VoiceTurnCreate,
    VoiceTurnIntentCreate,
)
from ai_interviewer_api.services import voice_interview as voice_interview_service
from ai_interviewer_api.services.ai_interview import (
    InterviewStreamResult,
    generate_interview_reply,
)


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.tables.clear()


@pytest.fixture(autouse=True)
def stub_voice_answer_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_evaluate(
        *,
        transcript: str,
        current_question: dict | None,
        current_field: dict | None,
        field_state: dict,
        evidence_message_id: str,
    ):
        question_text = str((current_question or {}).get("text") or "")
        normalized = transcript.strip().replace("です。", "").replace("です", "")
        normalized = normalized.replace("はい、", "").replace("はい", "").strip(" 、。")
        if "自己紹介" in question_text:
            if "所属" in transcript and "担当" in transcript:
                return voice_interview_service.VoiceAnswerEvaluation(
                    decision="CONFIRMABLE",
                    normalized_answer="宮崎正之です。設備保全部で担当業務は設備保全です。",
                    record_answer="宮崎正之です。設備保全部で担当業務は設備保全です。",
                    is_relevant=True,
                    is_sufficient=True,
                    missing_information=[],
                    follow_up_question=None,
                    evidence_transcript_ids=[evidence_message_id],
                )
            return voice_interview_service.VoiceAnswerEvaluation(
                decision="NEEDS_MORE_INFORMATION",
                normalized_answer="宮崎正之",
                record_answer="宮崎正之",
                is_relevant=True,
                is_sufficient=False,
                missing_information=["所属", "担当業務"],
                follow_up_question="所属と担当業務についても教えてください。",
                evidence_transcript_ids=[evidence_message_id],
            )
        if normalized in {"雑談", "関係ない", "関係ないです"}:
            return voice_interview_service.VoiceAnswerEvaluation(
                decision="NOT_ANSWER",
                normalized_answer="",
                is_relevant=False,
                is_sufficient=False,
                missing_information=[],
                follow_up_question="すみません。あなたの名前を教えてください。",
                evidence_transcript_ids=[evidence_message_id],
            )
        if normalized in {"", "えっと", "あの"}:
            return voice_interview_service.VoiceAnswerEvaluation(
                decision="UNCLEAR",
                normalized_answer="",
                is_relevant=False,
                is_sufficient=False,
                missing_information=[],
                follow_up_question="すみません。回答内容を正しく理解できませんでした。もう一度教えてください。",
                evidence_transcript_ids=[evidence_message_id],
            )
        if "宮崎ではなく宮崎健一" in transcript:
            normalized = "宮崎健一"
        elif "宮崎正之" in transcript:
            normalized = "宮崎正之"
        elif "宮崎" in transcript:
            normalized = "宮崎"
        return voice_interview_service.VoiceAnswerEvaluation(
            decision="CONFIRMABLE",
            normalized_answer=normalized,
            record_answer=normalized,
            is_relevant=True,
            is_sufficient=True,
            missing_information=[],
            follow_up_question=None,
            evidence_transcript_ids=[evidence_message_id],
        )

    def fake_confirmation(
        *,
        current_question: dict | None,
        candidate_answer: str,
        user_reply: str,
        field_state: dict,
    ):
        text = user_reply.strip()
        compact = text.replace("。", "").replace("、", "").replace(" ", "")
        if compact in {"はい", "はいそうです", "そうです", "そのとおりです", "合っています", "問題ありません"}:
            return voice_interview_service.VoiceConfirmationEvaluation(
                outcome="CONFIRM",
                revised_answer=None,
                record_answer=candidate_answer,
                clarification_question=None,
            )
        if compact in {"ダメです", "違います", "いいえ", "間違っています"}:
            return voice_interview_service.VoiceConfirmationEvaluation(
                outcome="REJECT_WITHOUT_CONTENT",
                revised_answer=None,
                clarification_question="承知しました。どの部分が違いますか。正しい内容を教えてください。",
            )
        if compact in {"たぶんそうです", "そうだったと思います", "よく分かりません", "だいたい合っています"}:
            return voice_interview_service.VoiceConfirmationEvaluation(
                outcome="UNCLEAR",
                revised_answer=None,
                clarification_question="内容を確定してよいか判断できませんでした。正しければ『はい』、修正があれば正しい内容を教えてください。",
            )
        if "いえ、まさしです" in text:
            return voice_interview_service.VoiceConfirmationEvaluation(
                outcome="REVISE_WITH_CONTENT",
                revised_answer="宮崎まさし",
                record_answer="宮崎まさし",
                clarification_question=None,
            )
        if "宮崎健一" in text:
            return voice_interview_service.VoiceConfirmationEvaluation(
                outcome="REVISE_WITH_CONTENT",
                revised_answer="宮崎健一",
                record_answer="宮崎健一",
                clarification_question=None,
            )
        return voice_interview_service.VoiceConfirmationEvaluation(
            outcome="UNCLEAR",
            revised_answer=None,
            clarification_question="内容を確定してよいか判断できませんでした。正しければ『はい』、修正があれば正しい内容を教えてください。",
        )

    monkeypatch.setattr(
        "ai_interviewer_api.services.voice_interview._evaluate_voice_answer_candidate",
        fake_evaluate,
    )
    monkeypatch.setattr(
        "ai_interviewer_api.services.voice_interview._evaluate_confirmation_response",
        fake_confirmation,
    )


def _create_record_with_field(user: UserContext) -> dict:
    knowledge_db = create_knowledge_db(KnowledgeDbCreate(name="voice db"), user)
    knowledge = create_knowledge(
        knowledge_db["id"],
        KnowledgeCreate(name="音声インタビュー", targetEquipment="圧入機A"),
        user,
    )
    create_field(
        knowledge["id"],
        KnowledgeFieldCreate(
            name="現象",
            inputType="long_text",
            required=True,
            askByAi=True,
            aiQuestionExamples=["どのような現象が起きていますか？"],
            displayOrder=1,
        ),
        user,
    )
    return create_record(knowledge["id"], RecordCreate(title="朝一の荷重ばらつき"), user)


def _create_record_with_name_and_role_fields(user: UserContext) -> dict:
    knowledge_db = create_knowledge_db(KnowledgeDbCreate(name="voice db"), user)
    knowledge = create_knowledge(
        knowledge_db["id"],
        KnowledgeCreate(name="音声インタビュー"),
        user,
    )
    create_field(
        knowledge["id"],
        KnowledgeFieldCreate(
            name="氏名",
            inputType="short_text",
            required=True,
            askByAi=True,
            retrievalPolicy="never",
            aiQuestionExamples=["あなたの名前は？"],
            displayOrder=1,
        ),
        user,
    )
    create_field(
        knowledge["id"],
        KnowledgeFieldCreate(
            name="担当",
            inputType="short_text",
            required=True,
            askByAi=True,
            retrievalPolicy="never",
            aiQuestionExamples=["あなたの担当は？"],
            displayOrder=2,
        ),
        user,
    )
    return create_record(knowledge["id"], RecordCreate(title="担当者インタビュー"), user)


def _create_record_with_self_intro_field(user: UserContext) -> dict:
    knowledge_db = create_knowledge_db(KnowledgeDbCreate(name="voice db"), user)
    knowledge = create_knowledge(
        knowledge_db["id"],
        KnowledgeCreate(name="音声インタビュー"),
        user,
    )
    create_field(
        knowledge["id"],
        KnowledgeFieldCreate(
            name="自己紹介",
            inputType="long_text",
            required=True,
            askByAi=True,
            retrievalPolicy="never",
            aiQuestionExamples=["自己紹介をお願いします。"],
            displayOrder=1,
        ),
        user,
    )
    return create_record(knowledge["id"], RecordCreate(title="自己紹介インタビュー"), user)


def _create_record_with_self_intro_and_hobby_fields(user: UserContext) -> dict:
    knowledge_db = create_knowledge_db(KnowledgeDbCreate(name="voice db"), user)
    knowledge = create_knowledge(
        knowledge_db["id"],
        KnowledgeCreate(name="人物インタビュー"),
        user,
    )
    create_field(
        knowledge["id"],
        KnowledgeFieldCreate(
            name="自己紹介",
            inputType="short_text",
            required=True,
            askByAi=True,
            retrievalPolicy="never",
            aiQuestionExamples=["自己紹介をお願いします。"],
            displayOrder=1,
        ),
        user,
    )
    create_field(
        knowledge["id"],
        KnowledgeFieldCreate(
            name="趣味",
            inputType="short_text",
            required=True,
            askByAi=True,
            retrievalPolicy="never",
            aiQuestionExamples=["具体的な趣味を教えてください。"],
            displayOrder=2,
        ),
        user,
    )
    return create_record(knowledge["id"], RecordCreate(title="人物インタビュー"), user)


def _create_record_without_voice_field(user: UserContext) -> dict:
    knowledge_db = create_knowledge_db(KnowledgeDbCreate(name="voice db"), user)
    knowledge = create_knowledge(
        knowledge_db["id"],
        KnowledgeCreate(name="音声インタビュー", targetEquipment="圧入機A"),
        user,
    )
    create_field(
        knowledge["id"],
        KnowledgeFieldCreate(
            name="現象",
            inputType="long_text",
            required=True,
            askByAi=False,
            displayOrder=1,
        ),
        user,
    )
    return create_record(knowledge["id"], RecordCreate(title="朝一の荷重ばらつき"), user)


def test_create_get_and_stop_voice_session() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)

    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)

    assert session["recordId"] == record["id"]
    assert session["ownerUserId"] == user.user_id
    assert session["provider"] == "transcribe_polly"
    assert session["currentQuestionId"] == "q-001"
    assert "どのような現象が起きていますか？" in (session.get("initialReplyText") or "")
    assert session["initialQuestionId"] == "q-001"
    assert session["initialReplyStatus"] == "pending"

    fetched = get_record_voice_session(session["id"], user)
    assert fetched["id"] == session["id"]
    assert fetched["initialReplyText"] == session["initialReplyText"]

    claimed = claim_internal_initial_reply(session["id"])
    assert claimed["claimed"] is True
    assert claimed["initialReplyText"] == session["initialReplyText"]
    assert claimed["initialQuestionId"] == "q-001"

    claimed_again = claim_internal_initial_reply(session["id"])
    assert claimed_again["claimed"] is False
    assert claimed_again["reason"] == "already_sending"

    marked = mark_internal_initial_reply_sent(session["id"])
    assert marked["initialReplyStatus"] == "sent"
    assert marked["initialReplySentAt"]

    claimed_after_sent = claim_internal_initial_reply(session["id"])
    assert claimed_after_sent["claimed"] is False
    assert claimed_after_sent["reason"] == "already_sent"

    stopped = stop_record_voice_session(session["id"], user)
    assert stopped["status"] == "stopped"
    assert stopped["connectionStatus"] == "closed"


def test_voice_session_preserves_transcribe_polly_provider() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)

    session = create_record_voice_session(
        record["id"],
        VoiceSessionCreate(provider="transcribe_polly"),
        user,
    )

    assert session["provider"] == "transcribe_polly"


def test_initial_question_is_not_saved_as_chat_message_before_spoken() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_name_and_role_fields(user)

    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)

    assert session["initialReplyText"] == "これからインタビューを開始します。あなたの名前は？"
    messages = [
        row
        for row in store.list("messages", user.tenant_id)
        if row.get("recordId") == record["id"]
    ]
    assert messages == []


def test_voice_session_uses_existing_current_question_for_initial_reply() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_name_and_role_fields(user)
    first = generate_interview_reply(record, user, persist_assistant_messages=False)
    assert first.metadata["question"]["questionId"] == "q-001"

    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)

    assert session["currentQuestionId"] == "q-001"
    assert session["initialQuestionId"] == "q-001"
    assert session["initialReplyText"] == "これからインタビューを開始します。あなたの名前は？"
    assert session["initialReplyStatus"] == "pending"


def test_voice_turn_requires_confirmation_before_answer_is_committed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_name_and_role_fields(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="宮崎です"),
    )

    def fail_agent(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("direct capture questions must not run interview agent")

    monkeypatch.setattr(
        "ai_interviewer_api.services.ai_interview.run_adapted_interview_turn",
        fail_agent,
    )

    result = process_internal_voice_turn(session["id"], turn["id"])
    snapshot = get_record_voice_session(session["id"], user)
    state = store.get("interview_states", f"interview-state-{record['id']}")
    messages = [
        row
        for row in store.list("messages", user.tenant_id)
        if row.get("recordId") == record["id"]
    ]
    field_id = state["currentFieldId"]

    assert result["retrievalPolicy"] == "never"
    assert result["retrievalExecuted"] is False
    assert result["text"] == "氏名は「宮崎です」という理解でよろしいですか？"
    assert result["questionId"] == "q-001"
    assert snapshot["currentQuestionId"] == "q-001"
    assert state["completedFieldIds"] == []
    assert state["fieldStates"][field_id]["answerSummary"] is None
    assert state["fieldStates"][field_id]["answerState"] == "AWAITING_CONFIRMATION"
    assert state["fieldStates"][field_id]["candidateAnswer"] == "宮崎"
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "宮崎です"
    assert messages[0]["voiceTurnId"] == turn["id"]


def test_voice_turn_commits_only_after_explicit_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_name_and_role_fields(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])

    def fail_agent(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("direct capture questions must not run interview agent before confirmation")

    monkeypatch.setattr(
        "ai_interviewer_api.services.ai_interview.run_adapted_interview_turn",
        fail_agent,
    )

    first_turn = create_internal_voice_turn(session["id"], VoiceTurnCreate(transcript="はい、宮崎です"))
    first_result = process_internal_voice_turn(session["id"], first_turn["id"])
    assert first_result["questionId"] == "q-001"

    second_turn = create_internal_voice_turn(session["id"], VoiceTurnCreate(transcript="はい"))
    second_result = process_internal_voice_turn(session["id"], second_turn["id"])
    state = store.get("interview_states", f"interview-state-{record['id']}")
    messages = [
        row
        for row in store.list("messages", user.tenant_id)
        if row.get("recordId") == record["id"]
    ]
    first_field_id = state["askedQuestions"][0]["fieldId"]

    assert second_result["retrievalPolicy"] == "never"
    assert second_result["retrievalExecuted"] is False
    assert second_result["text"] == "あなたの担当は？"
    assert second_result["questionId"] == "q-002"
    assert state["completedFieldIds"] == [first_field_id]
    assert state["fieldStates"][first_field_id]["answerSummary"] is None
    assert state["fieldStates"][first_field_id]["recordAnswer"] == "宮崎"
    assert state["fieldStates"][first_field_id]["answerState"] == "CONFIRMED"
    assert [message["content"] for message in messages if message.get("isActualUtterance") is False] == ["宮崎"]


def test_voice_confirmation_is_natural_and_reads_next_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_self_intro_and_hobby_fields(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])

    def evaluate_answer(**kwargs):  # type: ignore[no-untyped-def]
        field_name = str((kwargs.get("current_field") or {}).get("name") or "")
        confirmation_question = (
            "お名前が田中さんでよろしかったですか？"
            if "自己紹介" in field_name
            else "バスケでいいですか？"
        )
        return voice_interview_service.VoiceAnswerEvaluation(
            decision="CONFIRMABLE",
            normalized_answer=kwargs["transcript"],
            is_relevant=True,
            is_sufficient=True,
            missing_information=[],
            follow_up_question=None,
            evidence_transcript_ids=[kwargs["evidence_message_id"]],
            confirmation_question=confirmation_question,
        )

    def confirm_with_llm_record_answer(**kwargs):  # type: ignore[no-untyped-def]
        return voice_interview_service.VoiceConfirmationEvaluation(
            outcome="CONFIRM",
            record_answer=kwargs["candidate_answer"],
        )

    monkeypatch.setattr(voice_interview_service, "_evaluate_voice_answer_candidate", evaluate_answer)
    monkeypatch.setattr(voice_interview_service, "_evaluate_confirmation_response", confirm_with_llm_record_answer)

    self_intro_turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="田中です"),
    )
    self_intro_result = process_internal_voice_turn(session["id"], self_intro_turn["id"])
    assert self_intro_result["text"] == "自己紹介は「田中です」という理解でよろしいですか？"

    confirmation_turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="はい"),
    )
    confirmation_result = process_internal_voice_turn(session["id"], confirmation_turn["id"])
    assert confirmation_result["text"] == "具体的な趣味を教えてください。"
    assert confirmation_result["questionId"] == "q-002"
    assert store.get("voice_turns", self_intro_turn["id"])["lifecycleStatus"] == "COMMITTED"
    assert store.get("voice_turns", confirmation_turn["id"])["lifecycleStatus"] == "COMMITTED"

    hobby_turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="バスケです"),
    )
    hobby_result = process_internal_voice_turn(session["id"], hobby_turn["id"])
    assert hobby_result["text"] == "趣味は「バスケです」という理解でよろしいですか？"

    state = store.get("interview_states", f"interview-state-{record['id']}")
    first_field_id = state["askedQuestions"][0]["fieldId"]
    second_field_id = state["askedQuestions"][1]["fieldId"]
    assert state["fieldStates"][first_field_id]["answerState"] == "CONFIRMED"
    assert state["fieldStates"][first_field_id]["answerSummary"] is None
    assert state["fieldStates"][first_field_id]["recordAnswer"] == "田中です"
    assert state["fieldStates"][second_field_id]["answerState"] == "AWAITING_CONFIRMATION"
    assert state["fieldStates"][second_field_id]["answerSummary"] is None


def test_voice_turn_updates_candidate_on_correction_and_reconfirms() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_name_and_role_fields(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])

    first_turn = create_internal_voice_turn(session["id"], VoiceTurnCreate(transcript="宮崎です"))
    process_internal_voice_turn(session["id"], first_turn["id"])

    correction_turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="いいえ、宮崎ではなく宮崎健一です"),
    )
    correction_result = process_internal_voice_turn(session["id"], correction_turn["id"])
    state = store.get("interview_states", f"interview-state-{record['id']}")
    field_id = state["currentFieldId"]

    assert correction_result["text"] == "氏名は「宮崎健一」という理解でよろしいですか？"
    assert correction_result["questionId"] == "q-001"
    assert state["completedFieldIds"] == []
    assert state["fieldStates"][field_id]["answerSummary"] is None
    assert state["fieldStates"][field_id]["candidateAnswer"] == "宮崎健一"
    assert state["fieldStates"][field_id]["answerState"] == "AWAITING_CONFIRMATION"
    superseded_turn = store.get("voice_turns", first_turn["id"])
    assert superseded_turn["lifecycleStatus"] == "SUPERSEDED"
    assert superseded_turn["supersededByTurnId"] == correction_turn["id"]
    assert (
        store.get("voice_turns", correction_turn["id"])["lifecycleStatus"]
        == "COMMITTED"
    )


def test_voice_turn_keeps_waiting_on_ambiguous_confirmation_reply() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_name_and_role_fields(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])

    first_turn = create_internal_voice_turn(session["id"], VoiceTurnCreate(transcript="宮崎です"))
    process_internal_voice_turn(session["id"], first_turn["id"])

    ambiguous_turn = create_internal_voice_turn(session["id"], VoiceTurnCreate(transcript="たぶんそうです"))
    ambiguous_result = process_internal_voice_turn(session["id"], ambiguous_turn["id"])
    state = store.get("interview_states", f"interview-state-{record['id']}")
    field_id = state["currentFieldId"]

    assert ambiguous_result["text"] == "内容を確定してよいか判断できませんでした。正しければ『はい』、修正があれば正しい内容を教えてください。"
    assert ambiguous_result["questionId"] == "q-001"
    assert state["completedFieldIds"] == []
    assert state["fieldStates"][field_id]["answerSummary"] is None
    assert state["fieldStates"][field_id]["answerState"] == "AWAITING_CONFIRMATION"


def test_voice_turn_falls_back_to_clarification_when_ai_evaluation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_name_and_role_fields(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    turn = create_internal_voice_turn(session["id"], VoiceTurnCreate(transcript="宮崎です"))

    def fail_evaluator(**kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("evaluate failed")

    monkeypatch.setattr(
        "ai_interviewer_api.services.voice_interview._evaluate_voice_answer_candidate",
        fail_evaluator,
    )

    result = process_internal_voice_turn(session["id"], turn["id"])
    state = store.get("interview_states", f"interview-state-{record['id']}")
    field_id = state["currentFieldId"]

    assert result["text"] == "回答処理で一時的な問題が発生しました。もう一度お答えください。"
    assert result["questionId"] == "q-001"
    assert state["fieldStates"][field_id]["answerSummary"] is None
    assert state["fieldStates"][field_id]["answerState"] == "CANDIDATE_PENDING"


def test_voice_answer_evaluation_deadline_returns_fallback_and_discards_late_result(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_self_intro_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    turn = create_internal_voice_turn(session["id"], VoiceTurnCreate(transcript="田中です"))
    release_evaluator = Event()

    def delayed_evaluator(**kwargs):  # type: ignore[no-untyped-def]
        release_evaluator.wait(timeout=30.0)
        return voice_interview_service.VoiceAnswerEvaluation(
            decision="CONFIRMABLE",
            normalized_answer="遅れて返った回答",
            is_relevant=True,
            is_sufficient=True,
            missing_information=[],
            follow_up_question=None,
            evidence_transcript_ids=[kwargs["evidence_message_id"]],
        )

    monkeypatch.setattr(voice_interview_service, "_evaluate_voice_answer_candidate", delayed_evaluator)

    started_at = time.monotonic()
    result = process_internal_voice_turn(session["id"], turn["id"])
    elapsed = time.monotonic() - started_at

    assert elapsed < 2.0
    assert result["text"] == "回答処理で一時的な問題が発生しました。もう一度お答えください。"
    state = store.get("interview_states", f"interview-state-{record['id']}")
    field_id = state["currentFieldId"]
    field_state = state["fieldStates"][field_id]
    assert field_state["candidateAnswer"] is None
    assert field_state["answerSummary"] is None
    assert field_state["answerState"] == "CANDIDATE_PENDING"
    assert field_state["evaluationDegraded"] is True
    assert field_state["degradedReason"] == "bedrock_timeout"

    release_evaluator.set()
    time.sleep(0.1)
    state_after_late_result = store.get("interview_states", f"interview-state-{record['id']}")
    late_field_state = state_after_late_result["fieldStates"][field_id]
    assert late_field_state["candidateAnswer"] is None
    assert late_field_state["answerSummary"] is None
    assert "late_evaluation_result_discarded" in caplog.text


def test_voice_turn_requests_more_information_without_confirming_partial_answer() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_self_intro_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    turn = create_internal_voice_turn(session["id"], VoiceTurnCreate(transcript="宮崎正之です。"))

    result = process_internal_voice_turn(session["id"], turn["id"])
    state = store.get("interview_states", f"interview-state-{record['id']}")
    field_id = state["currentFieldId"]

    assert result["text"] == "所属と担当業務についても教えてください。"
    assert result["action"] == "ask_follow_up"
    assert state["fieldStates"][field_id]["answerState"] == "CANDIDATE_PENDING"
    assert state["fieldStates"][field_id]["candidateAnswer"] == "宮崎正之"
    assert state["fieldStates"][field_id]["answerSummary"] is None
    assert state["fieldStates"][field_id]["missingInformation"] == ["所属", "担当業務"]


def test_voice_turn_rejects_irrelevant_answer_without_creating_confirmation_candidate() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_name_and_role_fields(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    turn = create_internal_voice_turn(session["id"], VoiceTurnCreate(transcript="関係ないです"))

    result = process_internal_voice_turn(session["id"], turn["id"])
    state = store.get("interview_states", f"interview-state-{record['id']}")
    field_id = state["currentFieldId"]

    assert result["text"] == "すみません。あなたの名前を教えてください。"
    assert result["action"] == "ask_follow_up"
    assert state["fieldStates"][field_id]["answerState"] == "CANDIDATE_PENDING"
    assert state["fieldStates"][field_id]["candidateAnswer"] is None
    assert state["fieldStates"][field_id]["answerSummary"] is None


def test_voice_answer_evaluation_stabilizes_irrelevant_needs_more_as_not_answer() -> None:
    evaluation = voice_interview_service.VoiceAnswerEvaluation(
        decision="NEEDS_MORE_INFORMATION",
        normalized_answer="",
        is_relevant=False,
        is_sufficient=False,
        missing_information=["設定にない不足情報"],
        follow_up_question="質問に沿って回答してください。",
        evidence_transcript_ids=["message-1"],
    )

    stabilized = voice_interview_service._stabilize_voice_answer_evaluation(evaluation)

    assert stabilized.decision == "NOT_ANSWER"
    assert stabilized.normalized_answer == ""
    assert stabilized.is_relevant is False
    assert stabilized.is_sufficient is False
    assert stabilized.missing_information == []


def test_confirmation_reject_without_content_keeps_candidate_and_requests_correction() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_name_and_role_fields(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])

    first_turn = create_internal_voice_turn(session["id"], VoiceTurnCreate(transcript="宮崎正之です"))
    process_internal_voice_turn(session["id"], first_turn["id"])

    reject_turn = create_internal_voice_turn(session["id"], VoiceTurnCreate(transcript="ダメです"))
    result = process_internal_voice_turn(session["id"], reject_turn["id"])
    state = store.get("interview_states", f"interview-state-{record['id']}")
    field_id = state["currentFieldId"]

    assert result["text"] == "承知しました。どの部分が違いますか。正しい内容を教えてください。"
    assert state["fieldStates"][field_id]["answerState"] == "CANDIDATE_PENDING"
    assert state["fieldStates"][field_id]["candidateAnswer"] == "宮崎正之"
    assert state["fieldStates"][field_id]["answerSummary"] is None


def test_confirmation_revise_with_content_merges_revised_answer() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_name_and_role_fields(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])

    first_turn = create_internal_voice_turn(session["id"], VoiceTurnCreate(transcript="宮崎正之です"))
    process_internal_voice_turn(session["id"], first_turn["id"])

    revise_turn = create_internal_voice_turn(session["id"], VoiceTurnCreate(transcript="いえ、まさしです"))
    result = process_internal_voice_turn(session["id"], revise_turn["id"])
    state = store.get("interview_states", f"interview-state-{record['id']}")
    field_id = state["currentFieldId"]

    assert result["text"] == "氏名は「宮崎まさし」という理解でよろしいですか？"
    assert state["fieldStates"][field_id]["answerState"] == "AWAITING_CONFIRMATION"
    assert state["fieldStates"][field_id]["candidateAnswer"] == "宮崎まさし"


def test_voice_session_requires_owner_match() -> None:
    owner = DEV_TOKENS["dev-manager"]
    other = DEV_TOKENS["dev-interviewer"]
    record = _create_record_with_field(owner)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), owner)

    with pytest.raises(HTTPException) as exc_info:
        get_record_voice_session(session["id"], other)

    assert exc_info.value.status_code == 403


def test_create_voice_session_rejects_record_without_voice_questions_without_completing_state() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_without_voice_field(user)

    with pytest.raises(HTTPException) as exc_info:
        create_record_voice_session(record["id"], VoiceSessionCreate(), user)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "voice_session_missing_questions"
    assert store.get("interview_states", f"interview-state-{record['id']}") is None


def test_create_voice_session_recovers_state_when_voice_question_is_added_after_completed_state() -> None:
    user = DEV_TOKENS["dev-manager"]
    knowledge_db = create_knowledge_db(KnowledgeDbCreate(name="voice db"), user)
    knowledge = create_knowledge(
        knowledge_db["id"],
        KnowledgeCreate(name="音声インタビュー", targetEquipment="圧入機A"),
        user,
    )
    record = create_record(knowledge["id"], RecordCreate(title="朝一の荷重ばらつき"), user)

    generate_interview_reply(record, user)
    assert store.get("interview_states", f"interview-state-{record['id']}")["status"] == "completed"

    create_field(
        knowledge["id"],
        KnowledgeFieldCreate(
            name="現象",
            inputType="long_text",
            required=True,
            askByAi=True,
            aiQuestionExamples=["どのような現象が起きていますか？"],
            displayOrder=1,
        ),
        user,
    )

    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)

    assert session["currentQuestionId"] == "q-001"
    assert (session.get("initialReplyText") or "").startswith("これからインタビューを開始します。")
    assert "どのような現象が起きていますか？" in (session.get("initialReplyText") or "")


def test_create_voice_turn_defaults_answer_to_current_question() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)

    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="朝一だけ圧入荷重が不安定です"),
    )

    assert turn["sequence"] == 1
    assert turn["answerToQuestionId"] == session["currentQuestionId"]
    assert turn["processingStatus"] == "pending"
    assert turn["lifecycleStatus"] == "RECEIVED"


def test_control_voice_turn_has_no_answer_scope_or_candidate() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)

    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="インタビュー開始して", turnType="CONTROL"),
    )
    result = process_internal_voice_turn(session["id"], turn["id"])
    state = store.get("interview_states", f"interview-state-{record['id']}")
    messages = [
        row
        for row in store.list("messages", user.tenant_id)
        if row.get("recordId") == record["id"] and row.get("voiceTurnId") == turn["id"]
    ]

    assert turn["turnType"] == "CONTROL"
    assert turn["answerToQuestionId"] is None
    assert turn["processingMode"] == "control"
    assert result["text"] == "承知しました。"
    field_state = state["fieldStates"][state["currentFieldId"]]
    assert field_state["rawAnswerHistory"] == []
    assert field_state["capturedItems"] == []
    assert messages[0]["turnType"] == "CONTROL"
    assert messages[0]["answerToQuestionId"] is None


def test_voice_turn_intent_classification_is_semantic_and_pre_save(monkeypatch: pytest.MonkeyPatch) -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)

    def classify(*, system_prompt, prompt, output_model):
        assert "固定フレーズ" in system_prompt
        assert "current_question:" in prompt
        return output_model(turnType="CONTROL")

    monkeypatch.setattr(voice_interview_service, "_run_voice_structured_output", classify)
    result = classify_internal_voice_turn_intent(
        session["id"],
        VoiceTurnIntentCreate(
            transcript="会話を終了してください",
            answerToQuestionId=session["currentQuestionId"],
            expectedStateVersion=session["stateVersion"],
        ),
    )

    assert result == {"turnType": "CONTROL"}
    assert store.list("voice_turns", user.tenant_id) == []


def test_process_voice_turn_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="朝一だけ圧入荷重が不安定です"),
    )

    def fake_generate_interview_reply(
        record: dict,
        current_user: UserContext,
        *,
        persist_assistant_messages: bool = True,
    ) -> InterviewStreamResult:
        assert current_user.user_id == user.user_id
        assert persist_assistant_messages is False
        return InterviewStreamResult(
            reply_chunks=["確認ありがとうございます。", "次に、発生条件を教えてください。"],
            metadata={
                "reply": "確認ありがとうございます。\n次に、発生条件を教えてください。",
                "action": "ask_follow_up",
                "question": {
                    "questionId": "q-follow-up-001",
                    "questionType": "follow_up",
                    "fieldId": "field-1",
                    "text": "次に、発生条件を教えてください。",
                },
            },
        )

    monkeypatch.setattr(
        "ai_interviewer_api.services.voice_interview.generate_interview_reply",
        fake_generate_interview_reply,
    )

    first = process_internal_voice_turn(session["id"], turn["id"])
    second = process_internal_voice_turn(session["id"], turn["id"])

    assert first["turnId"] == turn["id"]
    assert first["action"] == "ask_confirmation"
    assert first["questionId"] == "q-001"
    assert first["stateVersion"] == 2
    assert first["responseId"] == second["responseId"]
    assert second["voiceTurn"]["processingStatus"] == "completed"
    assert second["voiceTurn"]["lifecycleStatus"] == "COMMITTED"
    assert second["voiceSession"]["currentQuestionId"] == "q-001"


def test_awaiting_confirmation_turn_is_idempotent() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_name_and_role_fields(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="宮崎です"),
    )

    first = process_internal_voice_turn(session["id"], turn["id"])
    second = process_internal_voice_turn(session["id"], turn["id"])
    state = store.get("interview_states", f"interview-state-{record['id']}")
    field_id = state["currentFieldId"]
    messages = [
        row
        for row in store.list("messages", user.tenant_id)
        if row.get("recordId") == record["id"]
    ]

    assert first["action"] == "ask_confirmation"
    assert second["responseId"] == first["responseId"]
    assert state["fieldStates"][field_id]["answerState"] == "AWAITING_CONFIRMATION"
    assert state["fieldStates"][field_id]["answerSummary"] is None
    assert len(messages) == 2


def test_finish_assistant_transcript_is_not_saved_as_chat_message() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)

    event = create_internal_assistant_event(
        session["id"],
        AssistantEventCreate(
            eventType="assistant_transcript_final",
            responseId="voice-response-finish-001",
            transcript="以上で、設定されているすべての質問項目へのインタビューが完了しました。ご協力ありがとうございました。",
            detail={"action": "finish"},
        ),
    )

    messages = [
        row
        for row in store.list("messages", user.tenant_id)
        if row.get("recordId") == record["id"]
    ]
    events = [
        row
        for row in store.list("voice_assistant_events", user.tenant_id)
        if row.get("recordId") == record["id"]
    ]

    assert event["eventType"] == "assistant_transcript_final"
    assert messages == []
    assert len(events) == 1


def test_local_confirmation_preface_is_saved_once_with_source() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    payload = AssistantEventCreate(
        eventType="assistant_transcript_final",
        responseId="local-preface-response:turn-1",
        generation=4,
        transcript="確認します。",
        detail={"source": "local_fixed_preface", "turnId": "turn-1"},
    )

    create_internal_assistant_event(session["id"], payload)
    create_internal_assistant_event(session["id"], payload)

    messages = [
        row
        for row in store.list("messages", user.tenant_id)
        if row.get("recordId") == record["id"]
    ]
    assert len(messages) == 1
    assert messages[0]["content"] == "確認します。"
    assert messages[0]["source"] == "local_fixed_preface"
    assert messages[0]["voiceResponseId"] == "local-preface-response:turn-1"


def test_stopped_voice_session_rejects_turns() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    stop_record_voice_session(session["id"], user)

    with pytest.raises(HTTPException) as exc_info:
        create_internal_voice_turn(session["id"], VoiceTurnCreate(transcript="回答です"))

    assert exc_info.value.status_code == 409


def test_client_turn_id_is_idempotent_and_rejects_different_payload() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    payload = VoiceTurnCreate(
        transcript="回答です",
        clientTurnId="client-turn-1",
        expectedStateVersion=session["stateVersion"],
    )

    first = create_internal_voice_turn(session["id"], payload)
    second = create_internal_voice_turn(session["id"], payload)

    assert second["id"] == first["id"]
    with pytest.raises(HTTPException) as exc_info:
        create_internal_voice_turn(
            session["id"],
            VoiceTurnCreate(
                transcript="異なる回答です",
                clientTurnId="client-turn-1",
                expectedStateVersion=session["stateVersion"],
            ),
        )
    assert exc_info.value.detail == "turn_duplicate_conflict"


def test_stale_state_version_rejects_turn_before_candidate_is_saved() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)

    with pytest.raises(HTTPException) as exc_info:
        create_internal_voice_turn(
            session["id"],
            VoiceTurnCreate(
                transcript="古い回答です",
                clientTurnId="client-turn-stale",
                expectedStateVersion=session["stateVersion"] - 1,
            ),
        )

    assert exc_info.value.detail == "turn_state_conflict"
    assert store.list("voice_turns", user.tenant_id) == []


def test_cancel_before_late_turn_save_creates_client_turn_tombstone() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    expected_version = session["stateVersion"]

    result = cancel_internal_voice_turn(
        session["id"],
        VoiceTurnCancel(
            clientTurnId="client-turn-late",
            expectedStateVersion=expected_version,
        ),
    )

    assert result["cancelled"] is True
    with pytest.raises(HTTPException) as exc_info:
        create_internal_voice_turn(
            session["id"],
            VoiceTurnCreate(
                transcript="遅れて到着した回答です",
                clientTurnId="client-turn-late",
                expectedStateVersion=expected_version,
            ),
        )
    assert exc_info.value.detail == "turn_cancelled"


def test_cancel_committed_turn_is_rejected_without_rollback() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    expected_version = session["stateVersion"]
    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(
            transcript="宮崎です",
            clientTurnId="client-turn-cancel",
            expectedStateVersion=expected_version,
        ),
    )
    process_internal_voice_turn(session["id"], turn["id"])
    committed_state = deepcopy(
        store.get("interview_states", f"interview-state-{record['id']}")
    )
    committed_session = deepcopy(store.get("voice_sessions", session["id"]))
    committed_field_id = committed_state["currentFieldId"]
    committed_candidate = committed_state["fieldStates"][committed_field_id][
        "candidateAnswer"
    ]
    committed_state_version = committed_session["stateVersion"]
    committed_messages = [
        deepcopy(row)
        for row in store.list("messages", user.tenant_id)
        if row.get("voiceTurnId") == turn["id"]
    ]

    with pytest.raises(HTTPException) as exc_info:
        cancel_internal_voice_turn(
            session["id"],
            VoiceTurnCancel(
                clientTurnId="client-turn-cancel",
                expectedStateVersion=expected_version,
            ),
        )

    assert exc_info.value.detail == "turn_already_committed"
    assert (
        store.get("interview_states", f"interview-state-{record['id']}")
        == committed_state
    )
    assert store.get("voice_sessions", session["id"]) == committed_session
    current_state = store.get("interview_states", f"interview-state-{record['id']}")
    current_session = store.get("voice_sessions", session["id"])
    assert (
        current_state["fieldStates"][committed_field_id]["candidateAnswer"]
        == committed_candidate
    )
    assert current_session["stateVersion"] == committed_state_version
    assert [
        row
        for row in store.list("messages", user.tenant_id)
        if row.get("voiceTurnId") == turn["id"]
    ] == committed_messages
    committed_turn = store.get("voice_turns", turn["id"])
    assert committed_turn["processingStatus"] == "completed"
    assert committed_turn["lifecycleStatus"] == "COMMITTED"


def test_cancel_evaluating_turn_restores_pending_state_and_rejects_commit() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    expected_version = session["stateVersion"]
    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(
            transcript="評価中の回答です",
            clientTurnId="client-turn-evaluating",
            expectedStateVersion=expected_version,
        ),
    )
    base_state = deepcopy(
        store.get("interview_states", f"interview-state-{record['id']}")
    )
    stored_turn = store.get("voice_turns", turn["id"])
    stored_turn["processingStatus"] = "processing"
    stored_turn["lifecycleStatus"] = "EVALUATING"
    stored_turn["baseInterviewState"] = deepcopy(base_state)
    current_field_id = base_state["currentFieldId"]
    dirty_state = deepcopy(base_state)
    dirty_state["fieldStates"][current_field_id]["candidateAnswer"] = "残してはいけない候補"
    dirty_state["fieldStates"][current_field_id]["answerState"] = "AWAITING_CONFIRMATION"
    store.upsert("interview_states", dirty_state)

    result = cancel_internal_voice_turn(
        session["id"],
        VoiceTurnCancel(
            clientTurnId="client-turn-evaluating",
            expectedStateVersion=expected_version,
        ),
    )

    assert result["cancelled"] is True
    assert (
        store.get("interview_states", f"interview-state-{record['id']}")
        == base_state
    )
    cancelled_turn = store.get("voice_turns", turn["id"])
    assert cancelled_turn["lifecycleStatus"] == "CANCELLED"
    with pytest.raises(HTTPException) as exc_info:
        process_internal_voice_turn(session["id"], turn["id"])
    assert exc_info.value.detail == "turn_cancelled"
