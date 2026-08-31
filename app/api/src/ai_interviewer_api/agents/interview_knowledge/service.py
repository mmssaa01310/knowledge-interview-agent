from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from copy import deepcopy
from time import monotonic
from typing import Any
from uuid import uuid4

from ai_interviewer_api.agents.interview_knowledge.coordinator import (
    apply_document_candidate,
    apply_structured_output,
    build_initial_structured_state,
    confirm_tentative_target,
    evaluate_completion,
    is_current_question_confirmation_target,
    resolve_profile,
    select_next_question_target,
    sync_structured_state_fields,
)
from ai_interviewer_api.agents.interview_knowledge.provider import (
    BedrockResponsesStructuredProvider,
    StructuredInterviewProvider,
)
from ai_interviewer_api.agents.interview_knowledge.schemas import (
    InterviewProfile,
    StructuredInterviewOutput,
)
from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.core.config import settings
from ai_interviewer_api.core.interview_locale import (
    InterviewLocale,
    interview_language_instruction,
    localized_interview_confirmation_question,
    localized_interview_document_confirmation_question,
    localized_interview_fallbacks,
    localized_interview_tentative_transition,
    resolve_interview_locale,
)
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.models.interview_plan import STRUCTURED_INTERVIEW_MODEL_IDS
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.schemas.retrieval import (
    DocumentQuestionCandidate,
    RetrievedKnowledgeContext,
    source_references,
)
from ai_interviewer_api.services.interview_confirmation import (
    is_unambiguous_confirmation,
)
from ai_interviewer_api.services.interview_document_retrieval import (
    retrieve_interview_document_context,
    validate_document_question_candidate,
)


STRUCTURED_PROFILES: frozenset[str] = frozenset({"fixed_form", "business_process", "system_requirement"})
logger = logging.getLogger(__name__)


def is_structured_interview_enabled(knowledge: Mapping[str, Any]) -> bool:
    return bool(settings.structured_interview_enabled and resolve_profile(knowledge) in STRUCTURED_PROFILES)


