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
from time import monotonic
from typing import Any, Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field

from ai_interviewer_api.agents.common.strands_runtime import (
    create_agent,
    create_voice_evaluation_bedrock_model,
)
from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.core.config import settings
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
    VoiceTurnCancel,
    VoiceTurnCreate,
)
from ai_interviewer_api.services.ai_interview import (
    _field_retrieval_policy,
    _persist_interview_state,
    generate_interview_reply,
    get_interview_state_snapshot,
)
from ai_interviewer_api.services.interview_answer_processor import (
    AnswerEvaluation,
    ConfirmationEvaluation,
    InterviewAnswerProcessor,
    compose_record_answer,
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
    clarification_question: str | None = None
    captured_items: list[dict[str, Any]] = field(default_factory=list)
    evaluation_status: Literal["OK", "EVALUATION_ERROR"] = "OK"


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


class VoiceConfirmationEvaluationOutput(BaseModel):
    outcome: Literal["CONFIRM", "REVISE_WITH_CONTENT", "REJECT_WITHOUT_CONTENT", "UNCLEAR"]
    revised_answer: str | None = None
    record_answer: str | None = None
    clarification_question: str | None = None
    captured_items: list[CapturedInterviewItem] = Field(default_factory=list)


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


def create_voice_session(record_id: str, payload: VoiceSessionCreate, user: UserContext) -> dict:
    record = get_scoped_item("records", record_id, user, "record_not_found")
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
        recordId=record_id,
        provider=payload.provider,
        currentQuestionId=current_question_id,
        initialReplyText=initial_reply,
        initialQuestionId=current_question_id if initial_reply else None,
        initialReplyStatus="pending" if initial_reply else None,
        stateVersion=1 if initial_reply else 0,
        startedAt=utc_now(),
    ).model_dump()
    voice_session_repository.save(session)
    return session


