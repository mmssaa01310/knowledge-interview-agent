"""
Role:
    音声インタビューAPIのsession・turn境界。

Summary:
    音声turnを共通回答Processorへ接続し、評価期限と永続化を調停する。

Relations:
    Uses InterviewAnswerProcessor and voice repositories. Used by internal voice routes.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from time import monotonic, time
from typing import Any, Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from ai_interviewer_api.agents.common.strands_runtime import invoke_voice_bedrock_text
from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.core.config import settings
from ai_interviewer_api.core.interview_configuration import require_interview_configuration
from ai_interviewer_api.core.interview_locale import (
    InterviewLocale,
    interview_language_instruction,
    localized_interview_fallbacks,
    resolve_interview_locale,
)
from ai_interviewer_api.core.permissions import require_record_action
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.models.domain import VoiceSession, VoiceTurn
from ai_interviewer_api.models.interview_plan import CapturedInterviewItem
from ai_interviewer_api.repositories import (
    voice_session_repository,
    voice_turn_repository,
)
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.common import get_scoped_item
from ai_interviewer_api.schemas.voice import (
    AssistantEventCreate,
    ConnectionEventCreate,
    VoiceSessionCreate,
    VoiceTurnIntentCreate,
    VoiceTurnCancel,
    VoiceTurnCreate,
)
from ai_interviewer_api.services.ai_interview import (
    _dialogue_response_text,
    _field_retrieval_policy,
    _persist_interview_state,
    generate_interview_reply,
    get_interview_state_snapshot,
)
from ai_interviewer_api.agents.interview_knowledge.service import (
    generate_structured_interview_result,
    is_structured_interview_enabled,
)
from ai_interviewer_api.services.dialogue_interpreter import (
    DialogueAct,
    DialogueInterpretation,
    should_route_to_answer_processor,
)
from ai_interviewer_api.services.record_lifecycle import sync_record_status_after_interview
from ai_interviewer_api.services.interview_answer_processor import (
    AnswerEvaluation,
    ConfirmationEvaluation,
    InterviewAnswerProcessor,
    compose_record_answer,
)
from ai_interviewer_api.services.interview_confirmation import (
    is_unambiguous_confirmation,
)
from ai_interviewer_api.services.voice_evaluation_deadline import (
    VoiceEvaluationDeadlineExceeded,
    VoiceEvaluationRequest,
    run_with_evaluation_deadline,
)

logger = logging.getLogger(__name__)

INITIAL_VOICE_GREETING = "これからインタビューを開始します。"
VOICE_CONFIRM_PREFIX = "確認します。"
ANSWER_STATE_UNANSWERED = "UNANSWERED"
ANSWER_STATE_CANDIDATE_PENDING = "CANDIDATE_PENDING"
ANSWER_STATE_AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
ANSWER_STATE_CONFIRMED = "CONFIRMED"
VOICE_ANSWER_EVALUATION_DEADLINE_SECONDS = min(
    settings.voice_answer_evaluation_deadline_seconds,
    1.5,
)


@dataclass(frozen=True)
class VoiceAnswerEvaluation:
    decision: Literal["CONFIRMABLE", "NEEDS_MORE_INFORMATION", "NOT_ANSWER", "UNCLEAR"]
    normalized_answer: str
    is_relevant: bool | None
    is_sufficient: bool
    missing_information: list[str]
    follow_up_question: str | None
    evidence_transcript_ids: list[str]
    record_answer: str = ""
    confirmation_question: str | None = None
    retrieval_needed: bool = False
    evaluation_reason: str | None = None
    evaluation_degraded: bool = False
    degraded_reason: str | None = None
    captured_items: list[dict[str, Any]] = field(default_factory=list)
    answer_disposition: Literal["ANSWERED", "UNCLEAR", "IRRELEVANT"] | None = None
    evaluation_status: Literal["OK", "EVALUATION_ERROR"] = "OK"


@dataclass(frozen=True)
class VoiceConfirmationEvaluation:
    outcome: Literal["CONFIRM", "REVISE_WITH_CONTENT", "REJECT_WITHOUT_CONTENT", "UNCLEAR"]
    revised_answer: str | None = None
    record_answer: str | None = None
    confirmation_question: str | None = None
    clarification_question: str | None = None
    captured_items: list[dict[str, Any]] = field(default_factory=list)
    evaluation_status: Literal["OK", "EVALUATION_ERROR"] = "OK"


def _normalize_captured_items(value: Any) -> list[Any]:
    """Keep only structurally valid optional extraction items.

    Plain-text model responses occasionally collapse a one-item list into an
    object. Normalize that container shape, but never invent a missing item
    value; malformed items are ignored and the backend continues with the
    validated answer fields.
    """
    if value is None:
        return []
    values = [value] if isinstance(value, dict) else value
    if not isinstance(values, list):
        return []
    return [
        item
        for item in values
        if isinstance(item, CapturedInterviewItem)
        or (
            isinstance(item, dict)
            and isinstance(item.get("itemId"), str)
            and isinstance(item.get("value"), str)
        )
    ]


class VoiceAnswerEvaluationOutput(BaseModel):
    decision: Literal["CONFIRMABLE", "NEEDS_MORE_INFORMATION", "NOT_ANSWER", "UNCLEAR"]
    normalized_answer: str = ""
    is_relevant: bool = False
    is_sufficient: bool = False
    missing_information: list[str] = Field(default_factory=list)
    follow_up_question: str | None = None
    confirmation_question: str | None = None
    evidence_transcript_ids: list[str] = Field(default_factory=list)
    record_answer: str | None = None
    retrieval_needed: bool = False
    evaluation_reason: str | None = None
    captured_items: list[CapturedInterviewItem] = Field(default_factory=list)
    answer_disposition: Literal["ANSWERED", "UNCLEAR", "IRRELEVANT"] | None = None
    evaluation_status: Literal["OK", "EVALUATION_ERROR"] = "OK"

    @field_validator("captured_items", mode="before")
    @classmethod
    def normalize_captured_items(cls, value: Any) -> Any:
        return _normalize_captured_items(value)


class VoiceConfirmationEvaluationOutput(BaseModel):
    outcome: Literal["CONFIRM", "REVISE_WITH_CONTENT", "REJECT_WITHOUT_CONTENT", "UNCLEAR"]
    revised_answer: str | None = None
    record_answer: str | None = None
    confirmation_question: str | None = None
    clarification_question: str | None = None
    captured_items: list[CapturedInterviewItem] = Field(default_factory=list)

    @field_validator("captured_items", mode="before")
    @classmethod
    def normalize_captured_items(cls, value: Any) -> Any:
        return _normalize_captured_items(value)


class VoiceTurnEvaluationOutput(BaseModel):
    """Compact plain-text contract for one normal voice turn."""

    turnType: Literal["ANSWER", "CONTROL"] = "ANSWER"
    act: DialogueAct = "ANSWER"
    responseText: str | None = None
    reason: str | None = None
    decision: Literal["CONFIRMABLE", "NEEDS_MORE_INFORMATION", "NOT_ANSWER", "UNCLEAR"] = "UNCLEAR"
    normalizedAnswer: str = ""
    isRelevant: bool = False
    isSufficient: bool = False
    missingInformation: list[str] = Field(default_factory=list)
    followUpQuestion: str | None = None
    confirmationQuestion: str | None = None
    evidenceTranscriptIds: list[str] = Field(default_factory=list)
    recordAnswer: str | None = None
    retrievalNeeded: bool = False
    evaluationReason: str | None = None
    capturedItems: list[CapturedInterviewItem] = Field(default_factory=list)
    answerDisposition: Literal["ANSWERED", "UNCLEAR", "IRRELEVANT"] | None = None

    @field_validator("capturedItems", mode="before")
    @classmethod
    def normalize_captured_items(cls, value: Any) -> Any:
        return _normalize_captured_items(value)


@dataclass(frozen=True)
class VoiceTurnProcessResult:
    turn_id: str
    response_id: str
    text: str
    action: str
    question_id: str | None
    state_version: int
    voice_session: dict
    voice_turn: dict
    retrieval_policy: str | None = None
    retrieval_executed: bool = False

    def model_dump(self) -> dict:
        return {
            "turnId": self.turn_id,
            "responseId": self.response_id,
            "text": self.text,
            "action": self.action,
            "questionId": self.question_id,
            "stateVersion": self.state_version,
            "retrievalPolicy": self.retrieval_policy,
            "retrievalExecuted": self.retrieval_executed,
            "voiceSession": self.voice_session,
            "voiceTurn": self.voice_turn,
        }


class VoiceTurnIntentOutput(BaseModel):
    turnType: Literal["ANSWER", "CONTROL"]


_VOICE_TURN_INTENT_SYSTEM_PROMPT = """
あなたは音声インタビューの発話意図分類器です。
確定したユーザー発話を、現在の質問への回答と、インタビューの進行を制御する発話のどちらかに分類してください。

ANSWERは、現在の質問への回答、確認への肯定・否定、訂正、追加情報、または質問内容に関する返答です。
CONTROLは、現在の質問への回答をせず、インタビューの開始・終了・一時停止・再開・音声操作など、会話の進行自体を操作する主意図です。
発話の単語や固定フレーズの一致ではなく、現在の質問と会話状態を踏まえた意味で判断してください。
確認待ちの「はい」「違います」などは、確認に対する回答なのでANSWERです。
結果は指定されたJSONオブジェクトだけで返してください。Markdownや説明文は不要です。
""".strip()


_VOICE_TURN_EVALUATION_SYSTEM_PROMPT = """
あなたは音声インタビューの判定器です。質問・項目・状態・発話の意味で判断し、JSONオブジェクト1個だけを返してください。
固定キーワードだけで判断しないでください。
turnTypeはANSWERまたはCONTROL。CONTROLは開始・終了・一時停止・再開・音声操作など回答以外の進行操作です。
actはANSWER, CLARIFICATION_REQUEST, QUESTION_TO_ASSISTANT, CONVERSATION_REQUEST, BACKCHANNEL, HESITATION, CORRECTION, REJECTION, CONFIRMATION, IRRELEVANT, OTHERのいずれかです。
回答ならdecisionはCONFIRMABLE, NEEDS_MORE_INFORMATION, NOT_ANSWER, UNCLEARのいずれかです。
recordAnswer等の文章値は文字列またはnull、isRelevant/isSufficient/retrievalNeededはboolean、missingInformation/capturedItemsは配列です。
capturedItemsは[{"itemId":"...","value":"..."}]形式で、取得できなければ[]です。questionPlanの不足・完了・正式確定はbackendが判断します。
回答処理へ渡すactはANSWER、確認待ちの訂正はCORRECTION、否定はREJECTION、承認はCONFIRMATIONです。それ以外はresponseTextに短い返答を入れてください。
JSON以外、Markdown、コードフェンスは禁止です。例: {"turnType":"ANSWER","act":"ANSWER","decision":"CONFIRMABLE","recordAnswer":"山田","isRelevant":true,"isSufficient":true,"missingInformation":[],"capturedItems":[],"confirmationQuestion":"山田さんでよろしいですか？"}
""".strip()


_VOICE_CONFIRMATION_TEXT_SYSTEM_PROMPT = """
あなたは音声インタビューの確認返答判定器です。
候補回答に対するユーザー返答そのものの意味を判断し、次の4つのoutcomeのいずれかを含むJSONオブジェクトだけを返してください。

- CONFIRM: 候補をそのまま承認
- REVISE_WITH_CONTENT: 具体的な訂正内容がある
- REJECT_WITHOUT_CONTENT: 候補を否定したが訂正内容がない
- UNCLEAR: 判断不能、質問、曖昧な返答

