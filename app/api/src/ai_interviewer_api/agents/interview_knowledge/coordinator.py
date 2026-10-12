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
from ai_interviewer_api.services.conversation_control import (
    extracted_update_summary,
    has_evaluable_answer_content,
    normalize_conversation_output,
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
APPLICABILITY_OVERVIEW_TOPICS: frozenset[str] = frozenset(
    {"branch", "exception", "external_system", "error_handling", "handoff", "input_output"}
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

# A probe is a child of the current basic question.  Keeping this limit in the
# coordinator makes the backend guarantee independent from model output.
MAX_PROBE_COUNT = 1
MAX_OPTIONAL_DEEPENING_PER_FIELD = MAX_PROBE_COUNT


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
        # Conversation concurrency and delayed analysis commits have separate
        # clocks. Background analysis must not invalidate the next user turn.
        "analysisVersion": 0,
        "currentFieldId": None,
        "currentQuestionId": None,
        "currentTurnType": "BASIC",
        "completedFieldIds": [],
        "pendingFieldIds": [field_id for field_id in field_states],
        "askedQuestions": [],
        "questionAnswers": {},
        "followUpCounts": {},
        "fieldStates": field_states,
        "lastProcessedUserMessageId": None,
        "lastUtteranceCompleteness": None,
        "lastTranscriptAssessment": None,
        "lastAnswerAssessment": None,
        "lastAnswerEvaluation": None,
        "lastAnswerStatus": None,
        "lastProbeDecision": None,
        "lastProbeDimension": None,
        "lastProbeReason": None,
        "lastClarificationKind": "NONE",
        "lastAspectAssessments": [],
        "lastMissingInformation": [],
        "lastExtractedFields": [],
        "lastNextAction": None,
        "lastConversationTrace": None,
        "lastUserIntent": None,
        "lastConversationAction": None,
        "lastInterpretationMatch": "NONE",
        "lastUserInterpretation": None,
        "lastOriginalQuestionIntent": None,
        "lastResumeCurrentQuestion": True,
        "lastClarificationTarget": None,
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
        "clarificationQueue": [],
        "activeClarificationRequest": None,
        "clarificationHistory": [],
        "pendingBackgroundValidations": [],
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
        defaults = {
            "originalAnswer": None,
            "answer": field_state.get("recordAnswer"),
            "answerSource": "GENERATED",
            "probes": [],
            "answerStatus": "NOT_ANSWERED",
            "probeDecision": "NONE",
            "probeCount": 0,
            "probeStatus": "NONE",
        }
        for key, value in defaults.items():
            if key not in field_state:
                field_state[key] = deepcopy(value)
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
        required_items = _field_required_items(target_state)
        if len(required_items) == 1:
            target_state["candidateItems"] = [
                {
                    "itemId": str(required_items[0]["itemId"]),
                    "value": candidate_value,
                    "evidenceTranscriptIds": [],
                }
            ]
            target_state["capturedItems"] = deepcopy(target_state["candidateItems"])
            _sync_field_progress(target_state)
        else:
            target_state["candidateItems"] = []
    else:
        target_state["value"] = None

    target["candidateSource"] = "document_reference"
    target["candidateValue"] = candidate_value
    target["candidateSourceIds"] = list(normalized_source_ids)
    return True


def apply_prior_knowledge_candidates(
    state: dict[str, Any],
    target: dict[str, Any],
    *,
    candidates: Sequence[Mapping[str, Any]],
) -> bool:
    """Stage exact prior-knowledge values for explicit participant confirmation."""

    target_type = str(target.get("targetType") or target.get("kind") or "")
    target_id = str(target.get("targetId") or "").strip()
    normalized: list[dict[str, Any]] = []
    for candidate in candidates:
        aspect_id = str(candidate.get("aspectId") or "").strip()
        value = str(candidate.get("value") or "").strip()
        applicability_status = str(candidate.get("applicabilityStatus") or "").strip()
        source_ids = list(
            dict.fromkeys(
                str(source_id).strip()
                for source_id in candidate.get("sourceIds", [])
                if str(source_id).strip()
            )
        )
        if aspect_id and value and source_ids:
            normalized.append({
                "aspectId": aspect_id,
                "value": value,
                "sourceIds": source_ids,
                "applicabilityStatus": applicability_status,
            })
    if not target_id or not normalized:
        return False

    if target_type == "field":
        target_state = state.get("fieldStates", {}).get(target_id)
        if not isinstance(target_state, dict):
            return False
        if target_state.get("answerState") == "CONFIRMED":
            return False
        if str(target_state.get("candidateAnswer") or "").strip():
            return False
        required_items = _field_required_items(target_state)
        required_by_id = {
            str(item.get("itemId") or ""): item
            for item in required_items
            if str(item.get("itemId") or "").strip()
        }
        missing_ids = {
            str(item.get("itemId") or "")
            for item in _field_missing_required_items(target_state)
        }
        candidate_items: list[dict[str, Any]] = []
        seen_item_ids: set[str] = set()
        for candidate in normalized:
            item_id = str(candidate["aspectId"])
            if item_id not in required_by_id or item_id not in missing_ids or item_id in seen_item_ids:
                return False
            seen_item_ids.add(item_id)
            candidate_items.append(
                {
                    "itemId": item_id,
                    "value": candidate["value"],
                    "evidenceTranscriptIds": [],
                    "sourceIds": list(candidate["sourceIds"]),
                }
            )
        source_ids = list(
            dict.fromkeys(
                source_id
                for item in candidate_items
                for source_id in item["sourceIds"]
            )
        )
        target_state.update(
            {
                "answerState": "AWAITING_CONFIRMATION",
                "answerResolution": "CONFIRM_REQUIRED",
                "status": "asking",
                "recordAnswer": None,
                "candidateItems": candidate_items,
                "capturedItems": deepcopy(candidate_items),
                "candidateAnswer": _compose_field_answer(candidate_items, required_items),
                "candidateSource": "prior_knowledge",
                "candidateSourceIds": source_ids,
            }
        )
        _sync_field_progress(target_state)
    elif target_type in {"requirement", "process"}:
        if len(normalized) != 1 or normalized[0]["aspectId"] != target_id:
            return False
        target_state = state.get("requirementStates", {}).get(target_id)
        if not isinstance(target_state, dict):
            return False
        if target_state.get("status") == "CONFIRMED":
            return False
        if str(target_state.get("candidateValue") or "").strip():
            return False
        candidate = normalized[0]
        target_state.update(
            {
                "status": "AWAITING_CONFIRMATION",
                "answerResolution": "CONFIRM_REQUIRED",
                "value": None,
                "candidateValue": candidate["value"],
                "candidateSource": "prior_knowledge",
                "candidateSourceIds": list(candidate["sourceIds"]),
                "evidenceTranscriptIds": [],
            }
        )
    elif target_type in {"applicability", "applicability_overview"}:
        applicability_states = state.get("applicabilityState", {})
        if not isinstance(applicability_states, dict):
            return False
        if target_type == "applicability":
            if len(normalized) != 1 or normalized[0]["aspectId"] != target_id:
                return False
            candidates_by_topic = {target_id: normalized[0]}
            group_type = None
            group_id = None
        else:
            candidates_by_topic = {}
            for candidate in normalized:
                topic = str(candidate["aspectId"])
                if topic not in APPLICABILITY_OVERVIEW_TOPICS or topic in candidates_by_topic:
                    return False
                candidates_by_topic[topic] = candidate
            if not candidates_by_topic:
                return False
            group_type = target_type
            group_id = target_id

        for topic, candidate in candidates_by_topic.items():
            applicability_state = applicability_states.get(topic)
            if (
                not isinstance(applicability_state, dict)
                or applicability_state.get("status") != "unknown"
                or applicability_state.get("candidateStatus")
                or candidate["applicabilityStatus"] not in {"present", "not_applicable"}
            ):
                return False
        for topic, candidate in candidates_by_topic.items():
            applicability_state = applicability_states[topic]
            applicability_state.update(
                {
                    "candidateStatus": candidate["applicabilityStatus"],
                    "candidateValue": candidate["value"],
                    "candidateSource": "prior_knowledge",
                    "candidateSourceIds": list(candidate["sourceIds"]),
                    "candidateGroupTargetType": group_type,
                    "candidateGroupTargetId": group_id,
                }
            )
        target["candidateSource"] = "prior_knowledge"
        target["candidateValue"] = "、".join(
            str(candidate["value"]) for candidate in candidates_by_topic.values()
        )
        target["candidateSourceIds"] = list(
            dict.fromkeys(
                source_id
                for candidate in candidates_by_topic.values()
                for source_id in candidate["sourceIds"]
            )
        )
        return True
    else:
        return False

    target["candidateSource"] = "prior_knowledge"
    target["candidateValue"] = (
        str(target_state.get("candidateAnswer") or "")
        if target_type == "field"
        else str(target_state.get("candidateValue") or "")
    )
    target["candidateSourceIds"] = list(target_state.get("candidateSourceIds") or [])
    return True


def record_interpretation_assessment(
    state: dict[str, Any],
    output: StructuredInterviewOutput,
    *,
    latest_message_id: str,
    raw_transcript: str | None = None,
    user_intent: str | None = None,
    conversation_action: str | None = None,
    interpretation_match: str | None = None,
    user_interpretation: str | None = None,
    original_question_intent: str | None = None,
    resume_current_question: bool | None = None,
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
    state["lastAnswerEvaluation"] = output.answerEvaluation
    state["lastAnswerStatus"] = output.answerStatus
    state["lastProbeDecision"] = output.probeDecision
    state["lastProbeDimension"] = output.probeDimension
    state["lastProbeReason"] = output.probeReason
    state["lastClarificationKind"] = output.clarificationKind
    state["lastAspectAssessments"] = [
        aspect.model_dump() for aspect in output.aspectAssessments
    ]
    state["lastMissingInformation"] = list(output.missingInformation)
    state["lastExtractedFields"] = extracted_update_summary(output)
    state["lastUserIntent"] = user_intent or output.userIntent
    if conversation_action is not None:
        state["lastConversationAction"] = conversation_action
    state["lastInterpretationMatch"] = interpretation_match or output.interpretationMatch
    state["lastUserInterpretation"] = user_interpretation or output.userInterpretation
    state["lastOriginalQuestionIntent"] = (
        original_question_intent or output.originalQuestionIntent
    )
    state["lastResumeCurrentQuestion"] = (
        resume_current_question
        if resume_current_question is not None
        else output.resumeCurrentQuestion
    )
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
    """Stage only transcript corrections that are mapped to explicit targets."""

    assessment = output.transcriptAssessment
    normalized = assessment.normalizedTranscript.strip()
    correction_status = assessment.correctionStatus
    if correction_status not in {"CORRECTED", "UNCERTAIN"}:
        return False
    if correction_status == "CORRECTED" and not normalized:
        return False

    record_interpretation_assessment(
        state,
        output,
        latest_message_id=latest_message_id,
        raw_transcript=raw_transcript,
    )
    target_refs: list[dict[str, str]] = []
    previous_field_states: dict[str, Any] = {}
    previous_requirement_states: dict[str, Any] = {}
    previous_progress = {
        key: deepcopy(state.get(key))
        for key in (
            "completedFieldIds",
            "pendingFieldIds",
            "lastTentativeTarget",
            "pendingTranscriptConfirmation",
        )
    }
    field_ids = {str(field.get("id")) for field in fields if field.get("id")}
    full_transcripts = tuple(
        normalized_value
        for value in (raw_transcript, normalized)
        if (normalized_value := _lightly_normalize_transcript(value))
    )
    uncertain_candidates = tuple(
        normalized_value
        for value in assessment.correctionCandidates
        if (normalized_value := _lightly_normalize_transcript(value))
    )

    def is_full_transcript_candidate(value: str, *, multiple_targets: bool) -> bool:
        if not multiple_targets:
            return False
        normalized_value = _lightly_normalize_transcript(value)
        return any(
            normalized_value == transcript
            or (len(transcript) >= 12 and transcript in normalized_value)
            for transcript in full_transcripts
        )

    def is_unsupported_uncertain_candidate(value: str) -> bool:
        if correction_status != "UNCERTAIN":
            return False
        normalized_value = _lightly_normalize_transcript(value)
        if not normalized_value:
            return True
        if not any(normalized_value in transcript for transcript in full_transcripts):
            return True
        if len(uncertain_candidates) > 1:
            overlaps = [
                normalized_value == candidate
                or normalized_value in candidate
                or candidate in normalized_value
                for candidate in uncertain_candidates
            ]
            if any(overlaps) and not all(overlaps):
                return True
        return False

    for update in output.fieldUpdates:
        if (
            update.fieldId not in field_ids
            or not update.value.strip()
            or update.candidateSource == "document_reference"
            or not _has_valid_evidence(update.evidenceTranscriptIds, valid_evidence_ids)
        ):
            continue
        field_state = state.get("fieldStates", {}).get(update.fieldId, {})
        required_items = _field_required_items(field_state)
        required_item_ids = {str(item.get("itemId") or "") for item in required_items}
        if (update.itemId and update.itemId not in required_item_ids) or (
            len(required_item_ids) > 1 and not update.itemId
        ):
            continue
        if is_full_transcript_candidate(
            update.value,
            multiple_targets=(
                len(required_item_ids) > 1
                or len(output.fieldUpdates) + len(output.requirementUpdates) > 1
            ),
        ) or is_unsupported_uncertain_candidate(update.value):
            continue
        previous_field_states.setdefault(
            update.fieldId,
            deepcopy(state.get("fieldStates", {}).get(update.fieldId)),
        )
        _apply_field_update(
            state,
            update.model_copy(update={"answerResolution": "CONFIRM_REQUIRED"}),
            latest_message_id,
            valid_evidence_ids,
            force_awaiting_confirmation=True,
        )
        target_ref = {"targetType": "field", "targetId": update.fieldId}
        if target_ref not in target_refs:
            target_refs.append(target_ref)

    for update in output.requirementUpdates:
        if (
            update.requirementId not in state.setdefault("requirementStates", {})
            or not update.value.strip()
            or update.candidateSource == "document_reference"
            or not _has_valid_evidence(update.evidenceTranscriptIds, valid_evidence_ids)
        ):
            continue
        if is_full_transcript_candidate(
            update.value,
            multiple_targets=(
                len(output.fieldUpdates) + len(output.requirementUpdates) > 1
            ),
        ) or is_unsupported_uncertain_candidate(update.value):
            continue
        previous_requirement_states.setdefault(
            update.requirementId,
            deepcopy(state["requirementStates"].get(update.requirementId)),
        )
        _apply_requirement_update(
            state,
            update.model_copy(update={"answerResolution": "CONFIRM_REQUIRED"}),
            latest_message_id,
            valid_evidence_ids,
            force_awaiting_confirmation=True,
        )
        target_ref = {
            "targetType": (
                "requirement"
                if update.requirementId.startswith("requirement.")
                else "process"
            ),
            "targetId": update.requirementId,
        }
        if target_ref not in target_refs:
            target_refs.append(target_ref)

    if not target_refs:
        return False
    pending_confirmation = {
        "messageId": latest_message_id,
        "rawTranscript": raw_transcript.strip(),
        "normalizedTranscript": normalized,
        "correctionCandidates": list(assessment.correctionCandidates),
        "targetRefs": target_refs,
        "sourceQuestion": dict(current_question) if current_question else None,
        "candidateMappingVersion": 1,
    }
    state["pendingTranscriptConfirmation"] = pending_confirmation
    if not _pending_transcript_confirmation_value(state, pending_confirmation):
        for target_id, previous in previous_field_states.items():
            if previous is None:
                state.get("fieldStates", {}).pop(target_id, None)
            else:
                state.setdefault("fieldStates", {})[target_id] = previous
        for target_id, previous in previous_requirement_states.items():
            if previous is None:
                state.get("requirementStates", {}).pop(target_id, None)
            else:
                state.setdefault("requirementStates", {})[target_id] = previous
        for key, previous in previous_progress.items():
            if previous is None:
                state.pop(key, None)
            else:
                state[key] = previous
        return False
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
    if (
        output.utteranceCompleteness != "COMPLETE"
        and not has_evaluable_answer_content(output)
    ):
        # A genuinely incomplete utterance is still only a candidate. A
        # complete utterance with insufficient semantic coverage is different:
        # its grounded portion may be applied while the coordinator keeps the
        # missing aspects open for a targeted follow-up. The same applies when
        # one semantic aspect is complete but the next clause is unfinished.
        return []

    changed_topics: list[str] = []
    field_ids = {str(field.get("id")) for field in fields if field.get("id")}
    current_target = _target_for_current_question(state, current_question)
    confirmation_target = current_target
    confirmation_applicability_topics: set[str] = set()
    if output.dialogueAct == "CONFIRMATION" and isinstance(current_target, Mapping):
        current_target_type = str(current_target.get("targetType") or "")
        current_target_id = str(current_target.get("targetId") or "")
        if current_target_type == "applicability":
            confirmation_applicability_topics.add(current_target_id)
        elif current_target_type == "applicability_overview":
            confirmation_applicability_topics.update(
                str(topic)
                for topic, applicability_state in state.get("applicabilityState", {}).items()
                if isinstance(applicability_state, Mapping)
                and applicability_state.get("candidateGroupTargetType")
                == current_target_type
                and str(applicability_state.get("candidateGroupTargetId") or "")
                == current_target_id
                and applicability_state.get("candidateStatus")
            )
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
            confirmation_applied
            and output.dialogueAct == "CONFIRMATION"
            and update.topic in confirmation_applicability_topics
        ):
            # The participant confirmed the exact candidate values already
            # staged for this target. Do not replace them with a second,
            # transcript-derived interpretation in the same turn.
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

    valid_contradictions = [
        contradiction
        for contradiction in output.contradictions
        if _has_valid_evidence(contradiction.evidenceTranscriptIds, valid_evidence_ids)
    ]
    changed_topics.extend(
        f"contradiction:{contradiction_id}"
        for contradiction_id in _upsert_contradictions(
            state,
            valid_contradictions,
            latest_message_id,
            valid_evidence_ids=valid_evidence_ids,
        )
    )
    changed_topics.extend(
        f"resolved_contradiction:{contradiction_id}"
        for contradiction_id in _resolve_contradictions(
            state,
            output.resolvedContradictionIds,
            latest_message_id,
        )
    )
    valid_open_issues = [
        issue
        for issue in output.openIssues
        if _has_valid_evidence(issue.evidenceTranscriptIds, valid_evidence_ids)
    ]
    changed_topics.extend(
        f"open_issue:{issue_id}"
        for issue_id in _upsert_open_issues(
            state,
            valid_open_issues,
            latest_message_id,
            valid_evidence_ids=valid_evidence_ids,
        )
    )
    state["lastStructuredDialogueAct"] = output.dialogueAct
    state["lastProcessedUserMessageId"] = latest_message_id
    return changed_topics


def apply_background_structured_output(
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
    """Apply a background proposal without allowing conversation control.

    Background Structured Interpreter output is useful for extraction and
    validation, but it is not the owner of the foreground conversation
    policy.  Reusing the normal coordinator keeps evidence validation and
    field/process merge rules identical while forcing the dialogue act to a
    neutral value so confirmation, rejection, and target progression cannot
    be replayed from a stale background snapshot.
    """

    safe_output = normalize_conversation_output(output)
    if safe_output.answerEvaluation in {
        "OFF_TARGET",
        "CLARIFICATION_REQUEST",
        "NOT_HEARD",
        "REPEAT_REQUEST",
    }:
        safe_output = safe_output.model_copy(
            update={
                "fieldUpdates": [],
                "requirementUpdates": [],
                "processPatch": ProcessPatch(),
                "applicability": [],
                "contradictions": [],
                "resolvedContradictionIds": [],
                "openIssues": [],
            }
        )
    if safe_output.answerAssessment.sufficiency in {"UNANSWERABLE", "REFUSAL"}:
        safe_output = safe_output.model_copy(
            update={
                "fieldUpdates": [],
                "requirementUpdates": [],
            }
        )
    elif (
        safe_output.answerEvaluation == "PARTIALLY_ANSWERED"
        or safe_output.answerAssessment.sufficiency != "SUFFICIENT"
    ):
        # Background observation has no foreground target-selection step that
        # can selectively downgrade the active target. Keep every extracted
        # value tentative until the foreground coordinator evaluates the same
        # transcript and decides which missing aspect was answered.
        safe_output = safe_output.model_copy(
            update={
                "fieldUpdates": [
                    update.model_copy(update={"answerResolution": "TENTATIVE"})
                    for update in safe_output.fieldUpdates
                ],
                "requirementUpdates": [
                    update.model_copy(update={"answerResolution": "TENTATIVE"})
                    for update in safe_output.requirementUpdates
                ],
            }
        )
    safe_output = safe_output.model_copy(update={"dialogueAct": "OTHER"})
    return apply_structured_output(
        state,
        safe_output,
        latest_message_id=latest_message_id,
        fields=fields,
        profile=profile,
        valid_evidence_ids=valid_evidence_ids,
        current_question=current_question,
        raw_transcript=raw_transcript,
    )


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


def _pending_transcript_confirmation_value(
    state: Mapping[str, Any],
    pending: Mapping[str, Any],
) -> str:
    """Build a confirmation value only from current-turn, target-mapped items."""

    target_refs = pending.get("targetRefs")
    if not isinstance(target_refs, list):
        return ""
    message_id = str(pending.get("messageId") or "").strip()
    legacy_mapping = pending.get("candidateMappingVersion") != 1
    unique_target_count = len(
        {
            (
                str(item.get("targetType") or ""),
                str(item.get("targetId") or "").strip(),
            )
            for item in target_refs
            if isinstance(item, Mapping)
        }
    )
    full_transcripts = tuple(
        _lightly_normalize_transcript(str(value))
        for value in (
            pending.get("normalizedTranscript"),
            pending.get("rawTranscript"),
        )
        if str(value or "").strip()
    )
    field_states = state.get("fieldStates")
    requirement_states = state.get("requirementStates")
    pairs: list[tuple[str, str]] = []
    seen_targets: set[tuple[str, str]] = set()

    def add_pair(
        label: str,
        value: object,
        *,
        reject_full_transcript: bool,
    ) -> None:
        normalized_value = str(value or "").strip()
        if (
            not label
            or not normalized_value
            or len(normalized_value) > 64
            or (
                reject_full_transcript
                and any(
                    normalized_value == transcript
                    or (len(transcript) >= 12 and transcript in normalized_value)
                    for transcript in full_transcripts
                )
            )
        ):
            return
        pair = (label, normalized_value)
        if pair not in pairs:
            pairs.append(pair)

    for reference in target_refs:
        target = reference if isinstance(reference, Mapping) else {}
        target_type = str(target.get("targetType") or "")
        target_id = str(target.get("targetId") or "").strip()
        key = (target_type, target_id)
        if not target_id or key in seen_targets:
            continue
        seen_targets.add(key)

        if target_type == "field" and isinstance(field_states, Mapping):
            field_state = field_states.get(target_id)
            if not isinstance(field_state, Mapping):
                continue
            plan = field_state.get("questionPlan")
            required_items = (
                plan.get("requiredItems")
                if isinstance(plan, Mapping)
                else None
            )
            item_labels: dict[str, str] = {}
            if isinstance(required_items, list):
                for required_item in required_items:
                    if not isinstance(required_item, Mapping):
                        continue
                    item_id = str(required_item.get("itemId") or "").strip()
                    label = str(required_item.get("label") or "").strip()
                    if item_id and label:
                        item_labels[item_id] = label
            candidate_items = field_state.get("candidateItems")
            if isinstance(candidate_items, list):
                for candidate in candidate_items:
                    if not isinstance(candidate, Mapping):
                        continue
                    evidence_ids = candidate.get("evidenceTranscriptIds")
                    if message_id and (
                        not isinstance(evidence_ids, list)
                        or message_id not in evidence_ids
                    ):
                        continue
                    item_id = str(candidate.get("itemId") or "").strip()
                    label = item_labels.get(item_id, "")
                    if not label and len(item_labels) <= 1:
                        label = str(field_state.get("fieldId") or target_id)
                    add_pair(
                        label,
                        candidate.get("value"),
                        reject_full_transcript=(
                            legacy_mapping
                            or unique_target_count > 1
                            or len(item_labels) > 1
                        ),
                    )
        elif target_type in {"requirement", "process"} and isinstance(
            requirement_states, Mapping
        ):
            requirement_state = requirement_states.get(target_id)
            if not isinstance(requirement_state, Mapping):
                continue
            evidence_ids = requirement_state.get("evidenceTranscriptIds")
            if message_id and (
                not isinstance(evidence_ids, list)
                or message_id not in evidence_ids
            ):
                continue
            add_pair(
                str(requirement_state.get("label") or "").strip(),
                requirement_state.get("candidateValue"),
                reject_full_transcript=(legacy_mapping or unique_target_count > 1),
            )

    summary = "、".join(f"{label}は「{value}」" for label, value in pairs)
    return summary if len(summary) <= 120 else ""


def select_next_question_target(
    state: dict[str, Any],
    profile: InterviewProfile,
    fields: Sequence[Mapping[str, Any]],
    *,
    excluded_target_keys: Iterable[str] = (),
) -> dict[str, Any] | None:
    """Select exactly one target using the fixed backend priority."""

    excluded = {str(key) for key in excluded_target_keys if str(key).strip()}
    provisional_answered = state.get("provisionalAnsweredTargetKeys")
    if isinstance(provisional_answered, list):
        excluded.update(
            str(key)
            for key in provisional_answered
            if str(key).strip()
        )

    active_clarification = state.get("activeClarificationRequest")
    if not isinstance(active_clarification, Mapping):
        clarification_queue = state.get("clarificationQueue")
        if isinstance(clarification_queue, list) and clarification_queue:
            clarification_queue.sort(
                key=lambda item: int(item.get("priority") or 9)
                if isinstance(item, Mapping)
                else 9
            )
            candidate = clarification_queue.pop(0)
            if isinstance(candidate, Mapping):
                active_clarification = dict(candidate)
                active_clarification["status"] = "active"
                state["activeClarificationRequest"] = active_clarification
                logger.info(
                    "clarification_dequeued request_id=%s source_turn_id=%s priority=%s",
                    active_clarification.get("requestId"),
                    active_clarification.get("sourceTurnId"),
                    active_clarification.get("priority"),
                )
    if isinstance(active_clarification, Mapping):
        source_target = active_clarification.get("sourceTarget")
        if isinstance(source_target, Mapping):
            target = deepcopy(dict(source_target))
            target["priority"] = int(active_clarification.get("priority") or 2)
            target["clarificationRequest"] = {
                "requestId": active_clarification.get("requestId"),
                "reason": active_clarification.get("reason"),
                "sourceTurnId": active_clarification.get("sourceTurnId"),
                "sourceMessageId": active_clarification.get("sourceMessageId"),
            }
            if target_key(target) not in excluded:
                return target

    pending_transcript = state.get("pendingTranscriptConfirmation")
    if isinstance(pending_transcript, Mapping):
        message_id = str(pending_transcript.get("messageId") or "").strip()
        candidate_value = _pending_transcript_confirmation_value(
            state,
            pending_transcript,
        )
        if message_id and candidate_value:
            return _target(
                "transcript_confirmation",
                message_id,
                "回答項目",
                1,
                candidate_value=candidate_value,
            )
        # Older persisted turns could stage the entire transcript as a field
        # candidate. Drop any pending target that cannot be rendered from
        # item-mapped values so the interview asks for the target again.
        _reject_target(
            state,
            {
                "targetType": "transcript_confirmation",
                "targetId": message_id,
            },
        )

    active_probe = _active_probe_target(state)
    if active_probe is not None and target_key(active_probe) not in excluded:
        return active_probe

    # Preserve the legacy queue of ordinary candidates, but deliberately leave
    # TENTATIVE candidates in the conversation so a later answer can confirm
    # them implicitly.
    _promote_one_candidate(state, include_tentative=False)
    contradictions = [
        item
        for item in state.get("contradictions", [])
        if item.get("status", "open") == "open"
        and target_key(
            {
                "targetType": "contradiction",
                "targetId": item.get("contradictionId"),
            }
        )
        not in excluded
    ]
    if contradictions:
        item = contradictions[0]
        return _target("contradiction", str(item.get("contradictionId")), str(item.get("topic") or "矛盾"), 1)

    pending_confirmations = [
        target
        for target in list_pending_confirmation_targets(state)
        if target_key(target) not in excluded
    ]
    if pending_confirmations:
        return pending_confirmations[0]

    deferred_context = _select_deferred_proposal_context(state, profile)
    if deferred_context and target_key(deferred_context) not in excluded:
        return deferred_context

    # A TENTATIVE candidate is intentionally excluded here. This lets the
    # interview collect the next answer and use it as natural confirmation.
    missing_required = list_missing_required_targets(
        state,
        profile,
        fields,
        include_tentative=False,
    )
    missing_required = [
        target for target in missing_required if target_key(target) not in excluded
    ]
    unknown_applicability = list_unknown_applicability(state, profile)
    if missing_required:
        process_model_only = all(
            target.get("targetId") == "process.main_flow"
            for target in missing_required
        )
        if not (process_model_only and unknown_applicability):
            return missing_required[0]

    if unknown_applicability:
        if (
            profile == "system_requirement"
            and "process" in unknown_applicability
            and "applicability:process" not in excluded
        ):
            return _target("applicability", "process", "処理の流れがあるか", 4)
        if (
            not state.get("applicabilityOverviewAsked")
                and any(topic in APPLICABILITY_OVERVIEW_TOPICS for topic in unknown_applicability)
            and "applicability_overview:optional_cases" not in excluded
        ):
            return _target(
                "applicability_overview",
                "optional_cases",
                "通常と異なるケースや条件による処理変更の有無",
                4,
            )
        available_topics = [
            topic
            for topic in unknown_applicability
            if f"applicability:{topic}" not in excluded
        ]
        if available_topics:
            topic = available_topics[0]
            if topic == "process" and profile == "system_requirement":
                label = "処理の流れがあるか"
            else:
                label = APPLICABILITY_LABELS.get(topic, topic)
            return _target("applicability", topic, label, 4)

    optional = _select_optional_target(state, profile)
    if optional and target_key(optional) not in excluded:
        return optional
    if (
        str(state.get("closingState") or "UNANSWERED") != "CONFIRMED"
        and "closing:open_ended" not in excluded
    ):
        return _target("closing", CLOSING_TARGET_ID, CLOSING_TARGET_LABEL, 6)
    # Once there is no other useful question, fall back to an explicit stop
    # only for the remaining candidate. This is the exceptional confirmation
    # path, not the default after every answer.
    _promote_one_candidate(state, include_tentative=True)
    pending_confirmations = [
        target
        for target in list_pending_confirmation_targets(state)
        if target_key(target) not in excluded
    ]
    if pending_confirmations:
        return pending_confirmations[0]
    return None


def enqueue_clarification_request(
    state: dict[str, Any],
    *,
    source_turn_id: str | None,
    source_message_id: str | None,
    source_target: Mapping[str, Any] | None,
    reason: str,
    priority: int,
) -> dict[str, Any] | None:
    """Queue a safe, post-turn clarification without interrupting playback."""

    if not isinstance(source_target, Mapping):
        return None
    target_type = str(source_target.get("targetType") or source_target.get("kind") or "").strip()
    target_id = str(source_target.get("targetId") or "").strip()
    if not target_type or not target_id:
        return None
    source_turn = str(source_turn_id or "").strip()
    source_message = str(source_message_id or "").strip()
    request_id = "clarification-" + "-".join(
        part
        for part in (source_turn or source_message or "unknown", target_type, target_id)
        if part
    )
    queue = state.setdefault("clarificationQueue", [])
    if not isinstance(queue, list):
        queue = []
        state["clarificationQueue"] = queue
    active = state.get("activeClarificationRequest")
    existing = []
    if isinstance(active, Mapping):
        existing.append(active)
    existing.extend(item for item in queue if isinstance(item, Mapping))
    if any(str(item.get("requestId") or "") == request_id for item in existing):
        return None
    request = {
        "requestId": request_id,
        "sourceTurnId": source_turn or None,
        "sourceMessageId": source_message or None,
        "sourceTarget": deepcopy(dict(source_target)),
        "reason": str(reason).strip()[:240],
        "priority": max(1, min(int(priority), 9)),
        "status": "pending",
    }
    queue.append(request)
    queue.sort(key=lambda item: int(item.get("priority") or 9))
    return request


def complete_clarification_request(
    state: dict[str, Any],
    current_question: Mapping[str, Any] | None,
) -> bool:
    """Close the active clarification only after its answer was processed."""

    if not isinstance(current_question, Mapping):
        return False
    request = current_question.get("clarificationRequest")
    if not isinstance(request, Mapping):
        return False
    request_id = str(request.get("requestId") or "").strip()
    active = state.get("activeClarificationRequest")
    if not request_id or not isinstance(active, Mapping):
        return False
    if str(active.get("requestId") or "") != request_id:
        return False
    completed = dict(active)
    completed["status"] = "completed"
    history = state.setdefault("clarificationHistory", [])
    if isinstance(history, list):
        history.append(completed)
        del history[:-20]
    state.pop("activeClarificationRequest", None)
    return True


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
        "questionText",
        "targetDescription",
        "required",
        "optional",
        "deepeningItemIds",
        "deepeningItems",
        "optionalDeepening",
        "probeMode",
        "probeDimension",
        "probeReason",
        "probeQuestion",
        "parentQuestionId",
    ):
        if key in value:
            target[key] = deepcopy(value[key])
    target["probeType"] = str(value.get("probeType") or "CLARIFY")
    target["probeCount"] = int(value.get("probeCount") or 0)
    target["probeMode"] = str(value.get("probeMode") or "MISSING_INFORMATION")
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
        # Keep the active-probe bookkeeping object compact.  The selected
        # question's definition is reconstructed when the probe becomes the
        # current question, so it should not expand this persisted object.
        target = _field_target_with_progress(
            state,
            target,
            include_definition=False,
            include_single_item_progress=False,
        )
        target_type = str(target.get("targetType") or target.get("kind") or "")
        target_id = str(target.get("targetId") or "").strip()
        field_state = state.get("fieldStates", {}).get(target_id, {})
        if target_type == "field" and isinstance(field_state, Mapping):
            # Before question-definition propagation, a single-item field
            # returned from list_missing_required_targets had only the base
            # target keys.  Preserve that active-probe shape while retaining
            # the definition on the current question itself.
            if len(_field_required_items(field_state)) <= 1:
                target = {
                    key: value
                    for key, value in target.items()
                    if key
                    not in {
                        "missingItemIds",
                        "missingItems",
                        "capturedItemIds",
                        "questionPlan",
                        "sourceQuestion",
                        "sourceDescription",
                        "questionText",
                        "targetDescription",
                        "required",
                        "optional",
                    }
                }
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
        "questionText",
        "targetDescription",
        "required",
        "optional",
        "deepeningItemIds",
        "deepeningItems",
        "optionalDeepening",
        "probeMode",
        "probeDimension",
        "probeReason",
        "probeQuestion",
        "parentQuestionId",
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
    applicability_groups: dict[tuple[str, str], list[tuple[str, Mapping[str, Any]]]] = {}
    for topic, applicability_state in state.get("applicabilityState", {}).items():
        if not isinstance(applicability_state, Mapping) or not applicability_state.get("candidateStatus"):
            continue
        group_type = str(
            applicability_state.get("candidateGroupTargetType") or "applicability"
        )
        group_id = str(applicability_state.get("candidateGroupTargetId") or topic)
        applicability_groups.setdefault((group_type, group_id), []).append(
            (str(topic), applicability_state)
        )
    for (group_type, group_id), candidates in applicability_groups.items():
        if group_type == "applicability_overview":
            label = "通常と異なるケースや条件による処理変更の有無"
        else:
            label = APPLICABILITY_LABELS.get(group_id, group_id)
        target = _target(
            group_type,
            group_id,
            label,
            2,
            candidate_source="prior_knowledge",
            candidate_value="、".join(
                str(candidate.get("candidateValue") or "").strip()
                for _, candidate in candidates
                if str(candidate.get("candidateValue") or "").strip()
            ),
            candidate_source_ids=[
                source_id
                for _, candidate in candidates
                for source_id in candidate.get("candidateSourceIds", [])
                if str(source_id).strip()
            ],
        )
        targets.append(target)
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
        # Evidence and the currently adopted answer are intentionally kept
        # separate so a management edit never erases the interview trail.
        "originalAnswer": None,
        "answer": None,
        "answerSource": "GENERATED",
        "probes": [],
        "answerStatus": "NOT_ANSWERED",
        "probeDecision": "NONE",
        "probeCount": 0,
        "probeStatus": "NONE",
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
        "questionGoal": _clean_optional_text(plan.questionGoal),
        "preferredProbeDimensions": [
            str(value).strip().upper()
            for value in plan.preferredProbeDimensions
            if str(value).strip()
        ],
        "maxProbeCount": max(0, min(int(plan.maxProbeCount), MAX_PROBE_COUNT)),
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
    include_definition: bool = True,
    include_single_item_progress: bool = False,
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
    missing_items = _field_missing_required_items(field_state, field)
    if include_definition:
        _copy_field_definition(result, field_state, field)
    if result.get("optionalDeepening"):
        result["capturedItemIds"] = captured_ids
        return result
    if len(required_items) <= 1:
        if not include_single_item_progress:
            return result
        result["missingItemIds"] = [str(item["itemId"]) for item in missing_items]
        result["missingItems"] = deepcopy(missing_items)
        result["capturedItemIds"] = captured_ids
        return result
    if missing_items and captured_ids:
        result["label"] = _join_item_labels(missing_items)
    result["missingItemIds"] = [str(item["itemId"]) for item in missing_items]
    result["missingItems"] = deepcopy(missing_items)
    result["capturedItemIds"] = captured_ids
    return result


def _copy_field_definition(
    target: dict[str, Any],
    field_state: Mapping[str, Any],
    field: Mapping[str, Any] | None,
) -> None:
    """Keep immutable question-definition data on every field target.

    A single required item used to return before copying this metadata. That
    made the Question Generator see only the field label and forced it to
    infer the intended interview content.
    """

    for key in ("questionPlan", "sourceQuestion", "sourceDescription"):
        value = field_state.get(key)
        if value is None and field is not None:
            value = field.get(key)
        if value is not None:
            target[key] = deepcopy(value)
    if field is None:
        return
    if "questionText" in field:
        target["questionText"] = deepcopy(field.get("questionText"))
    if "targetDescription" in field:
        target["targetDescription"] = deepcopy(field.get("targetDescription"))
    if "required" in field:
        target["required"] = bool(field.get("required"))
        target["optional"] = not target["required"]
    elif "optional" in field:
        target["optional"] = bool(field.get("optional"))


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
    if candidate_source in {"assistant_proposal", "document_reference", "prior_knowledge"}:
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
        candidate_value = _pending_transcript_confirmation_value(state, pending)
        return _target(
            "transcript_confirmation",
            target_id,
            "回答項目",
            1,
            candidate_value=candidate_value,
        )
    target = _target(
        target_type,
        target_id,
        str(current_question.get("targetLabel") or current_question.get("label") or target_id),
        2,
    )
    if isinstance(current_question.get("questionDefinition"), Mapping):
        # A question snapshot carries the immutable Interview Plan contract.
        # Keep that exact snapshot when rebuilding the target for an action;
        # do not infer a new definition from mutable answer progress.
        target["questionDefinition"] = deepcopy(current_question["questionDefinition"])
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
    elif target_type == "applicability":
        applicability_state = state.get("applicabilityState", {}).get(target_id, {})
        target["candidateSource"] = applicability_state.get("candidateSource")
        target["candidateValue"] = applicability_state.get("candidateValue")
        target["candidateSourceIds"] = list(
            applicability_state.get("candidateSourceIds") or []
        )
    elif target_type == "applicability_overview":
        candidates = [
            applicability_state
            for applicability_state in state.get("applicabilityState", {}).values()
            if isinstance(applicability_state, Mapping)
            and applicability_state.get("candidateGroupTargetType") == target_type
            and str(applicability_state.get("candidateGroupTargetId") or "") == target_id
            and applicability_state.get("candidateStatus")
        ]
        target["candidateSource"] = "prior_knowledge" if candidates else None
        target["candidateValue"] = "、".join(
            str(candidate.get("candidateValue") or "").strip()
            for candidate in candidates
            if str(candidate.get("candidateValue") or "").strip()
        )
        target["candidateSourceIds"] = list(
            dict.fromkeys(
                source_id
                for candidate in candidates
                for source_id in candidate.get("candidateSourceIds", [])
                if str(source_id).strip()
            )
        )
    else:
        requirement_state = state.get("requirementStates", {}).get(target_id, {})
        target["candidateSource"] = requirement_state.get("candidateSource")
        target["candidateValue"] = requirement_state.get("candidateValue")
        target["candidateSourceIds"] = list(requirement_state.get("candidateSourceIds") or [])
    return target


def _applicability_confirmation_candidates(
    state: Mapping[str, Any],
    target_type: str,
    target_id: str,
) -> list[tuple[str, dict[str, Any]]]:
    applicability_states = state.get("applicabilityState", {})
    if not isinstance(applicability_states, Mapping):
        return []
    if target_type == "applicability":
        applicability_state = applicability_states.get(target_id)
        if isinstance(applicability_state, dict) and applicability_state.get(
            "candidateStatus"
        ):
            return [(target_id, applicability_state)]
        return []
    if target_type != "applicability_overview":
        return []
    return [
        (str(topic), applicability_state)
        for topic, applicability_state in applicability_states.items()
        if isinstance(applicability_state, dict)
        and applicability_state.get("candidateGroupTargetType") == target_type
        and str(applicability_state.get("candidateGroupTargetId") or "") == target_id
        and applicability_state.get("candidateStatus")
    ]


def _is_confirmation_target(state: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    target_type = target.get("targetType") or target.get("kind")
    target_id = str(target.get("targetId") or "")
    if target_type == "transcript_confirmation":
        pending = state.get("pendingTranscriptConfirmation")
        return bool(
            isinstance(pending, Mapping)
            and str(pending.get("messageId") or "") == target_id
            and _pending_transcript_confirmation_value(state, pending)
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
    if target_type == "applicability":
        return bool(_applicability_confirmation_candidates(state, str(target_type), target_id))
    if target_type == "applicability_overview":
        return bool(
            _applicability_confirmation_candidates(state, str(target_type), target_id)
        )
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


def reject_pending_transcript_confirmation(state: dict[str, Any]) -> bool:
    """Discard a legacy transcript confirmation without item-mapped values."""

    pending = state.get("pendingTranscriptConfirmation")
    if not isinstance(pending, Mapping):
        return False
    _reject_target(
        state,
        {
            "targetType": "transcript_confirmation",
            "targetId": str(pending.get("messageId") or ""),
        },
    )
    return True


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
    if kind in {"applicability", "applicability_overview"}:
        for _, applicability_state in _applicability_confirmation_candidates(
            state,
            str(kind),
            target_id,
        ):
            applicability_state.update(
                {
                    "status": applicability_state.get("candidateStatus"),
                    "reason": applicability_state.get("candidateValue"),
                    "evidenceTranscriptIds": [],
                    "confirmedSource": applicability_state.get("candidateSource"),
                    "confirmedSourceIds": list(
                        applicability_state.get("candidateSourceIds") or []
                    ),
                    "confirmationEvidenceTranscriptIds": (
                        [confirmation_message_id] if confirmation_message_id else []
                    ),
                    "candidateStatus": None,
                    "candidateValue": None,
                    "candidateSource": None,
                    "candidateSourceIds": [],
                    "candidateGroupTargetType": None,
                    "candidateGroupTargetId": None,
                }
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


def _remember_rejected_prior_knowledge_candidate(
    state: dict[str, Any],
    *,
    target_type: str,
    target_id: str,
    aspect_id: str,
    value: str,
) -> None:
    candidate_value = str(value or "").strip()
    if not candidate_value:
        return
    rejected = state.setdefault("priorKnowledgeRejectedCandidates", [])
    if not isinstance(rejected, list):
        rejected = []
        state["priorKnowledgeRejectedCandidates"] = rejected
    item = {
        "targetType": target_type,
        "targetId": target_id,
        "aspectId": aspect_id,
        "value": candidate_value,
    }
    if item not in rejected:
        rejected.append(item)
    if len(rejected) > 100:
        del rejected[:-100]


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
    if kind in {"applicability", "applicability_overview"}:
        for topic, applicability_state in _applicability_confirmation_candidates(
            state,
            str(kind),
            target_id,
        ):
            _remember_rejected_prior_knowledge_candidate(
                state,
                target_type="applicability",
                target_id=topic,
                aspect_id=topic,
                value=str(applicability_state.get("candidateValue") or ""),
            )
            applicability_state.update(
                {
                    "candidateStatus": None,
                    "candidateValue": None,
                    "candidateSource": None,
                    "candidateSourceIds": [],
                    "candidateGroupTargetType": None,
                    "candidateGroupTargetId": None,
                }
            )
        return
    if kind == "field":
        field_state = state.get("fieldStates", {}).get(target_id)
        if field_state:
            if field_state.get("candidateSource") == "prior_knowledge":
                for candidate in field_state.get("candidateItems", []):
                    if isinstance(candidate, Mapping):
                        _remember_rejected_prior_knowledge_candidate(
                            state,
                            target_type="field",
                            target_id=target_id,
                            aspect_id=str(candidate.get("itemId") or ""),
                            value=str(candidate.get("value") or ""),
                        )
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
        if requirement_state.get("candidateSource") == "prior_knowledge":
            _remember_rejected_prior_knowledge_candidate(
                state,
                target_type=str(kind or "requirement"),
                target_id=target_id,
                aspect_id=target_id,
                value=str(requirement_state.get("candidateValue") or ""),
            )
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
        # Corrections replace one item, not the other facts already collected.
        field_state["candidateItems"] = deepcopy(field_state.get("confirmedItems") or [])
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
) -> list[str]:
    items = state.setdefault("contradictions", [])
    changed_ids: list[str] = []
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
        changed_ids.append(str(contradiction.contradictionId))
    return list(dict.fromkeys(changed_ids))


def _resolve_contradictions(
    state: dict[str, Any],
    contradiction_ids: Iterable[str],
    message_id: str,
) -> list[str]:
    identifiers = {str(item).strip() for item in contradiction_ids if str(item).strip()}
    if not identifiers:
        return []
    resolved_ids: list[str] = []
    for item in state.setdefault("contradictions", []):
        if item.get("contradictionId") in identifiers:
            item["status"] = "resolved"
            item["resolvedEvidenceTranscriptIds"] = [message_id]
            resolved_ids.append(str(item["contradictionId"]))
    return resolved_ids


def _upsert_open_issues(
    state: dict[str, Any],
    issues: Iterable[OpenIssue],
    message_id: str,
    *,
    valid_evidence_ids: Set[str] | None = None,
) -> list[str]:
    items = state.setdefault("openIssues", [])
    changed_ids: list[str] = []
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
        changed_ids.append(str(issue.issueId))
    return list(dict.fromkeys(changed_ids))


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
