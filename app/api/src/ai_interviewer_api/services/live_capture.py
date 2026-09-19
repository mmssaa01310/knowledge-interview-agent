"""Observe Live transcripts without running the foreground question generator."""

import logging
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
from time import monotonic, time
from typing import Any

from fastapi import HTTPException

from ai_interviewer_api.agents.interview_knowledge.coordinator import (
    confirm_closing_answer,
    evaluate_completion,
    resolve_profile,
)
from ai_interviewer_api.agents.interview_knowledge.provider import BedrockResponsesStructuredProvider
from ai_interviewer_api.agents.interview_knowledge.service import resolve_structured_model_id
from ai_interviewer_api.agents.interview_knowledge.schemas import StructuredInterviewOutput
from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.core.permissions import require_record_action
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.schemas.live import LiveCaptureCreate
from ai_interviewer_api.services.ai_interview import get_interview_state_snapshot
from ai_interviewer_api.services.interview_state_transition import (
    _state_writer_lock,
    apply_background_state_proposal,
    commit_interview_state,
)
from ai_interviewer_api.services.live_interview import _delegation_lock, build_live_interview_context
from ai_interviewer_api.services.record_lifecycle import sync_record_status_after_interview
from ai_interviewer_api.services.audit import write_audit_log

logger = logging.getLogger(__name__)


def receive_live_capture(
    payload: LiveCaptureCreate,
    *,
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
) -> dict[str, Any]:
    """Acknowledge only durable receipt; interpretation belongs to the server consumer."""
    message_id = _message_id(payload, user)
    with store.live_capture_lock(f"live-receive:{user.tenant_id}:{record['id']}"):
        # An authenticated retry also upgrades an older, unprocessed receipt.
        for row in store.list("messages", user.tenant_id):
            if (row.get("recordId") == record["id"] and row.get("liveCaptureId")
                    and row.get("createdByUserId") == user.user_id
                    and not row.get("liveCaptureApplied") and not row.get("liveCaptureUser")):
                store.upsert("messages", {**row, "liveCaptureUser": asdict(user)})
        existing = store.get("messages", message_id)
        fragments = _fragments(payload, message_id)
        if existing:
            if not _same_fragments(existing["liveTranscriptFragments"], fragments):
                raise HTTPException(409, "live_capture_revision_conflict")
        else:
            history = [row for row in store.list("messages", user.tenant_id)
                       if row.get("recordId") == record["id"]
                       and row.get("liveCaptureId") == payload.capture_id]
            if payload.revision != max((row["liveCaptureRevision"] for row in history), default=0) + 1:
                raise HTTPException(409, "live_capture_out_of_order")
            store.upsert("messages", {
                "id": message_id, "tenantId": user.tenant_id, "recordId": record["id"],
                "createdByUserId": user.user_id, "createdAt": utc_now(), "updatedAt": utc_now(),
                "role": "user", "content": "", "isActualUtterance": False,
                "rawTranscript": "".join(f.text for f in payload.fragments if f.role == "user"),
                "liveCaptureId": payload.capture_id, "liveCaptureRevision": payload.revision,
                "liveTranscriptFragments": fragments, "liveCaptureApplied": False,
                "liveCaptureUser": asdict(user),
            })
    return {"status": "accepted", "revision": payload.revision}


def _message_id(payload: LiveCaptureCreate, user: UserContext) -> str:
    return "msg-live-capture-" + sha256(
        f"{user.tenant_id}:{payload.record_id}:{payload.capture_id}:{payload.revision}".encode()
    ).hexdigest()


def _fragments(payload: LiveCaptureCreate, message_id: str) -> list[dict[str, Any]]:
    return [{**fragment.model_dump(), "id": f"{message_id}:{fragment.role}:{index}"}
            for index, fragment in enumerate(payload.fragments)]