ユーザー返答が候補を肯定していればCONFIRMです。「はい」「そうです」「正しいです」は、候補を肯定する文脈ならCONFIRMにしてください。
REVISE_WITH_CONTENTではrecord_answerとrevised_answerに訂正後の回答内容だけを入れてください。
CONFIRMではrecord_answerに候補回答を入れてください。REJECT_WITH_CONTENTという値は使わないでください。
訂正・候補の意味を変えず、固定キーワード一致ではなく文脈で判断してください。
UNCLEARではclarification_questionに、ユーザーが次に何を言えばよいか分かる短い自然な案内を入れてください。
質問や確認文への質問なら、候補を確定せず、clarification_questionで自然に説明してください。
captured_itemsは必ず配列で、各要素はitemIdとvalueを持つオブジェクトにしてください。
例: 候補=「山田」、返答=「はい」なら{"outcome":"CONFIRM","record_answer":"山田"}。
例: 候補=「山田」、返答=「違います、佐藤です」ならREVISE_WITH_CONTENTです。
JSON以外の文章、Markdown、コードフェンスは返さないでください。出力は必要なキーだけの短いJSONにしてください。
""".strip()


def create_voice_session(record_id: str, payload: VoiceSessionCreate, user: UserContext) -> dict:
    started_at = monotonic()
    record = get_scoped_item("records", record_id, user, "record_not_found")
    require_record_action(record, user, "answer")
    knowledge = get_scoped_item("knowledges", record["knowledgeId"], user, "knowledge_not_found")
    require_interview_configuration(knowledge)
    interview_locale = resolve_interview_locale(record, knowledge)
    if not _has_voice_interview_fields(record, user):
        raise HTTPException(status_code=409, detail="voice_session_missing_questions")
    initial_reply = _initialize_initial_question(record, user)
    snapshot = get_interview_state_snapshot(record, user)
    interview_state = snapshot.get("interviewState", {})
    current_question_id = interview_state.get("currentQuestionId")
    if current_question_id is None:
        raise HTTPException(status_code=409, detail="voice_session_missing_current_question")
    session = VoiceSession(
        tenantId=user.tenant_id,
        createdByUserId=user.user_id,
        updatedByUserId=user.user_id,
        ownerUserId=user.user_id,
        ownerRole=user.role,
        recordId=record_id,
        provider=payload.provider,
        interviewLocale=interview_locale,
        currentQuestionId=current_question_id,
        initialReplyText=initial_reply,
        initialQuestionId=current_question_id if initial_reply else None,
        initialReplyStatus="pending" if initial_reply else None,
        stateVersion=1 if initial_reply else 0,
        startedAt=utc_now(),
    ).model_dump()
    voice_session_repository.save(session)
    logger.info(
        "voice_session_created voice_session_id=%s record_id=%s provider=%s initial_question_id=%s initial_reply_status=%s elapsed_ms=%s",
        session["id"],
        record_id,
        payload.provider,
        current_question_id,
        session.get("initialReplyStatus"),
        round((monotonic() - started_at) * 1000),
    )
    return session


def get_voice_session(voice_session_id: str, user: UserContext) -> dict:
    session = _get_voice_session_for_user(voice_session_id, user)
    return session


def classify_voice_turn_intent(
    voice_session_id: str,
    payload: VoiceTurnIntentCreate,
) -> dict:
    session = _get_voice_session_for_internal_use(voice_session_id)
    _ensure_session_accepts_turns(session)
    if (
        payload.expectedStateVersion is not None
        and int(session.get("stateVersion") or 0) != payload.expectedStateVersion
    ):
        raise HTTPException(status_code=409, detail="turn_state_conflict")
    transcript = payload.transcript.strip()
    if not transcript:
        raise HTTPException(status_code=422, detail="turn_transcript_required")
    interview_state = store.get("interview_states", f"interview-state-{session['recordId']}") or {}
    question_id = payload.answerToQuestionId or session.get("currentQuestionId")
    current_question = _find_question_by_id(interview_state, question_id) if question_id else None
    field_id = current_question.get("fieldId") if current_question else None
    field_state = (
        _ensure_voice_field_state(interview_state, field_id)
        if field_id
        else {}
    )
    prompt = "\n".join(
        [
            "current_question:",
            f"- id: {question_id or 'none'}",
            f"- text: {str((current_question or {}).get('text') or '').strip() or 'none'}",
            "current_answer_state:",
            f"- answer_state: {field_state.get('answerState') or 'UNANSWERED'}",
            f"- candidate_answer: {str(field_state.get('candidateAnswer') or '').strip() or 'none'}",
            "user_transcript:",
            transcript,
        ]
    )
    try:
        result = _run_voice_json_output(
            system_prompt=_VOICE_TURN_INTENT_SYSTEM_PROMPT,
            prompt=prompt,
            output_model=VoiceTurnIntentOutput,
            max_tokens=48,
        )
    except Exception as exc:
        logger.exception(
            "voice_turn_intent_classification_failed voice_session_id=%s question_id=%s error_type=%s",
            voice_session_id,
            question_id,
            exc.__class__.__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="voice_turn_intent_classification_failed",
        ) from exc
    return {"turnType": result.turnType}


def stop_voice_session(voice_session_id: str, user: UserContext) -> dict:
    session = _get_voice_session_for_user(voice_session_id, user)
    session["status"] = "stopped"
    session["connectionStatus"] = "closed"
    session["stoppedAt"] = utc_now()
    session["updatedByUserId"] = user.user_id
    session["updatedAt"] = utc_now()
    voice_session_repository.save(session)
    return session


def create_voice_turn(voice_session_id: str, payload: VoiceTurnCreate) -> dict:
    session = _get_voice_session_for_internal_use(voice_session_id)
    _ensure_session_accepts_turns(session)
    if (
        payload.clientTurnId
        and payload.clientTurnId in session.get("cancelledClientTurnIds", [])
    ):
        raise HTTPException(status_code=409, detail="turn_cancelled")
    if payload.clientTurnId:
        existing = next(
            (
                item
                for item in voice_turn_repository.list_for_session(
                    session["tenantId"],
                    voice_session_id,
                )
                if item.get("clientTurnId") == payload.clientTurnId
            ),
            None,
        )
        if existing is not None:
            if (
                existing.get("transcript") == payload.transcript.strip()
                and existing.get("expectedStateVersion") == payload.expectedStateVersion
            ):
                return existing
            raise HTTPException(status_code=409, detail="turn_duplicate_conflict")
    if (
        payload.expectedStateVersion is not None
        and int(session.get("stateVersion") or 0) != payload.expectedStateVersion
    ):
        raise HTTPException(status_code=409, detail="turn_state_conflict")
    interview_state = store.get("interview_states", f"interview-state-{session['recordId']}") or {}
    record = store.get("records", session["recordId"]) or {}
    knowledge = store.get("knowledges", record.get("knowledgeId")) or {}
    structured_mode = is_structured_interview_enabled(knowledge)
    turn_type = payload.turnType
    question_id = payload.answerToQuestionId
    question = None
    field_id = None
    processing_mode = "control"
    if turn_type == "ANSWER":
        # Keep the legacy voice client default, but persist the resolved target
        # so every stored answer turn has an explicit question scope.
        question_id = question_id or session.get("currentQuestionId")
        if question_id != session.get("currentQuestionId"):
            raise HTTPException(status_code=409, detail="turn_question_conflict")
        question = _find_question_by_id(interview_state, question_id)
        field_id = question.get("fieldId") if question else None
        if not question_id or (not field_id and not structured_mode):
            raise HTTPException(status_code=409, detail="voice_turn_missing_target_field")
        if structured_mode:
            processing_mode = "structured_interpretation"
        else:
            field_state = _ensure_voice_field_state(interview_state, field_id)
            processing_mode = (
                "confirmation_reply"
                if field_state.get("answerState") == ANSWER_STATE_AWAITING_CONFIRMATION
                and field_state.get("pendingQuestionId") == question_id
                and field_state.get("pendingFieldId") == field_id
                else "answer_evaluation"
            )
    turn = VoiceTurn(
        tenantId=session["tenantId"],
        createdByUserId=session["ownerUserId"] or session["createdByUserId"],
        updatedByUserId=session["ownerUserId"] or session["updatedByUserId"],
        ownerUserId=session["ownerUserId"],
        voiceSessionId=voice_session_id,
        recordId=session["recordId"],
        sequence=int(session.get("lastTurnSequence") or 0) + 1,
        transcript=payload.transcript.strip(),
        turnType=turn_type,
        clientTurnId=payload.clientTurnId,
        expectedStateVersion=payload.expectedStateVersion,
        answerToQuestionId=question_id,
        answerToFieldId=field_id,
        processingMode=processing_mode,
        startedAtMs=payload.startedAtMs,
        endedAtMs=payload.endedAtMs,
    ).model_dump()
    voice_turn_repository.save(turn)
    session["lastTurnSequence"] = turn["sequence"]
    session["updatedAt"] = utc_now()
    voice_session_repository.save(session)
    return turn


def cancel_voice_turn(voice_session_id: str, payload: VoiceTurnCancel) -> dict:
    session = _get_voice_session_for_internal_use(voice_session_id)
    turn = next(
        (
            item
            for item in voice_turn_repository.list_for_session(
                session["tenantId"],
                voice_session_id,
            )
            if item.get("clientTurnId") == payload.clientTurnId
        ),
        None,
    )
    if turn is None:
        current_version = int(session.get("stateVersion") or 0)
        if current_version != payload.expectedStateVersion:
            raise HTTPException(status_code=409, detail="turn_state_conflict")
        cancelled_ids = session.setdefault("cancelledClientTurnIds", [])
        if payload.clientTurnId not in cancelled_ids:
            cancelled_ids.append(payload.clientTurnId)
        session["stateVersion"] = current_version + 1
        session["updatedAt"] = utc_now()
        voice_session_repository.save(session)
        return {
            "cancelled": True,
            "stateVersion": session["stateVersion"],
            "turnId": None,
        }
    lifecycle_status = _voice_turn_lifecycle_status(turn)
    if lifecycle_status == "COMMITTED":
        raise HTTPException(status_code=409, detail="turn_already_committed")
    if lifecycle_status not in {"RECEIVED", "EVALUATING"}:
        raise HTTPException(
            status_code=409,
            detail=f"turn_not_cancellable_{lifecycle_status.lower()}",
        )
    current_version = int(session.get("stateVersion") or 0)
    if current_version != payload.expectedStateVersion:
        raise HTTPException(status_code=409, detail="turn_state_conflict")
    base_state = turn.get("baseInterviewState")
    _restore_cancelled_turn_artifacts(turn, session)
    turn["processingStatus"] = "cancelled"
    turn["lifecycleStatus"] = "CANCELLED"
    turn["updatedAt"] = utc_now()
    voice_turn_repository.save(turn)
    restored_state = base_state if isinstance(base_state, dict) else {}
    session["currentQuestionId"] = restored_state.get(
        "currentQuestionId",
        turn.get("answerToQuestionId"),
    )
    session["status"] = "active"
    cancelled_ids = session.setdefault("cancelledClientTurnIds", [])
    if payload.clientTurnId not in cancelled_ids:
        cancelled_ids.append(payload.clientTurnId)
    session["stateVersion"] = current_version + 1
    session["updatedAt"] = utc_now()
    voice_session_repository.save(session)
    return {
        "cancelled": True,
        "stateVersion": session["stateVersion"],
        "turnId": turn["id"],
    }


def get_internal_voice_session(voice_session_id: str) -> dict:
    return _get_voice_session_for_internal_use(voice_session_id)


def mark_initial_reply_sent(voice_session_id: str) -> dict:
    session = _get_voice_session_for_internal_use(voice_session_id)
    if session.get("initialReplyText"):
        session["initialReplyStatus"] = "sent"
        session["initialReplySentAt"] = utc_now()
        session["updatedAt"] = utc_now()
        voice_session_repository.save(session)
    return session


def mark_initial_reply_failed(voice_session_id: str, *, retryable: bool = True) -> dict:
    session = _get_voice_session_for_internal_use(voice_session_id)
    if session.get("initialReplyText") and session.get("initialReplyStatus") != "sent":
        session["initialReplyStatus"] = "failed_retryable" if retryable else "failed_terminal"
        session["updatedAt"] = utc_now()
        voice_session_repository.save(session)
    return session


def claim_initial_reply(voice_session_id: str) -> dict:
    session = _get_voice_session_for_internal_use(voice_session_id)
    initial_reply_text = session.get("initialReplyText")
    if not initial_reply_text:
        return {"claimed": False, "reason": "missing_initial_reply"}
    status = session.get("initialReplyStatus")
    if status == "sent":
        return {"claimed": False, "reason": "already_sent"}
    if status == "sending":
        return {"claimed": False, "reason": "already_sending"}
    if status == "failed_terminal":
        return {"claimed": False, "reason": "failed_terminal"}
    if session.get("initialQuestionId") != session.get("currentQuestionId"):
        return {"claimed": False, "reason": "question_mismatch"}
    if session.get("status") in {"stopped", "completed"}:
        return {"claimed": False, "reason": f"session_{session.get('status')}"}

    session["initialReplyStatus"] = "sending"
    session["updatedAt"] = utc_now()
    voice_session_repository.save(session)
    return {
        "claimed": True,
        "initialReplyText": initial_reply_text,
        "initialQuestionId": session.get("initialQuestionId"),
    }


def process_voice_turn(voice_session_id: str, turn_id: str) -> dict:
    process_started_at = monotonic()
    session = _get_voice_session_for_internal_use(voice_session_id)
    _ensure_session_accepts_turns(session)
    turn = _get_voice_turn_for_session(turn_id, session)
    lifecycle_status = _voice_turn_lifecycle_status(turn)
    if lifecycle_status == "CANCELLED":
        raise HTTPException(status_code=409, detail="turn_cancelled")
    if lifecycle_status == "COMMITTED":
        return _build_process_result(session, turn).model_dump()
    expected_state_version = turn.get("expectedStateVersion")
    if (
        expected_state_version is not None
        and int(session.get("stateVersion") or 0) != int(expected_state_version)
    ):
        raise HTTPException(status_code=409, detail="turn_state_conflict")

    turn["processingStatus"] = "processing"
    turn["lifecycleStatus"] = "EVALUATING"
    turn["updatedAt"] = utc_now()
    voice_turn_repository.save(turn)

    user = _build_user_context_from_session(session)
    record = get_scoped_item("records", session["recordId"], user, "record_not_found")
    user_message = _save_voice_user_message(record, turn, user)
    interview_state = store.get("interview_states", f"interview-state-{record['id']}")
    if interview_state is None:
        snapshot = get_interview_state_snapshot(record, user)
        interview_state = snapshot.get("interviewState", {})
    turn["baseInterviewState"] = deepcopy(interview_state)
    voice_turn_repository.save(turn)
    if is_structured_interview_enabled(store.get("knowledges", record.get("knowledgeId")) or {}):
        return _process_structured_voice_turn(
            session=session,
            turn=turn,
            record=record,
            user=user,
            interview_state=interview_state,
        )
    if turn.get("turnType") == "CONTROL":
        try:
            return _commit_control_turn(session=session, turn=turn, interview_state=interview_state)
        except Exception:
            latest_turn = voice_turn_repository.get(turn_id)
            if latest_turn is not None and latest_turn.get("processingStatus") == "cancelled":
                _restore_cancelled_turn_artifacts(latest_turn, session)
            elif latest_turn is not None:
                latest_turn["processingStatus"] = "failed"
                latest_turn["lifecycleStatus"] = "RECEIVED"
                latest_turn["updatedAt"] = utc_now()
                voice_turn_repository.save(latest_turn)
            raise
    current_question = _find_question_by_id(interview_state, turn.get("answerToQuestionId"))
    current_field_id = turn.get("answerToFieldId")
    question_type = current_question.get("questionType") if current_question else None
    if not current_question or (not current_field_id and question_type != "structured"):
        raise HTTPException(status_code=409, detail="voice_turn_missing_target_field")
    field_state = _ensure_voice_field_state(interview_state, current_field_id)
    logger.info(
        "voice_user_message_saved voice_session_id=%s turn_id=%s question_id=%s state_version=%s",
        voice_session_id,
        turn_id,
        turn.get("answerToQuestionId"),
        session.get("stateVersion"),
    )
    current_field = _resolve_field(record, current_field_id, user) or {
        "id": current_field_id,
        "name": current_field_id,
    }

    try:
        logger.info(
            "voice_interview_process_started voice_session_id=%s turn_id=%s question_id=%s state_version=%s",
            voice_session_id,
            turn_id,
            turn.get("answerToQuestionId"),
            session.get("stateVersion"),
        )
        confirmation_evaluation: VoiceConfirmationEvaluation | None = None
        answer_evaluation: VoiceAnswerEvaluation | None = None
        if field_state.get("answerState") == ANSWER_STATE_AWAITING_CONFIRMATION:
            evaluation_request = VoiceEvaluationRequest(
                voice_session_id=session["id"],
                voice_turn_id=turn["id"],
                question_id=turn.get("answerToQuestionId"),
                field_id=current_field_id,
                evaluation_request_id=f"voice-confirmation-{uuid4().hex}",
                state_version=int(session.get("stateVersion") or 0),
                deadline_at=monotonic() + VOICE_ANSWER_EVALUATION_DEADLINE_SECONDS,
            )
            try:
                confirmation_evaluation = run_with_evaluation_deadline(
                    lambda: _evaluate_confirmation_response(
                        current_question=current_question,
                        candidate_answer=str(field_state.get("candidateAnswer") or "").strip(),
                        user_reply=str(turn.get("transcript") or ""),
                        field_state=field_state,
                        interview_locale=resolve_interview_locale(session, {}),
                    ),
                    request=evaluation_request,
                )
            except Exception as exc:
                logger.exception(
                    "voice_confirmation_evaluation_failed voice_session_id=%s turn_id=%s question_id=%s error_type=%s",
                    session["id"],
                    turn["id"],
                    turn.get("answerToQuestionId"),
                    exc.__class__.__name__,
                )
                confirmation_evaluation = VoiceConfirmationEvaluation(
                    outcome="UNCLEAR",
                    clarification_question=_localized_confirmation_fallback(
                        resolve_interview_locale(session, {})
                    ),
                    evaluation_status="EVALUATION_ERROR",
                )
            interpretation = DialogueInterpretation(act="CONFIRMATION")
        else:
            evaluation_request = VoiceEvaluationRequest(
                voice_session_id=session["id"],
                voice_turn_id=turn["id"],
                question_id=turn.get("answerToQuestionId"),
                field_id=current_field_id,
                evaluation_request_id=f"voice-turn-{uuid4().hex}",
                state_version=int(session.get("stateVersion") or 0),
                deadline_at=monotonic() + VOICE_ANSWER_EVALUATION_DEADLINE_SECONDS,
            )
            try:
                combined_evaluation = run_with_evaluation_deadline(
                    lambda: _evaluate_voice_turn_candidate(
                        transcript=str(turn.get("transcript") or ""),
                        current_question=current_question,
                        current_field=current_field,
                        field_state=field_state,
                        evidence_message_id=user_message["id"],
                        interview_locale=resolve_interview_locale(session, {}),
                    ),
                    request=evaluation_request,
                )
            except Exception as exc:
                logger.exception(
                    "voice_turn_evaluation_failed voice_session_id=%s turn_id=%s question_id=%s error_type=%s",
                    session["id"],
                    turn["id"],
                    turn.get("answerToQuestionId"),
                    exc.__class__.__name__,
                )
                answer_evaluation = VoiceAnswerEvaluation(
                    decision="UNCLEAR",
                    normalized_answer="",
                    is_relevant=None,
                    is_sufficient=False,
                    missing_information=[],
                    follow_up_question=_build_evaluation_fallback_prompt(
                        current_question,
                        resolve_interview_locale(session, {}),
                    ),
                    evidence_transcript_ids=[user_message["id"]],
                    evaluation_degraded=True,
                    degraded_reason=_voice_evaluation_degraded_reason(exc),
                    evaluation_status="EVALUATION_ERROR",
                )
                combined_evaluation = VoiceTurnEvaluation(
                    turn_type="ANSWER",
                    interpretation=DialogueInterpretation(act="ANSWER"),
                    answer_evaluation=answer_evaluation,
                )
            if combined_evaluation.turn_type == "CONTROL":
                turn["turnType"] = "CONTROL"
                turn["answerToQuestionId"] = None
                turn["answerToFieldId"] = None
                turn["processingMode"] = "control"
                turn["dialogueAct"] = combined_evaluation.interpretation.act
                voice_turn_repository.save(turn)
                user_message.update(
                    {
                        "turnType": "CONTROL",
                        "answerToQuestionId": None,
                        "answerToFieldId": None,
                        "questionType": None,
                        "dialogueAct": combined_evaluation.interpretation.act,
                    }
                )
                store.upsert("messages", user_message)
                return _commit_control_turn(
                    session=session,
                    turn=turn,
                    interview_state=interview_state,
                )
            interpretation = combined_evaluation.interpretation
            answer_evaluation = combined_evaluation.answer_evaluation
        user_message["dialogueAct"] = interpretation.act
        store.upsert("messages", user_message)
        turn["dialogueAct"] = interpretation.act
        voice_turn_repository.save(turn)
        if not should_route_to_answer_processor(
            interpretation,
            awaiting_confirmation=field_state.get("answerState") == ANSWER_STATE_AWAITING_CONFIRMATION,
        ):
            reply_text = _dialogue_response_text(
                interpretation,
                current_question,
                resolve_interview_locale(session, {}),
            )
            field_state["lastDialogueAct"] = interpretation.act
            field_state["lastDialogueResponse"] = reply_text
            interview_state["lastProcessedUserMessageId"] = user_message["id"]
            _persist_interview_state(interview_state, user)
            result_payload = {
                "replyText": reply_text,
                "action": "ask_follow_up",
                "questionId": turn.get("answerToQuestionId"),
                "retrievalPolicy": _field_retrieval_policy(current_field).value,
                "retrievalExecuted": False,
                "currentFieldId": current_field_id,
            }
        elif field_state.get("answerState") == ANSWER_STATE_AWAITING_CONFIRMATION:
            result_payload = _process_confirmation_turn(
                record=record,
                session=session,
                turn=turn,
                user=user,
                interview_state=interview_state,
                field_state=field_state,
                current_question=current_question,
                user_message=user_message,
                precomputed_confirmation=confirmation_evaluation,
            )
        else:
            result_payload = _process_candidate_turn(
                record=record,
                session=session,
                turn=turn,
                user=user,
                interview_state=interview_state,
                field_state=field_state,
                current_question=current_question,
                user_message=user_message,
                precomputed_evaluation=answer_evaluation,
            )

        reply_text = result_payload["replyText"]
        action = result_payload["action"]
        current_question_id = result_payload["questionId"]
        retrieval_policy = result_payload["retrievalPolicy"]
        retrieval_executed = result_payload["retrievalExecuted"]
        response_id = f"voice-response-{uuid4().hex[:12]}"
        latest_session = _get_voice_session_for_internal_use(voice_session_id)
        latest_turn = _get_voice_turn_for_session(turn_id, latest_session)
        if latest_turn.get("processingStatus") == "cancelled":
            raise HTTPException(status_code=409, detail="turn_cancelled")
        if (
            expected_state_version is not None
            and int(latest_session.get("stateVersion") or 0)
            != int(expected_state_version)
        ):
            raise HTTPException(status_code=409, detail="turn_state_conflict")
        next_state_version = int(session.get("stateVersion") or 0) + 1

        turn["processingStatus"] = "completed"
        turn["lifecycleStatus"] = "COMMITTED"
        turn["responseText"] = reply_text
        turn["action"] = action
        turn["stateVersion"] = next_state_version
        turn["responseId"] = response_id
        turn["questionId"] = current_question_id
        turn["retrievalPolicy"] = retrieval_policy
        turn["retrievalExecuted"] = retrieval_executed
        turn["updatedAt"] = utc_now()
        voice_turn_repository.save(turn)
        if (
            turn.get("processingMode") == "confirmation_reply"
            and action == "ask_confirmation"
        ):
            _mark_previous_turn_superseded(session, turn)

        session["currentQuestionId"] = current_question_id
        session["stateVersion"] = next_state_version
        session["status"] = "completed" if action == "finish" else session.get("status", "active")
        session["updatedAt"] = utc_now()
        voice_session_repository.save(session)
        _save_voice_assistant_message(
            session,
            AssistantEventCreate(
                eventType="assistant_transcript_final",
                responseId=response_id,
                transcript=reply_text,
                detail={
                    "turnId": turn_id,
                    "action": action,
                    "questionId": current_question_id,
                    "source": "interview_turn_commit",
                },
            ),
        )
        logger.info(
            "voice_interview_process_completed voice_session_id=%s turn_id=%s question_id=%s state_version=%s retrieval_policy=%s retrieval_executed=%s response_id=%s process_total_ms=%s",
            voice_session_id,
            turn_id,
            current_question_id,
            next_state_version,
            retrieval_policy,
            retrieval_executed,
            response_id,
            round((monotonic() - process_started_at) * 1000, 1),
        )
        return _build_process_result(session, turn).model_dump()
    except Exception:
        latest_turn = voice_turn_repository.get(turn_id)
        if latest_turn is not None and latest_turn.get("processingStatus") == "cancelled":
            _restore_cancelled_turn_artifacts(latest_turn, session)
        elif latest_turn is not None:
            latest_turn["processingStatus"] = "failed"
            latest_turn["lifecycleStatus"] = "RECEIVED"
            latest_turn["updatedAt"] = utc_now()
            voice_turn_repository.save(latest_turn)
        raise


def _process_structured_voice_turn(
    *,
    session: dict[str, Any],
    turn: dict[str, Any],
    record: dict[str, Any],
    user: UserContext,
    interview_state: dict[str, Any],
) -> dict:
    """Send a voice transcript through the same semantic engine as text."""

    if turn.get("turnType") == "CONTROL":
        return _commit_control_turn(session=session, turn=turn, interview_state=interview_state)

    try:
        knowledge = store.get("knowledges", record.get("knowledgeId")) or {}
        result = generate_structured_interview_result(
            record,
            knowledge,
            user,
            persist_assistant_messages=False,
        )
        sync_record_status_after_interview(record, result.get("status"), user)
        reply_text = str(result.get("reply") or "").strip()
        action = str(result.get("action") or "ask_structured").strip() or "ask_structured"
        question = result.get("question") if isinstance(result.get("question"), dict) else None
        question_id = question.get("questionId") if question else None
        response_id = f"voice-response-{uuid4().hex[:12]}"
        latest_session = _get_voice_session_for_internal_use(session["id"])
        latest_turn = _get_voice_turn_for_session(turn["id"], latest_session)
        if latest_turn.get("processingStatus") == "cancelled":
            raise HTTPException(status_code=409, detail="turn_cancelled")
        expected_state_version = turn.get("expectedStateVersion")
        if (
            expected_state_version is not None
            and int(latest_session.get("stateVersion") or 0) != int(expected_state_version)
        ):
            raise HTTPException(status_code=409, detail="turn_state_conflict")

        next_state_version = int(session.get("stateVersion") or 0) + 1
        turn["processingStatus"] = "completed"
        turn["lifecycleStatus"] = "COMMITTED"
        turn["responseText"] = reply_text
        turn["action"] = action
        turn["stateVersion"] = next_state_version
        turn["responseId"] = response_id
        turn["questionId"] = question_id
        turn["retrievalPolicy"] = "auto"
        turn["retrievalExecuted"] = False
        turn["updatedAt"] = utc_now()
        voice_turn_repository.save(turn)
        session["currentQuestionId"] = question_id
        session["stateVersion"] = next_state_version
        session["status"] = "completed" if action == "finish" else session.get("status", "active")
        session["updatedAt"] = utc_now()
        voice_session_repository.save(session)
        _save_voice_assistant_message(
            session,
            AssistantEventCreate(
                eventType="assistant_transcript_final",
                responseId=response_id,
                transcript=reply_text,
                detail={
                    "turnId": turn["id"],
                    "action": action,
                    "questionId": question_id,
                    "questionType": "structured",
                    "fieldId": question.get("fieldId") if question else None,
                    "targetType": question.get("targetType") if question else None,
                    "targetId": question.get("targetId") if question else None,
                    "source": "structured_interview_turn_commit",
                },
            ),
        )
        return _build_process_result(session, turn).model_dump()
    except Exception:
        latest_turn = voice_turn_repository.get(turn["id"])
        if latest_turn is not None and latest_turn.get("processingStatus") == "cancelled":
            _restore_cancelled_turn_artifacts(latest_turn, session)
        elif latest_turn is not None:
            latest_turn["processingStatus"] = "failed"
            latest_turn["lifecycleStatus"] = "RECEIVED"
            latest_turn["updatedAt"] = utc_now()
            voice_turn_repository.save(latest_turn)
        raise


def _commit_control_turn(
    *,
    session: dict,
    turn: dict,
    interview_state: dict[str, Any],
) -> dict:
    current_question_id = interview_state.get("currentQuestionId")
    reply_text = localized_interview_fallbacks(
        resolve_interview_locale(session, {})
    )["control_ack"]
    action = "ask_configured_field"
    response_id = f"voice-response-{uuid4().hex[:12]}"
    latest_session = _get_voice_session_for_internal_use(session["id"])
    latest_turn = _get_voice_turn_for_session(turn["id"], latest_session)
    if latest_turn.get("processingStatus") == "cancelled":
        raise HTTPException(status_code=409, detail="turn_cancelled")
    next_state_version = int(session.get("stateVersion") or 0) + 1
    turn["processingStatus"] = "completed"
    turn["lifecycleStatus"] = "COMMITTED"
    turn["responseText"] = reply_text
    turn["action"] = action
    turn["stateVersion"] = next_state_version
    turn["responseId"] = response_id
    turn["questionId"] = current_question_id
    turn["retrievalPolicy"] = None
    turn["retrievalExecuted"] = False
    turn["updatedAt"] = utc_now()
    voice_turn_repository.save(turn)
    session["stateVersion"] = next_state_version
    session["updatedAt"] = utc_now()
    voice_session_repository.save(session)
    _save_voice_assistant_message(
        session,
        AssistantEventCreate(
            eventType="assistant_transcript_final",
            responseId=response_id,
            transcript=reply_text,
            detail={
                "turnId": turn["id"],
                "action": action,
                "questionId": current_question_id,
                "source": "control_turn_commit",
            },
        ),
    )
    return _build_process_result(session, turn).model_dump()


def create_assistant_event(voice_session_id: str, payload: AssistantEventCreate) -> dict:
    session = _get_voice_session_for_internal_use(voice_session_id)
    item = {
        "id": f"voice-assistant-event-{uuid4().hex[:12]}",
        "tenantId": session["tenantId"],
        "voiceSessionId": voice_session_id,
        "recordId": session["recordId"],
        "eventType": payload.eventType,
        "responseId": payload.responseId,
        "generation": payload.generation,
        "transcript": payload.transcript,
        "detail": payload.detail,
        "createdAt": utc_now(),
    }
    store.upsert("voice_assistant_events", item)
    if _should_persist_voice_assistant_message(payload):
        _save_voice_assistant_message(session, payload)
    return item


def create_connection_event(voice_session_id: str, payload: ConnectionEventCreate) -> dict:
    session = _get_voice_session_for_internal_use(voice_session_id)
    logger.info(
        "voice_connection_event voice_session_id=%s event_type=%s connection_status=%s detail_keys=%s",
        voice_session_id,
        payload.eventType,
        payload.connectionStatus,
        sorted((payload.detail or {}).keys()),
    )
    item = {
        "id": f"voice-connection-event-{uuid4().hex[:12]}",
        "tenantId": session["tenantId"],
        "voiceSessionId": voice_session_id,
        "recordId": session["recordId"],
        "eventType": payload.eventType,
        "connectionStatus": payload.connectionStatus,
        "detail": payload.detail,
        "createdAt": utc_now(),
    }
    store.upsert("voice_connection_events", item)
    return item


def _initialize_initial_question(record: dict, user: UserContext) -> str | None:
    started_at = monotonic()
    snapshot = get_interview_state_snapshot(record, user)
    interview_state = snapshot.get("interviewState", {})
    current_question_text = _find_current_question_text(interview_state)
    if current_question_text:
        logger.info(
            "voice_initial_question_reused record_id=%s question_id=%s elapsed_ms=%s",
            record.get("id"),
            interview_state.get("currentQuestionId"),
            round((monotonic() - started_at) * 1000),
        )
        return f"{INITIAL_VOICE_GREETING}{current_question_text}"
    if interview_state.get("status") == "completed":
        return None
    result = generate_interview_reply(record, user, persist_assistant_messages=False)
    initial_question = "\n".join(result.reply_chunks).strip()
    if not initial_question:
        return None
    logger.info(
        "voice_initial_question_generated record_id=%s elapsed_ms=%s",
        record.get("id"),
        round((monotonic() - started_at) * 1000),
    )
    return f"{INITIAL_VOICE_GREETING}{initial_question}"


def _find_current_question_text(interview_state: dict) -> str | None:
    current_question_id = interview_state.get("currentQuestionId")
    if not current_question_id:
        return None
    question = _find_question_by_id(interview_state, current_question_id)
    if question is not None:
        text = str(question.get("text") or "").strip()
        return text or None
    return None


def _find_question_by_id(interview_state: dict[str, Any], question_id: str | None) -> dict[str, Any] | None:
    if not question_id:
        return None
    for question in interview_state.get("askedQuestions", []):
        if question.get("questionId") == question_id:
            return question
    return None


def _has_voice_interview_fields(record: dict, user: UserContext) -> bool:
    knowledge = store.get("knowledges", record.get("knowledgeId")) or {}
    if is_structured_interview_enabled(knowledge):
        return True
    return any(
        row
        for row in store.list("knowledge_fields", user.tenant_id)
        if row.get("knowledgeId") == record["knowledgeId"]
    )


def _get_voice_session_for_user(voice_session_id: str, user: UserContext) -> dict:
    session = _get_voice_session_for_internal_use(voice_session_id)
    record = get_scoped_item("records", session["recordId"], user, "record_not_found")
    require_record_action(record, user, "answer")
    if user.role == "interviewer" and session.get("ownerUserId") != user.user_id:
        raise HTTPException(status_code=403, detail="voice_session_forbidden")
    return session


def _get_voice_session_for_internal_use(voice_session_id: str) -> dict:
    session = voice_session_repository.get(voice_session_id)
    if not session:
        raise HTTPException(status_code=404, detail="voice_session_not_found")
    return session


def _get_voice_turn_for_session(turn_id: str, session: dict) -> dict:
    turn = voice_turn_repository.get(turn_id)
    if not turn or turn.get("voiceSessionId") != session["id"]:
        raise HTTPException(status_code=404, detail="voice_turn_not_found")
    return turn


def _ensure_session_accepts_turns(session: dict) -> None:
    if session.get("status") in {"stopped", "completed"}:
        raise HTTPException(
            status_code=409,
            detail=f"voice_session_{session.get('status')}",
        )


def _delete_voice_turn_messages(turn_id: str, tenant_id: str) -> None:
    for message in tuple(store.list("messages", tenant_id)):
        if message.get("voiceTurnId") == turn_id:
            store.delete("messages", message["id"])


def _restore_cancelled_turn_artifacts(turn: dict, session: dict) -> None:
    base_state = turn.get("baseInterviewState")
    if isinstance(base_state, dict):
        store.upsert("interview_states", deepcopy(base_state))
    _delete_voice_turn_messages(turn["id"], session["tenantId"])


def _voice_turn_lifecycle_status(
    turn: dict,
) -> Literal["RECEIVED", "EVALUATING", "COMMITTED", "CANCELLED", "SUPERSEDED"]:
    explicit = turn.get("lifecycleStatus")
    if explicit in {
        "RECEIVED",
        "EVALUATING",
        "COMMITTED",
        "CANCELLED",
        "SUPERSEDED",
    }:
        return explicit
    legacy = turn.get("processingStatus")
    if legacy == "processing":
        return "EVALUATING"
    if legacy == "completed":
        return "COMMITTED"
    if legacy == "cancelled":
        return "CANCELLED"
    return "RECEIVED"


def _mark_previous_turn_superseded(session: dict, correction_turn: dict) -> None:
    candidates = [
        item
        for item in voice_turn_repository.list_for_session(
            session["tenantId"],
            session["id"],
        )
        if item["id"] != correction_turn["id"]
        and item.get("answerToQuestionId")
        == correction_turn.get("answerToQuestionId")
        and _voice_turn_lifecycle_status(item) == "COMMITTED"
    ]
    if not candidates:
        return
    previous = max(candidates, key=lambda item: int(item.get("sequence") or 0))
    previous["lifecycleStatus"] = "SUPERSEDED"
    previous["supersededByTurnId"] = correction_turn["id"]
    previous["updatedAt"] = utc_now()
    voice_turn_repository.save(previous)


def _build_user_context_from_session(session: dict) -> UserContext:
    user_id = session.get("ownerUserId") or session.get("createdByUserId")
    return UserContext(
        user_id=user_id,
        tenant_id=session["tenantId"],
        role=session.get("ownerRole", "interviewer"),
        display_name=user_id,
    )


def _list_voice_record_messages(record: dict, user: UserContext) -> list[dict[str, Any]]:
    return sorted(
        [
            row
            for row in store.list("messages", user.tenant_id)
            if row.get("recordId") == record["id"]
        ],
        key=lambda row: (row.get("createdAt") or "", row.get("id") or ""),
    )


def _latest_voice_assistant_message(record: dict, user: UserContext) -> dict[str, Any] | None:
    for message in reversed(_list_voice_record_messages(record, user)):
        if message.get("role") == "assistant":
            return message
    return None


def _save_voice_user_message(record: dict, turn: dict, user: UserContext) -> dict:
    interview_state = store.get("interview_states", f"interview-state-{record['id']}") or {}
    current_question_id = turn.get("answerToQuestionId")
    current_field_id = turn.get("answerToFieldId")
    if turn.get("turnType") == "CONTROL":
        message = {
            "id": f"voice-msg-{turn['id']}",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "content": turn["transcript"],
            "role": "user",
            "isActualUtterance": True,
            "turnType": "CONTROL",
            "createdAt": turn.get("createdAt") or utc_now(),
            "updatedAt": utc_now(),
            "answerToQuestionId": None,
            "answerToFieldId": None,
            "voiceSessionId": turn["voiceSessionId"],
            "voiceTurnId": turn["id"],
            "targetType": None,
            "targetId": None,
        }
        return store.upsert("messages", message)
    question = _find_question_by_id(interview_state, current_question_id)
    question_type = question.get("questionType") if question else None
    if not current_question_id or (not current_field_id and question_type != "structured"):
        raise HTTPException(status_code=409, detail="voice_turn_missing_target_field")

    message = {
        "id": f"voice-msg-{turn['id']}",
        "tenantId": user.tenant_id,
        "recordId": record["id"],
        "content": turn["transcript"],
        "role": "user",
        "isActualUtterance": True,
        "turnType": "ANSWER",
        "createdAt": turn.get("createdAt") or utc_now(),
        "updatedAt": utc_now(),
        "answerToQuestionId": current_question_id,
        "answerToFieldId": current_field_id,
        "questionType": question_type,
        "targetType": question.get("targetType") if question else None,
        "targetId": question.get("targetId") if question else None,
        "voiceSessionId": turn["voiceSessionId"],
        "voiceTurnId": turn["id"],
    }
    return store.upsert("messages", message)


def _save_confirmed_voice_answer_message(
    *,
    record: dict,
    session: dict,
    turn: dict,
    question_id: str | None,
    field_id: str | None,
    question_type: str | None,
    content: str,
    user: UserContext,
) -> dict:
    if not question_id or not field_id:
        raise HTTPException(status_code=409, detail="voice_turn_missing_target_field")
    message = {
        "id": f"voice-confirmed-msg-{turn['id']}",
        "tenantId": user.tenant_id,
        "recordId": record["id"],
        "content": content,
        "role": "user",
        "isActualUtterance": False,
        "messageType": "confirmed_answer",
        "turnType": "ANSWER",
        "createdAt": utc_now(),
        "updatedAt": utc_now(),
        "answerToQuestionId": question_id,
        "answerToFieldId": field_id,
        "questionType": question_type,
        "voiceSessionId": session["id"],
        "voiceTurnId": turn["id"],
    }
    return store.upsert("messages", message)


def _save_voice_assistant_message(session: dict, payload: AssistantEventCreate) -> dict:
    detail = payload.detail or {}
    response_id = payload.responseId or f"voice-response-{uuid4().hex[:12]}"
    message_id = f"voice-assistant-msg-{response_id}"
    existing = store.get("messages", message_id) or {}
    message = {
        "id": message_id,
        "tenantId": session["tenantId"],
        "recordId": session["recordId"],
        "content": payload.transcript or "",
        "role": "assistant",
        "isActualUtterance": True,
        "createdAt": existing.get("createdAt") or utc_now(),
        "updatedAt": utc_now(),
        "questionId": detail.get("questionId"),
        "questionType": detail.get("questionType"),
        "fieldId": detail.get("fieldId"),
        "targetType": detail.get("targetType"),
        "targetId": detail.get("targetId"),
        "voiceSessionId": session["id"],
        "voiceTurnId": detail.get("turnId"),
        "voiceResponseId": response_id,
        "source": detail.get("source"),
    }
    return store.upsert("messages", message)


def _should_persist_voice_assistant_message(payload: AssistantEventCreate) -> bool:
    if payload.eventType != "assistant_transcript_final" or not payload.transcript:
        return False

    detail = payload.detail or {}
    return detail.get("action") != "finish"


def _build_process_result(session: dict, turn: dict) -> VoiceTurnProcessResult:
    return VoiceTurnProcessResult(
        turn_id=turn["id"],
        response_id=turn.get("responseId") or f"voice-response-{turn['id']}",
        text=turn.get("responseText") or "",
        action=turn.get("action") or "ask_configured_field",
        question_id=turn.get("questionId"),
        state_version=int(turn.get("stateVersion") or session.get("stateVersion") or 0),
        retrieval_policy=turn.get("retrievalPolicy"),
        retrieval_executed=bool(turn.get("retrievalExecuted", False)),
        voice_session=session,
        voice_turn=turn,
    )


def _process_candidate_turn(
    *,
    record: dict,
    session: dict,
    turn: dict,
    user: UserContext,
    interview_state: dict[str, Any],
    field_state: dict[str, Any],
    current_question: dict[str, Any] | None,
    user_message: dict[str, Any],
    precomputed_evaluation: VoiceAnswerEvaluation | None = None,
) -> dict[str, Any]:
    evaluation_started_at = monotonic()
    evaluation_started_at_ms = int(time() * 1000)
    current_question_id = (
        field_state.get("pendingQuestionId")
        or turn.get("answerToQuestionId")
        or interview_state.get("currentQuestionId")
    )
    current_field_id = turn.get("answerToFieldId")
    if not current_field_id:
        raise HTTPException(status_code=409, detail="voice_turn_missing_target_field")
    current_field = _resolve_field(record, current_field_id, user) or {
        "id": current_field_id,
        "name": current_field_id,
    }
    retrieval_policy = _field_retrieval_policy(current_field).value
    field_state["status"] = "asking"
    field_state["answerState"] = ANSWER_STATE_CANDIDATE_PENDING
    field_state["answerSummary"] = None
    field_state["pendingQuestionId"] = current_question_id
    field_state["pendingFieldId"] = current_field_id
    evaluation_request_id = f"voice-evaluation-{uuid4().hex}"
    evaluation_deadline_at = monotonic() + VOICE_ANSWER_EVALUATION_DEADLINE_SECONDS
    evaluation_request = VoiceEvaluationRequest(
        voice_session_id=session["id"],
        voice_turn_id=turn["id"],
        question_id=current_question_id,
        field_id=current_field_id,
        evaluation_request_id=evaluation_request_id,
        state_version=int(session.get("stateVersion") or 0),
        deadline_at=evaluation_deadline_at,
    )
    field_state["evaluationRequestId"] = evaluation_request_id
    field_state["evaluationDeadlineAt"] = round(evaluation_deadline_at * 1000)
    state_persist_started_at = monotonic()
    _persist_interview_state(interview_state, user)
    logger.info(
        "state_persist_completed voice_session_id=%s turn_id=%s evaluation_request_id=%s state_persist_ms=%s",
        session["id"],
        turn["id"],
        evaluation_request_id,
        round((monotonic() - state_persist_started_at) * 1000, 1),
    )
    logger.info(
        "answer_evaluation_started voice_session_id=%s turn_id=%s question_id=%s field_id=%s playback_generation_id=%s input_state=%s monotonic_timestamp_ms=%s transcript_ended_at_ms=%s evaluation_deadline_ms=%s",
        session["id"],
        turn["id"],
        current_question_id,
        current_field_id,
        None,
        "ANSWER_PROCESSING",
        int(monotonic() * 1000),
        turn.get("endedAtMs"),
        int(VOICE_ANSWER_EVALUATION_DEADLINE_SECONDS * 1000),
    )
    logger.info(
        "knowledge_retrieval_started voice_session_id=%s turn_id=%s question_id=%s field_id=%s playback_generation_id=%s input_state=%s monotonic_timestamp_ms=%s retrieval_policy=%s",
        session["id"],
        turn["id"],
        current_question_id,
        current_field_id,
        None,
        "ANSWER_PROCESSING",
        int(monotonic() * 1000),
        retrieval_policy,
    )
    try:
        evaluation = precomputed_evaluation or run_with_evaluation_deadline(
            lambda: _evaluate_voice_answer_candidate(
                transcript=turn["transcript"],
                current_question=current_question,
                current_field=current_field,
                field_state=field_state,
                evidence_message_id=user_message["id"],
                interview_locale=resolve_interview_locale(session, {}),
            ),
            request=evaluation_request,
        )
    except Exception as exc:
        fallback_started_at = monotonic()
        degraded_reason = _voice_evaluation_degraded_reason(exc)
        logger.exception(
            "voice_answer_evaluation_failed voice_session_id=%s turn_id=%s question_id=%s evaluation_request_id=%s degraded_reason=%s",
            session["id"],
            turn["id"],
            current_question_id,
            evaluation_request_id,
            degraded_reason,
        )
        evaluation = VoiceAnswerEvaluation(
            decision="UNCLEAR",
            normalized_answer="",
            is_relevant=None,
            is_sufficient=False,
            missing_information=[],
            follow_up_question=_build_evaluation_fallback_prompt(
                current_question,
                resolve_interview_locale(session, {}),
            ),
            evidence_transcript_ids=[user_message["id"]],
            evaluation_degraded=True,
            degraded_reason=degraded_reason,
            evaluation_status="EVALUATION_ERROR",
        )
        logger.info(
            "evaluation_fallback_completed voice_session_id=%s turn_id=%s evaluation_request_id=%s evaluation_fallback_ms=%s degraded_reason=%s",
            session["id"],
            turn["id"],
            evaluation_request_id,
            round((monotonic() - fallback_started_at) * 1000, 1),
            degraded_reason,
        )
    logger.info(
        "knowledge_retrieval_completed voice_session_id=%s turn_id=%s question_id=%s field_id=%s playback_generation_id=%s input_state=%s monotonic_timestamp_ms=%s retrieval_policy=%s retrieval_executed=%s",
        session["id"],
        turn["id"],
        current_question_id,
        current_field_id,
        None,
        "ANSWER_PROCESSING",
        int(monotonic() * 1000),
        retrieval_policy,
        False,
    )
    evaluation_completed_at_ms = int(time() * 1000)
    transcript_to_evaluation_completed_ms = None
    if isinstance(turn.get("endedAtMs"), (int, float)):
        transcript_to_evaluation_completed_ms = max(
            0,
            evaluation_completed_at_ms - int(turn["endedAtMs"]),
        )
    logger.info(
        "answer_evaluation_completed voice_session_id=%s turn_id=%s question_id=%s field_id=%s evaluation_request_id=%s playback_generation_id=%s input_state=%s monotonic_timestamp_ms=%s decision=%s is_relevant=%s is_sufficient=%s retrieval_policy=%s retrieval_executed=%s answer_evaluation_total_ms=%s knowledge_retrieval_ms=%s transcript_to_evaluation_start_ms=%s transcript_to_evaluation_completed_ms=%s",
        session["id"],
        turn["id"],
        current_question_id,
        current_field_id,
        evaluation_request_id,
        None,
        "ANSWER_PROCESSING",
        int(monotonic() * 1000),
        evaluation.decision,
        evaluation.is_relevant,
        evaluation.is_sufficient,
        retrieval_policy,
        False,
        round((monotonic() - evaluation_started_at) * 1000, 1),
        0.0,
        (
            max(0, evaluation_started_at_ms - int(turn["endedAtMs"]))
            if isinstance(turn.get("endedAtMs"), (int, float))
            else None
        ),
        transcript_to_evaluation_completed_ms,
    )
    field_state["evaluationDegraded"] = evaluation.evaluation_degraded
    field_state["degradedReason"] = evaluation.degraded_reason
    def use_completed_evaluation(**_: Any) -> AnswerEvaluation:
        return AnswerEvaluation(
            decision=evaluation.decision,
            normalized_answer=evaluation.normalized_answer,
            record_answer=evaluation.record_answer,
            is_relevant=evaluation.is_relevant,
            is_sufficient=evaluation.is_sufficient,
            missing_information=list(evaluation.missing_information),
            follow_up_question=evaluation.follow_up_question,
            confirmation_question=evaluation.confirmation_question,
            retrieval_needed=evaluation.retrieval_needed,
            evaluation_reason=evaluation.evaluation_reason,
            evidence_transcript_ids=list(evaluation.evidence_transcript_ids),
            captured_items=list(evaluation.captured_items),
            answer_disposition=evaluation.answer_disposition,
            evaluation_status=evaluation.evaluation_status,
        )

    turn_result = InterviewAnswerProcessor(evaluator=use_completed_evaluation).process_turn_sync(
        record_id=record["id"],
        question_id=str(current_question_id or ""),
        field_id=current_field_id,
        transcript=turn["transcript"],
        current_state=interview_state,
        question=current_question or {},
        field=current_field,
        evidence_transcript_id=user_message["id"],
        retrieval_policy=retrieval_policy,
    )
    field_state["needsClarification"] = (
        evaluation.evaluation_status == "OK"
        and turn_result.decision in {"NOT_ANSWER", "UNCLEAR"}
    )
    field_state["clarificationQuestion"] = (
        turn_result.reply_text if turn_result.action == "ask_follow_up" else None
    )
    field_state["followUpQuestion"] = (
        turn_result.reply_text if turn_result.decision in {"NEEDS_MORE_INFORMATION", "NEEDS_FOLLOWUP"} else None
    )
    state_persist_started_at = monotonic()
    _persist_interview_state(interview_state, user)
    logger.info(
        "state_persist_completed voice_session_id=%s turn_id=%s evaluation_request_id=%s state_persist_ms=%s",
        session["id"],
        turn["id"],
        evaluation_request_id,
        round((monotonic() - state_persist_started_at) * 1000, 1),
    )

    return {
        "replyText": turn_result.reply_text,
        "action": turn_result.action,
        "questionId": turn_result.question_id,
        "retrievalPolicy": turn_result.retrieval_policy,
        "retrievalExecuted": turn_result.retrieval_executed,
        "currentFieldId": turn_result.field_id,
    }


def _process_confirmation_turn(
    *,
    record: dict,
    session: dict,
    turn: dict,
    user: UserContext,
    interview_state: dict[str, Any],
    field_state: dict[str, Any],
    current_question: dict[str, Any] | None,
    user_message: dict[str, Any],
    precomputed_confirmation: VoiceConfirmationEvaluation | None = None,
) -> dict[str, Any]:
    current_question_id = (
        field_state.get("pendingQuestionId")
        or turn.get("answerToQuestionId")
        or interview_state.get("currentQuestionId")
    )
    current_field_id = field_state.get("pendingFieldId") or turn.get("answerToFieldId")
    if not current_field_id:
        raise HTTPException(status_code=409, detail="voice_turn_missing_target_field")
    current_field = _resolve_field(record, current_field_id, user) or {
        "id": current_field_id,
        "name": current_field_id,
    }
    retrieval_policy = _field_retrieval_policy(current_field).value

    def unexpected_candidate_evaluation(**_: Any) -> AnswerEvaluation:
        raise RuntimeError("candidate evaluation called while awaiting confirmation")

    def evaluate_confirmation(**kwargs: Any) -> ConfirmationEvaluation:
        if precomputed_confirmation is not None:
            decision = precomputed_confirmation
        else:
            try:
                decision = _evaluate_confirmation_response(
                    current_question=current_question,
                    candidate_answer=kwargs["candidate_answer"],
                    user_reply=kwargs["user_reply"],
                    field_state=field_state,
                    interview_locale=resolve_interview_locale(session, {}),
                )
            except Exception:
                logger.exception(
                    "voice_confirmation_evaluation_failed voice_session_id=%s turn_id=%s question_id=%s",
                    session["id"],
                    turn["id"],
                    current_question_id,
                )
                decision = VoiceConfirmationEvaluation(
                    outcome="UNCLEAR",
                    clarification_question=_localized_confirmation_fallback(
                        resolve_interview_locale(session, {})
                    ),
                    evaluation_status="EVALUATION_ERROR",
                )
        return ConfirmationEvaluation(
            outcome=decision.outcome,
            revised_answer=decision.revised_answer,
            record_answer=decision.record_answer,
            confirmation_question=decision.confirmation_question,
            clarification_question=decision.clarification_question,
            captured_items=decision.captured_items,
            evaluation_status=decision.evaluation_status,
        )

    turn_result = InterviewAnswerProcessor(
        evaluator=unexpected_candidate_evaluation,
        confirmation_evaluator=evaluate_confirmation,
    ).process_turn_sync(
        record_id=record["id"],
        question_id=str(current_question_id or ""),
        field_id=current_field_id,
        transcript=turn["transcript"],
        current_state=interview_state,
        question=current_question or {},
        field=current_field,
        evidence_transcript_id=user_message["id"],
        retrieval_policy=retrieval_policy,
    )

    if turn_result.action == "confirmed":
        confirmed_answer = str(
            field_state.get("recordAnswer")
            or compose_record_answer(list(field_state.get("rawAnswerHistory") or []))
            or ""
        ).strip()
        _save_confirmed_voice_answer_message(
            record=record,
            session=session,
            turn=turn,
            question_id=current_question_id,
            field_id=current_field_id,
            question_type=current_question.get("questionType") if isinstance(current_question, dict) else None,
            content=confirmed_answer,
            user=user,
        )
        field_state["candidateEvidenceTranscriptIds"] = []
        field_state["needsClarification"] = False
        field_state["clarificationQuestion"] = None
        field_state["followUpQuestion"] = None
        interview_state["lastProcessedUserMessageId"] = user_message["id"]
        interview_state["currentQuestionId"] = None
        interview_state["currentFieldId"] = None
        _persist_interview_state(interview_state, user)
        return _build_voice_next_question_result(
            record=record,
            session=session,
            turn=turn,
            user=user,
            completed_question_id=current_question_id,
            completed_field_id=current_field_id,
        )

    field_state["needsClarification"] = turn_result.decision in {
        "REJECT_WITHOUT_CONTENT",
        "UNCLEAR",
    }
    field_state["clarificationQuestion"] = (
        turn_result.reply_text if turn_result.action == "ask_follow_up" else None
    )
    interview_state["lastProcessedUserMessageId"] = user_message["id"]
    _persist_interview_state(interview_state, user)
    return {
        "replyText": turn_result.reply_text,
        "action": turn_result.action,
        "questionId": turn_result.question_id,
        "retrievalPolicy": turn_result.retrieval_policy,
        "retrievalExecuted": turn_result.retrieval_executed,
        "currentFieldId": turn_result.field_id,
    }


def _build_voice_next_question_result(
    *,
    record: dict,
    session: dict,
    turn: dict,
    user: UserContext,
    completed_question_id: str | None,
    completed_field_id: str,
) -> dict[str, Any]:
    stream_result = generate_interview_reply(record, user, persist_assistant_messages=False)
    metadata = stream_result.metadata or {}
    reply_text = str(metadata.get("reply") or "\n".join(stream_result.reply_chunks)).strip()
    action = str(metadata.get("action") or "ask_configured_field").strip() or "ask_configured_field"
    question = metadata.get("question") if isinstance(metadata.get("question"), dict) else None
    if question and action in {"ask_configured_field", "ask_follow_up"}:
        next_question_text = str(question.get("text") or "").strip()
        if next_question_text:
            reply_text = next_question_text
    question_id = (
        question.get("questionId")
        if question
        else get_interview_state_snapshot(record, user).get("interviewState", {}).get("currentQuestionId")
    )
    retrieval_policy = str(
        (question or {}).get("retrievalPolicy")
        or metadata.get("retrievalPolicy")
        or "auto"
    )
    retrieval_executed = bool(metadata.get("retrievalExecuted", False))
    logger.info(
        "voice_retrieval_decision_completed voice_session_id=%s turn_id=%s question_id=%s "
        "state_version=%s retrieval_policy=%s retrieval_executed=%s response_id=%s tool_use_id=%s",
        session["id"],
        turn["id"],
        completed_question_id,
        session.get("stateVersion"),
        retrieval_policy,
        retrieval_executed,
        None,
        None,
    )
    return {
        "replyText": reply_text,
        "action": action,
        "questionId": question_id,
        "retrievalPolicy": retrieval_policy,
        "retrievalExecuted": retrieval_executed,
        "currentFieldId": completed_field_id,
    }


@dataclass(frozen=True)
class VoiceTurnEvaluation:
    turn_type: Literal["ANSWER", "CONTROL"]
    interpretation: DialogueInterpretation
    answer_evaluation: VoiceAnswerEvaluation | None = None


def _evaluate_voice_turn_candidate(
    *,
    transcript: str,
    current_question: dict[str, Any] | None,
    current_field: dict[str, Any] | None,
    field_state: dict[str, Any],
    evidence_message_id: str,
    interview_locale: InterviewLocale = "ja-JP",
) -> VoiceTurnEvaluation:
    result = _run_voice_json_output(
        system_prompt=(
            f"{_VOICE_TURN_EVALUATION_SYSTEM_PROMPT}\n\n"
            f"{interview_language_instruction(interview_locale)}"
        ),
        prompt=_build_voice_turn_evaluation_prompt(
            transcript=transcript,
            current_question=current_question,
            current_field=current_field,
            field_state=field_state,
            evidence_message_id=evidence_message_id,
            interview_locale=interview_locale,
        ),
        output_model=VoiceTurnEvaluationOutput,
        max_tokens=256,
    )
    interpretation = DialogueInterpretation(
        act=result.act,
        response_text=result.responseText.strip() if isinstance(result.responseText, str) else None,
        reason=result.reason,
    )
    if result.turnType == "CONTROL":
        return VoiceTurnEvaluation(
            turn_type="CONTROL",
            interpretation=interpretation,
        )
    answer_evaluation = VoiceAnswerEvaluation(
        decision=result.decision,
        normalized_answer=result.normalizedAnswer.strip(),
        record_answer=result.recordAnswer.strip()
        if isinstance(result.recordAnswer, str)
        else "",
        is_relevant=result.isRelevant,
        is_sufficient=result.isSufficient,
        missing_information=[item.strip() for item in result.missingInformation if item.strip()],
        follow_up_question=(
            result.followUpQuestion.strip()
            if isinstance(result.followUpQuestion, str) and result.followUpQuestion.strip()
            else None
        ),
        confirmation_question=(
            result.confirmationQuestion.strip()
            if isinstance(result.confirmationQuestion, str) and result.confirmationQuestion.strip()
            else None
        ),
        evidence_transcript_ids=result.evidenceTranscriptIds or [evidence_message_id],
        retrieval_needed=result.retrievalNeeded,
        evaluation_reason=result.evaluationReason,
        captured_items=[item.model_dump() for item in result.capturedItems],
        answer_disposition=result.answerDisposition,
    )
    return VoiceTurnEvaluation(
        turn_type="ANSWER",
        interpretation=interpretation,
        answer_evaluation=_stabilize_voice_answer_evaluation(answer_evaluation),
    )


def _ensure_voice_field_state(interview_state: dict[str, Any], field_id: str | None) -> dict[str, Any]:
    if not field_id:
        raise HTTPException(status_code=409, detail="voice_turn_missing_target_field")
    field_states = interview_state.setdefault("fieldStates", {})
    resolved_field_id = str(field_id)
    field_state = field_states.setdefault(
        resolved_field_id,
        {
            "fieldId": resolved_field_id,
            "status": "asking",
            "answerSummary": None,
            "rawAnswer": None,
            "rawAnswerHistory": [],
            "recordAnswer": None,
            "capturedItems": [],
            "missingInformation": [],
        },
    )
    field_state.setdefault("answerState", ANSWER_STATE_UNANSWERED)
    field_state.setdefault("candidateAnswer", None)
    field_state.setdefault("rawAnswer", None)
    raw_answer_history = field_state.setdefault("rawAnswerHistory", [])
    if not raw_answer_history and field_state.get("rawAnswer"):
        raw_answer_history.append(str(field_state["rawAnswer"]))
    field_state.setdefault("recordAnswer", None)
    field_state.setdefault("capturedItems", [])
    if not field_state["capturedItems"]:
        field_state["capturedItems"] = list(
            field_state.get("candidateItems") or field_state.get("confirmedItems") or []
        )
    field_state.setdefault("candidateItems", [])
    field_state.setdefault("confirmedItems", [])
    field_state.setdefault("missingRequiredItemIds", [])
    field_state.setdefault("answerDisposition", None)
    field_state.setdefault("candidateEvidenceTranscriptIds", [])
    field_state.setdefault("needsClarification", False)
    field_state.setdefault("clarificationQuestion", None)
    field_state.setdefault("followUpQuestion", None)
    field_state.setdefault("evaluationReason", None)
    field_state.setdefault("isRelevant", None)
    field_state.setdefault("isSufficient", None)
    field_state.setdefault("pendingQuestionId", None)
    field_state.setdefault("pendingFieldId", resolved_field_id)
    return field_state


def _evaluate_voice_answer_candidate(
    *,
    transcript: str,
    current_question: dict[str, Any] | None,
    current_field: dict[str, Any] | None,
    field_state: dict[str, Any],
    evidence_message_id: str,
    interview_locale: InterviewLocale = "ja-JP",
) -> VoiceAnswerEvaluation:
    prompt = _build_voice_answer_evaluation_prompt(
        transcript=transcript,
        current_question=current_question,
        current_field=current_field,
        field_state=field_state,
        evidence_message_id=evidence_message_id,
        interview_locale=interview_locale,
    )
    result = _run_voice_json_output(
        system_prompt=_voice_answer_evaluation_system_prompt(interview_locale),
        prompt=prompt,
        output_model=VoiceAnswerEvaluationOutput,
        max_tokens=256,
    )
    evaluation = VoiceAnswerEvaluation(
        decision=result.decision,
        normalized_answer=result.normalized_answer.strip(),
        record_answer=result.record_answer.strip() if isinstance(result.record_answer, str) else "",
        is_relevant=result.is_relevant,
        is_sufficient=result.is_sufficient,
        missing_information=[item.strip() for item in result.missing_information if item.strip()],
        follow_up_question=result.follow_up_question.strip() if isinstance(result.follow_up_question, str) and result.follow_up_question.strip() else None,
        evidence_transcript_ids=result.evidence_transcript_ids or [evidence_message_id],
        confirmation_question=(
            result.confirmation_question.strip()
            if isinstance(result.confirmation_question, str) and result.confirmation_question.strip()
            else None
        ),
        retrieval_needed=result.retrieval_needed,
        evaluation_reason=result.evaluation_reason,
        captured_items=[item.model_dump() for item in result.captured_items],
        answer_disposition=result.answer_disposition,
        evaluation_status=result.evaluation_status,
    )
    return _stabilize_voice_answer_evaluation(evaluation)


def _stabilize_voice_answer_evaluation(
    evaluation: VoiceAnswerEvaluation,
) -> VoiceAnswerEvaluation:
    decision = evaluation.decision
    normalized_answer = evaluation.normalized_answer
    record_answer = evaluation.record_answer
    is_relevant = evaluation.is_relevant
    is_sufficient = evaluation.is_sufficient
    missing_information = evaluation.missing_information
    follow_up_question = evaluation.follow_up_question
    captured_items = evaluation.captured_items

    if decision == "CONFIRMABLE" and (normalized_answer or record_answer or captured_items):
        is_relevant = True
        is_sufficient = True
        missing_information = []
        follow_up_question = None
    elif decision == "CONFIRMABLE":
        decision = "UNCLEAR"
        is_relevant = False
        is_sufficient = False
    elif decision == "NEEDS_MORE_INFORMATION" and is_relevant is False:
        decision = "NOT_ANSWER"
        normalized_answer = ""
        is_sufficient = False
        missing_information = []
    elif decision in {"NOT_ANSWER", "UNCLEAR"}:
        normalized_answer = ""
        record_answer = ""
        is_relevant = False
        is_sufficient = False
        missing_information = []

    return VoiceAnswerEvaluation(
        decision=decision,
        normalized_answer=normalized_answer,
        is_relevant=is_relevant,
        is_sufficient=is_sufficient,
        missing_information=missing_information,
        follow_up_question=follow_up_question,
        evidence_transcript_ids=evaluation.evidence_transcript_ids,
        record_answer=record_answer,
        confirmation_question=evaluation.confirmation_question,
        retrieval_needed=evaluation.retrieval_needed,
        evaluation_reason=evaluation.evaluation_reason,
        evaluation_degraded=evaluation.evaluation_degraded,
        degraded_reason=evaluation.degraded_reason,
        captured_items=captured_items,
        answer_disposition=evaluation.answer_disposition,
        evaluation_status=evaluation.evaluation_status,
    )


def _evaluate_confirmation_response(
    *,
    current_question: dict[str, Any] | None,
    candidate_answer: str,
    user_reply: str,
    field_state: dict[str, Any],
    interview_locale: InterviewLocale = "ja-JP",
) -> VoiceConfirmationEvaluation:
    if is_unambiguous_confirmation(user_reply):
        logger.info(
            "voice_confirmation_fast_path outcome=CONFIRM candidate_length=%s",
            len(candidate_answer),
        )
        return VoiceConfirmationEvaluation(
            outcome="CONFIRM",
            record_answer=candidate_answer,
        )
    prompt = _build_voice_confirmation_prompt(
        current_question=current_question,
        candidate_answer=candidate_answer,
        user_reply=user_reply,
        field_state=field_state,
        interview_locale=interview_locale,
    )
    result = _run_voice_json_output(
        system_prompt=(
            f"{_VOICE_CONFIRMATION_TEXT_SYSTEM_PROMPT}\n\n"
            f"{interview_language_instruction(interview_locale)}"
        ),
        prompt=prompt,
        output_model=VoiceConfirmationEvaluationOutput,
        max_tokens=160,
    )
    return VoiceConfirmationEvaluation(
        outcome=result.outcome,
        revised_answer=result.revised_answer.strip()
        if isinstance(result.revised_answer, str) and result.revised_answer.strip()
        else None,
        record_answer=(
            result.revised_answer.strip()
            if result.outcome == "REVISE_WITH_CONTENT"
            and isinstance(result.revised_answer, str)
            and result.revised_answer.strip()
            else (
                result.record_answer.strip()
                if isinstance(result.record_answer, str) and result.record_answer.strip()
                else None
            )
        ),
        confirmation_question=result.confirmation_question.strip() if isinstance(result.confirmation_question, str) and result.confirmation_question.strip() else None,
        clarification_question=result.clarification_question.strip() if isinstance(result.clarification_question, str) and result.clarification_question.strip() else None,
        captured_items=[item.model_dump() for item in result.captured_items],
    )


def _localized_confirmation_fallback(locale: InterviewLocale) -> str:
    return {
        "ja-JP": "内容を確定してよいか判断できませんでした。正しければ『はい』、修正があれば正しい内容を教えてください。",
        "en-US": "I could not confirm the answer. Say yes if it is correct, or tell me the correct information.",
        "zh-CN": "我无法确认这个回答。如果正确请说“是”，如果需要修改请告诉我正确内容。",
        "pt-BR": "Não consegui confirmar a resposta. Diga sim se estiver correta ou informe o conteúdo correto.",
    }[locale]


def _build_evaluation_fallback_prompt(
    current_question: dict[str, Any] | None,
    locale: InterviewLocale = "ja-JP",
) -> str:
    question_text = str(current_question.get("text") or "").strip() if isinstance(current_question, dict) else ""
    if question_text:
        return {
            "ja-JP": f"回答処理に一時的な問題がありました。もう一度、{question_text}",
            "en-US": f"There was a temporary problem processing your answer. Please answer again: {question_text}",
            "zh-CN": f"处理回答时遇到临时问题。请重新回答：{question_text}",
            "pt-BR": f"Ocorreu um problema temporário ao processar sua resposta. Responda novamente: {question_text}",
        }[locale]
    return localized_interview_fallbacks(locale)["follow_up"]


def _voice_evaluation_degraded_reason(exc: Exception) -> str:
    if isinstance(exc, VoiceEvaluationDeadlineExceeded):
        return "bedrock_timeout"
    exception_name = type(exc).__name__.lower()
    if "timeout" in exception_name:
        return "bedrock_timeout"
    if any(token in exception_name for token in ("connection", "endpoint", "name resolution")):
        return "bedrock_connection_error"
    if isinstance(exc, (ValueError, json.JSONDecodeError)):
        return "invalid_response"
    return "bedrock_error"


def _resolve_field_name(record: dict, field_id: str | None, user: UserContext) -> str | None:
    field = _resolve_field(record, field_id, user)
    if field is None:
        return None
    return str(field.get("name") or "").strip() or None


def _resolve_field(record: dict, field_id: str | None, user: UserContext) -> dict[str, Any] | None:
    if not field_id:
        return None
    knowledge_fields = [
        row
        for row in store.list("knowledge_fields", user.tenant_id)
        if row.get("knowledgeId") == record["knowledgeId"]
    ]
    for field in knowledge_fields:
        if field.get("id") == field_id:
            return field
    return None


def _run_voice_json_output(
    *,
    system_prompt: str,
    prompt: str,
    output_model: type[BaseModel],
    max_tokens: int,
) -> BaseModel:
    invocation_started_at = monotonic()
    try:
        text = invoke_voice_bedrock_text(
            system_prompt=system_prompt,
            prompt=prompt,
            max_tokens=max_tokens,
        )
    except Exception:
        logger.info(
            "voice_bedrock_invocation_failed bedrock_connect_or_invoke_ms=%s",
            round((monotonic() - invocation_started_at) * 1000, 1),
        )
        raise
    model_completed_at = monotonic()
    logger.info(
        "voice_bedrock_text_completed bedrock_model_ms=%s",
        round((model_completed_at - invocation_started_at) * 1000, 1),
    )
    parse_started_at = monotonic()
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        parsed = output_model.model_validate_json(cleaned)
    except Exception:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("voice JSON response missing object") from None
        parsed = output_model.model_validate(json.loads(cleaned[start : end + 1]))
    logger.info(
        "voice_response_parse_completed response_parse_ms=%s",
        round((monotonic() - parse_started_at) * 1000, 1),
    )
    return parsed


def _voice_answer_evaluation_system_prompt(locale: InterviewLocale = "ja-JP") -> str:
    return (
        "あなたは音声インタビュー回答評価器です。\n"
        "ユーザーの生発話をそのまま回答確定してはいけません。\n"
        "質問との適合性、回答の十分性、既存候補との統合要否を判断し、"
        "構造化出力だけを返してください。\n"
        "questionPlanがある場合、今回の発話から取得できたcaptured_itemsだけを抽出し、"
        "過去の候補とのmerge、不足項目、COMPLETE/NEEDS_FOLLOWUPは判断しないでください。"
        "それらはbackendがquestionPlanを正本として決定します。\n"
        "normalized_answerは評価・確認用の分析値であり、正式な記録用回答ではありません。"
        "record_answerは質問に対する記録用の自然な回答文だけを返し、ユーザー発話をメタ説明文へ変換しないでください。"
        "確認待ちの訂正では訂正後の内容だけを返し、確認語はrecord_answerに含めないでください。\n"
        "推測で補完せず、不要語や訂正前の誤情報を本文へ残さないこと。\n"
        "回答の必須要素は、questionとfieldに明示された説明、回答要件、"
        "aiAssistPromptだけを根拠にしてください。一般的な期待を勝手に必須要素へ追加してはいけません。\n"
        "明示された必須要素がない場合、質問に関連する意味のある情報が含まれていれば、"
        "短い回答でもCONFIRMABLEにしてください。\n"
        "例えば質問が『自己紹介をお願いします。』で発話が『宮崎です』なら、"
        "氏名の具体的な回答なのでCONFIRMABLEです。"
        "質問が『具体的な趣味を教えてください。』で発話が『バスケです』なら、"
        "趣味の具体的な回答なのでCONFIRMABLEです。"
        "これらをNOT_ANSWERやUNCLEARにしてはいけません。\n"
        "短い名詞回答は、意味を変えずに不要な文末の丁寧表現を除き、"
        "normalized_answerを簡潔な名詞句にしてください。\n"
        "CONFIRMABLEでは、質問と項目の意味に合う自然なconfirmation_questionを返してください。"
        "固定の項目名辞書や定型的な引用表現に依存せず、候補の意味を変えてはいけません。"
        "confirmation_questionはユーザーがそのまま聞いて自然な質問文にし、"
        "decisionがCONFIRMABLEの場合はconfirmation_questionを必ず省略せず返し、"
        "空文字やnullにしないでください。"
        "生発話を括弧や引用符でそのまま包んだ『という理解でよろしいですか』形式にしないでください。"
        "例えば趣味の候補がバスケなら『趣味はバスケでいいですか？』、"
        "氏名の候補が宮崎なら『宮崎さんでよろしいですか？』のように返してください。\n"
        "NEEDS_MORE_INFORMATIONではmissing_informationと、次に答える内容が分かる"
        "具体的なfollow_up_questionを必ず返してください。元の質問をそのまま繰り返してはいけません。\n"
        "ユーザーが『何を答えればよいか』『どんな内容か』と案内を求めた場合は、"
        "回答候補にせずNOT_ANSWERとし、明示された要件の範囲で答え方を説明する"
        "follow_up_questionを返してください。要件がなければ、質問に沿った一般的な例を簡潔に示してください。\n"
        f"{interview_language_instruction(locale)}"
    )


def _build_voice_answer_evaluation_prompt(
    *,
    transcript: str,
    current_question: dict[str, Any] | None,
    current_field: dict[str, Any] | None,
    field_state: dict[str, Any],
    evidence_message_id: str,
    interview_locale: InterviewLocale,
) -> str:
    question_context = {
        "id": (current_question or {}).get("id"),
        "text": (current_question or {}).get("text"),
        "questionType": (current_question or {}).get("questionType"),
    }
    field_context = {
        "id": (current_field or {}).get("id"),
        "name": (current_field or {}).get("name"),
        "description": (current_field or {}).get("description"),
        "aiAssistPrompt": (current_field or {}).get("aiAssistPrompt"),
        "questionPlan": (current_field or {}).get("questionPlan"),
    }
    has_explicit_requirements = any(
        isinstance(field_context.get(key), str) and field_context[key].strip()
        for key in ("description", "aiAssistPrompt")
    )
    return json.dumps(
        {
            "question": question_context,
            "field": field_context,
            "hasExplicitAnswerRequirements": has_explicit_requirements,
            "fieldState": {
                "candidateAnswer": field_state.get("candidateAnswer"),
                "missingInformation": field_state.get("missingInformation") or [],
                "answerState": field_state.get("answerState"),
            },
            "transcript": transcript,
            "evidenceTranscriptId": evidence_message_id,
            "interviewLocale": interview_locale,
            "languageInstruction": interview_language_instruction(interview_locale),
            "instructions": {
                "allowedDecisions": ["CONFIRMABLE", "NEEDS_MORE_INFORMATION", "NOT_ANSWER", "UNCLEAR"],
                "allowedAnswerDispositions": ["ANSWERED", "UNCLEAR", "IRRELEVANT"],
                "extractCurrentTurnItemsOnly": True,
                "recordAnswerIsNaturalAnswerOnly": True,
                "recordAnswerMustNotBeMetaSummary": True,
                "backendOwnsCompletion": True,
                "mustNotInfer": True,
                "explicitRequirementsOnly": True,
                "requireSpecificFollowUpWhenNotConfirmable": True,
                "mustIntegrateExistingCandidate": True,
                "confirmationQuestionMustBeNatural": True,
                "confirmationQuestionRequiredWhenConfirmable": True,
                "whenNoExplicitRequirements": (
                    "発話に質問に関連する具体的な事実が1つでもあればCONFIRMABLE。"
                    "一般論から不足項目を作らない。"
                ),
            },
            "decisionExamples": [
                {
                    "transcript": "バスケです",
                    "question": "具体的な趣味を教えてください。",
                    "decision": "CONFIRMABLE",
                    "recordAnswer": "バスケです",
                    "confirmationQuestion": "趣味はバスケでいいですか？",
                },
                {
                    "condition": "趣味の候補が『バスケです』",
                    "decision": "CONFIRMABLE",
                    "confirmationQuestion": "趣味はバスケでいいですか？",
                },
                {
                    "condition": "氏名の候補が『宮崎です』",
                    "decision": "CONFIRMABLE",
                    "confirmationQuestion": "宮崎さんでよろしいですか？",
                },
                {
                    "condition": "広い質問でhasExplicitAnswerRequirements=false、具体的な個人情報を回答",
                    "decision": "CONFIRMABLE",
                },
                {
                    "condition": "ユーザーが何を答えるべきか案内を求める",
                    "decision": "NOT_ANSWER",
                    "followUp": "答え方の短い案内を返す",
                },
            ],
        },
        ensure_ascii=False,
    )


def _build_voice_turn_evaluation_prompt(
    *,
    transcript: str,
    current_question: dict[str, Any] | None,
    current_field: dict[str, Any] | None,
    field_state: dict[str, Any],
    evidence_message_id: str,
    interview_locale: InterviewLocale,
) -> str:
    return json.dumps(
        {
            "question": {
                "questionId": (current_question or {}).get("questionId"),
                "text": (current_question or {}).get("text"),
                "questionType": (current_question or {}).get("questionType"),
                "questionPlan": (current_question or {}).get("questionPlan"),
            },
            "field": {
                "id": (current_field or {}).get("id"),
                "name": (current_field or {}).get("name"),
                "description": (current_field or {}).get("description"),
                "aiAssistPrompt": (current_field or {}).get("aiAssistPrompt"),
                "questionPlan": (current_field or {}).get("questionPlan"),
            },
            "fieldState": {
                "answerState": field_state.get("answerState"),
                "candidateAnswer": field_state.get("candidateAnswer"),
                "recordAnswer": field_state.get("recordAnswer"),
                "capturedItems": field_state.get("capturedItems") or [],
                "missingInformation": field_state.get("missingInformation") or [],
                "pendingQuestionId": field_state.get("pendingQuestionId"),
                "pendingFieldId": field_state.get("pendingFieldId"),
            },
            "transcript": transcript,
            "evidenceTranscriptId": evidence_message_id,
            "interviewLocale": interview_locale,
            "languageInstruction": interview_language_instruction(interview_locale),
        },
        ensure_ascii=False,
    )


def _build_voice_confirmation_prompt(
    *,
    current_question: dict[str, Any] | None,
    candidate_answer: str,
    user_reply: str,
    field_state: dict[str, Any],
    interview_locale: InterviewLocale,
) -> str:
    return json.dumps(
        {
            "task": "candidateAnswerに対するuserReplyの意味だけを分類し、候補を正式確定するかは判断しない",
            "question": current_question or {},
            "candidateAnswer": candidate_answer,
            "userReply": user_reply,
            "interviewLocale": interview_locale,
            "languageInstruction": interview_language_instruction(interview_locale),
            "fieldState": {
                "answerState": field_state.get("answerState"),
                "pendingQuestionId": field_state.get("pendingQuestionId"),
                "pendingFieldId": field_state.get("pendingFieldId"),
                "candidateItems": field_state.get("candidateItems") or field_state.get("capturedItems") or [],
            },
            "instructions": {
                "allowedOutcomes": ["CONFIRM", "REVISE_WITH_CONTENT", "REJECT_WITHOUT_CONTENT", "UNCLEAR"],
                "mustKeepPriorContextForRevision": True,
                "recordAnswerIsAnswerOnly": True,
                "extractCapturedItems": True,
                "revisionConfirmationQuestionIsNatural": True,
            },
        },
        ensure_ascii=False,
    )