def generate_structured_interview_result(
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
    *,
    persist_assistant_messages: bool = True,
    provider: StructuredInterviewProvider | None = None,
) -> dict[str, Any]:
    fields = _list_interview_fields(knowledge, user)
    state = load_structured_interview_state(record, knowledge, user, fields=fields)
    messages = _list_record_messages(record, user)
    profile = _effective_profile(state, resolve_profile(knowledge))
    model_id = resolve_structured_model_id(knowledge)
    interview_locale = resolve_interview_locale(record, knowledge)

    if state.get("status") == "completed":
        return _build_result(
            record=record,
            state=state,
            messages=messages,
            fields=fields,
            reply=_completion_reply(interview_locale),
            question=None,
            action="finish",
            status="completed",
        )

    current_question = _get_current_question(state)
    latest_user_message = _latest_answer_message(messages, current_question)
    last_processed_id = state.get("lastProcessedUserMessageId")

    if current_question and (
        latest_user_message is None
        or latest_user_message.get("id") == last_processed_id
    ) and not state.get("questionGenerationPending"):
        return _build_result(
            record=record,
            state=state,
            messages=messages,
            fields=fields,
            reply=str(current_question.get("text") or ""),
            question=current_question,
            action="ask_structured",
            status="in_progress",
        )

    if current_question and latest_user_message and latest_user_message.get("id") != last_processed_id:
        if latest_user_message.get("turnType") == "CONTROL":
            state["lastProcessedUserMessageId"] = latest_user_message.get("id")
            _persist_state(state, user)
            return _build_result(
                record=record,
                state=state,
                messages=messages,
                fields=fields,
                reply=str(current_question.get("text") or ""),
                question=current_question,
                action="ask_structured",
                status="in_progress",
            )
        else:
            confirmed_field_ids_before = {
                field_id
                for field_id, field_state in state.get("fieldStates", {}).items()
                if field_state.get("answerState") == "CONFIRMED"
            }
            tentative_target_before = deepcopy(state.get("lastTentativeTarget"))
            structured_provider = _get_structured_provider(provider, model_id=model_id)
            interpreter_context = _build_interpreter_context(
                record=record,
                knowledge=knowledge,
                fields=fields,
                state=state,
                messages=messages,
                current_question=current_question,
            )
            initial_reasoning_effort = _select_reasoning_effort(state)
            selected_reasoning_effort = initial_reasoning_effort
            if (
                is_current_question_confirmation_target(state, current_question)
                and is_unambiguous_confirmation(
                    str(latest_user_message.get("content") or ""),
                    locale=interview_locale,
                )
            ):
                logger.info(
                    "structured_confirmation_fast_path outcome=CONFIRM record_id=%s question_id=%s",
                    record.get("id"),
                    current_question.get("questionId"),
                )
                output = StructuredInterviewOutput(dialogueAct="CONFIRMATION")
            else:
                output = structured_provider.interpret(
                    profile=profile,
                    context=interpreter_context,
                    reasoning_effort=initial_reasoning_effort,
                )
                if (
                    initial_reasoning_effort != settings.structured_interview_medium_reasoning_effort
                    and _requires_medium_reasoning(state, output)
                ):
                    medium_context = {
                        **interpreter_context,
                        "preliminaryOutput": output.model_dump(),
                    }
                    output = structured_provider.interpret(
                        profile=profile,
                        context=medium_context,
                        reasoning_effort=settings.structured_interview_medium_reasoning_effort,
                    )
                    selected_reasoning_effort = settings.structured_interview_medium_reasoning_effort
            if output.dialogueAct in {
                "ANSWER",
                "CORRECTION",
                "REJECTION",
                "CONFIRMATION",
            } or _has_structured_updates(output):
                if current_question.get("targetType") == "applicability_overview":
                    state["applicabilityOverviewAsked"] = True
                apply_structured_output(
                    state,
                    output,
                    latest_message_id=str(latest_user_message.get("id") or ""),
                    fields=fields,
                    profile=profile,
                    valid_evidence_ids={
                        str(message.get("id"))
                        for message in messages
                        if message.get("id")
                    },
                    current_question=current_question,
                )
                if _should_implicitly_confirm_tentative_target(
                    output,
                    tentative_target_before,
                    current_question,
                ):
                    confirm_tentative_target(state, tentative_target_before)
                _save_newly_confirmed_field_messages(
                    record=record,
                    state=state,
                    previous_confirmed_field_ids=confirmed_field_ids_before,
                    question=current_question,
                    user=user,
                )
            else:
                state["lastStructuredDialogueAct"] = output.dialogueAct
                state["lastProcessedUserMessageId"] = latest_user_message.get("id")
            state["lastStructuredOutput"] = output.model_dump()
            state["lastStructuredModelId"] = model_id
            state["lastStructuredReasoningEffort"] = selected_reasoning_effort
            completion = evaluate_completion(state, profile, fields)
            if completion["complete"]:
                state["status"] = "completed"
                state["currentFieldId"] = None
                state["currentQuestionId"] = None
                state["nextQuestionTarget"] = None
                _persist_state(state, user)
                return _build_result(
                    record=record,
                    state=state,
                    messages=messages,
                    fields=fields,
                    reply=_completion_reply(interview_locale),
                    question=None,
                    action="finish",
                    status="completed",
                )
            _persist_state(state, user)

    target = select_next_question_target(state, profile, fields)
    if target is None:
        # A defensive second evaluation prevents a premature completion when
        # a malformed provider response leaves the state incomplete.
        completion = evaluate_completion(state, profile, fields)
        if not completion["complete"]:
            target = {
                "targetType": "issue",
                "targetId": "incomplete_state",
                "label": "未確認の情報",
                "priority": 3,
            }
        else:
            state["status"] = "completed"
            state["currentFieldId"] = None
            state["currentQuestionId"] = None
            state["nextQuestionTarget"] = None
            _persist_state(state, user)
            return _build_result(
                record=record,
                state=state,
                messages=messages,
                fields=fields,
                reply=_completion_reply(interview_locale),
                question=None,
                action="finish",
                status="completed",
            )

    structured_provider = _get_structured_provider(provider, model_id=model_id)
    state["questionGenerationPending"] = True
    _persist_state(state, user)
    question_text, retrieved_context, document_candidate = _generate_question_text(
        structured_provider,
        profile=profile,
        target=target,
        record=record,
        knowledge=knowledge,
        user=user,
        fields=fields,
        state=state,
        messages=messages,
    )
    if document_candidate is not None:
        target = dict(target)
        if apply_document_candidate(
            state,
            target,
            value=document_candidate.value,
            source_ids=document_candidate.source_ids,
        ):
            question_text = localized_interview_document_confirmation_question(
                interview_locale,
                str(target.get("label") or "").strip(),
                document_candidate.value,
            )
        else:
            document_candidate = None
    state["lastQuestionModelId"] = model_id
    state["lastQuestionReasoningEffort"] = settings.structured_interview_reasoning_effort
    question = _build_question(
        state,
        target,
        question_text,
        retrieval_policy=_retrieval_policy_for_target(target, _field_for_target(target, fields)),
        retrieved_sources=source_references(retrieved_context),
    )
    reply = question["text"]
    assistant_message = (
        _save_assistant_message(user, str(record["id"]), reply, question)
        if persist_assistant_messages
        else None
    )
    if assistant_message and target.get("candidateSource") == "assistant_proposal":
        _attach_proposal_message_id(
            state,
            target=target,
            assistant_message_id=str(assistant_message.get("id") or ""),
        )
    state.pop("questionGenerationPending", None)
    state["currentFieldId"] = question.get("fieldId")
    state["currentQuestionId"] = question["questionId"]
    state["nextQuestionTarget"] = target
    state.setdefault("askedQuestions", []).append(question)
    if question.get("fieldId"):
        state.setdefault("fieldStates", {}).setdefault(
            question["fieldId"],
            {"fieldId": question["fieldId"], "status": "pending", "answerState": "UNANSWERED"},
        )["status"] = "asking"
    _persist_state(state, user)
    all_messages = [*messages, assistant_message] if assistant_message else messages
    completion = evaluate_completion(state, profile, fields)
    return _build_result(
        record=record,
        state=state,
        messages=all_messages,
        fields=fields,
        reply=reply,
        question=question,
        action="ask_structured",
        status="in_progress",
        assistant_message=assistant_message,
        missing_information=[
            str(item.get("label") or item.get("targetId"))
            for item in completion["missingRequiredTargets"]
        ],
    )


