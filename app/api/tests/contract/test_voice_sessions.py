from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections.abc import Mapping

import pytest
from fastapi import HTTPException

from ai_interviewer_api.agents.interview_knowledge import service as structured_service
from ai_interviewer_api.agents.interview_knowledge.schemas import (
    AnswerAssessment,
    FieldUpdate,
    QuestionGenerationOutput,
    StructuredInterviewOutput,
    TranscriptAssessment,
)
from ai_interviewer_api.auth.deps import DEV_TOKENS, UserContext
from ai_interviewer_api.models.interview_plan import InterviewPlan
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
from ai_interviewer_api.services.ai_interview import generate_interview_reply


class FakeStructuredProvider:
    def __init__(self) -> None:
        self.interpret_calls: list[dict[str, object]] = []
        self.question_calls: list[dict[str, object]] = []

    def generate_question(
        self,
        *,
        target: Mapping[str, object],
        context: Mapping[str, object],
        **_: object,
    ) -> QuestionGenerationOutput:
        self.question_calls.append({"target": dict(target), "context": dict(context)})
        if target.get("probeType"):
            return QuestionGenerationOutput(
                questionText="大きな転機でなくても、印象に残っている出来事はありますか？"
            )
        if context.get("interviewLocale") == "en-US":
            return QuestionGenerationOutput(
                questionText=f"Please tell me about {target.get('label') or 'that'}."
            )
        return QuestionGenerationOutput(
            questionText=f"{target.get('label') or 'その点'}について教えてください。"
        )

    def interpret(
        self,
        *,
        context: Mapping[str, object],
        **_: object,
    ) -> StructuredInterviewOutput:
        self.interpret_calls.append(dict(context))
        latest = context.get("latestUtterance")
        latest = latest if isinstance(latest, Mapping) else {}
        raw = str(latest.get("rawTranscript") or "").strip()
        message_id = str(latest.get("messageId") or "")
        assessment = TranscriptAssessment(
            rawTranscript=raw,
            normalizedTranscript=raw,
            correctionStatus="NONE",
        )
        if raw.endswith(("担当し", "関わっ")):
            return StructuredInterviewOutput(
                utteranceCompleteness="INCOMPLETE",
                transcriptAssessment=assessment,
                answerAssessment=AnswerAssessment(sufficiency="INCOMPLETE"),
            )
        compact_raw = raw.strip(" 、。！？!?.,")
        if compact_raw in {"特にありません", "特にない"} or "あまり覚えていません" in raw:
            return StructuredInterviewOutput(
                transcriptAssessment=assessment,
                answerAssessment=AnswerAssessment(
                    sufficiency="REFUSAL",
                    probeType="REFRAME",
                ),
            )

        question = context.get("currentQuestion")
        question = question if isinstance(question, Mapping) else {}
        fields = context.get("fields")
        fields = fields if isinstance(fields, list) else []
        updates: list[FieldUpdate] = []
        if "山田太郎" in raw:
            values = {
                "氏名": "山田太郎",
                "部署": "開発部",
                "役職": "主任",
                "担当領域": "社内システムの設計と開発",
                "担当": "開発部の主任",
            }
            for field in fields:
                if not isinstance(field, Mapping):
                    continue
                field_id = str(field.get("id") or "")
                label = str(field.get("name") or "")
                if field_id and label in values:
                    updates.append(
                        FieldUpdate(
                            fieldId=field_id,
                            value=values[label],
                            evidenceTranscriptIds=[message_id],
                            answerResolution="AUTO_CONFIRM",
                        )
                    )
        if not updates:
            target_id = str(question.get("targetId") or "")
            if target_id:
                updates.append(
                    FieldUpdate(
                        fieldId=target_id,
                        value=raw.removesuffix("です。").removesuffix("です").strip(" 、。"),
                        evidenceTranscriptIds=[message_id],
                        answerResolution="AUTO_CONFIRM",
                    )
                )
        return StructuredInterviewOutput(
            transcriptAssessment=assessment,
            answerAssessment=AnswerAssessment(sufficiency="SUFFICIENT"),
            fieldUpdates=updates,
        )


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.tables.clear()


@pytest.fixture(autouse=True)
def stub_structured_provider(monkeypatch: pytest.MonkeyPatch) -> FakeStructuredProvider:
    provider = FakeStructuredProvider()
    monkeypatch.setattr(
        structured_service,
        "_get_structured_provider",
        lambda *_args, **_kwargs: provider,
    )
    return provider


