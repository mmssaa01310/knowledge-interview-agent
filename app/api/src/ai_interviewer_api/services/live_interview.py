from __future__ import annotations

import logging
from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
from threading import RLock
from typing import Any

from fastapi import HTTPException

from ai_interviewer_api.agents.interview_knowledge.coordinator import (
    apply_structured_output,
    resolve_profile,
)
from ai_interviewer_api.agents.interview_knowledge.schemas import (
    AnswerAspectAssessment,
    AnswerAssessment,
    StructuredInterviewOutput,
    TranscriptAssessment,
)
from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.schemas.live import LiveStateUpdateCreate
from ai_interviewer_api.services.ai_interview import get_interview_state_snapshot
from ai_interviewer_api.services.interview_state_transition import commit_interview_state
from ai_interviewer_api.agents.interview_knowledge.service import get_current_structured_question
from ai_interviewer_api.services.prior_knowledge import build_prior_knowledge_context


logger = logging.getLogger(__name__)
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
                "question_id": _live_question_id("field", field_id),
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
    if current_question is None:
        first_field = next(
            (
                field
                for field in field_summaries
                if field.get("missing_required_items")
            ),
            field_summaries[0] if field_summaries else None,
        )
        if first_field:
            current_question = {
                "questionId": first_field["question_id"],
                "fieldId": first_field["id"],
                "targetType": "field",
                "targetId": first_field["id"],
                "targetLabel": first_field["label"],
                "text": first_field["description"] or first_field["label"],
                "questionDefinition": {
                    "questionGoal": None,
                    "requiredItems": [
                        {
                            "itemId": item["id"],
                            "label": item["label"],
                            "description": item.get("description", ""),
                        }
                        for item in first_field["required_items"]
                    ],
                    "optionalItems": [
                        {
                            "itemId": item["id"],
                            "label": item["label"],
                            "description": item.get("description", ""),
                        }
                        for item in first_field["optional_items"]
                    ],
                },
            }
    return {
        "state_version": state.get("stateVersion", 0),
        "profile": state.get("interviewProfile")
        or (knowledge.get("interviewPlan") or {}).get("profile"),
        "purpose": str((knowledge.get("interviewPlan") or {}).get("purpose") or ""),
        "prior_knowledge": build_prior_knowledge_context(knowledge, user),
        "persisted_state": _live_state_for_model(state),
        "fields": field_summaries,
        "current": {
            "field_id": state.get("currentFieldId")
            or (current_question or {}).get("fieldId"),
            "question_id": state.get("currentQuestionId")
            or (current_question or {}).get("questionId"),
            "target": (
                {
                    "targetType": current_question.get("targetType"),
                    "targetId": current_question.get("targetId"),
                    "label": current_question.get("targetLabel"),
                }
                if isinstance(current_question, Mapping)
                else None
            ),
            "question": current_question,
            "state_version": state.get("stateVersion", 0),
            "answer_evaluation": state.get("lastAnswerEvaluation"),
            "clarification_kind": state.get("lastClarificationKind", "NONE"),
            "clarification_target": state.get("lastClarificationTarget"),
            "aspect_assessments": list(state.get("lastAspectAssessments") or []),
            "missing_information": list(state.get("lastMissingInformation") or []),
        },
        "conversationControl": {
            "answerEvaluation": state.get("lastAnswerEvaluation"),
            "clarificationKind": state.get("lastClarificationKind", "NONE"),
            "clarificationTarget": state.get("lastClarificationTarget"),
            "aspectAssessments": list(state.get("lastAspectAssessments") or []),
            "missingInformation": list(state.get("lastMissingInformation") or []),
            "extractedFields": list(state.get("lastExtractedFields") or []),
        },
    }


