"""Durable GPT-Live transcript receipts.

GPT-Live semantic assessment is performed by the Responses Delegation agent.
This module only stores raw transcript evidence and exposes the current
state. It deliberately does not call an LLM or choose a conversation action.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import asdict
from hashlib import sha256
from time import monotonic
from typing import Any

from fastapi import HTTPException

from ai_interviewer_api.agents.interview_knowledge.coordinator import (
    evaluate_completion,
    resolve_profile,
)
from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.schemas.live import LiveCaptureCreate
from ai_interviewer_api.services.ai_interview import get_interview_state_snapshot
from ai_interviewer_api.services.live_interview import (
    _delegation_lock,
    build_live_interview_context,
)

logger = logging.getLogger(__name__)


def receive_live_capture(
    payload: LiveCaptureCreate,
    *,
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
) -> dict[str, Any]:
    """Persist a transcript receipt without waiting for semantic evaluation."""

    message_id = _message_id(payload, user)
    with store.live_capture_lock(f"live-receive:{user.tenant_id}:{record['id']}"):
        for row in store.list("messages", user.tenant_id):
            if (
                row.get("recordId") == record["id"]
                and row.get("liveCaptureId")
                and row.get("createdByUserId") == user.user_id
                and not row.get("liveCaptureApplied")
                and not row.get("liveCaptureUser")
            ):
                store.upsert("messages", {**row, "liveCaptureUser": asdict(user)})
        existing = store.get("messages", message_id)
        fragments = _fragments(payload, message_id)
        if existing:
            if not _same_fragments(existing["liveTranscriptFragments"], fragments):
                raise HTTPException(409, "live_capture_revision_conflict")
        else:
            history = [
                row
                for row in store.list("messages", user.tenant_id)
                if row.get("recordId") == record["id"]
                and row.get("liveCaptureId") == payload.capture_id
            ]
            if payload.revision != max(
                (row["liveCaptureRevision"] for row in history),
                default=0,
            ) + 1:
                raise HTTPException(409, "live_capture_out_of_order")
            store.upsert(
                "messages",
                {
                    "id": message_id,
                    "tenantId": user.tenant_id,
                    "recordId": record["id"],
                    "createdByUserId": user.user_id,
                    "createdAt": utc_now(),
                    "updatedAt": utc_now(),
                    "role": "user",
                    "content": "",
                    "isActualUtterance": False,
                    "rawTranscript": "".join(
                        fragment.text
                        for fragment in payload.fragments
                        if fragment.role == "user"
                    ),
                    "liveCaptureId": payload.capture_id,
                    "liveCaptureRevision": payload.revision,
                    "liveTranscriptFragments": fragments,
                    "liveCaptureApplied": False,
                    "liveCaptureUser": asdict(user),
                    "liveSemanticSource": "responses_delegation",
                },
            )
    return {"status": "accepted", "revision": payload.revision}


def _message_id(payload: LiveCaptureCreate, user: UserContext) -> str:
    return "msg-live-capture-" + sha256(
        f"{user.tenant_id}:{payload.record_id}:{payload.capture_id}:{payload.revision}".encode()
    ).hexdigest()


def _fragments(payload: LiveCaptureCreate, message_id: str) -> list[dict[str, Any]]:
    return [
        {
            **fragment.model_dump(),
            "id": f"{message_id}:{fragment.role}:{index}",
        }
        for index, fragment in enumerate(payload.fragments)
    ]


def _same_fragments(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    return [
        {key: value for key, value in item.items() if key != "id"}
        for item in left
    ] == [
        {key: value for key, value in item.items() if key != "id"}
        for item in right
    ]


def process_live_capture(
    payload: LiveCaptureCreate,
    *,
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
    coalesce: bool = False,
) -> dict[str, Any]:
    """Mark transcript receipts durable; Responses owns semantic assessment."""

    record_id = str(record["id"])
    started = monotonic()
    with _delegation_lock(record_id):
        # Keep state initialization as a side effect before accepting a receipt.
        get_interview_state_snapshot(dict(record), user, persist=True)
        history = sorted(
            [
                row
                for row in store.list("messages", user.tenant_id)
                if row.get("recordId") == record_id
                and row.get("liveCaptureId") == payload.capture_id
                and row.get("liveCaptureRevision", 0) <= payload.revision
            ],
            key=lambda row: row["liveCaptureRevision"],
        )
        message_id = _message_id(payload, user)
        existing = next((row for row in history if row["id"] == message_id), None)
        if existing and not _same_fragments(
            existing["liveTranscriptFragments"],
            _fragments(payload, message_id),
        ):
            raise HTTPException(409, "live_capture_revision_conflict")
        if existing and existing.get("liveCaptureApplied"):
            return _response(record, knowledge, user, "duplicate", capture_id=payload.capture_id)
        if not coalesce and any(
            not row.get("liveCaptureApplied")
            for row in history
            if row["id"] != message_id
        ):
            raise HTTPException(409, "live_capture_previous_pending")
        if not existing:
            previous = history[-1] if history else None
            if payload.revision != (previous["liveCaptureRevision"] + 1 if previous else 1):
                raise HTTPException(409, "live_capture_out_of_order")
            if previous and not previous.get("liveCaptureApplied"):
                raise HTTPException(409, "live_capture_previous_pending")
            existing = {
                "id": message_id,
                "tenantId": user.tenant_id,
                "recordId": record_id,
                "createdByUserId": user.user_id,
                "createdAt": utc_now(),
                "updatedAt": utc_now(),
                "role": "user",
                "content": "",
                "isActualUtterance": False,
                "rawTranscript": "".join(
                    fragment.text
                    for fragment in payload.fragments
                    if fragment.role == "user"
                ),
                "liveCaptureId": payload.capture_id,
                "liveCaptureRevision": payload.revision,
                "liveTranscriptFragments": _fragments(payload, message_id),
                "liveCaptureApplied": False,
                "liveSemanticSource": "responses_delegation",
            }
            store.upsert("messages", existing)
            history.append(existing)

        applied = history if coalesce else [existing]
        for row in applied:
            if row.get("liveCaptureApplied"):
                continue
            row.update(
                {
                    "runtime": "gpt_live",
                    "liveCaptureApplied": True,
                    "liveCaptureAppliedBy": "receipt_store",
                    "liveSemanticSource": "responses_delegation",
                    "updatedAt": utc_now(),
                }
            )
            store.upsert("messages", row)
        logger.info(
            "live_capture_receipts_persisted runtime=gpt_live record_id=%s "
            "capture_id=%s revision=%s receipt_count=%s semantic_evaluator=responses_delegation "
            "elapsed_ms=%s",
            record_id,
            payload.capture_id,
            payload.revision,
            len(applied),
            round((monotonic() - started) * 1000),
        )
        return _response(record, knowledge, user, "updated", capture_id=payload.capture_id)


def _response(
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
    status: str,
    *,
    capture_id: str | None = None,
) -> dict[str, Any]:
    snapshot = get_interview_state_snapshot(dict(record), user, persist=True)
    checklist = build_live_interview_context(record, knowledge, user)["fields"]
    state = snapshot["interviewState"]
    processing = any(
        row.get("liveCaptureId")
        and not row.get("liveCaptureApplied")
        for row in store.list("messages", user.tenant_id)
        if row.get("recordId") == record["id"]
        and (capture_id is None or row.get("liveCaptureId") == capture_id)
    )
    completion = evaluate_completion(
        state,
        resolve_profile(knowledge),
        [
            row
            for row in store.list("knowledge_fields", user.tenant_id)
            if row.get("knowledgeId") == knowledge["id"] and row.get("askByAi", True)
        ],
    )
    return {
        "status": status,
        "interviewState": state,
        "structuredDraft": snapshot["structuredDraft"],
        "checklist": checklist,
        "answerEvaluation": state.get("lastAnswerEvaluation"),
        "answerStatus": state.get("lastAnswerStatus"),
        "aspectAssessments": list(state.get("lastAspectAssessments") or []),
        "missingInformation": list(state.get("lastMissingInformation") or []),
        "extractedFields": list(state.get("lastExtractedFields") or []),
        "processing": processing,
        "semanticSource": "responses_delegation",
        "completion": completion,
    }