def _create_record_with_fields(user: UserContext, fields: list[tuple[str, str]]) -> dict:
    knowledge_db = create_knowledge_db(KnowledgeDbCreate(name="voice db"), user)
    knowledge = create_knowledge(
        knowledge_db["id"],
        KnowledgeCreate(
            name="音声インタビュー",
            targetEquipment="圧入機A",
            interviewPlan=InterviewPlan(
                profile="fixed_form",
                modelId="global.openai.gpt-5.6-terra",
            ),
        ),
        user,
    )
    for index, (name, input_type) in enumerate(fields, start=1):
        create_field(
            knowledge["id"],
            KnowledgeFieldCreate(
                name=name,
                inputType=input_type,
                required=True,
                askByAi=True,
                retrievalPolicy="never",
                aiQuestionExamples=[f"{name}を教えてください。"],
                displayOrder=index,
            ),
            user,
        )
    return create_record(
        knowledge["id"],
        RecordCreate(title="音声インタビュー"),
        user,
    )


def _create_record_with_field(user: UserContext, *, interview_locale: str | None = None) -> dict:
    knowledge_db = create_knowledge_db(KnowledgeDbCreate(name="voice db"), user)
    knowledge = create_knowledge(
        knowledge_db["id"],
        KnowledgeCreate(
            name="音声インタビュー",
            targetEquipment="圧入機A",
            interviewPlan=InterviewPlan(profile="fixed_form", modelId="global.openai.gpt-5.6-terra"),
        ),
        user,
    )
    create_field(
        knowledge["id"],
        KnowledgeFieldCreate(
            name="現象",
            inputType="long_text",
            required=True,
            askByAi=True,
            retrievalPolicy="never",
            aiQuestionExamples=["どのような現象が起きていますか？"],
            displayOrder=1,
        ),
        user,
    )
    return create_record(
        knowledge["id"],
        RecordCreate(title="朝一の荷重ばらつき", interviewLocale=interview_locale),
        user,
    )


def test_create_get_stop_and_atomically_claim_initial_reply() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)

    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    assert session["provider"] == "transcribe_polly"
    assert session["currentQuestionId"] == "q-001"
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


def test_voice_turn_uses_structured_interpreter_and_advances_once() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_fields(user, [("氏名", "short_text"), ("担当", "short_text")])
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])
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


def test_no_answer_gets_one_neutral_probe_then_advances() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_fields(user, [("転機", "long_text"), ("強み", "long_text")])
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    mark_internal_initial_reply_sent(session["id"])

    first = create_internal_voice_turn(session["id"], VoiceTurnCreate(transcript="あまり覚えていません。特に大きな転機はなかったと思います。"))
    first_result = process_internal_voice_turn(session["id"], first["id"])
    assert first_result["action"] == "ask_structured"
    assert "大きな転機でなくても" in first_result["text"]

    second = create_internal_voice_turn(session["id"], VoiceTurnCreate(transcript="特にありません。"))
    second_result = process_internal_voice_turn(session["id"], second["id"])
    state = store.get("interview_states", f"interview-state-{record['id']}")
    first_field_id = state["askedQuestions"][0]["fieldId"]
    assert second_result["questionId"] == "q-003"
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

    assert second["responseId"] == first["responseId"]
    assert second["text"] == first["text"]
    assert len(assistant_messages) == 1


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


def test_voice_turn_intent_classification_uses_structured_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)

    class IntentProvider:
        def request_structured_output(self, **_: object) -> dict[str, str]:
            return {"turnType": "CONTROL"}

    monkeypatch.setattr(voice_interview_service, "BedrockResponsesStructuredProvider", IntentProvider)

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


def test_cancel_before_processing_prevents_late_commit() -> None:
    user = DEV_TOKENS["dev-manager"]
    record = _create_record_with_field(user)
    session = create_record_voice_session(record["id"], VoiceSessionCreate(), user)
    turn = create_internal_voice_turn(
        session["id"],
        VoiceTurnCreate(transcript="回答", clientTurnId="client-1", expectedStateVersion=1),
    )

    cancelled = cancel_internal_voice_turn(
        session["id"],
        VoiceTurnCancel(clientTurnId="client-1", expectedStateVersion=1),
    )

    assert cancelled["cancelled"] is True
    with pytest.raises(HTTPException) as exc_info:
        process_internal_voice_turn(session["id"], turn["id"])
    assert exc_info.value.status_code == 409