def _same_fragments(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    # Keep old evidence IDs intact when resending receipts from before the
    # per-fragment ID change. IDs are server metadata, not client content.
    return [{k: v for k, v in item.items() if k != "id"} for item in left] == [
        {k: v for k, v in item.items() if k != "id"} for item in right
    ]


def process_live_capture(
    payload: LiveCaptureCreate,
    *,
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
    coalesce: bool = False,
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
                and row.get("liveCaptureRevision", 0) <= payload.revision
            ],
            key=lambda row: row["liveCaptureRevision"],
        )
        message_id = _message_id(payload, user)
        fragments = _fragments(payload, message_id)
        existing = next((row for row in history if row["id"] == message_id), None)
        if existing and not _same_fragments(existing["liveTranscriptFragments"], fragments):
            raise HTTPException(409, "live_capture_revision_conflict")
        if existing and existing.get("liveCaptureApplied"):
            return _response(record, knowledge, user, "duplicate", capture_id=payload.capture_id)
        if not coalesce and any(not row.get("liveCaptureApplied") for row in history if row["id"] != message_id):
            raise HTTPException(409, "live_capture_previous_pending")
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
        output = None
        if evidence_ids:
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
            if coalesce:
                latest_record = store.get("records", record_id)
                if not latest_record or latest_record.get("deletedAt") or latest_record.get("status") == "approved":
                    raise HTTPException(409, "live_capture_record_unavailable")
                require_record_action(latest_record, user, "interview_read")
            merge = apply_background_state_proposal(
                record_id=record_id,
                user=user,
                fields=fields,
                profile=resolve_profile(knowledge),
                source_question=None,
                proposal={
                    "sourceMessageId": message_id,
                    "baseStateVersion": state.get("stateVersion", 0),
                    "structuredOutput": output.model_dump(exclude={"closingQuestionEvidenceIds", "closingAnswerEvidenceIds"}),
                    "validEvidenceIds": sorted(evidence_ids),
                },
            )
            if merge.decision not in {"applied", "stale_safe_merge", "duplicate_background_proposal"}:
                raise HTTPException(409, "live_capture_merge_conflict")
        applied = history if coalesce else [existing]
        completed = _finalize_capture(record, knowledge, user, fields, conversation, output, {row["id"] for row in applied})
        completed_at = min((row["liveCaptureCompletedAt"] for row in history
                            if row.get("liveCaptureCompletedAt")), default=time())
        for row in applied:
            if not row.get("liveCaptureApplied"):
                row["liveCaptureApplied"] = True
                row["updatedAt"] = utc_now()
                if completed:
                    row["liveCaptureCompletedAt"] = completed_at
                store.upsert("messages", row)
        logger.info(
            "live_capture_applied record_id=%s revision=%s fragments=%s elapsed_ms=%s",
            record_id, payload.revision, len(fragments), round((monotonic() - started) * 1000),
        )
        return _response(record, knowledge, user, "updated", capture_id=payload.capture_id)


def _finalize_capture(
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
    fields: list[dict[str, Any]],
    conversation: list[dict[str, Any]],
    output: StructuredInterviewOutput | None,
    applied_ids: set[str],
) -> bool:
    state_id = f"interview-state-{record['id']}"
    with _state_writer_lock(state_id):
        latest_record = dict(store.get("records", record["id"]) or record)
        if latest_record.get("deletedAt") or latest_record.get("status") == "approved":
            raise HTTPException(409, "live_capture_record_unavailable")
        state = deepcopy(store.get("interview_states", state_id))
        if not state:
            return False
        original = deepcopy(state)
        question_ids = set(getattr(output, "closingQuestionEvidenceIds", []))
        answer_ids = set(getattr(output, "closingAnswerEvidenceIds", []))
        questions = [i for i, item in enumerate(conversation)
                     if item["role"] == "assistant" and item["id"] in question_ids]
        answers = [(i, item) for i, item in enumerate(conversation)
                   if item["role"] == "user" and item["id"] in answer_ids]
        completion_before_closing = evaluate_completion(state, resolve_profile(knowledge), fields)
        no_open_answer_blockers = not (
            completion_before_closing["missingRequiredTargets"]
            or completion_before_closing["pendingConfirmationTargets"]
            or completion_before_closing["unknownApplicabilityTopics"]
            or completion_before_closing["unresolvedContradictionIds"]
        )
        if (no_open_answer_blockers and questions and answers
                and min(i for i, _ in answers) > max(questions)
                and question_ids <= {item["id"] for item in conversation if item["role"] == "assistant"}
                and answer_ids <= {item["id"] for item in conversation if item["role"] == "user"}
                and output.utteranceCompleteness == "COMPLETE"
                and output.transcriptAssessment.correctionStatus == "NONE"):
            confirm_closing_answer(state, transcript="".join(item["content"] for _, item in answers),
                                   message_id=answers[-1][1]["id"], valid_evidence_ids=answer_ids)
        pending = any(row.get("liveCaptureId") and not row.get("liveCaptureApplied")
                      for row in store.list("messages", user.tenant_id)
                      if row.get("recordId") == record["id"] and row["id"] not in applied_ids)
        complete = evaluate_completion(state, resolve_profile(knowledge), fields)["complete"]
        if not pending and complete:
            state.update(status="completed", currentFieldId=None, currentQuestionId=None, nextQuestionTarget=None)
        elif state.get("status") == "completed":
            state["status"] = "in_progress"
        if state != original:
            commit_interview_state(state, user, source="live_capture_completion")
        if latest_record.get("status") == "submitted" and state.get("status") == "in_progress":
            latest_record.update(status="in_progress", updatedByUserId=user.user_id, updatedAt=utc_now())
            store.upsert("records", latest_record)
            write_audit_log(user, "record_status_change", "record", str(record["id"]),
                            {"from": "submitted", "to": "in_progress", "reason": "live_capture_pending"})
        sync_record_status_after_interview(latest_record, state.get("status"), user)
        return state.get("status") == "completed"


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
    return {
        "status": status,
        "interviewState": snapshot["interviewState"],
        "structuredDraft": snapshot["structuredDraft"],
        "checklist": checklist,
        "processing": any(row.get("liveCaptureId") and not row.get("liveCaptureApplied")
                          for row in store.list("messages", user.tenant_id)
                          if row.get("recordId") == record["id"]
                          and (capture_id is None or row.get("liveCaptureId") == capture_id)),
        "completion": evaluate_completion(snapshot["interviewState"], resolve_profile(knowledge), [
            row for row in store.list("knowledge_fields", user.tenant_id)
            if row.get("knowledgeId") == knowledge["id"] and row.get("askByAi", True)
        ]),
    }
