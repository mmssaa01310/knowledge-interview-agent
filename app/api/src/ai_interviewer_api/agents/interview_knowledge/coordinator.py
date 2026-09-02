from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence, Set
from copy import deepcopy
from typing import Any

from ai_interviewer_api.agents.interview_knowledge.schemas import (
    Contradiction,
    FieldUpdate,
    InterviewProfile,
    OpenIssue,
    ProcessPatch,
    RequirementUpdate,
    StructuredInterviewOutput,
)
from ai_interviewer_api.models.interview_plan import InterviewQuestionPlan
from ai_interviewer_api.services.interview_answer_resolution import (
    normalize_answer_resolution,
)


APPLICABILITY_TOPICS: tuple[str, ...] = (
    "process",
    "branch",
    "exception",
    "external_system",
    "error_handling",
    "handoff",
    "input_output",
)

APPLICABILITY_LABELS: dict[str, str] = {
    "process": "処理の流れ",
    "branch": "分岐",
    "exception": "例外処理",
    "external_system": "外部システム連携",
    "error_handling": "エラー処理",
    "handoff": "担当者間の引き継ぎ",
    "input_output": "入出力情報",
}

PROFILE_LABELS: dict[InterviewProfile, str] = {
    "fixed_form": "定型情報を聞き取る",
    "business_process": "業務フローを整理する",
    "system_requirement": "システム要件を整理する",
}

logger = logging.getLogger(__name__)

CLOSING_TARGET_ID = "open_ended"
CLOSING_TARGET_LABEL = "最後に伝えておきたいこと"

REQUIREMENT_DEFINITIONS: dict[InterviewProfile, tuple[tuple[str, str, str], ...]] = {
    "fixed_form": (),
    "business_process": (
        ("process.scope", "対象範囲", "process"),
        ("process.start", "開始条件", "process"),
        ("process.end", "終了条件", "process"),
        ("process.actors", "関係者", "process"),
        ("process.main_flow", "通常フロー", "process"),
    ),
    "system_requirement": (
        ("requirement.purpose_problem", "目的・課題", "requirement"),
        ("requirement.users", "利用者", "requirement"),
        ("requirement.request", "要求内容", "requirement"),
        ("requirement.expected_result", "期待結果", "requirement"),
        ("requirement.constraints", "制約", "requirement"),
        ("process.trigger", "処理の開始条件", "process"),
        ("process.actors", "処理に関係する利用者・担当者", "process"),
        ("process.main_flow", "処理の流れ", "process"),
        ("process.end", "処理の終了条件", "process"),
        ("process.interaction", "利用者・システム間のやり取り", "process"),
    ),
}

OPTIONAL_TARGETS: dict[InterviewProfile, tuple[tuple[str, str], ...]] = {
    "fixed_form": (),
    "business_process": (
        ("process.branch", "条件による分岐"),
        ("process.exception", "通常と異なる処理"),
        ("process.handoff", "担当者間の引き継ぎ"),
        ("process.input_output", "処理の入力と出力"),
        ("process.external_system", "外部システムとの連携"),
        ("process.error_handling", "エラー処理"),
    ),
    "system_requirement": (
        ("process.branch", "条件による分岐"),
        ("process.exception", "通常と異なる処理"),
        ("process.external_system", "外部システムとの連携"),
        ("process.error_handling", "エラー処理"),
        ("process.handoff", "担当者間の引き継ぎ"),
        ("process.input_output", "処理の入力と出力"),
    ),
}

PURPOSE_PROPOSAL_PREREQUISITES: tuple[tuple[str, str], ...] = (
    ("requirement.users", "利用者"),
    ("requirement.request", "要求内容"),
)

MAX_OPTIONAL_DEEPENING_PER_FIELD = 1


def resolve_profile(knowledge: Mapping[str, Any]) -> InterviewProfile:
    plan = knowledge.get("interviewPlan")
    raw_profile = plan.get("profile") if isinstance(plan, Mapping) else None
    if raw_profile in PROFILE_LABELS:
        return raw_profile
    return "fixed_form"


def profile_label(profile: InterviewProfile) -> str:
    return PROFILE_LABELS[profile]