def process_live_state_update(
    payload: LiveStateUpdateCreate,
    *,
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
) -> dict[str, Any]:
    """Persist one Responses semantic assessment without choosing dialogue.

    The payload is already assessed by the Responses Delegation agent. This
    function performs only deterministic validation, idempotency, and state
    persistence. It never calls an LLM and never creates a reply or next
    question.
    """

    record_id = str(record.get("id") or payload.record_id)
    with _delegation_lock(record_id):
        logger.info(
            "gpt_live_state_tool_call record_id=%s delegation_id=%s call_id=%s "
            "turn_id=%s base_state_version=%s question_id=%s",
            record_id,
            payload.delegation_id,
            payload.call_id,
            payload.turn_id,
            payload.base_state_version,
            payload.question_id,
        )
        snapshot = get_interview_state_snapshot(dict(record), user, persist=True)
        state = deepcopy(snapshot.get("interviewState") or {})
        current_version = int(state.get("stateVersion", 0) or 0)
        applied_turns = _string_list(state.get("liveAppliedTurnIds"))
        if payload.turn_id in applied_turns:
            return _state_update_response(
                "duplicate",
                snapshot,
                record=record,
                knowledge=knowledge,
                user=user,
            )
        if state.get("status") == "completed":
            return _state_update_response(
                "completed",
                snapshot,
                record=record,
                knowledge=knowledge,
                user=user,
            )
        if payload.base_state_version > current_version:
            raise HTTPException(status_code=409, detail="live_state_version_ahead")
        if payload.base_state_version < current_version:
            return _state_update_response(
                "stale",
                snapshot,
                record=record,
                knowledge=knowledge,
                user=user,
            )

        current_question_id = str(state.get("currentQuestionId") or "").strip()
        if current_question_id and payload.question_id != current_question_id:
            logger.warning(
                "gpt_live_state_update_question_stale record_id=%s turn_id=%s "
                "current_question_id=%s payload_question_id=%s",
                record_id,
                payload.turn_id,
                current_question_id,
                payload.question_id,
            )
            raise HTTPException(status_code=409, detail="live_question_not_current")

        fields = [
            row
            for row in store.list("knowledge_fields", user.tenant_id)
            if row.get("knowledgeId") == knowledge.get("id")
            and row.get("askByAi", True) is not False
        ]
        question = _question_for_live_update(state, fields, payload.question_id)
        if question is None:
            raise HTTPException(status_code=422, detail="live_question_id_unknown")
        allowed_aspects = _question_aspects(question)
        covered = _unique_aspect_map(payload.covered_aspects)
        missing = list(dict.fromkeys(str(item).strip() for item in payload.missing_aspects if str(item).strip()))
        evidence = _unique_evidence_map(payload.evidence)
        invalid_aspects = (set(covered) | set(missing) | set(evidence)) - set(allowed_aspects)
        if invalid_aspects:
            raise HTTPException(status_code=422, detail="live_aspect_id_unknown")
        if set(covered) & set(missing):
            raise HTTPException(status_code=422, detail="live_aspect_coverage_conflict")
        if set(evidence) - set(covered):
            raise HTTPException(status_code=422, detail="live_evidence_aspect_not_covered")

        transcript = payload.transcript.strip()
        message = _find_or_create_live_answer_message(
            payload,
            record_id=record_id,
            user=user,
            question=question,
            transcript=transcript,
        )
        evidence_ids = {str(message["id"])}
        aspect_assessments = _build_aspect_assessments(
            question,
            state=state,
            covered=covered,
            missing=missing,
        )
        field_updates, requirement_updates = _build_state_updates(
            question,
            covered=covered,
            answer_status=payload.answer_status,
            evidence_ids=evidence_ids,
        )
        output = StructuredInterviewOutput(
            dialogueAct="ANSWER",
            userIntent="USER_RESPONSE",
            answerEvaluation=_answer_evaluation(payload.answer_status),
            answerStatus=payload.answer_status,
            transcriptAssessment=TranscriptAssessment(
                rawTranscript=transcript,
                normalizedTranscript=transcript,
                recognitionStatus="CLEAR",
            ),
            answerAssessment=AnswerAssessment(
                sufficiency=(
                    "SUFFICIENT"
                    if payload.answer_status == "ANSWERED"
                    else "PARTIAL"
                    if payload.answer_status == "PARTIAL"
                    else "UNANSWERABLE"
                ),
            ),
            aspectAssessments=aspect_assessments,
            missingInformation=[
                str(item.label or item.aspectId)
                for item in aspect_assessments
                if item.status == "MISSING"
            ],
            fieldUpdates=field_updates,
            requirementUpdates=requirement_updates,
        )

        protected = {
            key: deepcopy(state.get(key))
            for key in (
                "currentFieldId",
                "currentQuestionId",
                "currentTurnType",
                "nextQuestionTarget",
                "status",
                "lastNextAction",
                "lastCanonicalIntent",
                "lastCanonicalAction",
                "lastConversationAction",
            )
            if key in state
        }
        apply_structured_output(
            state,
            output,
            latest_message_id=str(message["id"]),
            fields=fields,
            profile=resolve_profile(knowledge),
            valid_evidence_ids=evidence_ids,
            current_question=question,
            raw_transcript=transcript,
        )
        for key, value in protected.items():
            state[key] = value

        _set_live_question(state, question)
        _record_live_answer(
            state,
            question,
            payload,
            aspect_assessments,
            transcript,
            evidence,
        )
        applied_turns.append(payload.turn_id)
        state["liveAppliedTurnIds"] = applied_turns[-256:]
        state["liveLastDelegationId"] = payload.delegation_id
        state["liveLastToolCallId"] = payload.call_id
        state["liveStateSource"] = "responses_delegation"
        committed = commit_interview_state(
            state,
            user,
            source="gpt_live_responses_state_update",
            expected_state_version=current_version,
        )
        message.update(
            {
                "liveStateApplied": True,
                "liveStateTurnId": payload.turn_id,
                "liveDelegationId": payload.delegation_id,
                "liveToolCallId": payload.call_id,
                "updatedAt": utc_now(),
            }
        )
        store.upsert("messages", message)
        result_snapshot = get_interview_state_snapshot(dict(record), user, persist=True)
        result_state = result_snapshot.get("interviewState") or committed
        logger.info(
            "gpt_live_state_persisted record_id=%s delegation_id=%s call_id=%s "
            "turn_id=%s state_version=%s covered_aspects=%s missing_aspects=%s",
            record_id,
            payload.delegation_id,
            payload.call_id,
            payload.turn_id,
            result_state.get("stateVersion"),
            [item.aspectId for item in aspect_assessments if item.status == "ANSWERED"],
            [item.aspectId for item in aspect_assessments if item.status == "MISSING"],
        )
        updated_context = build_live_interview_context(record, knowledge, user)
        return {
            "status": "applied",
            "stateVersion": result_state.get("stateVersion"),
            "currentQuestionId": result_state.get("currentQuestionId"),
            "coveredAspects": [item.aspectId for item in aspect_assessments if item.status == "ANSWERED"],
            "missingAspects": [item.aspectId for item in aspect_assessments if item.status == "MISSING"],
            # The Live model owns dialogue, but it must receive the latest
            # persisted facts before deciding what to say next. This is state
            # context only; it does not contain a backend-generated action or
            # next-question text.
            "persistedState": updated_context["persisted_state"],
            "checklist": updated_context["fields"],
            "current": updated_context["current"],
        }


