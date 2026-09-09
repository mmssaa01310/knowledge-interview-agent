"""Voice session and turn boundary for the supported interview path.

Transcribe + Polly and Nova Sonic use this API boundary for persistence and
session bookkeeping.  Interview meaning is handled by the Structured
Interview service; this module does not contain a second voice answer
evaluator.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from queue import Queue
from threading import Lock, RLock, Thread
from time import monotonic
from typing import Any, Literal
from uuid import uuid4

from fastapi import HTTPException
from ai_interviewer_api.agents.interview_knowledge.coordinator import (
    is_current_question_confirmation_target,
)
from ai_interviewer_api.agents.interview_knowledge.service import (
    generate_structured_interview_result,
    start_fast_interview_turn,
    start_speculative_retrieval_for_interview_turn,
)
from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.core.config import settings
from ai_interviewer_api.core.interview_configuration import require_interview_configuration
from ai_interviewer_api.core.interview_locale import (
    InterviewLocale,
    localized_interview_fallbacks,
    localized_interview_greeting,
    localized_interview_transcript_retry,
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
from ai_interviewer_api.services.conversation_policy import (
    CanonicalAction,
    CanonicalIntentDecision,
    resolve_canonical_action,
    resolve_canonical_intent,
)
from ai_interviewer_api.services.interview_state_transition import commit_interview_state
from ai_interviewer_api.services.record_lifecycle import sync_record_status_after_interview
from ai_interviewer_api.services.voice_transcript_feedback import (
    build_transcribe_polly_transcript_feedback,
)


logger = logging.getLogger(__name__)
_VOICE_TURN_LOCKS: dict[str, Any] = {}
_VOICE_TURN_LOCKS_GUARD = Lock()


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
    latency_metrics: dict[str, float | int] = field(default_factory=dict)

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
            "latencyMetrics": self.latency_metrics,
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
    """Compatibility response backed by the canonical Conversation Policy."""

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
    current_target = _voice_target_from_question(current_question)
    pending_confirmation = is_current_question_confirmation_target(
        interview_state,
        current_question,
    )
    user = _build_user_context_from_session(session)
    recent_conversation = [
        message
        for message in store.list("messages", user.tenant_id)
        if message.get("recordId") == session.get("recordId")
    ]
    decision = resolve_canonical_intent(
        utterance=transcript,
        current_question=current_question,
        current_target=current_target,
        pending_confirmation=pending_confirmation,
        recent_conversation=recent_conversation,
    )
    action = resolve_canonical_action(
        decision.dialogueAct,
        pending_confirmation=pending_confirmation,
        current_target=current_target,
    )
    return {"turnType": "CONTROL" if action == "HANDLE_CONTROL" else "ANSWER"}


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
    # The Realtime sideband and a reconnect can submit the same completed
    # transcript at nearly the same time. Serialize the lookup-and-save pair
    # for a client id so both requests cannot create separate VoiceTurn rows.
    if payload.clientTurnId:
        with _voice_turn_lock(f"client-turn:{voice_session_id}:{payload.clientTurnId}"):
            return _create_voice_turn(voice_session_id, payload)
    return _create_voice_turn(voice_session_id, payload)


def _create_voice_turn(voice_session_id: str, payload: VoiceTurnCreate) -> dict:
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
                logger.info(
                    "voice_turn_reused voice_session_id=%s turn_id=%s client_turn_id=%s sequence=%s",
                    voice_session_id,
                    existing.get("id"),
                    payload.clientTurnId,
                    existing.get("sequence"),
                )
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
        if question is None and question_id == session.get("currentQuestionId"):
            provisional_question = session.get("provisionalQuestion")
            if (
                isinstance(provisional_question, dict)
                and provisional_question.get("questionId") == question_id
            ):
                question = provisional_question
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
    logger.info(
        "voice_turn_created voice_session_id=%s turn_id=%s client_turn_id=%s sequence=%s",
        voice_session_id,
        turn["id"],
        payload.clientTurnId,
        turn["sequence"],
    )
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


def process_voice_turn(
    voice_session_id: str,
    turn_id: str,
    *,
    on_stream_started: Callable[[str], None] | None = None,
    on_question_delta: Callable[[str], None] | None = None,
) -> dict:
    with _voice_turn_lock(turn_id):
        return _process_voice_turn(
            voice_session_id,
            turn_id,
            on_stream_started=on_stream_started,
            on_question_delta=on_question_delta,
        )


def stream_process_voice_turn(voice_session_id: str, turn_id: str) -> Iterator[bytes]:
    """Yield turn processing events while Question Generator emits deltas.

    The semantic turn is still committed by ``process_voice_turn`` before the
    final event is emitted.  Only the read-only question text deltas are sent
    early; callers must not treat a delta as committed interview state.
    """

    events: Queue[dict[str, Any] | None] = Queue()

    def emit(event: dict[str, Any]) -> None:
        events.put(event)

    def worker() -> None:
        try:
            payload = process_voice_turn(
                voice_session_id,
                turn_id,
                on_stream_started=lambda response_id: emit(
                    {"type": "started", "responseId": response_id}
                ),
                on_question_delta=lambda delta: emit(
                    {"type": "delta", "text": delta}
                ),
            )
            emit({"type": "complete", "payload": payload})
        except HTTPException as exc:
            emit(
                {
                    "type": "error",
                    "status": exc.status_code,
                    "detail": str(exc.detail),
                }
            )
        except Exception:  # noqa: BLE001 - stream boundary must close cleanly
            logger.exception(
                "voice_turn_stream_failed voice_session_id=%s turn_id=%s",
                voice_session_id,
                turn_id,
            )
            emit(
                {
                    "type": "error",
                    "status": 500,
                    "detail": "turn_process_failed",
                }
            )
        finally:
            events.put(None)

    Thread(
        target=worker,
        name=f"voice-turn-stream-{turn_id}",
        daemon=True,
    ).start()
    while True:
        event = events.get()
        if event is None:
            return
        yield (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )


def _process_voice_turn(
    voice_session_id: str,
    turn_id: str,
    *,
    on_stream_started: Callable[[str], None] | None = None,
    on_question_delta: Callable[[str], None] | None = None,
) -> dict:
    api_started_at = monotonic()
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
    interview_state = store.get("interview_states", f"interview-state-{record['id']}")
    if interview_state is None:
        interview_state = get_interview_state_snapshot(record, user).get("interviewState", {})
    turn["baseInterviewState"] = deepcopy(interview_state)
    try:
        current_question = _find_question_by_id(
            interview_state,
            turn.get("answerToQuestionId"),
        )
        if current_question is None:
            provisional_question = _get_provisional_question(session)
            if provisional_question and provisional_question.get("questionId") == turn.get(
                "answerToQuestionId"
            ):
                current_question = provisional_question
        canonical_intent, canonical_action = _resolve_voice_turn_policy(
            turn=turn,
            interview_state=interview_state,
            current_question=current_question,
            record=record,
            user=user,
        )
        turn.update(
            {
                "canonicalIntent": canonical_intent.dialogueAct,
                "canonicalAction": canonical_action,
                "canonicalRouterLatencyMs": canonical_intent.latency_ms,
                "fastCheckExecuted": False,
            }
        )
        if canonical_action == "HANDLE_CONTROL" and turn.get("turnType") != "CONTROL":
            # A provider may omit the legacy coarse turn type.  The canonical
            # policy must still prevent this utterance from being persisted as
            # an answer before the existing control action runs.
            turn.update(
                {
                    "turnType": "CONTROL",
                    "answerToQuestionId": None,
                    "answerToFieldId": None,
                    "processingMode": "control",
                }
            )
        logger.info(
            "conversation_policy_resolved canonical_turn_id=%s voice_session_id=%s "
            "canonical_intent=%s canonical_action=%s current_question_id=%s "
            "state_version_before=%s router_ms=%s",
            turn.get("id"),
            session.get("id"),
            canonical_intent.dialogueAct,
            canonical_action,
            current_question.get("questionId") if current_question else None,
            session.get("stateVersion"),
            canonical_intent.latency_ms,
        )
        voice_turn_repository.save(turn)
        user_message = _save_voice_user_message(record, turn, user)
        if canonical_action == "HANDLE_CONTROL":
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
            user_message=user_message,
            api_started_at=api_started_at,
            canonical_intent=canonical_intent,
            canonical_action=canonical_action,
            on_stream_started=on_stream_started,
            on_question_delta=on_question_delta,
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


def _voice_turn_lock(turn_id: str) -> Any:
    with _VOICE_TURN_LOCKS_GUARD:
        return _VOICE_TURN_LOCKS.setdefault(turn_id, RLock())


def _resolve_voice_turn_policy(
    *,
    turn: dict[str, Any],
    interview_state: Mapping[str, Any],
    current_question: Mapping[str, Any] | None,
    record: Mapping[str, Any],
    user: UserContext,
) -> tuple[CanonicalIntentDecision, CanonicalAction]:
    """Resolve the canonical intent before any action-specific processing."""

    if turn.get("turnType") == "CONTROL":
        decision = CanonicalIntentDecision(
            dialogueAct="CONVERSATION_REQUEST",
            latency_ms=0.0,
            provider="explicit_turn_type",
        )
        return decision, resolve_canonical_action(
            decision.dialogueAct,
            pending_confirmation=False,
            turn_type="CONTROL",
        )

    current_target = _voice_target_from_question(current_question)
    pending_confirmation = is_current_question_confirmation_target(
        interview_state,
        current_question,
    )
    recent_conversation = [
        message
        for message in store.list("messages", user.tenant_id)
        if message.get("recordId") == record.get("id")
    ]
    decision = resolve_canonical_intent(
        utterance=str(turn.get("transcript") or ""),
        current_question=current_question,
        current_target=current_target,
        pending_confirmation=pending_confirmation,
        recent_conversation=recent_conversation,
    )
    return decision, resolve_canonical_action(
        decision.dialogueAct,
        pending_confirmation=pending_confirmation,
        current_target=current_target,
    )


def _voice_target_from_question(
    question: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not question:
        return None
    return {
        "targetType": question.get("targetType"),
        "targetId": question.get("targetId"),
        "label": question.get("targetLabel") or question.get("label"),
    }


def _process_structured_voice_turn(
    *,
    session: dict[str, Any],
    turn: dict[str, Any],
    record: dict[str, Any],
    user: UserContext,
    interview_state: dict[str, Any],
    user_message: dict[str, Any],
    api_started_at: float,
    canonical_intent: CanonicalIntentDecision,
    canonical_action: CanonicalAction,
    on_stream_started: Callable[[str], None] | None = None,
    on_question_delta: Callable[[str], None] | None = None,
) -> dict:
    """Send one answer through the same semantic engine as text."""

    speculative_retrieval = None
    response_id = f"voice-response-{uuid4().hex[:12]}"
    fast_result = None
    try:
        knowledge = store.get("knowledges", record.get("knowledgeId")) or {}
        if on_stream_started is not None:
            on_stream_started(response_id)
        current_question = _find_question_by_id(
            interview_state,
            turn.get("answerToQuestionId"),
        )
        if current_question is None:
            provisional_question = _get_provisional_question(session)
            if provisional_question and provisional_question.get("questionId") == turn.get(
                "answerToQuestionId"
            ):
                current_question = provisional_question
        fast_eligible = (
            bool(current_question)
            and str((current_question or {}).get("targetType") or "")
            not in {"closing", "transcript_confirmation", "contradiction"}
        )
        if (
            settings.structured_interview_fast_path_enabled
            and fast_eligible
            and canonical_action == "PROCESS_ANSWER"
        ):
            fast_state = deepcopy(interview_state)
            provisional_keys = session.get("provisionalAnsweredTargetKeys")
            if isinstance(provisional_keys, list):
                fast_state["provisionalAnsweredTargetKeys"] = [
                    str(key)
                    for key in provisional_keys
                    if str(key).strip()
                ]
            fast_result = start_fast_interview_turn(
                record,
                knowledge,
                user,
                state=fast_state,
                current_question=current_question or {},
                latest_user_message=user_message,
                on_background_validation=lambda payload: _handle_fast_background_validation(
                    session["id"],
                    turn["id"],
                    payload,
                ),
                on_question_delta=on_question_delta,
            )
            result = {
                "status": "in_progress",
                "action": fast_result.action,
                "reply": fast_result.reply,
                "question": fast_result.question,
                "interviewState": fast_result.provisional_state or interview_state,
                "retrievalPolicy": fast_result.retrieval_policy,
                "retrievalExecuted": fast_result.retrieval_executed,
                "retrievedSources": fast_result.retrieved_sources,
                "latencyMetrics": fast_result.latency_metrics,
            }
            turn["fastAssessment"] = fast_result.assessment.model_dump()
            turn["fastCanProceed"] = fast_result.can_proceed
            turn["fastCheckExecuted"] = True
            turn["backgroundValidationStatus"] = "pending"
        else:
            speculative_retrieval = start_speculative_retrieval_for_interview_turn(
                record,
                knowledge,
                user,
            )
            result = generate_structured_interview_result(
                record,
                knowledge,
                user,
                persist_assistant_messages=False,
                speculative_retrieval=speculative_retrieval,
                canonical_intent=canonical_intent.dialogueAct,
                canonical_action=canonical_action,
                on_question_delta=on_question_delta,
            )
        latency_metrics = {
            str(name): value
            for name, value in (result.get("latencyMetrics") or {}).items()
            if isinstance(value, (int, float))
        }
        latency_metrics["conversation_router_ms"] = canonical_intent.latency_ms
        sync_record_status_after_interview(record, result.get("status"), user)
        reply_text = str(result.get("reply") or "").strip()
        action = str(result.get("action") or "ask_structured").strip() or "ask_structured"
        structured_dialogue_act = result.get("structuredDialogueAct")
        if isinstance(structured_dialogue_act, str) and structured_dialogue_act:
            turn["structuredDialogueAct"] = structured_dialogue_act
            turn["dialogueActMismatch"] = structured_dialogue_act != turn.get(
                "canonicalIntent"
            )
        transcript_assessment = result.get("interviewState", {}).get("lastTranscriptAssessment")
        if isinstance(transcript_assessment, dict):
            turn["rawTranscript"] = transcript_assessment.get("rawTranscript") or turn.get("transcript")
            turn["normalizedTranscript"] = transcript_assessment.get("normalizedTranscript")
            turn["correctionStatus"] = transcript_assessment.get("correctionStatus") or "NONE"
            turn["transcriptAssessment"] = dict(transcript_assessment)
        if session.get("provider") == "transcribe_polly":
            voice_feedback = build_transcribe_polly_transcript_feedback(
                result,
                turn,
                field_labels=_voice_interview_field_labels(knowledge, user),
                locale=resolve_interview_locale(record, knowledge),
                force_retry=reply_text
                == localized_interview_transcript_retry(
                    resolve_interview_locale(record, knowledge)
                ),
            )
            if voice_feedback:
                reply_text = voice_feedback
        question = result.get("question") if isinstance(result.get("question"), dict) else None
        question_id = question.get("questionId") if question else None
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
        latency_metrics["api_total_ms"] = round((monotonic() - api_started_at) * 1000, 1)
        latest_turn_fields: dict[str, Any] = {}
        if fast_result is not None:
            latest_turn_fields = voice_turn_repository.get(turn["id"]) or {}
            latency_metrics.update(
                {
                    str(name): value
                    for name, value in (latest_turn_fields.get("latencyMetrics") or {}).items()
                    if isinstance(value, (int, float))
                }
            )
            for key in (
                "backgroundValidationStatus",
                "backgroundCanProceed",
                "backgroundAgreesWithFast",
                "clarificationEnqueued",
            ):
                if key in latest_turn_fields:
                    turn[key] = latest_turn_fields[key]
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
                "latencyMetrics": latency_metrics,
                "updatedAt": utc_now(),
            }
        )
        logger.info(
            "conversation_turn_trace canonical_turn_id=%s voice_session_id=%s "
            "user_utterance_chars=%s canonical_intent=%s canonical_action=%s "
            "fast_check_executed=%s structured_dialogue_act=%s dialogue_act_mismatch=%s "
            "next_question_id=%s",
            turn.get("id"),
            session.get("id"),
            len(str(turn.get("transcript") or "")),
            turn.get("canonicalIntent"),
            turn.get("canonicalAction"),
            turn.get("fastCheckExecuted"),
            turn.get("structuredDialogueAct"),
            turn.get("dialogueActMismatch"),
            question_id,
        )
        voice_turn_repository.save(turn)
        logger.info(
            "voice_turn_api_latency turn_id=%s interpreter_ms=%s medium_retry_ms=%s "
            "patch_repair_ms=%s state_transition_ms=%s retrieval_ms=%s "
            "question_generation_ms=%s api_total_ms=%s interpreter_start=%s "
            "interpreter_end=%s rag_start=%s rag_end=%s question_llm_start=%s "
            "question_first_token=%s question_first_sentence=%s question_llm_end=%s "
            "interpreter_calls=%s "
            "medium_retry_calls=%s patch_repair_calls=%s retrieval_calls=%s "
            "question_generation_calls=%s speculative_retrieval_ms=%s "
            "retrieval_reused=%s retrieval_fallbacks=%s",
            turn["id"],
            latency_metrics.get("interpreter_ms", 0),
            latency_metrics.get("medium_retry_ms", 0),
            latency_metrics.get("patch_repair_ms", 0),
            latency_metrics.get("state_transition_ms", 0),
            latency_metrics.get("retrieval_ms", 0),
            latency_metrics.get("question_generation_ms", 0),
            latency_metrics.get("api_total_ms", 0),
            latency_metrics.get("interpreter_start_ms"),
            latency_metrics.get("interpreter_end_ms"),
            latency_metrics.get("rag_start_ms"),
            latency_metrics.get("rag_end_ms"),
            latency_metrics.get("question_llm_start_ms"),
            latency_metrics.get("question_first_token_ms"),
            latency_metrics.get("question_first_sentence_ms"),
            latency_metrics.get("question_llm_end_ms"),
            latency_metrics.get("interpreter_calls", 0),
            latency_metrics.get("medium_retry_calls", 0),
            latency_metrics.get("patch_repair_calls", 0),
            latency_metrics.get("retrieval_calls", 0),
            latency_metrics.get("question_generation_calls", 0),
            latency_metrics.get("speculative_retrieval_ms", 0),
            latency_metrics.get("retrieval_reused", 0),
            latency_metrics.get("retrieval_fallbacks", 0),
        )
        session["currentQuestionId"] = question_id
        if fast_result is not None and fast_result.can_proceed and question is not None:
            background_completed = (
                latest_turn_fields.get("backgroundValidationStatus") == "completed"
            )
            if background_completed:
                session.pop("provisionalQuestion", None)
                session.pop("provisionalSourceTurnId", None)
            else:
                session["provisionalQuestion"] = deepcopy(question)
                session["provisionalSourceTurnId"] = turn["id"]
            latest_overlay_session = voice_session_repository.get(session["id"]) or {}
            existing_provisional_keys = latest_overlay_session.get(
                "provisionalAnsweredTargetKeys"
            )
            provisional_keys = (
                list(existing_provisional_keys)
                if isinstance(existing_provisional_keys, list)
                else []
            )
            if (
                latest_turn_fields.get("backgroundValidationStatus") != "completed"
                and fast_result.source_target_key
                and fast_result.source_target_key not in provisional_keys
            ):
                provisional_keys.append(fast_result.source_target_key)
            session["provisionalAnsweredTargetKeys"] = provisional_keys
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
                    "fastPath": fast_result is not None,
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
    finally:
        if speculative_retrieval is not None and not speculative_retrieval.future.done():
            cancelled = speculative_retrieval.cancel()
            logger.info(
                "interview_speculative_rag_cancelled turn_id=%s cancelled=%s",
                turn.get("id"),
                cancelled,
            )


def _get_provisional_question(session: Mapping[str, Any]) -> dict[str, Any] | None:
    value = session.get("provisionalQuestion")
    return deepcopy(value) if isinstance(value, dict) else None


def _handle_fast_background_validation(
    voice_session_id: str,
    turn_id: str,
    payload: Mapping[str, Any],
) -> None:
    # ``process_voice_turn`` owns this turn lock while it commits the Fast
    # foreground result.  Waiting here keeps the late Background write from
    # racing with that commit; the foreground never waits for this callback.
    with _voice_turn_lock(turn_id):
        _persist_fast_background_validation(voice_session_id, turn_id, payload)


def _persist_fast_background_validation(
    voice_session_id: str,
    turn_id: str,
    payload: Mapping[str, Any],
) -> None:
    """Persist background telemetry without changing the foreground reply."""

    turn = voice_turn_repository.get(turn_id)
    if turn is None:
        return
    source_turn_id = str(payload.get("sourceTurnId") or turn_id)
    background_status = str(payload.get("backgroundStatus") or "failed")
    turn["backgroundValidationStatus"] = background_status
    turn["backgroundCanProceed"] = bool(payload.get("backgroundCanProceed", False))
    turn["backgroundAgreesWithFast"] = bool(
        payload.get("backgroundAgreesWithFast", False)
    )
    turn["clarificationEnqueued"] = bool(payload.get("clarificationEnqueued", False))
    background_merge = payload.get("backgroundMerge")
    if isinstance(background_merge, Mapping):
        turn["backgroundMergeDecision"] = background_merge.get("mergeDecision")
        turn["backgroundBaseStateVersion"] = background_merge.get("baseStateVersion")
        turn["backgroundCurrentStateVersion"] = background_merge.get("currentStateVersion")
        turn["backgroundResultingStateVersion"] = background_merge.get("resultingStateVersion")
        turn["backgroundAppliedFields"] = list(background_merge.get("appliedFields") or [])
        turn["backgroundDiscardedFields"] = list(
            background_merge.get("discardedFields") or []
        )
    transcript_assessment = payload.get("transcriptAssessment")
    if isinstance(transcript_assessment, Mapping):
        turn["rawTranscript"] = transcript_assessment.get("rawTranscript") or turn.get(
            "transcript"
        )
        turn["normalizedTranscript"] = transcript_assessment.get("normalizedTranscript")
        turn["correctionStatus"] = transcript_assessment.get("correctionStatus") or "NONE"
        turn["transcriptAssessment"] = dict(transcript_assessment)
    metrics = dict(turn.get("latencyMetrics") or {})
    metrics.update(
        {
            str(name): value
            for name, value in (payload.get("latencyMetrics") or {}).items()
            if isinstance(value, (int, float))
        }
    )
    turn["latencyMetrics"] = metrics
    turn["updatedAt"] = utc_now()
    voice_turn_repository.save(turn)
    session = voice_session_repository.get(voice_session_id)
    result = payload.get("result")
    validation_dialogue_act = (
        result.get("structuredDialogueAct") if isinstance(result, Mapping) else None
    )
    if isinstance(validation_dialogue_act, str) and validation_dialogue_act:
        turn["structuredDialogueAct"] = validation_dialogue_act
        turn["dialogueActMismatch"] = validation_dialogue_act != turn.get(
            "canonicalIntent"
        )
        voice_turn_repository.save(turn)
    source_target_key = str(payload.get("sourceTargetKey") or "")
    if (
        isinstance(session, dict)
        and source_target_key
        and background_status == "completed"
    ):
        provisional_keys = session.get("provisionalAnsweredTargetKeys")
        if isinstance(provisional_keys, list):
            session["provisionalAnsweredTargetKeys"] = [
                key for key in provisional_keys if str(key) != source_target_key
            ]
    if (
        isinstance(session, dict)
        and session.get("provisionalSourceTurnId") == payload.get("sourceTurnId")
        and background_status == "completed"
    ):
        session.pop("provisionalQuestion", None)
        session.pop("provisionalSourceTurnId", None)
    if isinstance(session, dict):
        session["updatedAt"] = utc_now()
        voice_session_repository.save(session)
    logger.info(
        "background_structured_state_proposal source_turn_id=%s voice_session_id=%s "
        "merge_decision=%s base_state_version=%s current_state_version=%s "
        "resulting_state_version=%s applied_fields=%s discarded_fields=%s "
        "canonical_action=%s direct_state_write=false",
        source_turn_id,
        voice_session_id,
        background_merge.get("mergeDecision") if isinstance(background_merge, Mapping) else None,
        background_merge.get("baseStateVersion") if isinstance(background_merge, Mapping) else None,
        background_merge.get("currentStateVersion")
        if isinstance(background_merge, Mapping)
        else None,
        background_merge.get("resultingStateVersion")
        if isinstance(background_merge, Mapping)
        else None,
        background_merge.get("appliedFields") if isinstance(background_merge, Mapping) else [],
        background_merge.get("discardedFields")
        if isinstance(background_merge, Mapping)
        else [],
        turn.get("canonicalAction"),
    )
    logger.info(
        "background_validation_persisted voice_session_id=%s turn_id=%s status=%s agrees=%s clarification_enqueued=%s",
        voice_session_id,
        turn_id,
        background_status,
        payload.get("backgroundAgreesWithFast"),
        payload.get("clarificationEnqueued"),
    )


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


def _voice_interview_field_labels(
    knowledge: dict[str, Any],
    user: UserContext,
) -> dict[str, str]:
    knowledge_id = str(knowledge.get("id") or "")
    return {
        str(row["id"]): str(row.get("name") or row["id"])
        for row in store.list("knowledge_fields", user.tenant_id)
        if row.get("knowledgeId") == knowledge_id and row.get("id")
    }


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
        commit_interview_state(
            deepcopy(base_state),
            _build_user_context_from_session(session),
            source="cancel_restore",
            increment_version=False,
        )
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
            "voiceClientTurnId": turn.get("clientTurnId"),
            "targetType": None,
            "targetId": None,
        }
        return store.upsert("messages", message)
    question = _find_question_by_id(interview_state, current_question_id)
    if question is None and current_question_id:
        session = voice_session_repository.get(turn.get("voiceSessionId")) or {}
        if current_question_id == session.get("currentQuestionId"):
            provisional_question = session.get("provisionalQuestion")
            if (
                isinstance(provisional_question, dict)
                and provisional_question.get("questionId") == current_question_id
            ):
                question = provisional_question
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
        "voiceClientTurnId": turn.get("clientTurnId"),
        # The structured service orders messages by timestamp.  Voice turns
        # already have a monotonic creation time; retain it so repeated
        # answers to the same question are interpreted in turn order rather
        # than by their random message ids.
        "createdAt": turn.get("createdAt") or utc_now(),
        "updatedAt": utc_now(),
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
        latency_metrics={
            str(name): value
            for name, value in (turn.get("latencyMetrics") or {}).items()
            if isinstance(value, (int, float))
        },
        voice_session=session,
        voice_turn=turn,
    )