def build_initial_structured_state(
    profile: InterviewProfile,
    fields: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    field_states = {
        str(field["id"]): _new_field_state(str(field["id"]), field)
        for field in fields
        if field.get("id")
    }
    requirement_states: dict[str, dict[str, Any]] = {}
    for target_id, label, kind in REQUIREMENT_DEFINITIONS[profile]:
        requirement_states[target_id] = _new_requirement_state(target_id, label, kind)
    for target_id, label in OPTIONAL_TARGETS[profile]:
        requirement_states.setdefault(target_id, _new_requirement_state(target_id, label, "process"))

    applicability_state = {
        topic: {
            "topic": topic,
            "status": "unknown",
            "evidenceTranscriptIds": [],
            "reason": None,
        }
        for topic in APPLICABILITY_TOPICS
        if _is_applicability_tracked(profile, topic)
    }
    process_state = {
        "version": 0,
        "participants": [],
        "nodes": [],
        "edges": [],
        "interactions": [],
        "sourceMessageIds": [],
    }
    return {
        "status": "in_progress",
        "interviewProfile": profile,
        "stateVersion": 0,
        "currentFieldId": None,
        "currentQuestionId": None,
        "completedFieldIds": [],
        "pendingFieldIds": [field_id for field_id in field_states],
        "askedQuestions": [],
        "followUpCounts": {},
        "fieldStates": field_states,
        "lastProcessedUserMessageId": None,
        "lastUtteranceCompleteness": None,
        "lastTranscriptAssessment": None,
        "lastAnswerAssessment": None,
        "activeProbeTarget": None,
        "pendingTranscriptConfirmation": None,
        "nextQuestionTarget": None,
        "lastTentativeTarget": None,
        "deferredProposalTarget": None,
        "closingState": "UNANSWERED",
        "closingAnswer": None,
        "requirementStates": requirement_states,
        "processState": process_state,
        "applicabilityState": applicability_state,
        "applicabilityOverviewAsked": False,
        "contradictions": [],
        "openIssues": [],
        "processVersion": 0,
    }


def sync_structured_state_fields(
    state: dict[str, Any],
    fields: Sequence[Mapping[str, Any]],
) -> bool:
    changed = False
    field_states = state.setdefault("fieldStates", {})
    pending_ids = state.setdefault("pendingFieldIds", [])
    for field in fields:
        field_id = str(field.get("id") or "").strip()
        if not field_id:
            continue
        if field_id not in field_states:
            field_states[field_id] = _new_field_state(field_id, field)
            pending_ids.append(field_id)
            changed = True
            continue
        field_state = field_states[field_id]
        if not isinstance(field_state, dict):
            field_states[field_id] = _new_field_state(field_id, field)
            changed = True
            continue
        metadata = _field_state_metadata(field_id, field)
        for key, value in metadata.items():
            # The question plan is part of the interview's evaluation
            # contract. Preserve a plan already captured in an in-progress
            # state, while backfilling it for older states.
            if key not in field_state:
                field_state[key] = deepcopy(value)
                changed = True
        if "deepeningItemIds" not in field_state:
            field_state["deepeningItemIds"] = []
            changed = True
        changed |= _sync_field_progress(field_state)
    return changed


def apply_document_candidate(
    state: dict[str, Any],
    target: dict[str, Any],
    *,
    value: str,
    source_ids: Sequence[str],
) -> bool:
    """Put a document-grounded value into the confirmation-only state.

    Document content is not a user statement and must never become a formal
    answer before the interviewee explicitly accepts it. This transition is
    intentionally separate from ``apply_structured_output`` because the
    candidate comes from Question Generator context, not from a transcript.
    """

    candidate_value = str(value or "").strip()
    normalized_source_ids = tuple(
        dict.fromkeys(str(source_id).strip() for source_id in source_ids if str(source_id).strip())
    )
    if not candidate_value or not normalized_source_ids:
        return False

    target_type = str(target.get("targetType") or target.get("kind") or "")
    target_id = str(target.get("targetId") or "")
    if target_type == "field":
        target_state = state.get("fieldStates", {}).get(target_id)
        candidate_key = "candidateAnswer"
        status_key = "answerState"
        pending_state = "AWAITING_CONFIRMATION"
        if not isinstance(target_state, dict) or target_state.get(status_key) == "CONFIRMED":
            return False
    elif target_type in {"requirement", "process"}:
        target_state = state.get("requirementStates", {}).get(target_id)
        candidate_key = "candidateValue"
        status_key = "status"
        pending_state = "AWAITING_CONFIRMATION"
        if not isinstance(target_state, dict) or target_state.get(status_key) == "CONFIRMED":
            return False
    else:
        return False

    existing_candidate = str(target_state.get(candidate_key) or "").strip()
    existing_source = target_state.get("candidateSource")
    if existing_candidate and existing_source != "document_reference":
        return False

    target_state[status_key] = pending_state
    target_state["answerResolution"] = "CONFIRM_REQUIRED"
    target_state["candidateSource"] = "document_reference"
    target_state["candidateSourceIds"] = list(normalized_source_ids)
    target_state[candidate_key] = candidate_value
    if target_type == "field":
        target_state["status"] = "asking"
        target_state["recordAnswer"] = None
        target_state["candidateItems"] = []
    else:
        target_state["value"] = None

    target["candidateSource"] = "document_reference"
    target["candidateValue"] = candidate_value
    target["candidateSourceIds"] = list(normalized_source_ids)
    return True


def record_interpretation_assessment(
    state: dict[str, Any],
    output: StructuredInterviewOutput,
    *,
    latest_message_id: str,
    raw_transcript: str | None = None,
) -> None:
    """Persist the model's safety decisions without promoting an answer.

    The raw transcript is supplied by the backend boundary, not trusted from
    the model response. This keeps the audit text stable while allowing the
    model to propose a normalized/corrected form for explicit confirmation.
    """

    assessment = output.transcriptAssessment.model_dump()
    raw = str(raw_transcript or assessment.get("rawTranscript") or "").strip()
    normalized = str(assessment.get("normalizedTranscript") or "").strip()
    if output.transcriptAssessment.correctionStatus == "NONE":
        normalized = _lightly_normalize_transcript(raw)
    elif output.transcriptAssessment.correctionStatus == "CORRECTED" and not normalized:
        # A correction without a candidate is unsafe. Treat it as uncertain
        # at the backend boundary rather than allowing an empty confirmation.
        assessment["correctionStatus"] = "UNCERTAIN"
    assessment["rawTranscript"] = raw
    assessment["normalizedTranscript"] = normalized
    state["lastUtteranceCompleteness"] = output.utteranceCompleteness
    state["lastTranscriptAssessment"] = assessment
    state["lastAnswerAssessment"] = output.answerAssessment.model_dump()
    state["lastProcessedUserMessageId"] = latest_message_id


def _lightly_normalize_transcript(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return re.sub(
        r"(?<=[\u3040-\u30ff\u3400-\u9fff])\s+(?=[\u3040-\u30ff\u3400-\u9fff])",
        "",
        normalized,
    )


def stage_transcript_correction(
    state: dict[str, Any],
    output: StructuredInterviewOutput,
    *,
    latest_message_id: str,
    raw_transcript: str,
    fields: Sequence[Mapping[str, Any]],
    valid_evidence_ids: Set[str] | None = None,
    current_question: Mapping[str, Any] | None = None,
) -> bool:
    """Store a single corrected transcript as a confirmation-only candidate."""

    assessment = output.transcriptAssessment
    normalized = assessment.normalizedTranscript.strip()
    if assessment.correctionStatus != "CORRECTED" or not normalized:
        return False

    record_interpretation_assessment(
        state,
        output,
        latest_message_id=latest_message_id,
        raw_transcript=raw_transcript,
    )
    target_refs: list[dict[str, str]] = []
    field_ids = {str(field.get("id")) for field in fields if field.get("id")}
    current_target = _target_for_current_question(state, current_question)
    for update in output.fieldUpdates:
        if (
            update.fieldId not in field_ids
            or not update.value.strip()
            or update.candidateSource == "document_reference"
            or not _has_valid_evidence(update.evidenceTranscriptIds, valid_evidence_ids)
        ):
            continue
        _apply_field_update(
            state,
            update.model_copy(update={"answerResolution": "CONFIRM_REQUIRED"}),
            latest_message_id,
            valid_evidence_ids,
            force_awaiting_confirmation=True,
        )
        target_refs.append({"targetType": "field", "targetId": update.fieldId})

    for update in output.requirementUpdates:
        if (
            update.requirementId not in state.setdefault("requirementStates", {})
            or not update.value.strip()
            or update.candidateSource == "document_reference"
            or not _has_valid_evidence(update.evidenceTranscriptIds, valid_evidence_ids)
        ):
            continue
        _apply_requirement_update(
            state,
            update.model_copy(update={"answerResolution": "CONFIRM_REQUIRED"}),
            latest_message_id,
            valid_evidence_ids,
            force_awaiting_confirmation=True,
        )
        target_refs.append(
            {
                "targetType": (
                    "requirement"
                    if update.requirementId.startswith("requirement.")
                    else "process"
                ),
                "targetId": update.requirementId,
            }
        )

    if not target_refs and current_target is not None:
        target_type = str(current_target.get("targetType") or "")
        target_id = str(current_target.get("targetId") or "")
        if target_type == "field" and target_id in field_ids:
            _apply_field_update(
                state,
                FieldUpdate(
                    fieldId=target_id,
                    value=normalized,
                    evidenceTranscriptIds=[latest_message_id],
                    answerResolution="CONFIRM_REQUIRED",
                ),
                latest_message_id,
                valid_evidence_ids,
                force_awaiting_confirmation=True,
            )
            target_refs.append({"targetType": "field", "targetId": target_id})
        elif target_type in {"requirement", "process"} and target_id in state["requirementStates"]:
            _apply_requirement_update(
                state,
                RequirementUpdate(
                    requirementId=target_id,
                    value=normalized,
                    evidenceTranscriptIds=[latest_message_id],
                    answerResolution="CONFIRM_REQUIRED",
                ),
                latest_message_id,
                valid_evidence_ids,
                force_awaiting_confirmation=True,
            )
            target_refs.append({"targetType": target_type, "targetId": target_id})

    if not target_refs:
        return False
    state["pendingTranscriptConfirmation"] = {
        "messageId": latest_message_id,
        "rawTranscript": raw_transcript.strip(),
        "normalizedTranscript": normalized,
        "correctionCandidates": list(assessment.correctionCandidates),
        "targetRefs": target_refs,
        "sourceQuestion": dict(current_question) if current_question else None,
    }
    state["activeProbeTarget"] = None
    state["lastProcessedUserMessageId"] = latest_message_id
    return True


def apply_structured_output(
    state: dict[str, Any],
    output: StructuredInterviewOutput,
    *,
    latest_message_id: str,
    fields: Sequence[Mapping[str, Any]],
    profile: InterviewProfile | None = None,
    valid_evidence_ids: Set[str] | None = None,
    current_question: Mapping[str, Any] | None = None,
    raw_transcript: str | None = None,
) -> list[str]:
    """Apply only validated meaning; completion remains a coordinator decision."""

    record_interpretation_assessment(
        state,
        output,
        latest_message_id=latest_message_id,
        raw_transcript=raw_transcript,
    )
    if output.utteranceCompleteness != "COMPLETE" or output.answerAssessment.sufficiency == "INCOMPLETE":
        # A final Transcribe result is still only a candidate utterance. The
        # Interpreter must establish both grammatical completeness and a
        # meaningful answer before any field or process state is changed.
        return []

    changed_topics: list[str] = []
    field_ids = {str(field.get("id")) for field in fields if field.get("id")}
    current_target = _target_for_current_question(state, current_question)
    confirmation_target = current_target
    if confirmation_target is None and output.dialogueAct in {"CONFIRMATION", "REJECTION"}:
        pending_targets = list_pending_confirmation_targets(state)
        confirmation_target = pending_targets[0] if pending_targets else None
    confirmation_applied = _apply_confirmation_or_rejection(
        state,
        output,
        current_question=current_question,
        confirmation_message_id=latest_message_id,
    )
    state["lastConfirmationApplied"] = confirmation_applied
    if _should_defer_purpose_proposal_request(state, output, profile, current_target):
        state["deferredProposalTarget"] = "requirement.purpose_problem"
    process_present_in_output = any(
        update.topic == "process"
        and update.status == "present"
        and _has_valid_evidence(update.evidenceTranscriptIds, valid_evidence_ids)
        for update in output.applicability
    )
    process_not_applicable_in_output = any(
        update.topic == "process"
        and update.status == "not_applicable"
        and _has_valid_evidence(update.evidenceTranscriptIds, valid_evidence_ids)
        for update in output.applicability
    )
    process_is_present = (
        process_present_in_output
        or (_process_is_present(state, profile) and not process_not_applicable_in_output)
    )

    valid_field_updates: list[FieldUpdate] = []
    for update in output.fieldUpdates:
        if (
            update.fieldId not in field_ids
            or not update.value.strip()
            or update.candidateSource == "document_reference"
            or not _has_valid_evidence(update.evidenceTranscriptIds, valid_evidence_ids)
        ):
            continue
        if normalize_answer_resolution(update.answerResolution) == "RETRY":
            continue
        if (
            confirmation_applied
            and output.dialogueAct == "CONFIRMATION"
            and _target_matches(confirmation_target, "field", update.fieldId)
        ):
            # Some providers repeat the confirmed candidate in fieldUpdates.
            # Applying it after _confirm_target would reopen the same field.
            continue
        valid_field_updates.append(update)

    grouped_field_updates: dict[str, list[FieldUpdate]] = {}
    for update in valid_field_updates:
        grouped_field_updates.setdefault(update.fieldId, []).append(update)
    for field_id, updates in grouped_field_updates.items():
        for update in updates:
            _apply_field_update(
                state,
                update,
                latest_message_id,
                valid_evidence_ids,
                force_awaiting_confirmation=_target_matches(current_target, "field", field_id),
                defer_auto_confirm=True,
            )
        if _field_updates_allow_auto_confirm(output, updates):
            field_state = state.get("fieldStates", {}).get(field_id)
            if isinstance(field_state, Mapping) and not _field_missing_required_items(field_state):
                _confirm_target(
                    state,
                    {"targetType": "field", "targetId": field_id},
                    confirmation_message_id=None,
                )
        changed_topics.extend(f"field:{field_id}" for _ in updates)

    for update in output.requirementUpdates:
        if _should_defer_purpose_proposal_update(state, update, profile):
            state["deferredProposalTarget"] = "requirement.purpose_problem"
            continue
        if (
            update.requirementId not in state.setdefault("requirementStates", {})
            or not update.value.strip()
            or update.candidateSource == "document_reference"
            or not _has_valid_evidence(update.evidenceTranscriptIds, valid_evidence_ids)
        ):
            continue
        if normalize_answer_resolution(update.answerResolution) == "RETRY":
            continue
        if (
            profile == "system_requirement"
            and update.requirementId.startswith("process.")
            and not process_is_present
        ):
            continue
        if (
            confirmation_applied
            and output.dialogueAct == "CONFIRMATION"
            and _target_matches(
                confirmation_target,
                "requirement" if update.requirementId.startswith("requirement.") else "process",
                update.requirementId,
            )
        ):
            # See the field-update guard above. A repeated requirement update
            # must not undo the confirmation that was just applied.
            continue
        _apply_requirement_update(
            state,
            update,
            latest_message_id,
            valid_evidence_ids,
            force_awaiting_confirmation=_target_matches(
                current_target,
                "requirement" if update.requirementId.startswith("requirement.") else "process",
                update.requirementId,
            ),
        )
        changed_topics.append(update.requirementId)

    process_changed = False
    if profile != "fixed_form" and _allows_process_patch(
        state,
        output,
        profile=profile,
        valid_evidence_ids=valid_evidence_ids,
    ):
        process_changed = _apply_process_patch(
            state,
            output.processPatch,
            latest_message_id,
            valid_evidence_ids=valid_evidence_ids,
        )
    if process_changed:
        changed_topics.append("process_model")

    for update in output.applicability:
        if update.topic not in state.setdefault("applicabilityState", {}):
            continue
        if (
            profile == "system_requirement"
            and update.topic != "process"
            and not process_is_present
        ):
            continue
        if not _has_valid_evidence(update.evidenceTranscriptIds, valid_evidence_ids):
            # Unknown evidence cannot establish applicability. Preserve unknown.
            continue
        state["applicabilityState"][update.topic] = {
            "topic": update.topic,
            "status": update.status,
            "evidenceTranscriptIds": _ensure_latest_evidence(
                update.evidenceTranscriptIds,
                latest_message_id,
                valid_evidence_ids,
            ),
            "reason": update.reason,
        }
        changed_topics.append(f"applicability:{update.topic}")

    _upsert_contradictions(
        state,
        (
            contradiction
            for contradiction in output.contradictions
            if _has_valid_evidence(contradiction.evidenceTranscriptIds, valid_evidence_ids)
        ),
        latest_message_id,
        valid_evidence_ids=valid_evidence_ids,
    )
    _resolve_contradictions(state, output.resolvedContradictionIds, latest_message_id)
    _upsert_open_issues(
        state,
        (
            issue
            for issue in output.openIssues
            if _has_valid_evidence(issue.evidenceTranscriptIds, valid_evidence_ids)
        ),
        latest_message_id,
        valid_evidence_ids=valid_evidence_ids,
    )
    state["lastStructuredDialogueAct"] = output.dialogueAct
    state["lastProcessedUserMessageId"] = latest_message_id
    return changed_topics


def evaluate_completion(
    state: Mapping[str, Any],
    profile: InterviewProfile,
    fields: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    unresolved_contradictions = [
        item for item in state.get("contradictions", []) if item.get("status", "open") == "open"
    ]
    pending_confirmations = list_pending_confirmation_targets(state)
    missing_required = list_missing_required_targets(state, profile, fields)
    unknown_applicability = list_unknown_applicability(state, profile)
    closing_state = str(state.get("closingState") or "UNANSWERED")
    closing_complete = closing_state == "CONFIRMED"
    complete = (
        not unresolved_contradictions
        and not pending_confirmations
        and not missing_required
        and not unknown_applicability
        and closing_complete
    )
    return {
        "complete": complete,
        "unresolvedContradictionIds": [str(item.get("contradictionId")) for item in unresolved_contradictions],
        "pendingConfirmationTargets": pending_confirmations,
        "missingRequiredTargets": missing_required,
        "unknownApplicabilityTopics": unknown_applicability,
        "closingState": closing_state,
        "closingRequired": not closing_complete,
    }


def select_next_question_target(
    state: dict[str, Any],
    profile: InterviewProfile,
    fields: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Select exactly one target using the fixed backend priority."""

    pending_transcript = state.get("pendingTranscriptConfirmation")
    if isinstance(pending_transcript, Mapping):
        message_id = str(pending_transcript.get("messageId") or "").strip()
        normalized = str(pending_transcript.get("normalizedTranscript") or "").strip()
        if message_id and normalized:
            return _target(
                "transcript_confirmation",
                message_id,
                "発話内容",
                1,
                candidate_value=normalized,
            )

    active_probe = _active_probe_target(state)
    if active_probe is not None:
        return active_probe

    # Preserve the legacy queue of ordinary candidates, but deliberately leave
    # TENTATIVE candidates in the conversation so a later answer can confirm
    # them implicitly.
    _promote_one_candidate(state, include_tentative=False)
    contradictions = [
        item for item in state.get("contradictions", []) if item.get("status", "open") == "open"
    ]
    if contradictions:
        item = contradictions[0]
        return _target("contradiction", str(item.get("contradictionId")), str(item.get("topic") or "矛盾"), 1)

    pending_confirmations = list_pending_confirmation_targets(state)
    if pending_confirmations:
        return pending_confirmations[0]

    deferred_context = _select_deferred_proposal_context(state, profile)
    if deferred_context:
        return deferred_context

    # A TENTATIVE candidate is intentionally excluded here. This lets the
    # interview collect the next answer and use it as natural confirmation.
    missing_required = list_missing_required_targets(
        state,
        profile,
        fields,
        include_tentative=False,
    )
    unknown_applicability = list_unknown_applicability(state, profile)
    if missing_required:
        process_model_only = all(
            target.get("targetId") == "process.main_flow"
            for target in missing_required
        )
        if not (process_model_only and unknown_applicability):
            return missing_required[0]

    if unknown_applicability:
        if profile == "system_requirement" and "process" in unknown_applicability:
            return _target("applicability", "process", "処理の流れがあるか", 4)
        grouped_topics = {
            "branch",
            "exception",
            "external_system",
            "error_handling",
            "handoff",
            "input_output",
        }
        if (
            not state.get("applicabilityOverviewAsked")
            and any(topic in grouped_topics for topic in unknown_applicability)
        ):
            return _target(
                "applicability_overview",
                "optional_cases",
                "通常と異なるケースや条件による処理変更の有無",
                4,
            )
        topic = unknown_applicability[0]
        if topic == "process" and profile == "system_requirement":
            label = "処理の流れがあるか"
        else:
            label = APPLICABILITY_LABELS.get(topic, topic)
        return _target("applicability", topic, label, 4)

    optional = _select_optional_target(state, profile)
    if optional:
        return optional
    if str(state.get("closingState") or "UNANSWERED") != "CONFIRMED":
        return _target("closing", CLOSING_TARGET_ID, CLOSING_TARGET_LABEL, 6)
    # Once there is no other useful question, fall back to an explicit stop
    # only for the remaining candidate. This is the exceptional confirmation
    # path, not the default after every answer.
    _promote_one_candidate(state, include_tentative=True)
    pending_confirmations = list_pending_confirmation_targets(state)
    if pending_confirmations:
        return pending_confirmations[0]
    return None


def _active_probe_target(state: Mapping[str, Any]) -> dict[str, Any] | None:
    value = state.get("activeProbeTarget")
    if not isinstance(value, Mapping):
        return None
    target_type = str(value.get("targetType") or "").strip()
    target_id = str(value.get("targetId") or "").strip()
    if target_type not in {"field", "requirement", "process", "applicability"} or not target_id:
        return None
    target = _target(
        target_type,
        target_id,
        str(value.get("label") or target_id),
        1,
    )
    for key in (
        "missingItemIds",
        "missingItems",
        "capturedItemIds",
        "questionPlan",
        "sourceQuestion",
        "sourceDescription",
        "deepeningItemIds",
        "deepeningItems",
        "optionalDeepening",
    ):
        if key in value:
            target[key] = deepcopy(value[key])
    target["probeType"] = str(value.get("probeType") or "CLARIFY")
    target["probeCount"] = int(value.get("probeCount") or 0)
    return target


def target_key(target: Mapping[str, Any] | None) -> str:
    if not target:
        return ""
    target_type = str(target.get("targetType") or target.get("kind") or "").strip()
    target_id = str(target.get("targetId") or "").strip()
    return f"{target_type}:{target_id}" if target_type and target_id else ""


def register_probe(
    state: dict[str, Any],
    *,
    target: Mapping[str, Any] | None,
    probe_type: str | None,
) -> int:
    """Keep the current target active while one focused probe is asked."""

    if target is not None:
        target = _field_target_with_progress(state, target)
    key = target_key(target)
    if not key:
        return 0
    counts = state.setdefault("followUpCounts", {})
    count = int(counts.get(key, counts.get(str(target.get("targetId") or ""), 0)) or 0) + 1
    counts[key] = count
    target_type = str(target.get("targetType") or target.get("kind") or "")
    target_id = str(target.get("targetId") or "")
    active_probe = {
        "targetType": target_type,
        "targetId": target_id,
        "label": str(target.get("label") or target_id),
        "probeType": probe_type if probe_type in {"REFRAME", "EXAMPLE", "REASON", "CRITERIA", "CLARIFY", "RETRY"} else "CLARIFY",
        "probeCount": count,
    }
    for key in (
        "missingItemIds",
        "missingItems",
        "capturedItemIds",
        "questionPlan",
        "sourceQuestion",
        "sourceDescription",
        "deepeningItemIds",
        "deepeningItems",
        "optionalDeepening",
    ):
        if key in target:
            active_probe[key] = deepcopy(target[key])
    state["activeProbeTarget"] = active_probe
    return count


def clear_probe(state: dict[str, Any], target: Mapping[str, Any] | None = None) -> None:
    active = state.get("activeProbeTarget")
    if target is None or (
        isinstance(active, Mapping) and target_key(active) == target_key(target)
    ):
        state["activeProbeTarget"] = None


def accept_no_answer(
    state: dict[str, Any],
    target: Mapping[str, Any] | None,
    *,
    transcript: str,
    message_id: str,
    valid_evidence_ids: Set[str] | None = None,
) -> bool:
    """Treat an explicit second refusal as a valid no-detail answer."""

    if target is None:
        return False
    value = transcript.strip()
    evidence = _ensure_latest_evidence([message_id], message_id, valid_evidence_ids)
    target_type = str(target.get("targetType") or target.get("kind") or "")
    target_id = str(target.get("targetId") or "")
    if target_type == "field":
        field_state = state.get("fieldStates", {}).get(target_id)
        if not isinstance(field_state, dict):
            return False
        field_state.update(
            {
                "answerState": "CONFIRMED",
                "answerResolution": "AUTO_CONFIRM",
                "status": "completed",
                "answerDisposition": "NO_DETAIL",
                "recordAnswer": value,
                "rawAnswer": value,
                "confirmedSource": "user_statement",
                "confirmedSourceIds": evidence,
                "candidateAnswer": None,
                "candidateSource": None,
                "candidateSourceIds": [],
                "candidateItems": [],
            }
        )
        raw_history = field_state.setdefault("rawAnswerHistory", [])
        if value and (not raw_history or raw_history[-1] != value):
            raw_history.append(value)
        _mark_field_completed(state, target_id)
    elif target_type in {"requirement", "process"}:
        requirement_state = state.get("requirementStates", {}).get(target_id)
        if not isinstance(requirement_state, dict):
            return False
        requirement_state.update(
            {
                "status": "CONFIRMED",
                "answerResolution": "AUTO_CONFIRM",
                "answerDisposition": "NO_DETAIL",
                "value": value,
                "confirmedSource": "user_statement",
                "confirmedSourceIds": evidence,
                "candidateValue": None,
                "candidateSource": None,
                "candidateSourceIds": [],
            }
        )
    elif target_type == "applicability":
        applicability = state.get("applicabilityState", {}).get(target_id)
        if not isinstance(applicability, dict):
            return False
        applicability.update(
            {
                "status": "not_applicable",
                "evidenceTranscriptIds": evidence,
                "reason": value,
            }
        )
    else:
        return False
    clear_probe(state, target)
    return True


def confirm_closing_answer(
    state: dict[str, Any],
    *,
    transcript: str,
    message_id: str,
    normalized_transcript: str | None = None,
    valid_evidence_ids: Set[str] | None = None,
) -> bool:
    """Finish the open-ended closing after one complete response."""

    raw = transcript.strip()
    if not raw:
        return False
    normalized = (normalized_transcript or raw).strip()
    state["closingState"] = "CONFIRMED"
    state["closingAnswer"] = {
        "rawTranscript": raw,
        "normalizedTranscript": normalized,
        "evidenceTranscriptIds": _ensure_latest_evidence(
            [message_id],
            message_id,
            valid_evidence_ids,
        ),
    }
    state["activeProbeTarget"] = None
    return True


def list_pending_confirmation_targets(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for field_id, field_state in state.get("fieldStates", {}).items():
        if field_state.get("answerState") == "AWAITING_CONFIRMATION":
            targets.append(
                _target(
                    "field",
                    str(field_id),
                    str(field_id),
                    2,
                    candidate_source=field_state.get("candidateSource"),
                )
            )
    for requirement_id, requirement_state in state.get("requirementStates", {}).items():
        if requirement_state.get("status") == "AWAITING_CONFIRMATION":
            targets.append(
                _target(
                    str(requirement_state.get("kind") or "requirement"),
                    str(requirement_id),
                    str(requirement_state.get("label") or requirement_id),
                    2,
                    candidate_source=requirement_state.get("candidateSource"),
                )
            )
    return targets


def list_missing_required_targets(
    state: Mapping[str, Any],
    profile: InterviewProfile,
    fields: Sequence[Mapping[str, Any]],
    *,
    include_tentative: bool = True,
) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    if profile == "fixed_form":
        for field in fields:
            field_id = str(field.get("id") or "").strip()
            if not field_id or not field.get("required"):
                continue
            field_state = state.get("fieldStates", {}).get(field_id, {})
            missing_items = _field_missing_required_items(field_state, field)
            if field_state.get("answerState") == "CONFIRMED" and not missing_items:
                continue
            if (
                not include_tentative
                and field_state.get("answerState") == "CANDIDATE_PENDING"
                and field_state.get("answerResolution") == "TENTATIVE"
                and not missing_items
            ):
                continue
            targets.append(
                _field_target_with_progress(
                    state,
                    _target("field", field_id, str(field.get("name") or field_id), 3),
                    field=field,
                )
            )

    process_is_present = _process_is_present(state, profile)
    for target_id, label, kind in REQUIREMENT_DEFINITIONS[profile]:
        if kind == "process" and profile == "system_requirement" and not process_is_present:
            continue
        requirement_state = state.get("requirementStates", {}).get(target_id, {})
        if requirement_state.get("status") != "CONFIRMED" and not (
            not include_tentative
            and requirement_state.get("status") == "CANDIDATE_PENDING"
            and requirement_state.get("answerResolution") == "TENTATIVE"
        ):
            targets.append(_target(kind, target_id, label, 3))
    targets.extend(
        _list_missing_present_optional_targets(
            state,
            profile,
            include_tentative=include_tentative,
        )
    )
    process_model_target = _process_model_recovery_target(state, profile, targets)
    if process_model_target is not None:
        targets.append(process_model_target)
    return targets


def _process_model_recovery_target(
    state: Mapping[str, Any],
    profile: InterviewProfile,
    existing_targets: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Return the required flow target when extraction left no usable graph."""

    if profile == "fixed_form" or not _process_is_present(state, profile):
        return None
    if _has_renderable_flowchart(state):
        return None
    if any(target.get("targetId") == "process.main_flow" for target in existing_targets):
        return None
    for target_id, label, kind in REQUIREMENT_DEFINITIONS[profile]:
        if target_id == "process.main_flow":
            return _target(kind, target_id, label, 3)
    return None


def _has_renderable_flowchart(state: Mapping[str, Any]) -> bool:
    process_state = state.get("processState") or {}
    active_nodes = {
        str(node.get("nodeId"))
        for node in process_state.get("nodes", [])
        if node.get("lifecycle") != "superseded" and str(node.get("nodeId") or "").strip()
    }
    active_edges = [
        edge
        for edge in process_state.get("edges", [])
        if edge.get("lifecycle") != "superseded"
        and str(edge.get("sourceNodeId") or "") in active_nodes
        and str(edge.get("targetNodeId") or "") in active_nodes
    ]
    return len(active_nodes) >= 2 and len(active_edges) >= 1


def list_unknown_applicability(state: Mapping[str, Any], profile: InterviewProfile) -> list[str]:
    process_status = state.get("applicabilityState", {}).get("process", {}).get("status", "unknown")
    return [
        topic
        for topic in APPLICABILITY_TOPICS
        if _is_applicability_tracked(profile, topic)
        and state.get("applicabilityState", {}).get(topic, {}).get("status", "unknown") == "unknown"
        and (topic != "process" or profile == "system_requirement")
        and not (
            profile == "system_requirement"
            and topic != "process"
            and process_status != "present"
        )
    ]


def _new_field_state(
    field_id: str,
    field: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = _field_state_metadata(field_id, field)
    required_items = metadata["questionPlan"]["requiredItems"]
    field_state = {
        "fieldId": field_id,
        "status": "pending",
        "answerSummary": None,
        "missingInformation": [str(item["label"]) for item in required_items],
        "answerState": "UNANSWERED",
        "answerResolution": None,
        "candidateAnswer": None,
        "candidateSource": None,
        "candidateSourceIds": [],
        "candidateProposalMessageId": None,
        "confirmedSource": None,
        "confirmedSourceIds": [],
        "confirmedProposalMessageId": None,
        "confirmationEvidenceTranscriptIds": [],
        "rawAnswer": None,
        "rawAnswerHistory": [],
        "recordAnswer": None,
        "capturedItems": [],
        "capturedItemIds": [],
        "candidateItems": [],
        "confirmedItems": [],
        "missingRequiredItemIds": [str(item["itemId"]) for item in required_items],
        "deepeningItemIds": [],
        "answerDisposition": None,
    }
    field_state.update(metadata)
    return field_state


def _field_state_metadata(
    field_id: str,
    field: Mapping[str, Any] | None,
) -> dict[str, Any]:
    field = field if isinstance(field, Mapping) else {}
    plan = _normalize_question_plan(field_id, field)
    return {
        "questionPlan": plan,
        "sourceQuestion": _field_question_text(field),
        "sourceDescription": _clean_optional_text(field.get("description")),
    }


def _normalize_question_plan(
    field_id: str,
    field: Mapping[str, Any],
) -> dict[str, Any]:
    raw_plan = field.get("questionPlan")
    try:
        plan = InterviewQuestionPlan.model_validate(raw_plan or {})
    except (TypeError, ValueError):
        plan = InterviewQuestionPlan()

    required_items = _normalize_plan_items(plan.requiredItems)
    optional_items = _normalize_plan_items(plan.optionalItems)
    if not required_items:
        label = _clean_optional_text(field.get("name")) or field_id
        description = _clean_optional_text(field.get("description"))
        required_items = [
            {
                "itemId": field_id,
                "label": label,
                "description": description,
            }
        ]

    required_ids = {str(item["itemId"]) for item in required_items}
    optional_items = [
        item for item in optional_items if str(item["itemId"]) not in required_ids
    ]
    return {
        "version": int(plan.version or 1),
        "purpose": _clean_optional_text(plan.purpose),
        "requiredItems": required_items,
        "optionalItems": optional_items,
        "completionCriteria": {"mode": "all_required_items"},
    }


def _normalize_plan_items(value: Any) -> list[dict[str, Any]]:
    items = value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in items:
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        if not isinstance(item, Mapping):
            continue
        item_id = _clean_optional_text(item.get("itemId"))
        label = _clean_optional_text(item.get("label"))
        if not item_id or not label or item_id in seen_ids:
            continue
        normalized.append(
            {
                "itemId": item_id,
                "label": label,
                "description": _clean_optional_text(item.get("description")),
            }
        )
        seen_ids.add(item_id)
    return normalized


def _field_question_text(field: Mapping[str, Any]) -> str | None:
    for key in ("questionText", "question"):
        value = _clean_optional_text(field.get(key))
        if value:
            return value
    examples = field.get("aiQuestionExamples")
    if isinstance(examples, Sequence) and not isinstance(examples, (str, bytes)):
        for example in examples:
            value = _clean_optional_text(example)
            if value:
                return value
    return None


def _clean_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _field_required_items(
    field_state: Mapping[str, Any],
    field: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    plan = field_state.get("questionPlan")
    if isinstance(plan, Mapping):
        required_items = _normalize_plan_items(plan.get("requiredItems"))
        if required_items:
            return required_items
    field_id = str(field_state.get("fieldId") or "").strip()
    if field is not None and field_id:
        return _normalize_question_plan(field_id, field)["requiredItems"]
    if not field_id:
        return []
    return [{"itemId": field_id, "label": field_id, "description": None}]


def _field_optional_items(field_state: Mapping[str, Any]) -> list[dict[str, Any]]:
    plan = field_state.get("questionPlan")
    if not isinstance(plan, Mapping):
        return []
    return _normalize_plan_items(plan.get("optionalItems"))


def select_optional_deepening_target(
    state: dict[str, Any],
    target: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Select at most one useful optional follow-up for a completed field."""

    if not isinstance(target, Mapping):
        return None
    target_type = str(target.get("targetType") or target.get("kind") or "")
    target_id = str(target.get("targetId") or "").strip()
    if target_type != "field" or not target_id or target.get("optionalDeepening"):
        return None
    field_state = state.get("fieldStates", {}).get(target_id)
    if not isinstance(field_state, dict):
        return None
    if field_state.get("answerState") == "CONFIRMED":
        return None
    if _field_missing_required_items(field_state):
        return None
    if field_state.get("answerDisposition") == "NO_DETAIL":
        return None

    asked_ids = [
        item_id
        for item_id in (
            _clean_optional_text(value)
            for value in field_state.get("deepeningItemIds", [])
        )
        if item_id
    ]
    if len(asked_ids) >= MAX_OPTIONAL_DEEPENING_PER_FIELD:
        return None
    captured_ids = set(_field_captured_item_ids(field_state))
    item = next(
        (
            candidate
            for candidate in _field_optional_items(field_state)
            if str(candidate["itemId"]) not in captured_ids
            and str(candidate["itemId"]) not in asked_ids
        ),
        None,
    )
    if item is None:
        return None

    item_id = str(item["itemId"])
    field_state["deepeningItemIds"] = [*asked_ids, item_id]
    result = _field_target_with_progress(state, target)
    result.update(
        {
            "label": str(item["label"]),
            "missingItemIds": [item_id],
            "missingItems": [deepcopy(item)],
            "deepeningItemIds": list(field_state["deepeningItemIds"]),
            "deepeningItems": [deepcopy(item)],
            "optionalDeepening": True,
            "capturedItemIds": _field_captured_item_ids(field_state),
        }
    )
    return result


def _field_captured_items(field_state: Mapping[str, Any]) -> list[dict[str, Any]]:
    answer_state = str(field_state.get("answerState") or "")
    preferred_keys = (
        ("confirmedItems", "capturedItems")
        if answer_state == "CONFIRMED"
        else ("candidateItems", "capturedItems", "confirmedItems")
    )
    for key in preferred_keys:
        value = field_state.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and value:
            return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _field_captured_item_ids(field_state: Mapping[str, Any]) -> list[str]:
    item_ids: list[str] = []
    for item in _field_captured_items(field_state):
        item_id = _clean_optional_text(item.get("itemId"))
        value = _clean_optional_text(item.get("value"))
        if item_id and value and item_id not in item_ids:
            item_ids.append(item_id)
    required_items = _field_required_items(field_state)
    if (
        str(field_state.get("answerState") or "") == "CONFIRMED"
        and _clean_optional_text(field_state.get("recordAnswer"))
        and not item_ids
    ):
        # States created before item-level tracking are already confirmed at
        # field level. Preserve those answers when adding the new metadata.
        return [str(item["itemId"]) for item in required_items]
    return item_ids


def _field_missing_required_items(
    field_state: Mapping[str, Any],
    field: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    required_items = _field_required_items(field_state, field)
    captured_ids = set(_field_captured_item_ids(field_state))
    if field_state.get("answerDisposition") == "NO_DETAIL":
        return []
    return [
        item for item in required_items if str(item["itemId"]) not in captured_ids
    ]


def _sync_field_progress(field_state: dict[str, Any]) -> bool:
    missing_items = _field_missing_required_items(field_state)
    missing_ids = [str(item["itemId"]) for item in missing_items]
    missing_information = [str(item["label"]) for item in missing_items]
    captured_ids = _field_captured_item_ids(field_state)
    changed = False
    for key, value in (
        ("missingRequiredItemIds", missing_ids),
        ("missingInformation", missing_information),
        ("capturedItemIds", captured_ids),
    ):
        if field_state.get(key) != value:
            field_state[key] = value
            changed = True
    return changed


def _field_target_with_progress(
    state: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    field: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = dict(target)
    target_type = str(result.get("targetType") or result.get("kind") or "")
    target_id = str(result.get("targetId") or "").strip()
    if target_type != "field" or not target_id:
        return result
    field_state = state.get("fieldStates", {}).get(target_id, {})
    if not isinstance(field_state, Mapping):
        return result
    required_items = _field_required_items(field_state, field)
    captured_ids = _field_captured_item_ids(field_state)
    if result.get("optionalDeepening"):
        result["capturedItemIds"] = captured_ids
        result["questionPlan"] = deepcopy(field_state.get("questionPlan") or {})
        for key in ("sourceQuestion", "sourceDescription"):
            if field_state.get(key):
                result[key] = field_state.get(key)
        return result
    if len(required_items) <= 1:
        return result
    missing_items = _field_missing_required_items(field_state, field)
    if missing_items and captured_ids:
        result["label"] = _join_item_labels(missing_items)
    result["missingItemIds"] = [str(item["itemId"]) for item in missing_items]
    result["missingItems"] = deepcopy(missing_items)
    result["capturedItemIds"] = captured_ids
    result["questionPlan"] = deepcopy(field_state.get("questionPlan") or {})
    for key in ("sourceQuestion", "sourceDescription"):
        if field_state.get(key):
            result[key] = field_state.get(key)
    return result


def _join_item_labels(items: Sequence[Mapping[str, Any]]) -> str:
    labels = [str(item.get("label") or "").strip() for item in items]
    labels = [label for label in labels if label]
    if len(labels) <= 1:
        return labels[0] if labels else ""
    if len(labels) == 2:
        return f"{labels[0]}と、{labels[1]}"
    return "、".join(labels)


def _new_requirement_state(target_id: str, label: str, kind: str) -> dict[str, Any]:
    return {
        "requirementId": target_id,
        "label": label,
        "kind": kind,
        "status": "UNANSWERED",
        "answerResolution": None,
        "candidateValue": None,
        "candidateSource": None,
        "candidateSourceIds": [],
        "candidateProposalMessageId": None,
        "confirmedSource": None,
        "confirmedSourceIds": [],
        "confirmedProposalMessageId": None,
        "confirmationEvidenceTranscriptIds": [],
        "value": None,
        "evidenceTranscriptIds": [],
        "answerDisposition": None,
    }


def _target(
    kind: str,
    target_id: str,
    label: str,
    priority: int,
    *,
    candidate_source: str | None = None,
    candidate_value: str | None = None,
    candidate_source_ids: Sequence[str] = (),
) -> dict[str, Any]:
    target = {
        "targetType": kind,
        "targetId": target_id,
        "label": label,
        "priority": priority,
    }
    if candidate_source in {"assistant_proposal", "document_reference"}:
        target["candidateSource"] = candidate_source
    if candidate_value:
        target["candidateValue"] = str(candidate_value).strip()
    if candidate_source_ids:
        target["candidateSourceIds"] = list(candidate_source_ids)
    return target


def _apply_confirmation_or_rejection(
    state: dict[str, Any],
    output: StructuredInterviewOutput,
    *,
    current_question: Mapping[str, Any] | None = None,
    confirmation_message_id: str | None = None,
) -> bool:
    if output.dialogueAct not in {"CONFIRMATION", "REJECTION"}:
        return False
    target = _target_for_current_question(state, current_question)
    if target is None:
        pending = list_pending_confirmation_targets(state)
        if not pending:
            return False
        target = pending[0]
    if not _is_confirmation_target(state, target):
        return False
    if output.dialogueAct == "CONFIRMATION":
        _confirm_target(state, target, confirmation_message_id=confirmation_message_id)
    else:
        _reject_target(state, target)
    return True


def _target_for_current_question(
    state: Mapping[str, Any],
    current_question: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not current_question:
        return None
    target_type = str(current_question.get("targetType") or current_question.get("kind") or "")
    target_id = str(current_question.get("targetId") or "")
    if not target_type or not target_id:
        return None
    if target_type == "transcript_confirmation":
        pending = state.get("pendingTranscriptConfirmation")
        if not isinstance(pending, Mapping):
            return None
        return _target(
            "transcript_confirmation",
            target_id,
            "発話内容",
            1,
            candidate_value=str(pending.get("normalizedTranscript") or "").strip(),
        )
    target = _target(
        target_type,
        target_id,
        str(current_question.get("targetLabel") or current_question.get("label") or target_id),
        2,
    )
    if target_type == "field":
        for key in ("deepeningItemIds", "deepeningItems", "optionalDeepening"):
            if key in current_question:
                target[key] = deepcopy(current_question[key])
    if target_type == "field":
        target = _field_target_with_progress(state, target)
    if target_type == "field":
        field_state = state.get("fieldStates", {}).get(target_id, {})
        target["candidateSource"] = field_state.get("candidateSource")
        target["candidateValue"] = field_state.get("candidateAnswer")
        target["candidateSourceIds"] = list(field_state.get("candidateSourceIds") or [])
    else:
        requirement_state = state.get("requirementStates", {}).get(target_id, {})
        target["candidateSource"] = requirement_state.get("candidateSource")
        target["candidateValue"] = requirement_state.get("candidateValue")
        target["candidateSourceIds"] = list(requirement_state.get("candidateSourceIds") or [])
    return target


def _is_confirmation_target(state: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    target_type = target.get("targetType") or target.get("kind")
    target_id = str(target.get("targetId") or "")
    if target_type == "transcript_confirmation":
        pending = state.get("pendingTranscriptConfirmation")
        return bool(
            isinstance(pending, Mapping)
            and str(pending.get("messageId") or "") == target_id
            and str(pending.get("normalizedTranscript") or "").strip()
        )
    if target_type == "contradiction":
        return any(
            str(item.get("contradictionId") or "") == target_id
            and item.get("status", "open") == "open"
            for item in state.get("contradictions", [])
        )
    if target_type == "field":
        return state.get("fieldStates", {}).get(target_id, {}).get("answerState") == "AWAITING_CONFIRMATION"
    if target_type in {"requirement", "process"}:
        return state.get("requirementStates", {}).get(target_id, {}).get("status") == "AWAITING_CONFIRMATION"
    return False


def _target_matches(
    target: Mapping[str, Any] | None,
    target_type: str,
    target_id: str,
) -> bool:
    if not target:
        return False
    current_type = target.get("targetType") or target.get("kind")
    return current_type == target_type and str(target.get("targetId") or "") == target_id


def _should_defer_purpose_proposal_request(
    state: Mapping[str, Any],
    output: StructuredInterviewOutput,
    profile: InterviewProfile,
    current_target: Mapping[str, Any] | None,
) -> bool:
    if profile != "system_requirement" or output.dialogueAct != "QUESTION_TO_ASSISTANT":
        return False
    return _target_matches(current_target, "requirement", "requirement.purpose_problem") and _purpose_proposal_context_is_incomplete(state)


def _should_defer_purpose_proposal_update(
    state: Mapping[str, Any],
    update: RequirementUpdate,
    profile: InterviewProfile,
) -> bool:
    return bool(
        profile == "system_requirement"
        and update.requirementId == "requirement.purpose_problem"
        and update.candidateSource == "assistant_proposal"
        and _purpose_proposal_context_is_incomplete(state)
    )


def _purpose_proposal_context_is_incomplete(state: Mapping[str, Any]) -> bool:
    requirement_states = state.get("requirementStates", {})
    if not isinstance(requirement_states, Mapping):
        return True
    for requirement_id, _ in PURPOSE_PROPOSAL_PREREQUISITES:
        requirement_state = requirement_states.get(requirement_id)
        if not isinstance(requirement_state, Mapping) or requirement_state.get("status") != "CONFIRMED":
            return True
    return False


def is_current_question_confirmation_target(
    state: Mapping[str, Any],
    current_question: Mapping[str, Any] | None,
) -> bool:
    """Return whether the current question is asking for a candidate confirmation."""

    target = _target_for_current_question(state, current_question)
    return target is not None and _is_confirmation_target(state, target)


def _confirm_target(
    state: dict[str, Any],
    target: Mapping[str, Any],
    *,
    confirmation_message_id: str | None = None,
) -> None:
    kind = target.get("targetType") or target.get("kind")
    target_id = str(target.get("targetId") or "")
    if kind == "transcript_confirmation":
        pending = state.get("pendingTranscriptConfirmation")
        if not isinstance(pending, Mapping):
            return
        references = [
            item
            for item in pending.get("targetRefs", [])
            if isinstance(item, Mapping)
        ]
        for reference in references:
            _confirm_target(
                state,
                reference,
                confirmation_message_id=confirmation_message_id,
            )
        state["pendingTranscriptConfirmation"] = None
        assessment = state.get("lastTranscriptAssessment")
        if isinstance(assessment, dict):
            assessment["confirmed"] = True
        return
    if kind == "contradiction":
        _resolve_contradictions(state, [target_id], confirmation_message_id or "")
        _confirm_candidate_for_contradiction(
            state,
            target_id,
            confirmation_message_id=confirmation_message_id,
        )
        return
    if kind == "field":
        field_state = state.get("fieldStates", {}).get(target_id)
        if field_state and field_state.get("candidateAnswer"):
            missing_items = _field_missing_required_items(field_state)
            if missing_items:
                # A candidate can be confirmed as a transcript correction (or
                # another domain confirmation) before the configured field is
                # complete. Keep the captured items and continue with only the
                # remaining items instead of falsely completing the field.
                field_state["answerState"] = "CANDIDATE_PENDING"
                field_state["answerResolution"] = "TENTATIVE"
                field_state["status"] = "asking"
                state["lastTentativeTarget"] = {
                    "targetType": "field",
                    "targetId": target_id,
                }
                _sync_field_progress(field_state)
                return
            field_state["answerState"] = "CONFIRMED"
            field_state["answerResolution"] = "AUTO_CONFIRM"
            field_state["status"] = "completed"
            field_state["recordAnswer"] = field_state["candidateAnswer"]
            field_state["answerSummary"] = None
            field_state["confirmedItems"] = list(field_state.get("candidateItems", []))
            field_state["confirmedSource"] = field_state.get("candidateSource")
            field_state["confirmedSourceIds"] = list(field_state.get("candidateSourceIds") or [])
            field_state["confirmedProposalMessageId"] = field_state.get("candidateProposalMessageId")
            field_state["confirmationEvidenceTranscriptIds"] = (
                [confirmation_message_id] if confirmation_message_id else []
            )
            field_state["candidateSource"] = None
            field_state["candidateSourceIds"] = []
            field_state["candidateAnswer"] = None
            field_state["candidateItems"] = []
            field_state["candidateProposalMessageId"] = None
            _sync_field_progress(field_state)
            if _target_matches(state.get("lastTentativeTarget"), "field", target_id):
                state["lastTentativeTarget"] = None
            _mark_field_completed(state, target_id)
        return
    requirement_state = state.get("requirementStates", {}).get(target_id)
    if requirement_state and requirement_state.get("candidateValue"):
        requirement_state["status"] = "CONFIRMED"
        requirement_state["answerResolution"] = "AUTO_CONFIRM"
        requirement_state["value"] = requirement_state["candidateValue"]
        requirement_state["confirmedSource"] = requirement_state.get("candidateSource")
        requirement_state["confirmedSourceIds"] = list(requirement_state.get("candidateSourceIds") or [])
        requirement_state["confirmedProposalMessageId"] = requirement_state.get("candidateProposalMessageId")
        requirement_state["confirmationEvidenceTranscriptIds"] = (
            [confirmation_message_id] if confirmation_message_id else []
        )
        requirement_state["candidateValue"] = None
        requirement_state["candidateSource"] = None
        requirement_state["candidateSourceIds"] = []
        requirement_state["candidateProposalMessageId"] = None
        if _target_matches(
            state.get("lastTentativeTarget"),
            "requirement" if kind == "requirement" else "process",
            target_id,
        ):
            state["lastTentativeTarget"] = None
        _maybe_confirm_process_entities(state)


def _confirm_candidate_for_contradiction(
    state: dict[str, Any],
    contradiction_id: str,
    *,
    confirmation_message_id: str | None = None,
) -> None:
    """Commit the sole candidate backed by a contradiction being confirmed.

    A contradiction question can itself be the confirmation of a newly spoken
    candidate (for example, "is X rather than Y?"). The interpreter may only
    resolve the contradiction, while the candidate remains pending. Evidence
    IDs provide the safe link between that contradiction and its candidate;
    ambiguous matches are deliberately left pending for an explicit question.
    """

    contradiction = next(
        (
            item
            for item in state.get("contradictions", [])
            if str(item.get("contradictionId") or "") == contradiction_id
        ),
        None,
    )
    if not contradiction:
        return
    contradiction_evidence_ids = {
        str(evidence_id).strip()
        for evidence_id in contradiction.get("evidenceTranscriptIds", [])
        if str(evidence_id).strip()
    }
    if not contradiction_evidence_ids:
        return

    topic = str(contradiction.get("topic") or "").strip().lower()
    candidates: list[tuple[str, str]] = []
    if topic == "field" or topic.startswith("field:"):
        for field_id, field_state in state.get("fieldStates", {}).items():
            if field_state.get("answerState") != "AWAITING_CONFIRMATION":
                continue
            candidate_evidence_ids = {
                str(evidence_id).strip()
                for item in field_state.get("candidateItems", [])
                if isinstance(item, Mapping)
                for evidence_id in item.get("evidenceTranscriptIds", [])
                if str(evidence_id).strip()
            }
            candidate_evidence_ids.update(
                str(evidence_id).strip()
                for evidence_id in field_state.get("candidateEvidenceTranscriptIds", [])
                if str(evidence_id).strip()
            )
            if contradiction_evidence_ids & candidate_evidence_ids:
                candidates.append(("field", str(field_id)))
    elif topic in {"requirement", "process"} or topic.startswith(("requirement:", "process:")):
        for requirement_id, requirement_state in state.get("requirementStates", {}).items():
            if requirement_state.get("status") != "AWAITING_CONFIRMATION":
                continue
            candidate_evidence_ids = {
                str(evidence_id).strip()
                for evidence_id in requirement_state.get("evidenceTranscriptIds", [])
                if str(evidence_id).strip()
            }
            if contradiction_evidence_ids & candidate_evidence_ids:
                candidates.append(("requirement", str(requirement_id)))

    if len(candidates) != 1:
        return
    candidate_type, candidate_id = candidates[0]
    _confirm_target(
        state,
        {"targetType": candidate_type, "targetId": candidate_id},
        confirmation_message_id=confirmation_message_id,
    )


def _reject_target(state: dict[str, Any], target: Mapping[str, Any]) -> None:
    kind = target.get("targetType") or target.get("kind")
    target_id = str(target.get("targetId") or "")
    if kind == "transcript_confirmation":
        pending = state.get("pendingTranscriptConfirmation")
        if isinstance(pending, Mapping):
            for reference in pending.get("targetRefs", []):
                if isinstance(reference, Mapping):
                    _reject_target(state, reference)
            source_question = pending.get("sourceQuestion")
            if isinstance(source_question, Mapping):
                source_question_id = str(source_question.get("questionId") or "").strip()
                if source_question_id and any(
                    str(item.get("questionId") or "") == source_question_id
                    for item in state.get("askedQuestions", [])
                ):
                    state["currentQuestionId"] = source_question_id
                    state["currentFieldId"] = source_question.get("fieldId")
                    source_type = str(
                        source_question.get("targetType")
                        or source_question.get("kind")
                        or "issue"
                    )
                    source_id = str(source_question.get("targetId") or "").strip()
                    if source_id:
                        state["nextQuestionTarget"] = _target(
                            source_type,
                            source_id,
                            str(
                                source_question.get("targetLabel")
                                or source_question.get("label")
                                or source_id
                            ),
                            1,
                        )
        state["pendingTranscriptConfirmation"] = None
        return
    if kind == "field":
        field_state = state.get("fieldStates", {}).get(target_id)
        if field_state:
            field_state["answerState"] = "UNANSWERED"
            field_state["answerResolution"] = None
            field_state["status"] = "asking"
            field_state["candidateAnswer"] = None
            field_state["candidateSource"] = None
            field_state["candidateSourceIds"] = []
            field_state["candidateProposalMessageId"] = None
            field_state["candidateItems"] = []
            field_state["recordAnswer"] = None
            _sync_field_progress(field_state)
            if _target_matches(state.get("lastTentativeTarget"), "field", target_id):
                state["lastTentativeTarget"] = None
        return
    requirement_state = state.get("requirementStates", {}).get(target_id)
    if requirement_state:
        requirement_state["status"] = "UNANSWERED"
        requirement_state["answerResolution"] = None
        requirement_state["candidateValue"] = None
        requirement_state["candidateSource"] = None
        requirement_state["candidateSourceIds"] = []
        requirement_state["candidateProposalMessageId"] = None
        if _target_matches(
            state.get("lastTentativeTarget"),
            "requirement" if kind == "requirement" else "process",
            target_id,
        ):
            state["lastTentativeTarget"] = None


def _apply_field_update(
    state: dict[str, Any],
    update: FieldUpdate,
    message_id: str,
    valid_evidence_ids: Set[str] | None = None,
    *,
    force_awaiting_confirmation: bool = False,
    defer_auto_confirm: bool = False,
) -> None:
    field_state = state.setdefault("fieldStates", {}).setdefault(
        update.fieldId,
        _new_field_state(update.fieldId),
    )
    resolution = normalize_answer_resolution(update.answerResolution)
    if update.candidateSource == "assistant_proposal" and resolution in {"AUTO_CONFIRM", "TENTATIVE"}:
        # Backend guarantee: an assistant proposal always needs an explicit
        # confirmation even if the model marked the proposal as reliable.
        resolution = "CONFIRM_REQUIRED"
    was_confirmed = field_state.get("answerState") == "CONFIRMED"
    if was_confirmed:
        field_state["recordAnswer"] = None
        field_state["confirmedItems"] = []
        state["completedFieldIds"] = [
            item for item in state.get("completedFieldIds", []) if item != update.fieldId
        ]
        pending = state.setdefault("pendingFieldIds", [])
        if update.fieldId not in pending:
            pending.append(update.fieldId)
    required_items = _field_required_items(field_state)
    item_id = _resolve_field_update_item_id(update, required_items)
    candidate_item = {
        "itemId": item_id,
        "value": update.value.strip(),
        "evidenceTranscriptIds": _ensure_latest_evidence(
            update.evidenceTranscriptIds,
            message_id,
            valid_evidence_ids,
        ),
    }
    candidate_items = _merge_field_items(
        field_state.get("candidateItems"),
        candidate_item,
    )
    field_state["candidateItems"] = candidate_items
    field_state["capturedItems"] = deepcopy(candidate_items)
    # Use a candidate state while checking item completeness so confirmedItems
    # from a previous answer cannot hide a newly required item.
    field_state["answerState"] = "CANDIDATE_PENDING"
    field_state["answerResolution"] = "TENTATIVE"
    field_state["status"] = "asking"
    field_state["recordAnswer"] = None
    field_state["candidateAnswer"] = _compose_field_answer(candidate_items, required_items)
    field_state["candidateSource"] = update.candidateSource
    field_state["candidateSourceIds"] = []
    field_state["candidateProposalMessageId"] = None
    field_state["rawAnswer"] = update.value.strip()
    raw_history = field_state.setdefault("rawAnswerHistory", [])
    if not raw_history or raw_history[-1] != update.value.strip():
        raw_history.append(update.value.strip())

    missing_items = _field_missing_required_items(field_state)
    if missing_items:
        # Item-level incompleteness takes precedence over a provider's
        # field-level resolution. The field must stay open until every
        # required item is captured.
        answer_state = "CANDIDATE_PENDING"
        resolution = "TENTATIVE"
    elif resolution == "AUTO_CONFIRM":
        answer_state = "AWAITING_CONFIRMATION"
    elif resolution == "TENTATIVE":
        answer_state = "CANDIDATE_PENDING"
    elif resolution == "CONFIRM_REQUIRED" or force_awaiting_confirmation:
        answer_state = "AWAITING_CONFIRMATION"
    else:
        current_pending = bool(list_pending_confirmation_targets(state))
        answer_state = "AWAITING_CONFIRMATION" if not current_pending else "CANDIDATE_PENDING"
        resolution = "CONFIRM_REQUIRED" if answer_state == "AWAITING_CONFIRMATION" else None
    field_state["answerState"] = answer_state
    field_state["answerResolution"] = resolution
    _sync_field_progress(field_state)
    if resolution == "TENTATIVE":
        state["lastTentativeTarget"] = {"targetType": "field", "targetId": update.fieldId}
    elif _target_matches(state.get("lastTentativeTarget"), "field", update.fieldId):
        state["lastTentativeTarget"] = None
    if resolution == "AUTO_CONFIRM" and not defer_auto_confirm:
        _confirm_target(
            state,
            {"targetType": "field", "targetId": update.fieldId},
            confirmation_message_id=None,
        )


def _resolve_field_update_item_id(
    update: FieldUpdate,
    required_items: Sequence[Mapping[str, Any]],
) -> str:
    requested = _clean_optional_text(update.itemId)
    if requested:
        return requested
    if len(required_items) == 1:
        return str(required_items[0]["itemId"])
    return f"{update.fieldId}:unmapped"


def _merge_field_items(
    existing: Any,
    new_item: Mapping[str, Any],
) -> list[dict[str, Any]]:
    items = [dict(item) for item in existing or [] if isinstance(item, Mapping)]
    item_id = str(new_item.get("itemId") or "")
    for index, item in enumerate(items):
        if str(item.get("itemId") or "") == item_id:
            items[index] = dict(new_item)
            return items
    items.append(dict(new_item))
    return items


def _compose_field_answer(
    items: Sequence[Mapping[str, Any]],
    required_items: Sequence[Mapping[str, Any]],
) -> str:
    order = {
        str(item.get("itemId")): index
        for index, item in enumerate(required_items)
        if item.get("itemId")
    }
    indexed_items = list(enumerate(items))
    indexed_items.sort(
        key=lambda pair: (order.get(str(pair[1].get("itemId") or ""), len(order)), pair[0])
    )
    values = [
        str(item.get("value") or "").strip()
        for _, item in indexed_items
        if str(item.get("value") or "").strip()
    ]
    return "、".join(dict.fromkeys(values))


def _field_updates_allow_auto_confirm(
    output: StructuredInterviewOutput,
    updates: Sequence[FieldUpdate],
) -> bool:
    return bool(
        output.answerAssessment.sufficiency == "SUFFICIENT"
        and updates
        and all(
            normalize_answer_resolution(update.answerResolution) == "AUTO_CONFIRM"
            for update in updates
        )
    )


def _apply_requirement_update(
    state: dict[str, Any],
    update: RequirementUpdate,
    message_id: str,
    valid_evidence_ids: Set[str] | None = None,
    *,
    force_awaiting_confirmation: bool = False,
) -> None:
    requirement_state = state["requirementStates"][update.requirementId]
    resolution = normalize_answer_resolution(update.answerResolution)
    if update.candidateSource == "assistant_proposal" and resolution in {"AUTO_CONFIRM", "TENTATIVE"}:
        resolution = "CONFIRM_REQUIRED"
    if requirement_state.get("status") == "CONFIRMED":
        requirement_state["value"] = None
    if resolution == "AUTO_CONFIRM":
        status = "AWAITING_CONFIRMATION"
    elif resolution == "TENTATIVE":
        status = "CANDIDATE_PENDING"
    elif resolution == "CONFIRM_REQUIRED" or force_awaiting_confirmation:
        status = "AWAITING_CONFIRMATION"
    else:
        current_pending = bool(list_pending_confirmation_targets(state))
        status = "AWAITING_CONFIRMATION" if not current_pending else "CANDIDATE_PENDING"
        resolution = "CONFIRM_REQUIRED" if status == "AWAITING_CONFIRMATION" else None
    requirement_state["status"] = status
    requirement_state["answerResolution"] = resolution
    requirement_state["value"] = None
    requirement_state["candidateValue"] = update.value.strip()
    requirement_state["candidateSource"] = update.candidateSource
    requirement_state["candidateSourceIds"] = []
    requirement_state["candidateProposalMessageId"] = None
    requirement_state["evidenceTranscriptIds"] = _ensure_latest_evidence(
        update.evidenceTranscriptIds,
        message_id,
        valid_evidence_ids,
    )
    if resolution == "TENTATIVE":
        state["lastTentativeTarget"] = {
            "targetType": "requirement" if update.requirementId.startswith("requirement.") else "process",
            "targetId": update.requirementId,
        }
    elif _target_matches(
        state.get("lastTentativeTarget"),
        "requirement" if update.requirementId.startswith("requirement.") else "process",
        update.requirementId,
    ):
        state["lastTentativeTarget"] = None
    if resolution == "AUTO_CONFIRM":
        _confirm_target(
            state,
            {
                "targetType": "requirement" if update.requirementId.startswith("requirement.") else "process",
                "targetId": update.requirementId,
            },
            confirmation_message_id=None,
        )


def _apply_process_patch(
    state: dict[str, Any],
    patch: ProcessPatch,
    message_id: str,
    *,
    valid_evidence_ids: Set[str] | None = None,
) -> bool:
    process_state = state.setdefault("processState", {})
    current_version = int(process_state.get("version", state.get("processVersion", 0)) or 0)
    if patch.baseProcessVersion != current_version:
        if _has_process_operations(patch):
            logger.warning(
                "structured_process_patch_rejected record_id=%s message_id=%s "
                "base_process_version=%s current_process_version=%s "
                "errors=%s operation_counts=%s",
                state.get("recordId"),
                message_id,
                patch.baseProcessVersion,
                current_version,
                [f"base_process_version_mismatch:{patch.baseProcessVersion}:{current_version}"],
                _process_patch_operation_counts(patch),
            )
            _upsert_open_issues(
                state,
                [
                    OpenIssue(
                        issueId=f"process-version-{current_version}",
                        topic="process_model",
                        description="ProcessModelの更新基準バージョンが現在の状態と一致しません。最新の状態を確認してください。",
                        evidenceTranscriptIds=[message_id],
                    )
                ],
                message_id,
            )
        return False

    validation_errors = process_patch_validation_errors(
        process_state,
        patch,
        valid_evidence_ids=valid_evidence_ids,
    )
    if validation_errors:
        logger.warning(
            "structured_process_patch_rejected record_id=%s message_id=%s "
            "base_process_version=%s current_process_version=%s errors=%s "
            "operation_counts=%s",
            state.get("recordId"),
            message_id,
            patch.baseProcessVersion,
            current_version,
            validation_errors,
            _process_patch_operation_counts(patch),
        )
        _upsert_open_issues(
            state,
            [
                OpenIssue(
                    issueId=f"invalid-process-patch-{current_version}",
                    topic="process_model",
                    description="ProcessModelの更新内容が現在の要素または根拠メッセージと一致しないため、更新を適用しませんでした。",
                    evidenceTranscriptIds=[message_id],
                )
            ],
            message_id,
        )
        return False

    next_process_state = deepcopy(process_state)
    changed = False
    changed |= _merge_entities(next_process_state, "participants", patch.addParticipants, patch.updateParticipants, "participantId")
    changed |= _merge_entities(next_process_state, "nodes", patch.addNodes, patch.updateNodes, "nodeId")
    changed |= _merge_entities(next_process_state, "interactions", patch.addInteractions, patch.updateInteractions, "interactionId")
    edges = next_process_state.setdefault("edges", [])
    for edge in patch.addEdges:
        edges.append(edge.model_dump())
        changed = True
    for edge in patch.updateEdges:
        edge_dict = edge.model_dump()
        for item in edges:
            if item.get("edgeId") == edge.edgeId:
                item.update(edge_dict)
                changed = True
                break
    for edge in patch.removeEdges:
        for item in edges:
            if item.get("edgeId") == edge and item.get("lifecycle") != "superseded":
                item["lifecycle"] = "superseded"
                changed = True
                break
    interactions = next_process_state.setdefault("interactions", [])
    for interaction_id in patch.removeInteractions:
        for item in interactions:
            if item.get("interactionId") == interaction_id and item.get("lifecycle") != "superseded":
                item["lifecycle"] = "superseded"
                changed = True
                break
    if changed:
        current_version += 1
        next_process_state["version"] = current_version
        source_message_ids = next_process_state.setdefault("sourceMessageIds", [])
        if message_id and message_id not in source_message_ids:
            source_message_ids.append(message_id)
        process_state.clear()
        process_state.update(next_process_state)
        state["processVersion"] = current_version
        state["openIssues"] = [
            issue
            for issue in state.get("openIssues", [])
            if not str(issue.get("issueId") or "").startswith(
                ("invalid-process-patch-", "process-version-")
            )
        ]
        logger.info(
            "structured_process_patch_applied record_id=%s message_id=%s "
            "process_version=%s operation_counts=%s",
            state.get("recordId"),
            message_id,
            current_version,
            _process_patch_operation_counts(patch),
        )
    return changed


def _has_process_operations(patch: ProcessPatch) -> bool:
    return bool(
        patch.addParticipants
        or patch.updateParticipants
        or patch.addNodes
        or patch.updateNodes
        or patch.addEdges
        or patch.updateEdges
        or patch.removeEdges
        or patch.addInteractions
        or patch.updateInteractions
        or patch.removeInteractions
    )


def _allows_process_patch(
    state: Mapping[str, Any],
    output: StructuredInterviewOutput,
    *,
    profile: InterviewProfile | None,
    valid_evidence_ids: Set[str] | None,
) -> bool:
    if profile != "system_requirement":
        return True
    valid_process_updates = [
        update
        for update in output.applicability
        if update.topic == "process"
        and _has_valid_evidence(update.evidenceTranscriptIds, valid_evidence_ids)
    ]
    if any(update.status == "not_applicable" for update in valid_process_updates):
        return False
    if state.get("applicabilityState", {}).get("process", {}).get("status") == "present":
        return True
    return any(
        update.topic == "process"
        and update.status == "present"
        and _has_valid_evidence(update.evidenceTranscriptIds, valid_evidence_ids)
        for update in output.applicability
    )


def process_patch_validation_errors(
    process_state: Mapping[str, Any],
    patch: ProcessPatch,
    *,
    valid_evidence_ids: Set[str] | None = None,
) -> list[str]:
    participants = process_state.get("participants", [])
    nodes = process_state.get("nodes", [])
    edges = process_state.get("edges", [])
    interactions = process_state.get("interactions", [])
    participant_ids = {item.get("participantId") for item in participants}
    node_ids = {item.get("nodeId") for item in nodes}
    edge_ids = {item.get("edgeId") for item in edges}
    interaction_ids = {item.get("interactionId") for item in interactions}
    errors: list[str] = []

    _append_required_identifier_errors(
        errors,
        patch.addParticipants,
        patch.updateParticipants,
        "participantId",
        "participant",
    )
    _append_required_identifier_errors(
        errors,
        patch.addNodes,
        patch.updateNodes,
        "nodeId",
        "node",
    )
    _append_required_identifier_errors(
        errors,
        patch.addEdges,
        patch.updateEdges,
        "edgeId",
        "edge",
    )
    _append_required_identifier_errors(
        errors,
        patch.addInteractions,
        patch.updateInteractions,
        "interactionId",
        "interaction",
    )

    for entity_type, entities, attribute in (
        ("participant", [*patch.addParticipants, *patch.updateParticipants], "name"),
        ("node", [*patch.addNodes, *patch.updateNodes], "label"),
    ):
        for entity in entities:
            if not str(getattr(entity, attribute, "") or "").strip():
                errors.append(f"empty_{attribute}:{entity_type}:{_entity_identifier(entity)}")
    for entity in [*patch.addEdges, *patch.updateEdges]:
        if not entity.sourceNodeId.strip() or not entity.targetNodeId.strip():
            errors.append(f"empty_edge_endpoint:edge:{_entity_identifier(entity)}")
    for entity in [*patch.addInteractions, *patch.updateInteractions]:
        if (
            not entity.sourceParticipantId.strip()
            or not entity.targetParticipantId.strip()
            or not entity.action.strip()
        ):
            errors.append(f"empty_interaction_endpoint_or_action:interaction:{_entity_identifier(entity)}")
    for entity_type, entities in (
        ("participant", patch.addParticipants),
        ("node", patch.addNodes),
        ("edge", patch.addEdges),
        ("interaction", patch.addInteractions),
    ):
        for entity in entities:
            if entity.lifecycle != "active":
                errors.append(f"non_active_lifecycle:{entity_type}:{_entity_identifier(entity)}")

    _append_id_validation_errors(
        errors,
        patch.addParticipants,
        patch.updateParticipants,
        "participantId",
        participant_ids,
        "participant",
    )
    participant_ids.update(item.participantId for item in patch.addParticipants)
    _append_id_validation_errors(errors, patch.addNodes, patch.updateNodes, "nodeId", node_ids, "node")
    node_ids.update(item.nodeId for item in patch.addNodes)
    _append_id_validation_errors(errors, patch.addEdges, patch.updateEdges, "edgeId", edge_ids, "edge")
    _append_id_validation_errors(
        errors,
        patch.addInteractions,
        patch.updateInteractions,
        "interactionId",
        interaction_ids,
        "interaction",
    )
    if any(edge_id not in edge_ids for edge_id in patch.removeEdges):
        errors.extend(
            f"remove_edge_not_found:edge:{edge_id}"
            for edge_id in patch.removeEdges
            if edge_id not in edge_ids
        )
    if any(interaction_id not in interaction_ids for interaction_id in patch.removeInteractions):
        errors.extend(
            f"remove_interaction_not_found:interaction:{interaction_id}"
            for interaction_id in patch.removeInteractions
            if interaction_id not in interaction_ids
        )

    for node in [*patch.addNodes, *patch.updateNodes]:
        for participant_id in node.participantIds:
            if participant_id not in participant_ids:
                errors.append(
                    f"unknown_participant_reference:node:{_entity_identifier(node)}:{participant_id}"
                )
    for edge in [*patch.addEdges, *patch.updateEdges]:
        if edge.sourceNodeId not in node_ids:
            errors.append(f"unknown_source_node_reference:edge:{_entity_identifier(edge)}:{edge.sourceNodeId}")
        if edge.targetNodeId not in node_ids:
            errors.append(f"unknown_target_node_reference:edge:{_entity_identifier(edge)}:{edge.targetNodeId}")
    for interaction in [*patch.addInteractions, *patch.updateInteractions]:
        if interaction.sourceParticipantId not in participant_ids:
            errors.append(
                f"unknown_source_participant_reference:interaction:{_entity_identifier(interaction)}:{interaction.sourceParticipantId}"
            )
        if interaction.targetParticipantId not in participant_ids:
            errors.append(
                f"unknown_target_participant_reference:interaction:{_entity_identifier(interaction)}:{interaction.targetParticipantId}"
            )
    for entity_type, entities in (
        ("participant", [*patch.addParticipants, *patch.updateParticipants]),
        ("node", [*patch.addNodes, *patch.updateNodes]),
        ("edge", [*patch.addEdges, *patch.updateEdges]),
        ("interaction", [*patch.addInteractions, *patch.updateInteractions]),
    ):
        for entity in entities:
            if entity.confirmationStatus != "candidate":
                errors.append(f"non_candidate_confirmation_status:{entity_type}:{_entity_identifier(entity)}")
            if not _has_valid_evidence(entity.evidenceTranscriptIds, valid_evidence_ids):
                errors.append(f"invalid_evidence:{entity_type}:{_entity_identifier(entity)}")
    return list(dict.fromkeys(errors))


def _validate_process_patch(
    process_state: Mapping[str, Any],
    patch: ProcessPatch,
    *,
    valid_evidence_ids: Set[str] | None = None,
) -> bool:
    """Keep a boolean compatibility helper for existing callers and tests."""

    return not process_patch_validation_errors(
        process_state,
        patch,
        valid_evidence_ids=valid_evidence_ids,
    )


def _append_required_identifier_errors(
    errors: list[str],
    added: Sequence[Any],
    updated: Sequence[Any],
    identifier: str,
    entity_type: str,
) -> None:
    for entity in [*added, *updated]:
        if not str(getattr(entity, identifier, "") or "").strip():
            errors.append(f"missing_{identifier}:{entity_type}")


def _append_id_validation_errors(
    errors: list[str],
    added: Sequence[Any],
    updated: Sequence[Any],
    identifier: str,
    existing_ids: set[Any],
    entity_type: str,
) -> None:
    added_ids = [getattr(item, identifier) for item in added]
    updated_ids = [getattr(item, identifier) for item in updated]
    for item_id in {item for item in added_ids if item in existing_ids}:
        errors.append(f"added_id_already_exists:{entity_type}:{item_id}")
    if len(added_ids) != len(set(added_ids)):
        errors.append(f"duplicate_added_id:{entity_type}")
    if len(updated_ids) != len(set(updated_ids)):
        errors.append(f"duplicate_updated_id:{entity_type}")
    for item_id in set(added_ids) & set(updated_ids):
        errors.append(f"id_in_add_and_update:{entity_type}:{item_id}")
    for item_id in set(updated_ids) - existing_ids:
        errors.append(f"updated_id_not_found:{entity_type}:{item_id}")


def _entity_identifier(entity: Any) -> str:
    for identifier in ("participantId", "nodeId", "edgeId", "interactionId"):
        value = str(getattr(entity, identifier, "") or "").strip()
        if value:
            return value
    return "<missing>"


def _process_patch_operation_counts(patch: ProcessPatch) -> dict[str, int]:
    return {
        name: len(getattr(patch, name))
        for name in (
            "addParticipants",
            "updateParticipants",
            "addNodes",
            "updateNodes",
            "addEdges",
            "updateEdges",
            "removeEdges",
            "addInteractions",
            "updateInteractions",
            "removeInteractions",
        )
    }


def _validate_add_update_ids(
    added: Sequence[Any],
    updated: Sequence[Any],
    identifier: str,
    existing_ids: set[Any],
) -> bool:
    added_ids = [getattr(item, identifier) for item in added]
    updated_ids = [getattr(item, identifier) for item in updated]
    if len(added_ids) != len(set(added_ids)) or len(updated_ids) != len(set(updated_ids)):
        return False
    if set(added_ids) & existing_ids:
        return False
    if set(added_ids) & set(updated_ids):
        return False
    return set(updated_ids).issubset(existing_ids)


def _merge_entities(
    process_state: dict[str, Any],
    key: str,
    added: Iterable[Any],
    updated: Iterable[Any],
    identifier: str,
) -> bool:
    entities = process_state.setdefault(key, [])
    changed = False
    for entity in added:
        entity_dict = entity.model_dump()
        entity_id = entity_dict.get(identifier)
        if not any(item.get(identifier) == entity_id for item in entities):
            entities.append(entity_dict)
            changed = True
    for entity in updated:
        entity_dict = entity.model_dump()
        entity_id = entity_dict.get(identifier)
        for item in entities:
            if item.get(identifier) == entity_id:
                item.update(entity_dict)
                changed = True
                break
    return changed


def _upsert_contradictions(
    state: dict[str, Any],
    contradictions: Iterable[Contradiction],
    message_id: str,
    *,
    valid_evidence_ids: Set[str] | None = None,
) -> None:
    items = state.setdefault("contradictions", [])
    for contradiction in contradictions:
        record = contradiction.model_dump()
        record["status"] = "open"
        record["evidenceTranscriptIds"] = _ensure_latest_evidence(
            contradiction.evidenceTranscriptIds,
            message_id,
            valid_evidence_ids,
        )
        existing = next(
            (item for item in items if item.get("contradictionId") == contradiction.contradictionId),
            None,
        )
        if existing is None:
            items.append(record)
        else:
            existing.update(record)


def _resolve_contradictions(
    state: dict[str, Any],
    contradiction_ids: Iterable[str],
    message_id: str,
) -> None:
    identifiers = {str(item).strip() for item in contradiction_ids if str(item).strip()}
    if not identifiers:
        return
    for item in state.setdefault("contradictions", []):
        if item.get("contradictionId") in identifiers:
            item["status"] = "resolved"
            item["resolvedEvidenceTranscriptIds"] = [message_id]


def _upsert_open_issues(
    state: dict[str, Any],
    issues: Iterable[OpenIssue],
    message_id: str,
    *,
    valid_evidence_ids: Set[str] | None = None,
) -> None:
    items = state.setdefault("openIssues", [])
    for issue in issues:
        record = issue.model_dump()
        record["evidenceTranscriptIds"] = _ensure_latest_evidence(
            issue.evidenceTranscriptIds,
            message_id,
            valid_evidence_ids,
        )
        existing = next((item for item in items if item.get("issueId") == issue.issueId), None)
        if existing is None:
            items.append(record)
        else:
            existing.update(record)


def _ensure_latest_evidence(
    evidence_ids: Iterable[str],
    latest_message_id: str,
    valid_evidence_ids: Set[str] | None = None,
) -> list[str]:
    result = [
        str(item)
        for item in evidence_ids
        if str(item).strip()
        and (valid_evidence_ids is None or str(item) in valid_evidence_ids)
    ]
    if latest_message_id.strip() and latest_message_id not in result:
        result.append(latest_message_id)
    return list(dict.fromkeys(result))


def _has_valid_evidence(
    evidence_ids: Iterable[str],
    valid_evidence_ids: Set[str] | None,
) -> bool:
    cleaned = {str(item).strip() for item in evidence_ids if str(item).strip()}
    if not cleaned:
        return False
    return valid_evidence_ids is None or cleaned.issubset(valid_evidence_ids)


def _mark_field_completed(state: dict[str, Any], field_id: str) -> None:
    completed = state.setdefault("completedFieldIds", [])
    pending = state.setdefault("pendingFieldIds", [])
    if field_id not in completed:
        completed.append(field_id)
    if field_id in pending:
        pending.remove(field_id)


def _promote_one_candidate(
    state: dict[str, Any],
    *,
    include_tentative: bool = False,
) -> None:
    if list_pending_confirmation_targets(state):
        return
    for field_state in state.get("fieldStates", {}).values():
        if (
            field_state.get("answerState") == "CANDIDATE_PENDING"
            and (
                include_tentative
                or field_state.get("answerResolution") != "TENTATIVE"
            )
        ):
            field_state["answerState"] = "AWAITING_CONFIRMATION"
            field_state["answerResolution"] = "CONFIRM_REQUIRED"
            return
    for requirement_state in state.get("requirementStates", {}).values():
        if (
            requirement_state.get("status") == "CANDIDATE_PENDING"
            and (
                include_tentative
                or requirement_state.get("answerResolution") != "TENTATIVE"
            )
        ):
            requirement_state["status"] = "AWAITING_CONFIRMATION"
            requirement_state["answerResolution"] = "CONFIRM_REQUIRED"
            return


def _select_optional_target(
    state: Mapping[str, Any],
    profile: InterviewProfile,
) -> dict[str, Any] | None:
    if profile == "system_requirement" and not _process_is_present(state, profile):
        return None
    for target_id, label in OPTIONAL_TARGETS[profile]:
        topic = target_id.removeprefix("process.")
        applicability = state.get("applicabilityState", {}).get(topic, {})
        if applicability.get("status") == "unknown":
            return _target("applicability", topic, label, 5)
        if applicability.get("status") == "present":
            target_state = state.get("requirementStates", {}).get(target_id, {})
            if target_state.get("status") != "CONFIRMED" and not _is_tentative_state(target_state):
                return _target("process", target_id, label, 5)
    return None


def _select_deferred_proposal_context(
    state: dict[str, Any],
    profile: InterviewProfile,
) -> dict[str, Any] | None:
    """Collect minimum context before proposing a system purpose."""

    if (
        profile != "system_requirement"
        or state.get("deferredProposalTarget") != "requirement.purpose_problem"
    ):
        return None

    requirement_states = state.get("requirementStates", {})
    if not isinstance(requirement_states, Mapping):
        requirement_id, label = PURPOSE_PROPOSAL_PREREQUISITES[0]
        return _target("requirement", requirement_id, label, 3)

    purpose_state = requirement_states.get("requirement.purpose_problem")
    if isinstance(purpose_state, Mapping) and purpose_state.get("status") == "CONFIRMED":
        state["deferredProposalTarget"] = None
        return None

    for requirement_id, label in PURPOSE_PROPOSAL_PREREQUISITES:
        requirement_state = requirement_states.get(requirement_id)
        if not isinstance(requirement_state, Mapping) or requirement_state.get("status") != "CONFIRMED":
            return _target("requirement", requirement_id, label, 3)

    state["deferredProposalTarget"] = None
    return None


def _list_missing_present_optional_targets(
    state: Mapping[str, Any],
    profile: InterviewProfile,
    *,
    include_tentative: bool = True,
) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    if profile == "system_requirement" and not _process_is_present(state, profile):
        return targets
    for target_id, label in OPTIONAL_TARGETS[profile]:
        topic = target_id.removeprefix("process.")
        if state.get("applicabilityState", {}).get(topic, {}).get("status") != "present":
            continue
        requirement_state = state.get("requirementStates", {}).get(target_id, {})
        if requirement_state.get("status") != "CONFIRMED" and not (
            not include_tentative and _is_tentative_state(requirement_state)
        ):
            targets.append(_target("process", target_id, label, 3))
    return targets


def _is_tentative_state(state: Mapping[str, Any]) -> bool:
    return bool(
        state.get("answerResolution") == "TENTATIVE"
        and (
            state.get("answerState") == "CANDIDATE_PENDING"
            or state.get("status") == "CANDIDATE_PENDING"
        )
    )


def confirm_tentative_target(
    state: dict[str, Any],
    target: Mapping[str, Any] | None,
) -> bool:
    """Commit one tentative target after a meaningful answer to the next target."""

    if target is None or not _is_tentative_target(state, target):
        return False
    _confirm_target(state, target, confirmation_message_id=None)
    return True


def _is_tentative_target(
    state: Mapping[str, Any],
    target: Mapping[str, Any],
) -> bool:
    target_type = str(target.get("targetType") or target.get("kind") or "")
    target_id = str(target.get("targetId") or "")
    if target_type == "field":
        target_state = state.get("fieldStates", {}).get(target_id, {})
    elif target_type in {"requirement", "process"}:
        target_state = state.get("requirementStates", {}).get(target_id, {})
    else:
        return False
    return _is_tentative_state(target_state)


def _maybe_confirm_process_entities(state: dict[str, Any]) -> None:
    profile = state.get("interviewProfile")
    if profile not in PROFILE_LABELS or not _process_is_present(state, profile):
        return
    required_process_ids = {
        target_id
        for target_id, _, kind in REQUIREMENT_DEFINITIONS[profile]
        if kind == "process"
    }
    requirement_states = state.get("requirementStates", {})
    if any(
        requirement_states.get(requirement_id, {}).get("status") != "CONFIRMED"
        for requirement_id in required_process_ids
    ):
        return
    process_state = state.get("processState", {})
    for collection_name in ("participants", "nodes", "edges", "interactions"):
        for entity in process_state.get(collection_name, []):
            if entity.get("lifecycle") != "superseded":
                entity["confirmationStatus"] = "confirmed"


def _process_is_present(state: Mapping[str, Any], profile: InterviewProfile) -> bool:
    if profile == "business_process":
        return True
    if profile != "system_requirement":
        return False
    return state.get("applicabilityState", {}).get("process", {}).get("status") == "present"


def _is_applicability_tracked(profile: InterviewProfile, topic: str) -> bool:
    if profile == "fixed_form":
        return False
    if profile == "system_requirement" and topic not in APPLICABILITY_TOPICS:
        return False
    return topic != "process" or profile == "system_requirement"
