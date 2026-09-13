from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections.abc import Mapping
from dataclasses import replace

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
from ai_interviewer_api.agents.interview_knowledge.fast_interpreter.schemas import (
    FastAnswerAssessment,
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
from ai_interviewer_api.services.conversation_policy import CanonicalIntentDecision
from ai_interviewer_api.services.interview_state_transition import (
    apply_background_state_proposal,
    commit_interview_state,
)


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


@pytest.fixture(autouse=True)
def stub_canonical_router(monkeypatch: pytest.MonkeyPatch) -> None:
    def resolve(*, utterance: str, pending_confirmation: bool, **_: object) -> CanonicalIntentDecision:
        normalized = utterance.strip()
        if pending_confirmation and normalized in {"はい", "大丈夫です。", "大丈夫です", "合っています"}:
            dialogue_act = "CONFIRMATION"
        elif pending_confirmation and normalized in {"違います。", "違います", "いいえ"}:
            dialogue_act = "REJECTION"
        elif any(token in normalized for token in ("どの範囲", "何を聞", "どういう意味")):
            dialogue_act = "QUESTION_TO_ASSISTANT"
        elif any(token in normalized for token in ("えーっと", "えっと", "ちょっと待")):
            dialogue_act = "HESITATION"
        elif normalized.startswith(("訂正", "違う", "間違")):
            dialogue_act = "CORRECTION"
        else:
            dialogue_act = "ANSWER"
        return CanonicalIntentDecision(
            dialogueAct=dialogue_act,
            latency_ms=0.1,
            provider="test",
        )

    monkeypatch.setattr(voice_interview_service, "resolve_canonical_intent", resolve)


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


def _set_pending_voice_field_candidate(record_id: str, candidate: str) -> None:
    state = store.get("interview_states", f"interview-state-{record_id}")
    assert state is not None
    question_id = state["currentQuestionId"]
    question = next(
        item for item in state["askedQuestions"] if item["questionId"] == question_id
    )
    field_id = question["fieldId"]
    field_state = state["fieldStates"][field_id]
    required_items = field_state["questionPlan"]["requiredItems"]
    candidate_items = [
        {
            "itemId": item["itemId"],
            "value": candidate,
            "evidenceTranscriptIds": ["candidate-message"],
        }
        for item in required_items
    ]
    field_state.update(
        {
            "answerState": "AWAITING_CONFIRMATION",
            "answerResolution": "CONFIRM_REQUIRED",
            "status": "asking",
            "candidateAnswer": candidate,
            "candidateSource": "user_statement",
            "candidateItems": candidate_items,
            "capturedItems": candidate_items,
            "capturedItemIds": [item["itemId"] for item in candidate_items],
            "missingRequiredItemIds": [],
        }
    )
    state["lastTentativeTarget"] = {"targetType": "field", "targetId": field_id}
    store.upsert("interview_states", state)


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