def get_voice_session(voice_session_id: str, user: UserContext) -> dict:
    session = _get_voice_session_for_user(voice_session_id, user)
    return session


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
        if not question_id or not field_id:
            raise HTTPException(status_code=409, detail="voice_turn_missing_target_field")
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
    snapshot = get_interview_state_snapshot(record, user)
    interview_state = snapshot.get("interviewState", {})
    turn["baseInterviewState"] = deepcopy(interview_state)
    voice_turn_repository.save(turn)
    if turn.get("turnType") == "CONTROL":
        try:
            current_question_id = interview_state.get("currentQuestionId")
            result_payload = {
                "replyText": "承知しました。",
                "action": "ask_configured_field",
                "questionId": current_question_id,
                "retrievalPolicy": None,
                "retrievalExecuted": False,
            }
            reply_text = result_payload["replyText"]
            action = result_payload["action"]
            current_question_id = result_payload["questionId"]
            response_id = f"voice-response-{uuid4().hex[:12]}"
            latest_session = _get_voice_session_for_internal_use(voice_session_id)
            latest_turn = _get_voice_turn_for_session(turn_id, latest_session)
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
                        "turnId": turn_id,
                        "action": action,
                        "questionId": current_question_id,
                        "source": "control_turn_commit",
                    },
                ),
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
    current_question = _find_question_by_id(interview_state, turn.get("answerToQuestionId"))
    current_field_id = turn.get("answerToFieldId")
    if not current_question or not current_field_id:
        raise HTTPException(status_code=409, detail="voice_turn_missing_target_field")
    field_state = _ensure_voice_field_state(interview_state, current_field_id)
    logger.info(
        "voice_user_message_saved voice_session_id=%s turn_id=%s question_id=%s state_version=%s",
        voice_session_id,
        turn_id,
        turn.get("answerToQuestionId"),
        session.get("stateVersion"),
    )

    try:
        logger.info(
            "voice_interview_process_started voice_session_id=%s turn_id=%s question_id=%s state_version=%s",
            voice_session_id,
            turn_id,
            turn.get("answerToQuestionId"),
            session.get("stateVersion"),
        )
        if field_state.get("answerState") == ANSWER_STATE_AWAITING_CONFIRMATION:
            result_payload = _process_confirmation_turn(
                record=record,
                session=session,
                turn=turn,
                user=user,
                interview_state=interview_state,
                field_state=field_state,
                current_question=current_question,
                user_message=user_message,
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
            "voice_interview_process_completed voice_session_id=%s turn_id=%s question_id=%s state_version=%s retrieval_policy=%s retrieval_executed=%s response_id=%s",
            voice_session_id,
            turn_id,
            current_question_id,
            next_state_version,
            retrieval_policy,
            retrieval_executed,
            response_id,
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
    snapshot = get_interview_state_snapshot(record, user)
    interview_state = snapshot.get("interviewState", {})
    current_question_text = _find_current_question_text(interview_state)
    if current_question_text:
        return f"{INITIAL_VOICE_GREETING}{current_question_text}"
    if interview_state.get("status") == "completed":
        return None
    result = generate_interview_reply(record, user, persist_assistant_messages=False)
    initial_question = "\n".join(result.reply_chunks).strip()
    if not initial_question:
        return None
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
    return any(
        row
        for row in store.list("knowledge_fields", user.tenant_id)
        if row.get("knowledgeId") == record["knowledgeId"] and row.get("askByAi")
    )


def _get_voice_session_for_user(voice_session_id: str, user: UserContext) -> dict:
    session = _get_voice_session_for_internal_use(voice_session_id)
    if session.get("ownerUserId") != user.user_id:
        raise HTTPException(status_code=403, detail="voice_session_forbidden")
    get_scoped_item("records", session["recordId"], user, "record_not_found")
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
        role="interviewer",
        display_name=user_id,
    )


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
        }
        return store.upsert("messages", message)
    if not current_question_id or not current_field_id:
        raise HTTPException(status_code=409, detail="voice_turn_missing_target_field")
    question = _find_question_by_id(interview_state, current_question_id)
    question_type = question.get("questionType") if question else None

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
        "questionType": None,
        "fieldId": None,
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
) -> dict[str, Any]:
    evaluation_started_at = monotonic()
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
        "answer_evaluation_started voice_session_id=%s turn_id=%s question_id=%s field_id=%s playback_generation_id=%s input_state=%s monotonic_timestamp_ms=%s",
        session["id"],
        turn["id"],
        current_question_id,
        current_field_id,
        None,
        "ANSWER_PROCESSING",
        int(monotonic() * 1000),
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
        evaluation = run_with_evaluation_deadline(
            lambda: _evaluate_voice_answer_candidate(
                transcript=turn["transcript"],
                current_question=current_question,
                current_field=current_field,
                field_state=field_state,
                evidence_message_id=user_message["id"],
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
            follow_up_question=_build_evaluation_fallback_prompt(current_question),
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
    logger.info(
        "answer_evaluation_completed voice_session_id=%s turn_id=%s question_id=%s field_id=%s evaluation_request_id=%s playback_generation_id=%s input_state=%s monotonic_timestamp_ms=%s decision=%s is_relevant=%s is_sufficient=%s retrieval_policy=%s retrieval_executed=%s answer_evaluation_total_ms=%s knowledge_retrieval_ms=%s",
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
        try:
            decision = _evaluate_confirmation_response(
                current_question=current_question,
                candidate_answer=kwargs["candidate_answer"],
                user_reply=kwargs["user_reply"],
                field_state=field_state,
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
                evaluation_status="EVALUATION_ERROR",
            )
        return ConfirmationEvaluation(
            outcome=decision.outcome,
            revised_answer=decision.revised_answer,
            record_answer=decision.record_answer,
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
) -> VoiceAnswerEvaluation:
    prompt = _build_voice_answer_evaluation_prompt(
        transcript=transcript,
        current_question=current_question,
        current_field=current_field,
        field_state=field_state,
        evidence_message_id=evidence_message_id,
    )
    result = _run_voice_structured_output(
        system_prompt=_voice_answer_evaluation_system_prompt(),
        prompt=prompt,
        output_model=VoiceAnswerEvaluationOutput,
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
) -> VoiceConfirmationEvaluation:
    prompt = _build_voice_confirmation_prompt(
        current_question=current_question,
        candidate_answer=candidate_answer,
        user_reply=user_reply,
        field_state=field_state,
    )
    result = _run_voice_structured_output(
        system_prompt=_voice_confirmation_system_prompt(),
        prompt=prompt,
        output_model=VoiceConfirmationEvaluationOutput,
    )
    return VoiceConfirmationEvaluation(
        outcome=result.outcome,
        revised_answer=result.revised_answer.strip() if isinstance(result.revised_answer, str) and result.revised_answer.strip() else None,
        record_answer=result.record_answer.strip() if isinstance(result.record_answer, str) and result.record_answer.strip() else None,
        clarification_question=result.clarification_question.strip() if isinstance(result.clarification_question, str) and result.clarification_question.strip() else None,
        captured_items=[item.model_dump() for item in result.captured_items],
    )


def _build_evaluation_fallback_prompt(current_question: dict[str, Any] | None) -> str:
    question_text = str(current_question.get("text") or "").strip() if isinstance(current_question, dict) else ""
    if question_text:
        return f"回答処理に一時的な問題がありました。もう一度、{question_text}"
    return "回答処理に一時的な問題がありました。もう一度お答えください。"


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


def _run_voice_structured_output(*, system_prompt: str, prompt: str, output_model: type[BaseModel]) -> BaseModel:
    invocation_started_at = monotonic()
    agent = create_agent(
        model=create_voice_evaluation_bedrock_model(),
        system_prompt=system_prompt,
        tools=[],
        hooks=[],
        name="Voice Interview Evaluator",
        description="Evaluates voice interview answers and confirmation replies.",
    )
    try:
        result = agent(
            prompt,
            invocation_state={},
            structured_output_model=output_model,
        )
    except Exception:
        logger.info(
            "voice_bedrock_invocation_failed bedrock_connect_or_invoke_ms=%s",
            round((monotonic() - invocation_started_at) * 1000, 1),
        )
        raise
    model_completed_at = monotonic()
    logger.info(
        "voice_bedrock_model_completed bedrock_model_ms=%s",
        round((model_completed_at - invocation_started_at) * 1000, 1),
    )
    parse_started_at = monotonic()
    structured_output = getattr(result, "structured_output", None)
    if isinstance(structured_output, output_model):
        parsed = structured_output
        logger.info("voice_response_parse_completed response_parse_ms=%s", round((monotonic() - parse_started_at) * 1000, 1))
        return parsed
    if structured_output is not None:
        parsed = output_model.model_validate(structured_output)
        logger.info("voice_response_parse_completed response_parse_ms=%s", round((monotonic() - parse_started_at) * 1000, 1))
        return parsed
    if isinstance(result, output_model):
        logger.info("voice_response_parse_completed response_parse_ms=%s", round((monotonic() - parse_started_at) * 1000, 1))
        return result
    text = str(result).strip()
    if text:
        try:
            parsed = output_model.model_validate_json(text)
        except Exception:  # noqa: BLE001 - SDK output may fail with multiple parse errors
            parsed = output_model.model_validate(json.loads(text))
        logger.info(
            "voice_response_parse_completed response_parse_ms=%s",
            round((monotonic() - parse_started_at) * 1000, 1),
        )
        return parsed
    raise ValueError("structured output missing")


def _voice_answer_evaluation_system_prompt() -> str:
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
        "短い名詞回答は、意味を変えずに不要な文末の丁寧表現を除き、"
        "normalized_answerを簡潔な名詞句にしてください。\n"
        "CONFIRMABLEでは、質問と項目の意味に合う自然なconfirmation_questionを返してください。"
        "固定の項目名辞書や定型的な引用表現に依存せず、候補の意味を変えてはいけません。\n"
        "NEEDS_MORE_INFORMATIONではmissing_informationと、次に答える内容が分かる"
        "具体的なfollow_up_questionを必ず返してください。元の質問をそのまま繰り返してはいけません。\n"
        "ユーザーが『何を答えればよいか』『どんな内容か』と案内を求めた場合は、"
        "回答候補にせずNOT_ANSWERとし、明示された要件の範囲で答え方を説明する"
        "follow_up_questionを返してください。要件がなければ、質問に沿った一般的な例を簡潔に示してください。"
    )


def _voice_confirmation_system_prompt() -> str:
    return (
        "あなたは音声インタビューの確認返答判定器です。\n"
        "確認待ちの候補回答とユーザー返答を見て、"
        "CONFIRM / REVISE_WITH_CONTENT / REJECT_WITHOUT_CONTENT / UNCLEAR を返してください。\n"
        "CONFIRMでは候補の回答内容だけをrecord_answerに返してください。"
        "REVISE_WITH_CONTENTでは、訂正後の回答内容だけをrecord_answerに返し、"
        "『はい』『いいえ』『違います』等の会話制御語やメタ説明を含めないでください。"
        "同時に訂正後のcaptured_itemsを返してください。"
    )


def _build_voice_answer_evaluation_prompt(
    *,
    transcript: str,
    current_question: dict[str, Any] | None,
    current_field: dict[str, Any] | None,
    field_state: dict[str, Any],
    evidence_message_id: str,
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
                "whenNoExplicitRequirements": (
                    "発話に質問に関連する具体的な事実が1つでもあればCONFIRMABLE。"
                    "一般論から不足項目を作らない。"
                ),
            },
            "decisionExamples": [
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


def _build_voice_confirmation_prompt(
    *,
    current_question: dict[str, Any] | None,
    candidate_answer: str,
    user_reply: str,
    field_state: dict[str, Any],
) -> str:
    return json.dumps(
        {
            "question": current_question or {},
            "candidateAnswer": candidate_answer,
            "userReply": user_reply,
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
            },
        },
        ensure_ascii=False,
    )