def get_structured_interview_state_snapshot(
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
    *,
    persist: bool = True,
) -> dict[str, Any]:
    fields = _list_interview_fields(knowledge, user)
    state = load_structured_interview_state(record, knowledge, user, fields=fields, persist=persist)
    messages = _list_record_messages(record, user)
    if not persist:
        messages = deepcopy(messages)
    return {
        "status": state.get("status", "in_progress"),
        "interviewState": state,
        "messages": messages,
        "structuredDraft": _build_structured_draft(state, fields),
    }


def load_structured_interview_state(
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
    *,
    fields: Sequence[Mapping[str, Any]] | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    state_id = f"interview-state-{record['id']}"
    profile = resolve_profile(knowledge)
    existing = store.get("interview_states", state_id)
    if existing:
        state = existing if persist else deepcopy(existing)
        state_profile = state.get("interviewProfile")
        if state_profile in STRUCTURED_PROFILES:
            profile = state_profile
        elif state_profile not in {None, "fixed_form"}:
            profile = "system_requirement"
        if state_profile != profile:
            state["interviewProfile"] = profile
        changed = _backfill_state(state, profile, fields or ())
        changed |= _repair_current_confirmation_question(
            state,
            locale=resolve_interview_locale(record, knowledge),
        )
        if changed and persist:
            _persist_state(state, user)
        return state

    state = build_initial_structured_state(profile, fields or ())
    state.update(
        {
            "id": state_id,
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "createdByUserId": user.user_id,
            "updatedByUserId": user.user_id,
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
        }
    )
    if persist:
        store.upsert("interview_states", state)
    return state


def _backfill_state(
    state: dict[str, Any],
    profile: InterviewProfile,
    fields: Sequence[Mapping[str, Any]],
) -> bool:
    changed = False
    initial = build_initial_structured_state(profile, fields)
    for key, value in initial.items():
        if key not in state:
            state[key] = value
            changed = True
    requirement_states = state.setdefault("requirementStates", {})
    for requirement_id, requirement_state in initial.get("requirementStates", {}).items():
        if requirement_id not in requirement_states:
            requirement_states[requirement_id] = requirement_state
            changed = True
    for field_id, initial_field_state in initial.get("fieldStates", {}).items():
        field_state = state.setdefault("fieldStates", {}).setdefault(field_id, initial_field_state)
        for key in ("candidateSourceIds", "confirmedSourceIds"):
            if key not in field_state:
                field_state[key] = []
                changed = True
    for requirement_id, initial_requirement_state in initial.get("requirementStates", {}).items():
        requirement_state = requirement_states.setdefault(requirement_id, initial_requirement_state)
        for key in ("candidateSourceIds", "confirmedSourceIds"):
            if key not in requirement_state:
                requirement_state[key] = []
                changed = True
    applicability_states = state.setdefault("applicabilityState", {})
    for topic, applicability_state in initial.get("applicabilityState", {}).items():
        if topic not in applicability_states:
            applicability_states[topic] = applicability_state
            changed = True
    changed |= sync_structured_state_fields(state, fields)
    if state.get("interviewProfile") != profile:
        state["interviewProfile"] = profile
        changed = True
    return changed


def _effective_profile(state: Mapping[str, Any], fallback: InterviewProfile) -> InterviewProfile:
    profile = state.get("interviewProfile")
    return profile if profile in STRUCTURED_PROFILES else fallback


def _list_interview_fields(knowledge: Mapping[str, Any], user: UserContext) -> list[dict[str, Any]]:
    knowledge_id = str(knowledge.get("id") or "")
    return sorted(
        [
            row
            for row in store.list("knowledge_fields", user.tenant_id)
            if row.get("knowledgeId") == knowledge_id
        ],
        key=lambda row: int(row.get("displayOrder") or 0),
    )


def _list_record_messages(record: Mapping[str, Any], user: UserContext) -> list[dict[str, Any]]:
    return sorted(
        [
            row
            for row in store.list("messages", user.tenant_id)
            if row.get("recordId") == record.get("id")
        ],
        key=lambda row: (row.get("createdAt") or "", row.get("id") or ""),
    )


def _get_current_question(state: Mapping[str, Any]) -> dict[str, Any] | None:
    question_id = state.get("currentQuestionId")
    if not question_id:
        return None
    return next(
        (
            question
            for question in state.get("askedQuestions", [])
            if question.get("questionId") == question_id
        ),
        None,
    )


def _latest_answer_message(
    messages: Sequence[Mapping[str, Any]],
    current_question: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    question_id = current_question.get("questionId") if current_question else None
    for message in reversed(messages):
        if message.get("role") != "user" or message.get("isActualUtterance") is False:
            continue
        if message.get("answerToQuestionId") == question_id:
            return dict(message)
    return None


def _build_interpreter_context(
    *,
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    fields: Sequence[Mapping[str, Any]],
    state: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    current_question: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "profile": state.get("interviewProfile"),
        "knowledge": {
            "id": knowledge.get("id"),
            "name": knowledge.get("name"),
            "description": knowledge.get("description"),
            "purpose": knowledge.get("purpose"),
            "targetBusiness": knowledge.get("targetBusiness"),
            "systemPrompt": knowledge.get("systemPrompt"),
        },
        "record": {
            "id": record.get("id"),
            "title": record.get("title"),
            "targetEquipment": record.get("targetEquipment"),
            "targetProcess": record.get("targetProcess"),
        },
        "fields": [
            {
                "id": field.get("id"),
                "name": field.get("name"),
                "description": field.get("description"),
                "required": field.get("required"),
                "inputType": field.get("inputType"),
                "aiAssistPrompt": field.get("aiAssistPrompt"),
                "questionPlan": field.get("questionPlan"),
            }
            for field in fields
        ],
        "currentQuestion": dict(current_question),
        "interviewState": _compact_state(state),
        "conversation": [
            {
                "id": message.get("id"),
                "role": message.get("role"),
                "content": message.get("content"),
                "questionId": message.get("questionId"),
                "answerToQuestionId": message.get("answerToQuestionId"),
                "sttConfidence": message.get("sttConfidence"),
            }
            for message in messages[-30:]
            if message.get("isActualUtterance") is not False
        ],
    }


def _compact_state(state: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(state)
    result.pop("lastStructuredOutput", None)
    result.pop("createdByUserId", None)
    result.pop("updatedByUserId", None)
    result.pop("createdAt", None)
    result.pop("updatedAt", None)
    return result


def _select_reasoning_effort(state: Mapping[str, Any]) -> str:
    process_state = state.get("processState") or {}
    complex_state = bool(
        state.get("contradictions")
        or state.get("openIssues")
        or len(process_state.get("nodes", [])) >= 10
        or len(process_state.get("edges", [])) >= 12
    )
    return (
        settings.structured_interview_medium_reasoning_effort
        if complex_state
        else settings.structured_interview_reasoning_effort
    )


def _requires_medium_reasoning(
    state: Mapping[str, Any],
    output: Any,
) -> bool:
    patch = output.processPatch
    process_operation_count = sum(
        len(getattr(patch, name))
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
    )
    process_state = state.get("processState") or {}
    existing_entity_count = sum(
        len(process_state.get(key, []))
        for key in ("participants", "nodes", "edges", "interactions")
    )
    return bool(
        output.contradictions
        or output.openIssues
        or len(output.fieldUpdates) + len(output.requirementUpdates) >= 6
        or process_operation_count >= 8
        or _has_multiple_process_components(process_state, patch)
        or (
            existing_entity_count >= 4
            and process_operation_count >= max(4, existing_entity_count // 2)
        )
    )


def _has_structured_updates(output: StructuredInterviewOutput) -> bool:
    """Allow a semantically useful output even when its dialogue act is conversational."""

    patch = output.processPatch
    return bool(
        output.fieldUpdates
        or output.requirementUpdates
        or output.applicability
        or output.contradictions
        or output.resolvedContradictionIds
        or output.openIssues
        or any(
            getattr(patch, name)
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
        )
    )


def _should_implicitly_confirm_tentative_target(
    output: StructuredInterviewOutput,
    tentative_target: Mapping[str, Any] | None,
    current_question: Mapping[str, Any] | None,
) -> bool:
    """Return whether this turn answered a different target normally.

    A correction or rejection must keep the tentative target open. Likewise,
    an update that mentions the tentative target may itself be a correction,
    so it is left to the next evaluation instead of being auto-confirmed here.
    """

    if not isinstance(tentative_target, Mapping) or output.dialogueAct != "ANSWER":
        return False
    tentative_type = str(
        tentative_target.get("targetType") or tentative_target.get("kind") or ""
    )
    tentative_id = str(tentative_target.get("targetId") or "")
    current_type = str(
        (current_question or {}).get("targetType")
        or (current_question or {}).get("kind")
        or ""
    )
    current_id = str((current_question or {}).get("targetId") or "")
    if tentative_type == current_type and tentative_id == current_id:
        return False

    for update in [*output.fieldUpdates, *output.requirementUpdates]:
        update_id = str(
            getattr(update, "fieldId", None)
            or getattr(update, "requirementId", None)
            or ""
        )
        update_type = (
            "field"
            if hasattr(update, "fieldId")
            else "requirement" if update_id.startswith("requirement.") else "process"
        )
        if update_type == tentative_type and update_id == tentative_id:
            return False
        if str(getattr(update, "value", "") or "").strip() and getattr(
            update, "answerResolution", None
        ) != "RETRY":
            return True
    return False


def _has_multiple_process_components(
    process_state: Mapping[str, Any],
    patch: Any,
) -> bool:
    nodes = {
        str(node.get("nodeId"))
        for node in process_state.get("nodes", [])
        if node.get("lifecycle") != "superseded" and node.get("nodeId")
    }
    nodes.update(node.nodeId for node in patch.addNodes)
    nodes.update(node.nodeId for node in patch.updateNodes)
    if len(nodes) < 2:
        return False
    connections = {node_id: set[str]() for node_id in nodes}
    for edge in process_state.get("edges", []):
        if edge.get("lifecycle") == "superseded":
            continue
        source = str(edge.get("sourceNodeId") or "")
        target = str(edge.get("targetNodeId") or "")
        if source in connections and target in connections:
            connections[source].add(target)
            connections[target].add(source)
    for edge in [*patch.addEdges, *patch.updateEdges]:
        if edge.sourceNodeId in connections and edge.targetNodeId in connections:
            connections[edge.sourceNodeId].add(edge.targetNodeId)
            connections[edge.targetNodeId].add(edge.sourceNodeId)
    remaining = set(nodes)
    components = 0
    while remaining:
        components += 1
        stack = [remaining.pop()]
        while stack:
            current = stack.pop()
            for neighbor in connections[current] & remaining:
                remaining.remove(neighbor)
                stack.append(neighbor)
    return components > 1


def _generate_question_text(
    provider: StructuredInterviewProvider,
    *,
    profile: InterviewProfile,
    target: Mapping[str, Any],
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
    fields: Sequence[Mapping[str, Any]],
    state: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
) -> tuple[str, list[RetrievedKnowledgeContext], DocumentQuestionCandidate | None]:
    started_at = monotonic()
    current_field = _field_for_target(target, fields)
    retrieval_policy = _retrieval_policy_for_target(target, current_field)
    retrieved_context = retrieve_interview_document_context(
        record=record,
        knowledge=knowledge,
        user=user,
        current_question=None,
        current_field=current_field,
        target=target,
        state=state,
        messages=messages,
        retrieval_policy=retrieval_policy,
    )
    context = {
        "knowledgeName": knowledge.get("name"),
        "recordTitle": record.get("title"),
        "customPrompt": knowledge.get("systemPrompt"),
        "interviewLocale": resolve_interview_locale(record, knowledge),
        "languageInstruction": interview_language_instruction(resolve_interview_locale(record, knowledge)),
        "currentState": _compact_state(state),
        "recentConversation": [
            {"role": message.get("role"), "content": message.get("content")}
            for message in messages[-12:]
            if message.get("isActualUtterance") is not False
        ],
        "fields": [{"id": field.get("id"), "name": field.get("name")} for field in fields],
        "tentativeCandidates": _list_tentative_candidates(state),
        "retrieved_knowledge": [item.model_dump() for item in retrieved_context],
    }
    generated = provider.generate_question(
        profile=profile,
        context=context,
        target=target,
        reasoning_effort=settings.structured_interview_reasoning_effort,
    )
    logger.info(
        "structured_question_generated model_id=%s target_type=%s target_id=%s reasoning_effort=%s elapsed_ms=%s",
        getattr(provider, "model_id", None),
        target.get("targetType") or target.get("kind"),
        target.get("targetId"),
        settings.structured_interview_reasoning_effort,
        round((monotonic() - started_at) * 1000),
    )
    question_text = generated.questionText.strip()
    document_candidate = validate_document_question_candidate(
        value=generated.documentCandidateValue,
        source_ids=generated.documentCandidateSourceIds,
        contexts=retrieved_context,
    )
    if document_candidate is not None and (
        _is_awaiting_confirmation_target(target, state)
        or _candidate_value_for_target(target, state)
    ):
        document_candidate = None
    if document_candidate is not None:
        # The backend owns the wording of document-backed confirmation so the
        # source is always explicit and the candidate cannot be omitted by a
        # provider response.
        return (
            localized_interview_document_confirmation_question(
                resolve_interview_locale(record, knowledge),
                str(target.get("label") or "").strip(),
                document_candidate.value,
            ),
            retrieved_context,
            document_candidate,
        )
    candidate_value = _candidate_value_for_target(target, state)
    candidate_source = _candidate_source_for_target(target, state)
    if (
        candidate_value
        and _is_awaiting_confirmation_target(target, state)
        and candidate_source == "document_reference"
    ):
        return (
            localized_interview_document_confirmation_question(
                resolve_interview_locale(record, knowledge),
                str(target.get("label") or "").strip(),
                candidate_value,
            ),
            retrieved_context,
            None,
        )
    if (
        candidate_value
        and _is_awaiting_confirmation_target(target, state)
        and not _contains_question_candidate(question_text, candidate_value)
    ):
        logger.warning(
            "structured_confirmation_question_candidate_missing target_type=%s target_id=%s",
            target.get("targetType") or target.get("kind"),
            target.get("targetId"),
        )
        return (
            localized_interview_confirmation_question(
                resolve_interview_locale(record, knowledge),
                candidate_value,
            ),
            retrieved_context,
            None,
        )
    tentative = _tentative_candidate_for_state(state)
    if tentative and not _is_awaiting_confirmation_target(target, state):
        tentative_value, _ = tentative
        if (
            not state.get("tentativeBridgeShown")
            and not _contains_question_candidate(question_text, tentative_value)
        ):
            question_text = (
                f"{localized_interview_tentative_transition(
                    resolve_interview_locale(record, knowledge),
                    tentative_value,
                    str(target.get("label") or "").strip(),
                )}{question_text}"
            )
        if isinstance(state, dict):
            state["tentativeBridgeShown"] = True
    return question_text, retrieved_context, None


def _field_for_target(
    target: Mapping[str, Any],
    fields: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    target_type = str(target.get("targetType") or target.get("kind") or "")
    target_id = str(target.get("targetId") or "")
    if target_type != "field" or not target_id:
        return None
    return next(
        (field for field in fields if str(field.get("id") or "") == target_id),
        None,
    )


def _retrieval_policy_for_target(
    target: Mapping[str, Any],
    field: Mapping[str, Any] | None,
) -> str:
    value = target.get("retrievalPolicy")
    if value is None and field is not None:
        value = field.get("retrievalPolicy")
    policy = str(value or "auto").strip().lower()
    return policy if policy in {"never", "auto", "required"} else "auto"


def _repair_current_confirmation_question(
    state: dict[str, Any],
    *,
    locale: InterviewLocale,
) -> bool:
    """Repair persisted questions that lost their confirmation candidate.

    Older turns could persist a generic field question while the field was
    already awaiting confirmation. Keep the question ID and audit history, but
    restore the domain-independent confirmation wording for the next client
    render or voice session.
    """

    current_question = _get_current_question(state)
    if not current_question:
        return False
    target_type = str(current_question.get("targetType") or current_question.get("kind") or "")
    target_id = str(current_question.get("targetId") or "")
    if target_type == "field":
        target_state = state.get("fieldStates", {}).get(target_id, {})
        is_pending = target_state.get("answerState") == "AWAITING_CONFIRMATION"
        candidate_value = str(target_state.get("candidateAnswer") or "").strip()
        candidate_source = target_state.get("candidateSource")
    elif target_type in {"requirement", "process"}:
        target_state = state.get("requirementStates", {}).get(target_id, {})
        is_pending = target_state.get("status") == "AWAITING_CONFIRMATION"
        candidate_value = str(target_state.get("candidateValue") or "").strip()
        candidate_source = target_state.get("candidateSource")
    else:
        return False
    if not is_pending or not candidate_value or _contains_question_candidate(
        str(current_question.get("text") or ""),
        candidate_value,
    ):
        return False

    if candidate_source == "document_reference":
        current_question["text"] = localized_interview_document_confirmation_question(
            locale,
            str(current_question.get("targetLabel") or current_question.get("label") or "").strip(),
            candidate_value,
        )
    else:
        current_question["text"] = localized_interview_confirmation_question(locale, candidate_value)
    return True


def _candidate_value_for_target(
    target: Mapping[str, Any],
    state: Mapping[str, Any],
) -> str:
    target_type = str(target.get("targetType") or target.get("kind") or "")
    target_id = str(target.get("targetId") or "")
    if target_type == "field":
        target_state = state.get("fieldStates", {}).get(target_id, {})
        return str(target_state.get("candidateAnswer") or "").strip()
    if target_type in {"requirement", "process"}:
        target_state = state.get("requirementStates", {}).get(target_id, {})
        return str(target_state.get("candidateValue") or "").strip()
    return ""


def _candidate_source_for_target(
    target: Mapping[str, Any],
    state: Mapping[str, Any],
) -> str | None:
    target_type = str(target.get("targetType") or target.get("kind") or "")
    target_id = str(target.get("targetId") or "")
    if target_type == "field":
        value = state.get("fieldStates", {}).get(target_id, {}).get("candidateSource")
    elif target_type in {"requirement", "process"}:
        value = state.get("requirementStates", {}).get(target_id, {}).get("candidateSource")
    else:
        return None
    return str(value) if value else None


def _is_awaiting_confirmation_target(
    target: Mapping[str, Any],
    state: Mapping[str, Any],
) -> bool:
    target_type = str(target.get("targetType") or target.get("kind") or "")
    target_id = str(target.get("targetId") or "")
    if target_type == "field":
        return state.get("fieldStates", {}).get(target_id, {}).get("answerState") == "AWAITING_CONFIRMATION"
    if target_type in {"requirement", "process"}:
        return state.get("requirementStates", {}).get(target_id, {}).get("status") == "AWAITING_CONFIRMATION"
    return False


def _tentative_candidate_for_state(
    state: Mapping[str, Any],
) -> tuple[str, str] | None:
    reference = state.get("lastTentativeTarget")
    if not isinstance(reference, Mapping):
        return None
    target_type = str(reference.get("targetType") or "")
    target_id = str(reference.get("targetId") or "")
    if target_type == "field":
        target_state = state.get("fieldStates", {}).get(target_id, {})
        if target_state.get("answerResolution") != "TENTATIVE":
            return None
        value = str(target_state.get("candidateAnswer") or "").strip()
        label = str(target_state.get("fieldId") or target_id).strip()
    elif target_type in {"requirement", "process"}:
        target_state = state.get("requirementStates", {}).get(target_id, {})
        if target_state.get("answerResolution") != "TENTATIVE":
            return None
        value = str(target_state.get("candidateValue") or "").strip()
        label = str(target_state.get("label") or target_id).strip()
    else:
        return None
    return (value, label) if value else None


def _list_tentative_candidates(state: Mapping[str, Any]) -> list[dict[str, str]]:
    candidate = _tentative_candidate_for_state(state)
    if candidate is None:
        return []
    value, label = candidate
    return [{"label": label, "value": value}]


def _contains_question_candidate(question_text: str, candidate_value: str) -> bool:
    compact_question = "".join(question_text.casefold().split())
    compact_candidate = "".join(candidate_value.casefold().split())
    return bool(compact_candidate and compact_candidate in compact_question)


def _get_structured_provider(
    provider: StructuredInterviewProvider | None,
    *,
    model_id: str,
) -> StructuredInterviewProvider:
    if provider is not None:
        return provider
    return BedrockResponsesStructuredProvider(model_id=model_id)


def resolve_structured_model_id(knowledge: Mapping[str, Any]) -> str:
    """Resolve the per-knowledge model selection with the backend default."""

    plan = knowledge.get("interviewPlan")
    selected_model_id = plan.get("modelId") if isinstance(plan, Mapping) else None
    if selected_model_id in STRUCTURED_INTERVIEW_MODEL_IDS:
        return str(selected_model_id)
    return settings.structured_interview_model_id


def _build_question(
    state: Mapping[str, Any],
    target: Mapping[str, Any],
    text: str,
    *,
    retrieval_policy: str = "auto",
    retrieved_sources: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    target_kind = str(target.get("targetType") or target.get("kind") or "issue")
    target_id = str(target.get("targetId") or "")
    field_id = target_id if target_kind == "field" else None
    question = {
        "questionId": f"q-{len(state.get('askedQuestions', [])) + 1:03d}",
        "questionType": "structured",
        "fieldId": field_id,
        "text": text,
        "retrievalPolicy": retrieval_policy,
        "targetType": target_kind,
        "targetId": target_id,
        "targetLabel": str(target.get("label") or "").strip() or None,
        "retrievedSources": [dict(source) for source in (retrieved_sources or [])],
    }
    if target.get("candidateSource"):
        question["candidateSource"] = target.get("candidateSource")
    if target.get("candidateValue"):
        question["candidateValue"] = target.get("candidateValue")
    if target.get("candidateSourceIds"):
        question["candidateSourceIds"] = list(target.get("candidateSourceIds") or [])
    return question


def _completion_reply(locale: InterviewLocale = "ja-JP") -> str:
    return localized_interview_fallbacks(locale)["completion"]


def _build_result(
    *,
    record: Mapping[str, Any],
    state: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    fields: Sequence[Mapping[str, Any]],
    reply: str,
    question: Mapping[str, Any] | None,
    action: str,
    status: str,
    assistant_message: Mapping[str, Any] | None = None,
    missing_information: list[str] | None = None,
) -> dict[str, Any]:
    current_target = state.get("nextQuestionTarget") or {}
    current_target_type = current_target.get("targetType") or current_target.get("kind")
    result_field_id = current_target.get("targetId") if current_target_type == "field" else None
    result_field = state.get("fieldStates", {}).get(result_field_id or "", {})
    completion = evaluate_completion(
        state,
        _effective_profile(state, "system_requirement"),
        fields,
    )
    return {
        "status": status,
        "action": action,
        "reply": reply,
        "question": dict(question) if question else None,
        "completedFieldId": result_field_id if result_field.get("answerState") == "CONFIRMED" else None,
        "currentFieldId": question.get("fieldId") if question else None,
        "answerSummary": None,
        "recordAnswer": result_field.get("recordAnswer"),
        "missingInformation": missing_information or [],
        "used_tools": [],
        "assistantMessage": dict(assistant_message) if assistant_message else None,
        "interviewState": dict(state),
        "structuredDraft": _build_structured_draft(state, fields),
        "messages": [dict(message) for message in messages],
        "nextQuestionTarget": dict(state.get("nextQuestionTarget") or {}) or None,
        "retrievalPolicy": str((question or {}).get("retrievalPolicy") or "auto"),
        "retrievalExecuted": bool((question or {}).get("retrievedSources")),
        "retrievedSources": [
            dict(source)
            for source in ((question or {}).get("retrievedSources") or [])
        ],
        "completionStatus": "completed" if completion["complete"] else "in_progress",
        "missingRequiredTargets": completion["missingRequiredTargets"],
    }


def _build_structured_draft(
    state: Mapping[str, Any],
    fields: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    field_names = {
        str(field.get("id")): str(field.get("name") or field.get("id"))
        for field in fields
        if field.get("id")
    }
    draft: dict[str, str] = {}
    for field_id, field_state in state.get("fieldStates", {}).items():
        if field_state.get("answerState") == "CONFIRMED" and field_state.get("recordAnswer"):
            draft[field_names.get(field_id, field_id)] = str(field_state["recordAnswer"])
    for requirement_state in state.get("requirementStates", {}).values():
        if requirement_state.get("status") == "CONFIRMED" and requirement_state.get("value"):
            label = str(requirement_state.get("label") or requirement_state.get("requirementId"))
            draft[label] = str(requirement_state["value"])
    return draft


def _save_assistant_message(
    user: UserContext,
    record_id: str,
    content: str,
    question: Mapping[str, Any],
) -> dict[str, Any]:
    message = {
        "id": f"msg-{uuid4().hex[:12]}",
        "tenantId": user.tenant_id,
        "recordId": record_id,
        "content": content,
        "role": "assistant",
        "isActualUtterance": True,
        "createdAt": utc_now(),
        "updatedAt": utc_now(),
        "questionId": question.get("questionId"),
        "questionType": "structured",
        "fieldId": question.get("fieldId"),
        "targetType": question.get("targetType"),
        "targetId": question.get("targetId"),
        "targetLabel": question.get("targetLabel"),
        "candidateSource": question.get("candidateSource"),
        "candidateValue": question.get("candidateValue"),
        "candidateSourceIds": list(question.get("candidateSourceIds") or []),
        "retrievedSources": [
            dict(source)
            for source in (question.get("retrievedSources") or [])
        ],
    }
    store.upsert("messages", message)
    return message


def _save_newly_confirmed_field_messages(
    *,
    record: Mapping[str, Any],
    state: Mapping[str, Any],
    previous_confirmed_field_ids: set[str],
    question: Mapping[str, Any],
    user: UserContext,
) -> None:
    for field_id, field_state in state.get("fieldStates", {}).items():
        if field_id in previous_confirmed_field_ids or field_state.get("answerState") != "CONFIRMED":
            continue
        content = str(field_state.get("recordAnswer") or "").strip()
        if not content:
            continue
        answer_question_id = _latest_field_question_id(state, field_id) or question.get("questionId")
        message = {
            "id": f"structured-confirmed-msg-{record['id']}-{field_id}",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "content": content,
            "role": "user",
            "isActualUtterance": False,
            "messageType": "confirmed_answer",
            "turnType": "ANSWER",
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
            "answerToQuestionId": answer_question_id,
            "answerToFieldId": field_id,
            "questionType": "structured",
            "confirmedSource": field_state.get("confirmedSource"),
            "confirmedSourceIds": list(field_state.get("confirmedSourceIds") or []),
        }
        store.upsert("messages", message)


def _latest_field_question_id(state: Mapping[str, Any], field_id: str) -> str | None:
    for asked_question in reversed(state.get("askedQuestions", [])):
        if asked_question.get("fieldId") != field_id:
            continue
        question_id = str(asked_question.get("questionId") or "").strip()
        if question_id:
            return question_id
    return None


def _persist_state(state: dict[str, Any], user: UserContext) -> None:
    state["stateVersion"] = int(state.get("stateVersion", 0) or 0) + 1
    state["updatedByUserId"] = user.user_id
    state["updatedAt"] = utc_now()
    store.upsert("interview_states", state)


def _attach_proposal_message_id(
    state: dict[str, Any],
    *,
    target: Mapping[str, Any],
    assistant_message_id: str,
) -> None:
    if not assistant_message_id:
        return
    target_type = target.get("targetType") or target.get("kind")
    target_id = str(target.get("targetId") or "")
    if target_type == "field":
        target_state = state.get("fieldStates", {}).get(target_id)
    else:
        target_state = state.get("requirementStates", {}).get(target_id)
    if target_state and target_state.get("candidateSource") == "assistant_proposal":
        target_state["candidateProposalMessageId"] = assistant_message_id