def _live_question_id(target_type: str, target_id: str) -> str:
    return f"live-{target_type}-{target_id}"


def _question_for_live_update(
    state: Mapping[str, Any],
    fields: list[dict[str, Any]],
    question_id: str,
) -> dict[str, Any] | None:
    for question in state.get("askedQuestions", []) or []:
        if isinstance(question, Mapping) and str(question.get("questionId") or "") == question_id:
            return deepcopy(dict(question))
    if not question_id.startswith("live-field-"):
        return None
    field_id = question_id.removeprefix("live-field-")
    field = next((item for item in fields if str(item.get("id") or "") == field_id), None)
    if not field:
        return None
    plan = field.get("questionPlan")
    plan = plan if isinstance(plan, Mapping) else {}
    return {
        "questionId": question_id,
        "questionType": "structured",
        "fieldId": field_id,
        "text": str(field.get("questionText") or field.get("description") or field.get("name") or field_id),
        "targetType": "field",
        "targetId": field_id,
        "targetLabel": str(field.get("name") or field_id),
        "questionDefinition": {
            "questionGoal": plan.get("questionGoal"),
            "purpose": plan.get("purpose"),
            "preferredProbeDimensions": list(plan.get("preferredProbeDimensions") or []),
            "requiredItems": deepcopy(list(plan.get("requiredItems") or [])),
            "optionalItems": deepcopy(list(plan.get("optionalItems") or [])),
            "questionPlan": deepcopy(dict(plan)),
        },
    }


