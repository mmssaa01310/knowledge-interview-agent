"""Single-writer boundary for persisted Structured Interview state.

The Structured Interview service can build provisional state and background
proposals, but persistence is intentionally kept here.  In particular, a
background Structured Interpreter result is applied as operations to the
latest state; it is never allowed to replace the current question or target
from the state snapshot that existed when the background call started.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
from typing import Any

from ai_interviewer_api.agents.interview_knowledge.coordinator import (
    apply_background_structured_output,
    enqueue_clarification_request,
)
from ai_interviewer_api.agents.interview_knowledge.schemas import (
    InterviewProfile,
    StructuredInterviewOutput,
)
from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.repositories.store import store


logger = logging.getLogger(__name__)
_STATE_WRITER_LOCKS: dict[str, RLock] = {}
_STATE_WRITER_LOCKS_GUARD = RLock()


BACKGROUND_PROTECTED_STATE_FIELDS: tuple[str, ...] = (
    "currentFieldId",
    "currentQuestionId",
    "nextQuestionTarget",
    "lastCanonicalIntent",
    "lastCanonicalAction",
    "status",
)
_BACKGROUND_APPLIED_SOURCE_IDS = "backgroundAppliedSourceMessageIds"
_BACKGROUND_SOURCE_HISTORY_LIMIT = 256
_BACKGROUND_LATEST_FIELDS: tuple[str, ...] = (
    "lastProcessedUserMessageId",
    "lastStructuredOutput",
    "lastStructuredDialogueAct",
    "lastStructuredValidationDialogueAct",
    "lastStructuredModelId",
    "lastStructuredReasoningEffort",
    "lastTranscriptAssessment",
    "lastAnswerAssessment",
    "lastConfirmationApplied",
)


def _state_writer_lock(state_id: str) -> RLock:
    with _STATE_WRITER_LOCKS_GUARD:
        return _STATE_WRITER_LOCKS.setdefault(state_id, RLock())


@dataclass(frozen=True)
class BackgroundMergeResult:
    """Auditable result of applying one background proposal."""

    decision: str
    source_turn_id: str | None
    base_state_version: int | None
    current_state_version: int | None
    resulting_state_version: int | None
    applied_fields: tuple[str, ...] = ()
    discarded_fields: tuple[str, ...] = ()
    clarification_enqueued: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "mergeDecision": self.decision,
            "sourceTurnId": self.source_turn_id,
            "baseStateVersion": self.base_state_version,
            "currentStateVersion": self.current_state_version,
            "resultingStateVersion": self.resulting_state_version,
            "appliedFields": list(self.applied_fields),
            "discardedFields": list(self.discarded_fields),
            "clarificationEnqueued": self.clarification_enqueued,
        }


def commit_interview_state(
    state: dict[str, Any],
    user: UserContext,
    *,
    source: str,
    increment_version: bool = True,
    expected_state_version: int | None = None,
) -> dict[str, Any]:
    """Commit Interview State through the single state-writer boundary."""

    state_id = str(state.get("id") or "")
    if not state_id:
        raise ValueError("interview_state_id_required")
    with _state_writer_lock(state_id):
        current_version = int(state.get("stateVersion", 0) or 0)
        if expected_state_version is not None and current_version != expected_state_version:
            raise ValueError("interview_state_version_conflict")
        if increment_version:
            state["stateVersion"] = current_version + 1
            state["updatedByUserId"] = user.user_id
            state["updatedAt"] = utc_now()
        store.upsert("interview_states", state)
        logger.info(
            "interview_state_committed state_id=%s record_id=%s source=%s state_version=%s",
            state_id,
            state.get("recordId"),
            source,
            state.get("stateVersion"),
        )
        return state


def commit_foreground_provisional_state(
    provisional_state: Mapping[str, Any],
    user: UserContext,
    *,
    source: str,
) -> dict[str, Any]:
    """Commit only foreground-owned question progression from a Fast result.

    A background result may have completed between Fast evaluation and this
    call.  Therefore the latest persisted state is used as the base and only
    question-progression fields are copied from the provisional snapshot.
    Extracted fields, issues, and other background-owned data are preserved.
    """

    state_id = str(provisional_state.get("id") or "")
    if not state_id:
        raise ValueError("interview_state_id_required")
    with _state_writer_lock(state_id):
        latest = store.get("interview_states", state_id)
        working = deepcopy(latest or dict(provisional_state))
        for key in ("currentFieldId", "currentQuestionId", "nextQuestionTarget"):
            if key in provisional_state:
                working[key] = deepcopy(provisional_state.get(key))

        existing_questions = working.setdefault("askedQuestions", [])
        if not isinstance(existing_questions, list):
            existing_questions = []
            working["askedQuestions"] = existing_questions
        known_question_ids = {
            str(item.get("questionId") or "")
            for item in existing_questions
            if isinstance(item, Mapping)
        }
        for item in provisional_state.get("askedQuestions", []):
            if not isinstance(item, Mapping):
                continue
            question_id = str(item.get("questionId") or "")
            if question_id and question_id not in known_question_ids:
                existing_questions.append(deepcopy(dict(item)))
                known_question_ids.add(question_id)

        return commit_interview_state(working, user, source=source)


def apply_background_state_proposal(
    *,
    record_id: str,
    user: UserContext,
    proposal: Mapping[str, Any],
    fields: Sequence[Mapping[str, Any]],
    profile: InterviewProfile,
    source_question: Mapping[str, Any] | None,
) -> BackgroundMergeResult:
    """Apply safe background operations to the latest state.

    A proposal is allowed to merge extraction/assessment information even if
    its base version is stale.  It is applied to the latest state through the
    domain coordinator, while protected conversation-policy fields are never
    copied from the background snapshot.
    """

    source_turn_id = _optional_string(proposal.get("sourceTurnId"))
    source_message_id = _optional_string(proposal.get("sourceMessageId"))
    base_version = _optional_int(proposal.get("baseStateVersion"))
    state_id = f"interview-state-{record_id}"

    with _state_writer_lock(state_id):
        latest = store.get("interview_states", state_id)
        if not isinstance(latest, Mapping):
            return _background_discard(
                "missing_current_state",
                source_turn_id,
                base_version,
                None,
                discarded_fields=_proposal_topics(proposal),
            )

        current_version = _optional_int(latest.get("stateVersion")) or 0
        source_key = source_message_id or source_turn_id
        applied_source_ids = _string_list(latest.get(_BACKGROUND_APPLIED_SOURCE_IDS))
        if source_key and source_key in applied_source_ids:
            return _background_discard(
                "duplicate_background_proposal",
                source_turn_id,
                base_version,
                current_version,
                discarded_fields=_proposal_topics(proposal),
            )
        if base_version is not None and current_version < base_version:
            return _background_discard(
                "future_base_version",
                source_turn_id,
                base_version,
                current_version,
                discarded_fields=_proposal_topics(proposal),
            )

        output_payload = proposal.get("structuredOutput")
        if not isinstance(output_payload, Mapping):
            return _background_discard(
                "missing_structured_output",
                source_turn_id,
                base_version,
                current_version,
                discarded_fields=_proposal_topics(proposal),
            )
        try:
            output = StructuredInterviewOutput.model_validate(output_payload)
        except ValueError:
            return _background_discard(
                "invalid_structured_output",
                source_turn_id,
                base_version,
                current_version,
                discarded_fields=_proposal_topics(proposal),
            )

        working = deepcopy(dict(latest))
        valid_evidence_ids = {
            str(value)
            for value in (proposal.get("validEvidenceIds") or [])
            if str(value).strip()
        }
        latest_fields = {
            key: deepcopy(latest.get(key))
            for key in _BACKGROUND_LATEST_FIELDS
            if key in latest
        }
        changed_topics = apply_background_structured_output(
            working,
            output,
            latest_message_id=source_message_id or source_turn_id or "background",
            fields=fields,
            profile=profile,
            valid_evidence_ids=valid_evidence_ids or None,
            current_question=source_question,
            raw_transcript=_optional_string(proposal.get("rawTranscript")),
        )
        # These fields describe the latest foreground turn.  A delayed
        # background proposal is not allowed to replace them with an older
        # snapshot.  The proposal itself remains available in the voice-turn
        # telemetry payload.
        working.update(latest_fields)
        if source_key:
            if not latest_fields.get("lastProcessedUserMessageId"):
                working["lastProcessedUserMessageId"] = source_message_id or source_turn_id
            applied_source_ids.append(source_key)
            working[_BACKGROUND_APPLIED_SOURCE_IDS] = applied_source_ids[-_BACKGROUND_SOURCE_HISTORY_LIMIT:]

        clarification_enqueued = False
        clarification = proposal.get("clarificationProposal")
        if isinstance(clarification, Mapping):
            request = enqueue_clarification_request(
                working,
                source_turn_id=_optional_string(clarification.get("sourceTurnId"))
                or source_turn_id,
                source_message_id=_optional_string(clarification.get("sourceMessageId"))
                or source_message_id,
                source_target=(
                    clarification.get("sourceTarget")
                    if isinstance(clarification.get("sourceTarget"), Mapping)
                    else source_question
                ),
                reason=str(clarification.get("reason") or "回答の補足確認が必要"),
                priority=int(clarification.get("priority") or 2),
            )
            clarification_enqueued = request is not None

        stale = base_version is not None and current_version != base_version
        decision = "stale_safe_merge" if stale else "applied"
        applied_fields = list(dict.fromkeys(changed_topics))
        if clarification_enqueued:
            applied_fields.append("clarificationQueue")
        committed = commit_interview_state(
            working,
            user,
            source="background_structured_proposal",
            expected_state_version=current_version,
        )
        result = BackgroundMergeResult(
            decision=decision,
            source_turn_id=source_turn_id,
            base_state_version=base_version,
            current_state_version=current_version,
            resulting_state_version=_optional_int(committed.get("stateVersion")),
            applied_fields=tuple(applied_fields),
            discarded_fields=BACKGROUND_PROTECTED_STATE_FIELDS,
            clarification_enqueued=clarification_enqueued,
        )
        logger.info(
            "background_result_received record_id=%s source_turn_id=%s base_state_version=%s "
            "current_state_version=%s merge_decision=%s applied_fields=%s discarded_fields=%s "
            "clarification_enqueued=%s",
            record_id,
            source_turn_id,
            base_version,
            current_version,
            result.decision,
            list(result.applied_fields),
            list(result.discarded_fields),
            clarification_enqueued,
        )
        return result


def _background_discard(
    decision: str,
    source_turn_id: str | None,
    base_version: int | None,
    current_version: int | None,
    *,
    discarded_fields: Sequence[str],
) -> BackgroundMergeResult:
    result = BackgroundMergeResult(
        decision=decision,
        source_turn_id=source_turn_id,
        base_state_version=base_version,
        current_state_version=current_version,
        resulting_state_version=current_version,
        discarded_fields=tuple(discarded_fields),
    )
    logger.info(
        "background_result_received source_turn_id=%s base_state_version=%s "
        "current_state_version=%s merge_decision=%s applied_fields=%s discarded_fields=%s "
        "clarification_enqueued=false",
        source_turn_id,
        base_version,
        current_version,
        result.decision,
        [],
        list(result.discarded_fields),
    )
    return result


def _proposal_topics(proposal: Mapping[str, Any]) -> tuple[str, ...]:
    topics = proposal.get("proposalTopics")
    if isinstance(topics, list):
        return tuple(str(topic) for topic in topics if str(topic).strip())
    return (
        "fieldUpdates",
        "requirementUpdates",
        "processPatch",
        "applicability",
        "contradictions",
        "openIssues",
        "clarificationProposal",
    )


def _optional_string(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
