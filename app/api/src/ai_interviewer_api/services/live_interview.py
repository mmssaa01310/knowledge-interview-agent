from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
from threading import RLock
from typing import Any

from fastapi import HTTPException

from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.schemas.live import LiveDelegationCreate
from ai_interviewer_api.services.ai_interview import (
    generate_interview_reply,
    get_interview_state_snapshot,
)


_LIVE_DELEGATION_LOCKS: dict[str, RLock] = {}
_LIVE_DELEGATION_LOCKS_GUARD = RLock()


def build_live_interview_context(
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
) -> dict[str, Any]:
    """Build the server-owned checklist context sent to GPT-Live."""

    snapshot = get_interview_state_snapshot(dict(record), user, persist=True)
    state = snapshot.get("interviewState") or {}
    fields = sorted(
        [
            row
            for row in store.list("knowledge_fields", user.tenant_id)
            if row.get("knowledgeId") == knowledge.get("id")
            and row.get("askByAi", True) is not False
        ],
        key=lambda row: int(row.get("displayOrder") or 0),
    )
    field_summaries: list[dict[str, Any]] = []
    for field in fields:
        field_id = str(field.get("id") or "")
        plan = field.get("questionPlan")
        plan = plan if isinstance(plan, Mapping) else {}
        required_items = [
            _item_summary(item)
            for item in plan.get("requiredItems", []) or []
            if isinstance(item, Mapping) and str(item.get("label") or "").strip()
        ]
        optional_items = [
            _item_summary(item)
            for item in plan.get("optionalItems", []) or []
            if isinstance(item, Mapping) and str(item.get("label") or "").strip()
        ]
        field_states = state.get("fieldStates")
        field_state = (
            field_states.get(field_id, {})
            if isinstance(field_states, Mapping)
            else {}
        )
        if not isinstance(field_state, Mapping):
            field_state = {}
        captured_ids = {
            str(item.get("itemId"))
            for item in field_state.get("capturedItems", []) or []
            if isinstance(item, Mapping) and item.get("itemId")
        }
        field_summaries.append(
            {
                "id": field_id,
                "label": str(field.get("name") or field_id),
                "description": str(field.get("description") or ""),
                "required": bool(field.get("required")),
                "required_items": required_items,
                "optional_items": optional_items,
                "answer_state": str(field_state.get("answerState") or "UNANSWERED"),
                "answer_resolution": field_state.get("answerResolution"),
                "candidate_answer": str(field_state.get("candidateAnswer") or "").strip(),
                "needs_confirmation": str(field_state.get("answerState") or "UNANSWERED") != "CONFIRMED",
                "captured_items": [
                    item["label"]
                    for item in required_items + optional_items
                    if item["id"] in captured_ids
                ],
                "missing_required_items": [
                    item["label"]
                    for item in required_items
                    if item["id"] not in captured_ids
                ],
            }
        )

    current_question = _current_question(state)
    return {
        "profile": state.get("interviewProfile")
        or (knowledge.get("interviewPlan") or {}).get("profile"),
        "purpose": str((knowledge.get("interviewPlan") or {}).get("purpose") or ""),
        "fields": field_summaries,
        "current": {
            "field_id": state.get("currentFieldId"),
            "question_id": state.get("currentQuestionId"),
            "target": deepcopy(state.get("nextQuestionTarget")),
            "question": current_question,
        },
    }


