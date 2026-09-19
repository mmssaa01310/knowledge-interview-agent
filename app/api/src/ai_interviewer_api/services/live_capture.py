"""Observe Live transcripts without running the foreground question generator."""

import logging
from collections.abc import Mapping
from hashlib import sha256
from time import monotonic
from typing import Any

from fastapi import HTTPException

from ai_interviewer_api.agents.interview_knowledge.coordinator import resolve_profile
from ai_interviewer_api.agents.interview_knowledge.provider import BedrockResponsesStructuredProvider
from ai_interviewer_api.agents.interview_knowledge.service import resolve_structured_model_id
from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.schemas.live import LiveCaptureCreate
from ai_interviewer_api.services.ai_interview import get_interview_state_snapshot
from ai_interviewer_api.services.interview_state_transition import apply_background_state_proposal
from ai_interviewer_api.services.live_interview import _delegation_lock, build_live_interview_context

logger = logging.getLogger(__name__)


def process_live_capture(
    payload: LiveCaptureCreate,
    *,
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
) -> dict[str, Any]:
    record_id = str(record["id"])
    started = monotonic()
    # Serialize only application writes, never the MediaTrack or Live responses.
    with _delegation_lock(record_id):
        snapshot = get_interview_state_snapshot(dict(record), user, persist=True)
        state = snapshot["interviewState"]
        history = sorted(
            [
                row for row in store.list("messages", user.tenant_id)
                if row.get("recordId") == record_id
                and row.get("liveCaptureId") == payload.capture_id
            ],
            key=lambda row: row["liveCaptureRevision"],
        )
        message_id = "msg-live-capture-" + sha256(
            f"{user.tenant_id}:{record_id}:{payload.capture_id}:{payload.revision}".encode()
        ).hexdigest()
        fragments = [
            {**fragment.model_dump(), "id": message_id if fragment.role == "user"
             else f"{message_id}:assistant:{index}"}
            for index, fragment in enumerate(payload.fragments)
        ]
        existing = next((row for row in history if row["id"] == message_id), None)
        if existing and existing["liveTranscriptFragments"] != fragments:
            raise HTTPException(409, "live_capture_revision_conflict")
        if existing and existing.get("liveCaptureApplied"):
            return _response(record, knowledge, user, "duplicate")
        if not existing:
            previous = history[-1] if history else None
            if payload.revision != (previous["liveCaptureRevision"] + 1 if previous else 1):
                raise HTTPException(409, "live_capture_out_of_order")
            if previous and not previous.get("liveCaptureApplied"):
                raise HTTPException(409, "live_capture_previous_pending")
            existing = {
                "id": message_id, "tenantId": user.tenant_id, "recordId": record_id,
                "createdByUserId": user.user_id, "createdAt": utc_now(), "updatedAt": utc_now(),
                "role": "user", "content": "", "isActualUtterance": False,
                "rawTranscript": "".join(f.text for f in payload.fragments if f.role == "user"),
                "liveCaptureId": payload.capture_id, "liveCaptureRevision": payload.revision,
                "liveTranscriptFragments": fragments, "liveCaptureApplied": False,
            }
            # Retain raw evidence even when the LLM/provider fails. Retry this receipt.
            store.upsert("messages", existing)
            history.append(existing)
        fields = [
            row for row in store.list("knowledge_fields", user.tenant_id)
            if row.get("knowledgeId") == knowledge["id"] and row.get("askByAi", True)
        ]
        conversation = [
            {"id": fragment["id"], "role": fragment["role"], "content": fragment["text"],
             "start_ms": fragment.get("start_ms"), "end_ms": fragment.get("end_ms")}
            for row in history for fragment in row["liveTranscriptFragments"]
        ]
        evidence_ids = {item["id"] for item in conversation if item["role"] == "user"}
        if any(fragment.role == "user" for fragment in payload.fragments):
            provider = BedrockResponsesStructuredProvider(model_id=resolve_structured_model_id(knowledge))
            output = provider.interpret(
                profile=resolve_profile(knowledge),
                reasoning_effort="low",
                context={
                    "captureMode": "live_observation", "fields": fields,
                    "interviewState": state, "conversation": conversation,
                    "currentQuestion": {}, "interviewLocale": "ja-JP",
                },
            )
            # Observation extracts user facts; it does not generate proposals.
            output = output.model_copy(update={
                "fieldUpdates": [u for u in output.fieldUpdates if u.candidateSource == "user_statement"],
                "requirementUpdates": [u for u in output.requirementUpdates if u.candidateSource == "user_statement"],
            })
            # A transcript correction is a proposal, not an established fact.
            if output.transcriptAssessment.correctionStatus != "NONE":
                output = output.model_copy(update={"utteranceCompleteness": "UNCERTAIN"})
            merge = apply_background_state_proposal(
                record_id=record_id,
                user=user,
                fields=fields,
                profile=resolve_profile(knowledge),
                source_question=None,
                proposal={
                    "sourceMessageId": message_id,
                    "baseStateVersion": state.get("stateVersion", 0),
                    "structuredOutput": output.model_dump(),
                    "validEvidenceIds": sorted(evidence_ids),
                },
            )
            if merge.decision not in {"applied", "stale_safe_merge", "duplicate_background_proposal"}:
                raise HTTPException(409, "live_capture_merge_conflict")
        existing["liveCaptureApplied"] = True
        existing["updatedAt"] = utc_now()
        store.upsert("messages", existing)
        logger.info(
            "live_capture_applied record_id=%s revision=%s fragments=%s elapsed_ms=%s",
            record_id, payload.revision, len(fragments), round((monotonic() - started) * 1000),
        )
        return _response(record, knowledge, user, "updated")


def _response(
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
    status: str,
) -> dict[str, Any]:
    snapshot = get_interview_state_snapshot(dict(record), user, persist=True)
    checklist = build_live_interview_context(record, knowledge, user)["fields"]
    return {
        "status": status,
        "interviewState": snapshot["interviewState"],
        "structuredDraft": snapshot["structuredDraft"],
        "checklist": checklist,
    }
