"""Voice session and turn boundary for the supported interview path.

Transcribe + Polly and Nova Sonic use this API boundary for persistence and
session bookkeeping.  Interview meaning is handled by the Structured
Interview service; this module does not contain a second voice answer
evaluator.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from threading import Lock
from time import monotonic
from typing import Any, Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel

from ai_interviewer_api.agents.interview_knowledge.provider import (
    BedrockResponsesStructuredProvider,
    StructuredInterviewProviderError,
)
from ai_interviewer_api.agents.interview_knowledge.service import (
    generate_structured_interview_result,
)
from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.core.config import settings
from ai_interviewer_api.core.interview_configuration import require_interview_configuration
from ai_interviewer_api.core.interview_locale import (
    InterviewLocale,
    localized_interview_fallbacks,
    localized_interview_greeting,
    resolve_interview_locale,
)
from ai_interviewer_api.core.permissions import require_record_action
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.models.domain import VoiceSession, VoiceTurn
from ai_interviewer_api.repositories import (
    voice_session_repository,
    voice_turn_repository,
)
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.common import get_scoped_item
from ai_interviewer_api.schemas.voice import (
    AssistantEventCreate,
    ConnectionEventCreate,
    VoiceTurnCancel,
    VoiceTurnCreate,
    VoiceTurnIntentCreate,
    VoiceSessionCreate,
)
from ai_interviewer_api.services.ai_interview import (
    generate_interview_reply,
    get_interview_state_snapshot,
)
from ai_interviewer_api.services.record_lifecycle import sync_record_status_after_interview


logger = logging.getLogger(__name__)
_VOICE_TURN_LOCKS: dict[str, Lock] = {}
_VOICE_TURN_LOCKS_GUARD = Lock()


class VoiceTurnIntentOutput(BaseModel):
    """Compatibility boundary for callers that omit an explicit turn type.

    Transcribe + Polly always sends ``ANSWER``.  The endpoint remains a small
    shared session-boundary helper for existing clients and Nova's bridge
    contract; it is not used for answer evaluation.
    """

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
    retrieved_sources: list[dict[str, Any]] = field(default_factory=list)

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
            "retrievedSources": self.retrieved_sources,
            "voiceSession": self.voice_session,
            "voiceTurn": self.voice_turn,
        }


def create_voice_session(record_id: str, payload: VoiceSessionCreate, user: UserContext) -> dict:
    started_at = monotonic()
    record = get_scoped_item("records", record_id, user, "record_not_found")
    require_record_action(record, user, "answer")
    knowledge = get_scoped_item("knowledges", record["knowledgeId"], user, "knowledge_not_found")
    require_interview_configuration(knowledge)
    interview_locale = resolve_interview_locale(record, knowledge)
    if not _has_voice_interview_fields(record, user):
        raise HTTPException(status_code=409, detail="voice_session_missing_questions")
    initial_reply = _initialize_initial_question(
        record,
        user,
        interview_locale=interview_locale,
    )
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
    return _get_voice_session_for_user(voice_session_id, user)


def classify_voice_turn_intent(
    voice_session_id: str,
    payload: VoiceTurnIntentCreate,
) -> dict:
    """Classify only an omitted turn type at the shared API boundary."""

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
        interview_state.get("fieldStates", {}).get(field_id, {})
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
        provider = BedrockResponsesStructuredProvider()
        result = VoiceTurnIntentOutput.model_validate(
            provider.request_structured_output(
                schema_name="voice_turn_intent",
                schema=VoiceTurnIntentOutput.model_json_schema(),
                system_prompt=_VOICE_TURN_INTENT_SYSTEM_PROMPT,
                user_payload={"prompt": prompt},
                reasoning_effort=settings.structured_interview_reasoning_effort,
                max_output_tokens=48,
            )
        )
    except (StructuredInterviewProviderError, ValueError) as exc:
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
    if payload.clientTurnId and payload.clientTurnId in session.get("cancelledClientTurnIds", []):
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
    field_id = None
    processing_mode = "control"
    if turn_type == "ANSWER":
        question_id = question_id or session.get("currentQuestionId")
        if question_id != session.get("currentQuestionId"):
            raise HTTPException(status_code=409, detail="turn_question_conflict")
        question = _find_question_by_id(interview_state, question_id)
        if not question_id or question is None:
            raise HTTPException(status_code=409, detail="voice_turn_missing_target_field")
        field_id = question.get("fieldId")
        processing_mode = "structured_interpretation"

    turn = VoiceTurn(
        tenantId=session["tenantId"],
        createdByUserId=session["ownerUserId"] or session["createdByUserId"],
        updatedByUserId=session["ownerUserId"] or session["updatedByUserId"],
        ownerUserId=session["ownerUserId"],
        voiceSessionId=voice_session_id,
        recordId=session["recordId"],
        sequence=int(session.get("lastTurnSequence") or 0) + 1,
        transcript=payload.transcript.strip(),
        rawTranscript=payload.transcript.strip(),
        correctionStatus="NONE",
        sttConfidence=payload.sttConfidence,
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
    _restore_cancelled_turn_artifacts(turn, session)
    turn["processingStatus"] = "cancelled"
    turn["lifecycleStatus"] = "CANCELLED"
    turn["updatedAt"] = utc_now()
    voice_turn_repository.save(turn)
    restored_state = turn.get("baseInterviewState")
    restored_state = restored_state if isinstance(restored_state, dict) else {}
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
    claimed_session = voice_session_repository.claim_initial_reply(voice_session_id)
    if claimed_session is None:
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
        return {"claimed": False, "reason": "not_claimed"}
    return {
        "claimed": True,
        "initialReplyText": claimed_session.get("initialReplyText") or initial_reply_text,
        "initialQuestionId": claimed_session.get("initialQuestionId"),
    }


def process_voice_turn(voice_session_id: str, turn_id: str) -> dict:
    with _voice_turn_lock(turn_id):
        return _process_voice_turn(voice_session_id, turn_id)


def _process_voice_turn(voice_session_id: str, turn_id: str) -> dict:
    session = _get_voice_session_for_internal_use(voice_session_id)
    turn = _get_voice_turn_for_session(turn_id, session)
    lifecycle_status = _voice_turn_lifecycle_status(turn)
    if lifecycle_status == "CANCELLED":
        raise HTTPException(status_code=409, detail="turn_cancelled")
    if lifecycle_status == "COMMITTED":
        return _build_process_result(session, turn).model_dump()
    _ensure_session_accepts_turns(session)
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
        interview_state = get_interview_state_snapshot(record, user).get("interviewState", {})
    turn["baseInterviewState"] = deepcopy(interview_state)
    voice_turn_repository.save(turn)
    try:
        if turn.get("turnType") == "CONTROL":
            return _commit_control_turn(
                session=session,
                turn=turn,
                interview_state=interview_state,
            )
        return _process_structured_voice_turn(
            session=session,
            turn=turn,
            record=record,
            user=user,
            interview_state=interview_state,
        )
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


def _voice_turn_lock(turn_id: str) -> Lock:
    with _VOICE_TURN_LOCKS_GUARD:
        return _VOICE_TURN_LOCKS.setdefault(turn_id, Lock())


def _process_structured_voice_turn(
    *,
    session: dict[str, Any],
    turn: dict[str, Any],
    record: dict[str, Any],
    user: UserContext,
    interview_state: dict[str, Any],
) -> dict:
    """Send one answer through the same semantic engine as text."""

    del interview_state
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
        transcript_assessment = result.get("interviewState", {}).get("lastTranscriptAssessment")
        if isinstance(transcript_assessment, dict):
            turn["rawTranscript"] = transcript_assessment.get("rawTranscript") or turn.get("transcript")
            turn["normalizedTranscript"] = transcript_assessment.get("normalizedTranscript")
            turn["correctionStatus"] = transcript_assessment.get("correctionStatus") or "NONE"
            turn["transcriptAssessment"] = dict(transcript_assessment)
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
        turn.update(
            {
                "processingStatus": "completed",
                "lifecycleStatus": "COMMITTED",
                "responseText": reply_text,
                "action": action,
                "stateVersion": next_state_version,
                "responseId": response_id,
                "questionId": question_id,
                "retrievalPolicy": str(
                    (question or {}).get("retrievalPolicy")
                    or result.get("retrievalPolicy")
                    or "auto"
                ),
                "retrievalExecuted": bool(result.get("retrievalExecuted", False)),
                "retrievedSources": [
                    dict(source)
                    for source in (result.get("retrievedSources") or [])
                    if isinstance(source, dict)
                ],
                "updatedAt": utc_now(),
            }
        )
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
                    "retrievedSources": turn["retrievedSources"],
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
    action = "ask_structured"
    response_id = f"voice-response-{uuid4().hex[:12]}"
    latest_session = _get_voice_session_for_internal_use(session["id"])
    latest_turn = _get_voice_turn_for_session(turn["id"], latest_session)
    if latest_turn.get("processingStatus") == "cancelled":
        raise HTTPException(status_code=409, detail="turn_cancelled")
    next_state_version = int(session.get("stateVersion") or 0) + 1
    turn.update(
        {
            "processingStatus": "completed",
            "lifecycleStatus": "COMMITTED",
            "responseText": reply_text,
            "action": action,
            "stateVersion": next_state_version,
            "responseId": response_id,
            "questionId": current_question_id,
            "retrievalPolicy": None,
            "retrievalExecuted": False,
            "updatedAt": utc_now(),
        }
    )
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
    event_id = _assistant_event_id(voice_session_id, payload)
    item = {
        "id": event_id,
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


def _initialize_initial_question(
    record: dict,
    user: UserContext,
    *,
    interview_locale: InterviewLocale | None = None,
) -> str | None:
    started_at = monotonic()
    locale = interview_locale or resolve_interview_locale(record, {})
    greeting = localized_interview_greeting(locale)
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
        return f"{greeting}{current_question_text}"
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
    return f"{greeting}{initial_question}"


def _find_current_question_text(interview_state: dict) -> str | None:
    question = _find_question_by_id(interview_state, interview_state.get("currentQuestionId"))
    if question is None:
        return None
    text = str(question.get("text") or "").strip()
    return text or None


def _find_question_by_id(
    interview_state: dict[str, Any],
    question_id: str | None,
) -> dict[str, Any] | None:
    if not question_id:
        return None
    return next(
        (
            question
            for question in interview_state.get("askedQuestions", [])
            if question.get("questionId") == question_id
        ),
        None,
    )


def _has_voice_interview_fields(record: dict, user: UserContext) -> bool:
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


def _build_user_context_from_session(session: dict) -> UserContext:
    user_id = session.get("ownerUserId") or session.get("createdByUserId")
    return UserContext(
        user_id=user_id,
        tenant_id=session["tenantId"],
        role=session.get("ownerRole", "interviewer"),
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
            "rawTranscript": turn.get("rawTranscript") or turn["transcript"],
            "normalizedTranscript": None,
            "correctionStatus": "NONE",
            "sttConfidence": turn.get("sttConfidence"),
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
    if not current_question_id or question is None:
        raise HTTPException(status_code=409, detail="voice_turn_missing_target_field")
    message = {
        "id": f"voice-msg-{turn['id']}",
        "tenantId": user.tenant_id,
        "recordId": record["id"],
        "content": turn["transcript"],
        "rawTranscript": turn.get("rawTranscript") or turn["transcript"],
        "normalizedTranscript": None,
        "correctionStatus": "NONE",
        "sttConfidence": turn.get("sttConfidence"),
        "role": "user",
        "isActualUtterance": True,
        "turnType": "ANSWER",
        "answerToQuestionId": current_question_id,
        "answerToFieldId": current_field_id,
        "questionType": question.get("questionType"),
        "targetType": question.get("targetType"),
        "targetId": question.get("targetId"),
        "voiceSessionId": turn["voiceSessionId"],
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
        "retrievedSources": detail.get("retrievedSources") or [],
    }
    return store.upsert("messages", message)


def _assistant_event_id(voice_session_id: str, payload: AssistantEventCreate) -> str:
    """Return an idempotency key for one assistant response event.

    Runtime reconnects may resend the same event. A response id is stable for
    one generated response, while event type and generation distinguish its
    lifecycle events. Keeping the key deterministic also deduplicates across
    API workers, where an in-process lock would not be sufficient.
    """

    if not payload.responseId:
        return f"voice-assistant-event-{uuid4().hex[:12]}"
    identity = "\x1f".join(
        (
            voice_session_id,
            payload.eventType,
            payload.responseId,
            str(payload.generation) if payload.generation is not None else "",
        )
    )
    digest = sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"voice-assistant-event-{digest}"


def _should_persist_voice_assistant_message(payload: AssistantEventCreate) -> bool:
    if payload.eventType != "assistant_transcript_final" or not payload.transcript:
        return False
    return (payload.detail or {}).get("action") != "finish"


def _build_process_result(session: dict, turn: dict) -> VoiceTurnProcessResult:
    return VoiceTurnProcessResult(
        turn_id=turn["id"],
        response_id=turn.get("responseId") or f"voice-response-{turn['id']}",
        text=turn.get("responseText") or "",
        action=turn.get("action") or "ask_structured",
        question_id=turn.get("questionId"),
        state_version=int(turn.get("stateVersion") or session.get("stateVersion") or 0),
        retrieval_policy=turn.get("retrievalPolicy"),
        retrieval_executed=bool(turn.get("retrievalExecuted", False)),
        retrieved_sources=[
            dict(source)
            for source in (turn.get("retrievedSources") or [])
            if isinstance(source, dict)
        ],
        voice_session=session,
        voice_turn=turn,
    )