def process_live_delegation(
    payload: LiveDelegationCreate,
    *,
    record: Mapping[str, Any],
    user: UserContext,
) -> dict[str, Any]:
    """Apply one model-controlled Live delegation to canonical interview state.

    Delegation is the Live model's application boundary. Transcript fragments,
    silence, and transport events never call this function directly.
    """

    record_id = str(record.get("id") or payload.record_id)
    with _delegation_lock(record_id):
        client_message_id = _delegation_client_message_id(payload.delegation_id)
        existing = next(
            (
                message
                for message in store.list("messages", user.tenant_id)
                if message.get("recordId") == record_id
                and message.get("clientMessageId") == client_message_id
            ),
            None,
        )
        if existing:
            return _delegation_response(
                "duplicate",
                get_interview_state_snapshot(dict(record), user, persist=True),
            )

        snapshot = get_interview_state_snapshot(dict(record), user, persist=True)
        state = snapshot.get("interviewState") or {}
        if state.get("status") == "completed":
            return _delegation_response("completed", snapshot)

        # A voice session may start before the user has pressed the text
        # interview's start button. Create the first canonical question once,
        # without persisting a duplicate assistant chat message.
        if not state.get("currentQuestionId"):
            initial_result = generate_interview_reply(
                dict(record),
                user,
                persist_assistant_messages=False,
            )
            _raise_if_interview_failed(initial_result.metadata)
            snapshot = get_interview_state_snapshot(dict(record), user, persist=True)
            state = snapshot.get("interviewState") or {}

        question = _current_question(state)
        if not question or not question.get("questionId"):
            raise HTTPException(status_code=409, detail="live_interview_question_not_ready")

        transcript = payload.transcript.strip()
        message = {
            "id": (
                f"msg-gpt-live-{record_id}-"
                f"{sha256(payload.delegation_id.encode()).hexdigest()[:24]}"
            ),
            "tenantId": user.tenant_id,
            "recordId": record_id,
            "content": transcript,
            "rawTranscript": transcript,
            "role": "user",
            "isActualUtterance": True,
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
            "turnType": "ANSWER",
            "answerToQuestionId": question.get("questionId"),
            "answerToFieldId": question.get("fieldId"),
            "questionType": question.get("questionType"),
            "targetType": question.get("targetType"),
            "targetId": question.get("targetId"),
            "clientMessageId": client_message_id,
            "liveDelegationId": payload.delegation_id,
        }
        store.upsert("messages", message)

        result = generate_interview_reply(
            dict(record),
            user,
            persist_assistant_messages=False,
        )
        try:
            _raise_if_interview_failed(result.metadata)
        except HTTPException:
            store.delete("messages", message["id"])
            raise

        updated_snapshot = get_interview_state_snapshot(dict(record), user, persist=True)
        return _delegation_response(
            "completed"
            if (updated_snapshot.get("interviewState") or {}).get("status") == "completed"
            else "updated",
            updated_snapshot,
        )


def _delegation_lock(record_id: str) -> RLock:
    with _LIVE_DELEGATION_LOCKS_GUARD:
        return _LIVE_DELEGATION_LOCKS.setdefault(record_id, RLock())


def _delegation_client_message_id(delegation_id: str) -> str:
    return f"gpt-live-delegation-{sha256(delegation_id.encode()).hexdigest()}"


def _current_question(state: Mapping[str, Any]) -> dict[str, Any] | None:
    current_id = state.get("currentQuestionId")
    if not current_id:
        return None
    return next(
        (
            dict(question)
            for question in reversed(state.get("askedQuestions", []) or [])
            if isinstance(question, Mapping) and question.get("questionId") == current_id
        ),
        None,
    )


def _delegation_response(status: str, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    state = snapshot.get("interviewState") or {}
    return {
        "status": status,
        "interviewState": state,
        "structuredDraft": snapshot.get("structuredDraft") or {},
        "stateVersion": state.get("stateVersion"),
    }


def _raise_if_interview_failed(metadata: Mapping[str, Any] | None) -> None:
    if metadata and metadata.get("error"):
        raise HTTPException(status_code=502, detail="live_interview_state_update_failed")


def _item_summary(item: Mapping[str, Any]) -> dict[str, str]:
    return {
        "id": str(item.get("itemId") or ""),
        "label": str(item.get("label") or "").strip(),
        "description": str(item.get("description") or "").strip(),
    }
