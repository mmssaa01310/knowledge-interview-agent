from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from threading import Lock
from time import monotonic
from typing import Any

from ai_interviewer_api.agents.interview_knowledge.coordinator import (
    accept_no_answer,
    apply_document_candidate,
    apply_structured_output,
    build_initial_structured_state,
    clear_probe,
    confirm_closing_answer,
    confirm_tentative_target,
    evaluate_completion,
    is_current_question_confirmation_target,
    register_probe,
    process_patch_validation_errors,
    record_interpretation_assessment,
    resolve_profile,
    select_next_question_target,
    stage_transcript_correction,
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
    localized_interview_confirmation_clarification_prompt,
    localized_interview_hesitation_prompt,
    localized_interview_confirmation_question,
    localized_interview_document_confirmation_question,
    localized_interview_incomplete_prompt,
    localized_interview_fallbacks,
    localized_interview_proposal_question,
    localized_interview_question_help,
    localized_interview_transcript_confirmation_question,
    localized_interview_transcript_retry,
    localized_interview_unanswerable_prompt,
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
_STRUCTURED_INTERVIEW_LOCKS: dict[str, Lock] = {}
_STRUCTURED_INTERVIEW_LOCKS_GUARD = Lock()


def generate_structured_interview_result(
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
    *,
    persist_assistant_messages: bool = True,
    provider: StructuredInterviewProvider | None = None,
) -> dict[str, Any]:
    """Run one record's Structured Interview turn serially.

    Reconnects and duplicate client submissions can otherwise observe the
    same unprocessed message and invoke Question Generator twice. The lock is
    scoped to a record so unrelated interviews continue concurrently.
    """

    record_id = str(record.get("id") or "")
    with _STRUCTURED_INTERVIEW_LOCKS_GUARD:
        lock = _STRUCTURED_INTERVIEW_LOCKS.setdefault(record_id, Lock())
    with lock:
        return _generate_structured_interview_result(
            record,
            knowledge,
            user,
            persist_assistant_messages=persist_assistant_messages,
            provider=provider,
        )


def _generate_structured_interview_result(
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
        completion = evaluate_completion(state, profile, fields)
        if completion["complete"]:
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
        logger.warning(
            "structured_completed_state_reopened record_id=%s missing_required_targets=%s "
            "unknown_applicability=%s",
            record.get("id"),
            [
                str(item.get("targetId") or item.get("label") or "")
                for item in completion["missingRequiredTargets"]
            ],
            completion["unknownApplicabilityTopics"],
        )
        state["status"] = "in_progress"
        state["currentFieldId"] = None
        state["currentQuestionId"] = None
        state["nextQuestionTarget"] = None
        _persist_state(state, user)

    current_question = _get_current_question(state)
    if _repair_current_confirmation_question(state, locale=interview_locale):
        _persist_state(state, user)
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
            valid_evidence_ids = {
                str(message.get("id"))
                for message in messages
                if message.get("id")
            }
            output, selected_reasoning_effort = _repair_invalid_process_patch(
                output=output,
                provider=structured_provider,
                profile=profile,
                state=state,
                context=interpreter_context,
                record_id=str(record.get("id") or ""),
                valid_evidence_ids=valid_evidence_ids,
                selected_reasoning_effort=selected_reasoning_effort,
            )
            latest_message_id = str(latest_user_message.get("id") or "")
            raw_transcript = str(
                latest_user_message.get("rawTranscript")
                or latest_user_message.get("content")
                or ""
            ).strip()
            pending_transcript_confirmation = isinstance(
                state.get("pendingTranscriptConfirmation"),
                Mapping,
            )
            effective_completeness = _effective_utterance_completeness(
                output,
                raw_transcript,
            )
            if effective_completeness != output.utteranceCompleteness:
                output = output.model_copy(
                    update={"utteranceCompleteness": effective_completeness}
                )
            transcript_assessment_status = output.transcriptAssessment.correctionStatus
            if (
                transcript_assessment_status == "CORRECTED"
                and _has_ambiguous_correction_candidates(output)
            ):
                # The backend must not turn a non-unique provider proposal into
                # a confirmation question. Keep the raw transcript and request
                # a re-utterance until there is one grounded candidate.
                output = output.model_copy(
                    update={
                        "transcriptAssessment": output.transcriptAssessment.model_copy(
                            update={"correctionStatus": "UNCERTAIN"}
                        )
                    }
                )
                transcript_assessment_status = "UNCERTAIN"
            if (
                transcript_assessment_status == "CORRECTED"
                and not output.transcriptAssessment.normalizedTranscript.strip()
            ):
                # A correction without a candidate is an unsafe provider
                # response. Normalize it before any branch can apply updates.
                output = output.model_copy(
                    update={
                        "transcriptAssessment": output.transcriptAssessment.model_copy(
                            update={"correctionStatus": "UNCERTAIN"}
                        )
                    }
                )
                transcript_assessment_status = "UNCERTAIN"
            if transcript_assessment_status == "UNCERTAIN":
                return _keep_current_question(
                    record=record,
                    state=state,
                    messages=messages,
                    fields=fields,
                    user=user,
                    latest_user_message=latest_user_message,
                    latest_message_id=latest_message_id,
                    output=output,
                    model_id=model_id,
                    reasoning_effort=selected_reasoning_effort,
                    raw_transcript=raw_transcript,
                    current_question=current_question,
                    reply=localized_interview_transcript_retry(interview_locale),
                )
            if output.dialogueAct in {"QUESTION_TO_ASSISTANT", "CLARIFICATION_REQUEST"} and not _has_structured_updates(output):
                return _keep_current_question(
                    record=record,
                    state=state,
                    messages=messages,
                    fields=fields,
                    user=user,
                    latest_user_message=latest_user_message,
                    latest_message_id=latest_message_id,
                    output=output,
                    model_id=model_id,
                    reasoning_effort=selected_reasoning_effort,
                    raw_transcript=raw_transcript,
                    current_question=current_question,
                    reply=localized_interview_question_help(
                        interview_locale,
                        str(
                            current_question.get("targetLabel")
                            or current_question.get("label")
                            or "この項目"
                        ),
                    ),
                )
            if output.dialogueAct in {"HESITATION", "BACKCHANNEL", "OTHER"}:
                # These acts contain no answer for the active target. Do not
                # create another question for the same target merely because
                # the latest user message has now been consumed.
                return _keep_current_question(
                    record=record,
                    state=state,
                    messages=messages,
                    fields=fields,
                    user=user,
                    latest_user_message=latest_user_message,
                    latest_message_id=latest_message_id,
                    output=output,
                    model_id=model_id,
                    reasoning_effort=selected_reasoning_effort,
                    raw_transcript=raw_transcript,
                    current_question=current_question,
                    reply=localized_interview_hesitation_prompt(interview_locale),
                )
            if (
                output.dialogueAct == "CONFIRMATION"
                and is_current_question_confirmation_target(state, current_question)
                and not is_unambiguous_confirmation(raw_transcript, locale=interview_locale)
            ):
                # A qualified reply must not promote a candidate merely because
                # the interpreter labeled it CONFIRMATION. The user needs a
                # chance to state the correction before the candidate changes.
                return _keep_current_question(
                    record=record,
                    state=state,
                    messages=messages,
                    fields=fields,
                    user=user,
                    latest_user_message=latest_user_message,
                    latest_message_id=latest_message_id,
                    output=output,
                    model_id=model_id,
                    reasoning_effort=selected_reasoning_effort,
                    raw_transcript=raw_transcript,
                    current_question=current_question,
                    reply=localized_interview_confirmation_clarification_prompt(interview_locale),
                )
            if effective_completeness != "COMPLETE" or output.answerAssessment.sufficiency == "INCOMPLETE":
                apply_structured_output(
                    state,
                    output,
                    latest_message_id=latest_message_id,
                    raw_transcript=raw_transcript,
                    fields=fields,
                    profile=profile,
                    valid_evidence_ids=valid_evidence_ids,
                    current_question=current_question,
                )
                _persist_transcript_assessment(
                    latest_user_message,
                    state.get("lastTranscriptAssessment"),
                )
                state["lastStructuredOutput"] = output.model_dump()
                state["lastStructuredModelId"] = model_id
                state["lastStructuredReasoningEffort"] = selected_reasoning_effort
                _persist_state(state, user)
                messages = _replace_message(
                    messages,
                    latest_message_id,
                    latest_user_message,
                )
                return _build_result(
                    record=record,
                    state=state,
                    messages=messages,
                    fields=fields,
                    reply=(
                        localized_interview_incomplete_prompt(interview_locale)
                        if effective_completeness == "INCOMPLETE"
                        else localized_interview_transcript_retry(interview_locale)
                    ),
                    question=current_question,
                    action="ask_follow_up",
                    status="in_progress",
                )

            staged_transcript_correction = False
            if transcript_assessment_status == "CORRECTED":
                staged_transcript_correction = stage_transcript_correction(
                    state,
                    output,
                    latest_message_id=latest_message_id,
                    raw_transcript=raw_transcript,
                    fields=fields,
                    valid_evidence_ids=valid_evidence_ids,
                    current_question=current_question,
                )
                if not staged_transcript_correction:
                    record_interpretation_assessment(
                        state,
                        output,
                        latest_message_id=latest_message_id,
                        raw_transcript=raw_transcript,
                    )
                    _persist_transcript_assessment(
                        latest_user_message,
                        state.get("lastTranscriptAssessment"),
                    )
                    state["lastStructuredOutput"] = output.model_dump()
                    state["lastStructuredModelId"] = model_id
                    state["lastStructuredReasoningEffort"] = selected_reasoning_effort
                    _persist_state(state, user)
                    messages = _replace_message(
                        messages,
                        latest_message_id,
                        latest_user_message,
                    )
                    return _build_result(
                        record=record,
                        state=state,
                        messages=messages,
                        fields=fields,
                        reply=localized_interview_transcript_retry(interview_locale),
                        question=current_question,
                        action="ask_follow_up",
                        status="in_progress",
                    )

            keep_current_question_for_unanswerable = False
            if output.dialogueAct in {
                "ANSWER",
                "CORRECTION",
                "REJECTION",
                "CONFIRMATION",
            } or _has_structured_updates(output):
                if current_question.get("targetType") == "applicability_overview":
                    state["applicabilityOverviewAsked"] = True
                if not staged_transcript_correction:
                    current_target = _target_from_question(current_question)
                    answer_sufficiency = output.answerAssessment.sufficiency
                    if answer_sufficiency in {"UNANSWERABLE", "REFUSAL"}:
                        output_to_apply = output.model_copy(
                            update={"fieldUpdates": [], "requirementUpdates": []}
                        )
                    elif answer_sufficiency != "SUFFICIENT":
                        output_to_apply = _downgrade_current_target_update(
                            output,
                            current_target,
                        )
                    else:
                        output_to_apply = output
                    apply_structured_output(
                        state,
                        output_to_apply,
                        latest_message_id=latest_message_id,
                        raw_transcript=raw_transcript,
                        fields=fields,
                        profile=profile,
                        valid_evidence_ids=valid_evidence_ids,
                        current_question=current_question,
                    )
                    if _should_implicitly_confirm_tentative_target(
                        output,
                        tentative_target_before,
                        current_question,
                    ):
                        confirm_tentative_target(state, tentative_target_before)
                    if answer_sufficiency in {"UNANSWERABLE", "REFUSAL"}:
                        follow_up_count = _follow_up_count(state, current_target)
                        if follow_up_count >= 1:
                            accept_no_answer(
                                state,
                                current_target,
                                transcript=str(
                                    state.get("lastTranscriptAssessment", {}).get(
                                        "normalizedTranscript"
                                    )
                                    or raw_transcript
                                ),
                                message_id=latest_message_id,
                                valid_evidence_ids=valid_evidence_ids,
                            )
                        else:
                            register_probe(
                                state,
                                target=current_target,
                                probe_type=output.answerAssessment.probeType,
                            )
                            keep_current_question_for_unanswerable = True
                    elif answer_sufficiency != "SUFFICIENT":
                        register_probe(
                            state,
                            target=current_target,
                            probe_type=output.answerAssessment.probeType,
                        )
                    else:
                        clear_probe(state, current_target)
                    _save_newly_confirmed_field_messages(
                        record=record,
                        state=state,
                        previous_confirmed_field_ids=confirmed_field_ids_before,
                        question=current_question,
                        user=user,
                    )
                    _persist_transcript_assessment(
                        latest_user_message,
                        state.get("lastTranscriptAssessment"),
                    )
                else:
                    _persist_transcript_assessment(
                        latest_user_message,
                        state.get("lastTranscriptAssessment"),
                    )
            else:
                apply_structured_output(
                    state,
                    output,
                    latest_message_id=latest_message_id,
                    raw_transcript=raw_transcript,
                    fields=fields,
                    profile=profile,
                    valid_evidence_ids=valid_evidence_ids,
                    current_question=current_question,
                )
                state["lastStructuredDialogueAct"] = output.dialogueAct
            if (
                current_question.get("targetType") == "closing"
                and not staged_transcript_correction
                and output.answerAssessment.sufficiency
                in {"SUFFICIENT", "UNANSWERABLE", "REFUSAL"}
                and output.dialogueAct
                not in {"QUESTION_TO_ASSISTANT", "CLARIFICATION_REQUEST"}
            ):
                assessment = state.get("lastTranscriptAssessment")
                normalized = (
                    assessment.get("normalizedTranscript")
                    if isinstance(assessment, Mapping)
                    else None
                )
                confirm_closing_answer(
                    state,
                    transcript=raw_transcript,
                    message_id=latest_message_id,
                    normalized_transcript=str(normalized or raw_transcript),
                    valid_evidence_ids=valid_evidence_ids,
                )
            if (
                pending_transcript_confirmation
                and output.dialogueAct == "REJECTION"
                and not staged_transcript_correction
            ):
                state["lastStructuredOutput"] = output.model_dump()
                state["lastStructuredModelId"] = model_id
                state["lastStructuredReasoningEffort"] = selected_reasoning_effort
                _persist_state(state, user)
                messages = _replace_message(
                    messages,
                    latest_message_id,
                    latest_user_message,
                )
                retry_question = _get_current_question(state) or current_question
                return _build_result(
                    record=record,
                    state=state,
                    messages=messages,
                    fields=fields,
                    reply=localized_interview_transcript_retry(interview_locale),
                    question=retry_question,
                    action="ask_follow_up",
                    status="in_progress",
                )
            messages = _replace_message(
                messages,
                latest_message_id,
                latest_user_message,
            )
            state["lastStructuredOutput"] = output.model_dump()
            state["lastStructuredModelId"] = model_id
            state["lastStructuredReasoningEffort"] = selected_reasoning_effort
            if keep_current_question_for_unanswerable and current_question.get("targetType") != "closing":
                _persist_state(state, user)
                return _build_result(
                    record=record,
                    state=state,
                    messages=messages,
                    fields=fields,
                    reply=localized_interview_unanswerable_prompt(
                        interview_locale,
                        str(
                            current_question.get("targetLabel")
                            or current_question.get("label")
                            or "この項目"
                        ),
                    ),
                    question=current_question,
                    action="ask_follow_up",
                    status="in_progress",
                )
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
    if target.get("targetType") == "closing":
        state["closingState"] = "ASKING"
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


def _keep_current_question(
    *,
    record: Mapping[str, Any],
    state: dict[str, Any],
    messages: Sequence[Mapping[str, Any]],
    fields: Sequence[Mapping[str, Any]],
    user: UserContext,
    latest_user_message: Mapping[str, Any],
    latest_message_id: str,
    output: StructuredInterviewOutput,
    model_id: str,
    reasoning_effort: str,
    raw_transcript: str,
    current_question: Mapping[str, Any],
    reply: str,
) -> dict[str, Any]:
    """Persist a non-answer without replacing the active question.

    Fillers, question-help requests, qualified confirmations, and unsafe STT
    results must consume the user turn for idempotency while preserving the
    same question ID and target.  They intentionally skip target selection,
    retrieval, and Question Generator.
    """

    record_interpretation_assessment(
        state,
        output,
        latest_message_id=latest_message_id,
        raw_transcript=raw_transcript,
    )
    _persist_transcript_assessment(
        latest_user_message,
        state.get("lastTranscriptAssessment"),
    )
    state["lastStructuredOutput"] = output.model_dump()
    state["lastStructuredDialogueAct"] = output.dialogueAct
    state["lastStructuredModelId"] = model_id
    state["lastStructuredReasoningEffort"] = reasoning_effort
    _persist_state(state, user)
    updated_messages = _replace_message(messages, latest_message_id, latest_user_message)
    return _build_result(
        record=record,
        state=state,
        messages=updated_messages,
        fields=fields,
        reply=reply,
        question=current_question,
        action="ask_follow_up",
        status="in_progress",
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
            state[key] = (
                "CONFIRMED"
                if key == "closingState" and state.get("status") == "completed"
                else value
            )
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
    if state.get("status") == "completed" and state.get("closingState") == "UNANSWERED":
        # States completed before the open-ended closing was introduced remain
        # completed; new in-progress states must still ask the closing question.
        state["closingState"] = "CONFIRMED"
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
        "latestUtterance": _latest_utterance_context(messages, current_question),
        "conversation": [
            {
                "id": message.get("id"),
                "role": message.get("role"),
                "content": message.get("content"),
                "rawTranscript": message.get("rawTranscript"),
                "normalizedTranscript": message.get("normalizedTranscript"),
                "correctionStatus": message.get("correctionStatus"),
                "correctionCandidates": message.get("correctionCandidates"),
                "questionId": message.get("questionId"),
                "answerToQuestionId": message.get("answerToQuestionId"),
                "sttConfidence": message.get("sttConfidence"),
            }
            for message in messages[-30:]
            if message.get("isActualUtterance") is not False
        ],
    }


def _latest_utterance_context(
    messages: Sequence[Mapping[str, Any]],
    current_question: Mapping[str, Any],
) -> dict[str, Any]:
    latest = _latest_answer_message(messages, current_question) or {}
    raw = str(latest.get("rawTranscript") or latest.get("content") or "").strip()
    return {
        "messageId": latest.get("id"),
        "rawTranscript": raw,
        "normalizedTranscript": latest.get("normalizedTranscript"),
        "correctionStatus": latest.get("correctionStatus") or "NONE",
        "correctionCandidates": list(latest.get("correctionCandidates") or []),
        "sttConfidence": latest.get("sttConfidence"),
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


_PROCESS_PATCH_OPERATION_NAMES: tuple[str, ...] = (
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


def _has_process_patch_operations(patch: Any) -> bool:
    return any(getattr(patch, name) for name in _PROCESS_PATCH_OPERATION_NAMES)


def _process_patch_repair_is_allowed(
    state: Mapping[str, Any],
    output: StructuredInterviewOutput,
    profile: InterviewProfile,
    valid_evidence_ids: set[str],
) -> bool:
    if profile == "business_process":
        return True
    if profile != "system_requirement":
        return False
    process_updates = [
        update
        for update in output.applicability
        if update.topic == "process"
        and set(update.evidenceTranscriptIds).issubset(valid_evidence_ids)
        and update.evidenceTranscriptIds
    ]
    if any(update.status == "not_applicable" for update in process_updates):
        return False
    return bool(
        state.get("applicabilityState", {}).get("process", {}).get("status") == "present"
        or any(update.status == "present" for update in process_updates)
    )


def _process_patch_validation_errors_for_state(
    state: Mapping[str, Any],
    patch: Any,
    valid_evidence_ids: set[str],
) -> list[str]:
    if not _has_process_patch_operations(patch):
        return []
    process_state = state.get("processState") or {}
    errors = process_patch_validation_errors(
        process_state,
        patch,
        valid_evidence_ids=valid_evidence_ids,
    )
    current_version = int(process_state.get("version", state.get("processVersion", 0)) or 0)
    if patch.baseProcessVersion != current_version:
        errors.insert(
            0,
            f"base_process_version_mismatch:{patch.baseProcessVersion}:{current_version}",
        )
    return list(dict.fromkeys(errors))


def _repair_invalid_process_patch(
    *,
    output: StructuredInterviewOutput,
    provider: StructuredInterviewProvider,
    profile: InterviewProfile,
    state: Mapping[str, Any],
    context: Mapping[str, Any],
    record_id: str,
    valid_evidence_ids: set[str],
    selected_reasoning_effort: str,
) -> tuple[StructuredInterviewOutput, str]:
    """Repair one rejected AI patch without reapplying other extracted values."""

    initial_errors = _process_patch_validation_errors_for_state(
        state,
        output.processPatch,
        valid_evidence_ids,
    )
    if not initial_errors or not _process_patch_repair_is_allowed(
        state,
        output,
        profile,
        valid_evidence_ids,
    ):
        return output, selected_reasoning_effort

    logger.warning(
        "structured_process_patch_validation_failed record_id=%s errors=%s",
        record_id,
        initial_errors,
    )
    repair_context = {
        **context,
        "processPatchRepair": {
            "previousProcessPatch": output.processPatch.model_dump(),
            "validationErrors": initial_errors,
        },
    }
    logger.info(
        "structured_process_patch_repair_started record_id=%s reasoning_effort=%s",
        record_id,
        settings.structured_interview_medium_reasoning_effort,
    )
    try:
        repaired_output = provider.interpret(
            profile=profile,
            context=repair_context,
            reasoning_effort=settings.structured_interview_medium_reasoning_effort,
        )
    except Exception:
        logger.exception("structured_process_patch_repair_failed record_id=%s", record_id)
        return output, selected_reasoning_effort

    repaired_errors = _process_patch_validation_errors_for_state(
        state,
        repaired_output.processPatch,
        valid_evidence_ids,
    )
    if not _has_process_patch_operations(repaired_output.processPatch):
        repaired_errors.append("empty_repaired_process_patch")
    if repaired_errors:
        logger.warning(
            "structured_process_patch_repair_rejected record_id=%s errors=%s",
            record_id,
            list(dict.fromkeys(repaired_errors)),
        )
        return output, selected_reasoning_effort

    logger.info(
        "structured_process_patch_repaired record_id=%s reasoning_effort=%s",
        record_id,
        settings.structured_interview_medium_reasoning_effort,
    )
    return (
        output.model_copy(update={"processPatch": repaired_output.processPatch}),
        settings.structured_interview_medium_reasoning_effort,
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
    pending_transcript = state.get("pendingTranscriptConfirmation")
    if (
        target.get("targetType") == "transcript_confirmation"
        and isinstance(pending_transcript, Mapping)
    ):
        # A correction confirmation is a backend-owned safety step. Do not
        # invoke Question Generator or retrieve unrelated context for it.
        return (
            localized_interview_transcript_confirmation_question(
                resolve_interview_locale(record, knowledge),
                str(pending_transcript.get("normalizedTranscript") or ""),
            ),
            [],
            None,
        )
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
        "answerAssessment": state.get("lastAnswerAssessment"),
        "activeProbe": state.get("activeProbeTarget"),
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
    question_text = _sanitize_question_text(generated.questionText)
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
        and candidate_source == "assistant_proposal"
    ):
        return (
            localized_interview_proposal_question(
                resolve_interview_locale(record, knowledge),
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
    if target_type == "transcript_confirmation":
        pending = state.get("pendingTranscriptConfirmation")
        if not isinstance(pending, Mapping):
            return False
        question_text = localized_interview_transcript_confirmation_question(
            locale,
            str(pending.get("normalizedTranscript") or ""),
        )
        if str(current_question.get("text") or "") == question_text:
            return False
        current_question["text"] = question_text
        return True
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
    if not is_pending or not candidate_value:
        return False

    if candidate_source == "assistant_proposal":
        question_text = localized_interview_proposal_question(locale, candidate_value)
    elif candidate_source == "document_reference":
        question_text = localized_interview_document_confirmation_question(
            locale,
            str(current_question.get("targetLabel") or current_question.get("label") or "").strip(),
            candidate_value,
        )
    elif _contains_question_candidate(str(current_question.get("text") or ""), candidate_value):
        return False
    else:
        question_text = localized_interview_confirmation_question(locale, candidate_value)

    if str(current_question.get("text") or "") == question_text:
        return False
    current_question["text"] = question_text
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


def _sanitize_question_text(value: str) -> str:
    """Keep one generated question and discard duplicated lead-in prose."""

    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    sentences = [part.strip() for part in re.split(r"(?<=[。！？!?])\s*", text) if part.strip()]
    question_sentences = [
        sentence
        for sentence in sentences
        if (
            "？" in sentence
            or "?" in sentence
            or re.search(
                r"(?:教えてください|お聞かせください|伺えますか|ありますか|ですか|でしょうか|ますか)[。！？!?]?$",
                sentence,
            )
        )
    ]
    if question_sentences and len(sentences) > 1:
        # A theme explanation followed by the actual question is not a
        # two-part reply. Keep the question itself as the sole utterance.
        return question_sentences[-1]
    return text


def _get_structured_provider(
    provider: StructuredInterviewProvider | None,
    *,
    model_id: str,
) -> StructuredInterviewProvider:
    if provider is not None:
        return provider
    return BedrockResponsesStructuredProvider(model_id=model_id)


def _target_from_question(question: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not question:
        return None
    target_type = str(question.get("targetType") or question.get("kind") or "").strip()
    target_id = str(question.get("targetId") or "").strip()
    if not target_type or not target_id:
        return None
    return {
        "targetType": target_type,
        "targetId": target_id,
        "label": str(question.get("targetLabel") or question.get("label") or target_id),
    }


def _follow_up_count(
    state: Mapping[str, Any],
    target: Mapping[str, Any] | None,
) -> int:
    if not target:
        return 0
    target_type = str(target.get("targetType") or target.get("kind") or "")
    target_id = str(target.get("targetId") or "")
    counts = state.get("followUpCounts") or {}
    if not isinstance(counts, Mapping):
        return 0
    return int(counts.get(f"{target_type}:{target_id}", counts.get(target_id, 0)) or 0)


def _downgrade_current_target_update(
    output: StructuredInterviewOutput,
    current_target: Mapping[str, Any] | None,
) -> StructuredInterviewOutput:
    """Prevent an insufficient current answer from being auto-confirmed."""

    if current_target is None:
        return output
    target_type = str(current_target.get("targetType") or current_target.get("kind") or "")
    target_id = str(current_target.get("targetId") or "")
    field_updates = [
        update.model_copy(update={"answerResolution": "TENTATIVE"})
        if target_type == "field" and update.fieldId == target_id
        else update
        for update in output.fieldUpdates
    ]
    requirement_updates = [
        update.model_copy(update={"answerResolution": "TENTATIVE"})
        if target_type in {"requirement", "process"} and update.requirementId == target_id
        else update
        for update in output.requirementUpdates
    ]
    return output.model_copy(
        update={
            "fieldUpdates": field_updates,
            "requirementUpdates": requirement_updates,
        }
    )


def _effective_utterance_completeness(
    output: StructuredInterviewOutput,
    transcript: str,
) -> str:
    """Apply a small syntactic safety floor below the model judgement.

    The Structured Interpreter remains the primary decision maker. This guard
    only catches an unmistakable trailing conjunction/verb stem when a model
    accidentally reports COMPLETE, so endpoint timing cannot advance the
    interview on text such as ``担当し`` or ``関わっ``.
    """

    if output.utteranceCompleteness != "COMPLETE":
        return output.utteranceCompleteness
    if output.transcriptAssessment.correctionStatus == "CORRECTED":
        corrected = output.transcriptAssessment.normalizedTranscript.strip()
        if corrected and not _looks_like_incomplete_utterance(corrected):
            # The raw final can itself be truncated by STT. A complete,
            # explicit correction remains confirmation-only, so accepting the
            # correction here cannot commit or advance the interview.
            return "COMPLETE"
    if _looks_like_incomplete_utterance(transcript):
        return "INCOMPLETE"
    return "COMPLETE"


def _has_ambiguous_correction_candidates(output: StructuredInterviewOutput) -> bool:
    candidates = {
        str(candidate).strip()
        for candidate in output.transcriptAssessment.correctionCandidates
        if str(candidate).strip()
    }
    return len(candidates) > 1


def _looks_like_incomplete_utterance(transcript: str) -> bool:
    value = re.sub(r"[\s。、！？!?.,…]+$", "", transcript.strip())
    if len(value) < 2:
        return False
    hesitation = re.sub(r"[\s、。！？!?.,…]+", "", value)
    if re.fullmatch(
        r"(?:えーっと|えーと|えっと|ええと|えー|あー|あの|その|うーん|うー)+",
        hesitation,
    ):
        return True
    if re.search(
        r"(?:し|っ|ですが|なので|けど|けれど|例えば|まず|それから|というか|担当しているのは|担当して|関わって|携わって|行って|私の場合は|と|や|も|は|が|を|に|で|の|から|まで|主に)$",
        value,
    ):
        return True
    return bool(re.search(r"(?:and|or|but|because|to|which|that|with|for)$", value.casefold()))


def _persist_transcript_assessment(
    message: Mapping[str, Any],
    assessment: Mapping[str, Any] | None,
) -> None:
    if not assessment:
        return
    message_id = str(message.get("id") or "")
    if not message_id:
        return
    stored = store.get("messages", message_id)
    if stored is None:
        return
    stored.update(
        {
            "rawTranscript": assessment.get("rawTranscript") or stored.get("content") or "",
            "normalizedTranscript": assessment.get("normalizedTranscript") or "",
            "correctionStatus": assessment.get("correctionStatus") or "NONE",
            "correctionCandidates": list(assessment.get("correctionCandidates") or []),
            "correctionReason": assessment.get("correctionReason"),
            "updatedAt": utc_now(),
        }
    )
    store.upsert("messages", stored)
    if isinstance(message, dict):
        message.update(stored)


def _replace_message(
    messages: Sequence[Mapping[str, Any]],
    message_id: str,
    message: Mapping[str, Any],
) -> list[dict[str, Any]]:
    return [
        dict(message) if str(item.get("id") or "") == message_id else dict(item)
        for item in messages
    ]


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
    question_id = str(question.get("questionId") or "")
    message = {
        # A question is a durable state transition. A deterministic message
        # ID makes retries/reconnects idempotent even when the caller did not
        # supply a client message ID.
        "id": f"structured-question-msg-{record_id}-{question_id}",
        "tenantId": user.tenant_id,
        "recordId": record_id,
        "content": content,
        "role": "assistant",
        "isActualUtterance": True,
        "createdAt": utc_now(),
        "updatedAt": utc_now(),
        "questionId": question_id,
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