def _question_aspects(question: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    definition = question.get("questionDefinition")
    definition = definition if isinstance(definition, Mapping) else {}
    aspects: dict[str, dict[str, Any]] = {}
    for required, key in ((True, "requiredItems"), (False, "optionalItems")):
        for item in definition.get(key, []) or []:
            if not isinstance(item, Mapping):
                continue
            aspect_id = str(item.get("itemId") or item.get("id") or "").strip()
            label = str(item.get("label") or aspect_id).strip()
            if aspect_id and label:
                aspects[aspect_id] = {
                    "label": label,
                    "description": str(item.get("description") or "").strip(),
                    "required": required,
                }
    if not aspects and question.get("targetId"):
        target_id = str(question.get("targetId"))
        aspects[target_id] = {
            "label": str(question.get("targetLabel") or target_id),
            "description": str(question.get("text") or ""),
            "required": True,
        }
    return aspects


def _unique_aspect_map(aspects: list[Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for aspect in aspects:
        aspect_id = str(getattr(aspect, "aspect_id", "") or "").strip()
        evidence = str(getattr(aspect, "evidence", "") or "").strip()
        if aspect_id and evidence:
            result[aspect_id] = evidence
    return result


def _unique_evidence_map(evidence: list[Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in evidence:
        aspect_id = str(getattr(item, "aspect_id", "") or "").strip()
        text = str(getattr(item, "text", "") or "").strip()
        if aspect_id and text:
            result[aspect_id] = text
    return result


def _build_aspect_assessments(
    question: Mapping[str, Any],
    *,
    state: Mapping[str, Any],
    covered: Mapping[str, str],
    missing: list[str],
) -> list[AnswerAspectAssessment]:
    definitions = _question_aspects(question)
    field_states = state.get("fieldStates")
    field_state = (
        field_states.get(question.get("targetId"), {})
        if isinstance(field_states, Mapping)
        else {}
    )
    if not isinstance(field_state, Mapping):
        field_state = {}
    prior_captured = {
        str(item.get("itemId"))
        for item in field_state.get("capturedItems", []) or []
        if isinstance(item, Mapping) and item.get("itemId")
    }
    assessments: list[AnswerAspectAssessment] = []
    for aspect_id, definition in definitions.items():
        is_covered = aspect_id in covered or aspect_id in prior_captured
        status = (
            "ANSWERED"
            if is_covered
            else "MISSING"
            if aspect_id in missing
            else "UNCERTAIN"
        )
        evidence = covered.get(aspect_id)
        assessments.append(
            AnswerAspectAssessment(
                aspectId=aspect_id,
                label=str(definition["label"]),
                status=status,
                required=bool(definition["required"]),
                evidence=evidence,
            )
        )
    return assessments


def _build_state_updates(
    question: Mapping[str, Any],
    *,
    covered: Mapping[str, str],
    answer_status: str,
    evidence_ids: set[str],
) -> tuple[list[Any], list[Any]]:
    from ai_interviewer_api.agents.interview_knowledge.schemas import (
        FieldUpdate,
        RequirementUpdate,
    )

    resolution = "AUTO_CONFIRM" if answer_status == "ANSWERED" else "TENTATIVE"
    target_type = str(question.get("targetType") or "")
    target_id = str(question.get("targetId") or "")
    if target_type == "field":
        return (
            [
                FieldUpdate(
                    fieldId=target_id,
                    itemId=aspect_id,
                    value=evidence,
                    evidenceTranscriptIds=list(evidence_ids),
                    answerResolution=resolution,
                )
                for aspect_id, evidence in covered.items()
            ],
            [],
        )
    if target_type in {"requirement", "process"} and covered:
        value = " ".join(dict.fromkeys(covered.values()))
        return (
            [],
            [
                RequirementUpdate(
                    requirementId=target_id,
                    value=value,
                    evidenceTranscriptIds=list(evidence_ids),
                    answerResolution=resolution,
                )
            ],
        )
    return [], []


def _answer_evaluation(answer_status: str) -> str:
    return {
        "ANSWERED": "ANSWERED",
        "PARTIAL": "PARTIALLY_ANSWERED",
        "NOT_ANSWERED": "OFF_TARGET",
    }[answer_status]


def _find_or_create_live_answer_message(
    payload: LiveStateUpdateCreate,
    *,
    record_id: str,
    user: UserContext,
    question: Mapping[str, Any],
    transcript: str,
) -> dict[str, Any]:
    existing = next(
        (
            row
            for row in store.list("messages", user.tenant_id)
            if row.get("recordId") == record_id
            and row.get("liveStateTurnId") == payload.turn_id
        ),
        None,
    )
    if existing:
        return existing
    if transcript:
        existing = next(
            (
                row
                for row in reversed(store.list("messages", user.tenant_id))
                if row.get("recordId") == record_id
                and row.get("liveCaptureId")
                and str(row.get("rawTranscript") or "").strip() == transcript
                and not row.get("liveStateApplied")
            ),
            None,
        )
    message = dict(existing) if existing else {
        "id": (
            f"msg-gpt-live-{record_id}-"
            f"{sha256(payload.turn_id.encode()).hexdigest()[:24]}"
        ),
        "tenantId": user.tenant_id,
        "recordId": record_id,
        "createdAt": utc_now(),
        "role": "user",
        "isActualUtterance": True,
    }
    message.update(
        {
            "content": transcript,
            "rawTranscript": transcript,
            "updatedAt": utc_now(),
            "turnType": "ANSWER",
            "isActualUtterance": True,
            "answerToQuestionId": question.get("questionId"),
            "answerToFieldId": question.get("fieldId"),
            "questionType": question.get("questionType"),
            "targetType": question.get("targetType"),
            "targetId": question.get("targetId"),
            "clientMessageId": _delegation_client_message_id(payload.turn_id),
            "liveStateTurnId": payload.turn_id,
            "liveDelegationId": payload.delegation_id,
            "liveToolCallId": payload.call_id,
        }
    )
    store.upsert("messages", message)
    return message


def _set_live_question(state: dict[str, Any], question: Mapping[str, Any]) -> None:
    question_id = str(question.get("questionId") or "").strip()
    if not question_id:
        return
    was_same_question = str(state.get("currentQuestionId") or "") == question_id
    was_probe = was_same_question and state.get("currentTurnType") == "PROBE"
    asked = state.setdefault("askedQuestions", [])
    if not any(
        isinstance(item, Mapping) and str(item.get("questionId") or "") == question_id
        for item in asked
    ):
        asked.append(deepcopy(dict(question)))
    state["currentQuestionId"] = question_id
    state["currentFieldId"] = question.get("fieldId")
    if not was_probe:
        state["currentTurnType"] = "BASIC"
        state["nextQuestionTarget"] = {
            "targetType": question.get("targetType"),
            "targetId": question.get("targetId"),
            "label": question.get("targetLabel"),
        }


def _record_live_answer(
    state: dict[str, Any],
    question: Mapping[str, Any],
    payload: LiveStateUpdateCreate,
    aspects: list[AnswerAspectAssessment],
    transcript: str,
    evidence: Mapping[str, str],
) -> None:
    question_id = str(question.get("questionId") or "")
    answers = state.setdefault("questionAnswers", {})
    answer = answers.setdefault(
        question_id,
        {
            "questionId": question_id,
            "fieldId": question.get("fieldId"),
            "targetType": question.get("targetType"),
            "targetId": question.get("targetId"),
            "originalAnswer": None,
            "answer": None,
            "answerSource": "GENERATED",
            "probes": [],
        },
    )
    if answer.get("originalAnswer") is None and transcript:
        answer["originalAnswer"] = transcript
    if answer.get("answerSource") != "MANUAL" and transcript:
        answer["answer"] = _merge_answer_text(answer.get("answer"), transcript)
    if answer.get("answerSource") != "MANUAL" and evidence:
        answer["answer"] = _merge_answer_text(
            answer.get("answer"),
            " ".join(evidence.values()),
        )
    stored_evidence = {
        str(item.get("aspectId")): str(item.get("text"))
        for item in answer.get("evidence", []) or []
        if isinstance(item, Mapping) and item.get("aspectId") and item.get("text")
    }
    stored_evidence.update(
        {aspect_id: text for aspect_id, text in evidence.items()}
    )
    answer.update(
        {
            "answerStatus": payload.answer_status,
            "coveredTopics": [item.aspectId for item in aspects if item.status == "ANSWERED"],
            "missingInformation": [item.label for item in aspects if item.status == "MISSING"],
            "evidence": [
                {"aspectId": aspect_id, "text": text}
                for aspect_id, text in stored_evidence.items()
            ],
            "updatedAt": utc_now(),
            "stateSource": "responses_delegation",
        }
    )


def _merge_answer_text(existing: Any, addition: str) -> str:
    existing_text = str(existing or "").strip()
    addition_text = str(addition or "").strip()
    if not existing_text:
        return addition_text
    if not addition_text or addition_text == existing_text:
        return existing_text
    if addition_text in existing_text:
        return existing_text
    return f"{existing_text} {addition_text}"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _delegation_lock(record_id: str) -> RLock:
    with _LIVE_DELEGATION_LOCKS_GUARD:
        return _LIVE_DELEGATION_LOCKS.setdefault(record_id, RLock())


def _delegation_client_message_id(turn_id: str) -> str:
    return f"gpt-live-turn-{sha256(turn_id.encode()).hexdigest()}"


def _current_question(state: Mapping[str, Any]) -> dict[str, Any] | None:
    return get_current_structured_question(state)


def _state_update_response(
    status: str,
    snapshot: Mapping[str, Any],
    *,
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
) -> dict[str, Any]:
    state = snapshot.get("interviewState") or {}
    context = build_live_interview_context(record, knowledge, user)
    return {
        "status": status,
        "interviewState": _live_state_for_model(state),
        "structuredDraft": snapshot.get("structuredDraft") or {},
        "stateVersion": state.get("stateVersion"),
        "currentQuestionId": state.get("currentQuestionId"),
        "persistedState": context["persisted_state"],
        "checklist": context["fields"],
        "current": context["current"],
    }


def _live_state_for_model(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return state context without normal-runtime action decisions."""

    allowed_keys = {
        "stateVersion",
        "status",
        "currentFieldId",
        "currentQuestionId",
        "currentTurnType",
        "fieldStates",
        "questionAnswers",
        "lastAnswerStatus",
        "lastAnswerEvaluation",
        "lastAspectAssessments",
        "lastMissingInformation",
        "lastExtractedFields",
        "liveStateSource",
    }
    return {
        key: deepcopy(value)
        for key, value in state.items()
        if key in allowed_keys
    }


def _item_summary(item: Mapping[str, Any]) -> dict[str, str]:
    return {
        "id": str(item.get("itemId") or ""),
        "label": str(item.get("label") or "").strip(),
        "description": str(item.get("description") or "").strip(),
    }
