from __future__ import annotations

import json
import logging
import re
from concurrent.futures import Future, ThreadPoolExecutor
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from threading import Event, Lock
from time import monotonic, time
from typing import Any

from ai_interviewer_api.agents.interview_knowledge.coordinator import (
    accept_no_answer,
    apply_document_candidate,
    apply_structured_output,
    build_initial_structured_state,
    clear_probe,
    complete_clarification_request,
    confirm_closing_answer,
    confirm_tentative_target,
    evaluate_completion,
    enqueue_clarification_request,
    is_current_question_confirmation_target,
    register_probe,
    process_patch_validation_errors,
    record_interpretation_assessment,
    resolve_profile,
    select_optional_deepening_target,
    select_next_question_target,
    stage_transcript_correction,
    sync_structured_state_fields,
    target_key,
)
from ai_interviewer_api.agents.interview_knowledge.provider import (
    BedrockResponsesStructuredProvider,
    StructuredInterviewProvider,
)
from ai_interviewer_api.agents.interview_knowledge.fast_interpreter.provider import (
    BedrockFastInterpreterProvider,
    FastInterpreterProvider,
)
from ai_interviewer_api.agents.interview_knowledge.fast_interpreter.schemas import (
    FastAnswerAssessment,
)
from ai_interviewer_api.agents.interview_knowledge.fast_interpreter.service import (
    build_fast_interpreter_context,
    can_proceed,
)
from ai_interviewer_api.agents.interview_knowledge.schemas import (
    InterviewProfile,
    ProcessPatch,
    StructuredDialogueAct,
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
from ai_interviewer_api.services.conversation_policy import CanonicalAction
from ai_interviewer_api.services.interview_document_retrieval import (
    MAX_INTERVIEW_DOCUMENT_CONTEXT,
    SpeculativeInterviewRetrieval,
    build_interview_document_query,
    retrieve_interview_document_context,
    start_speculative_interview_document_retrieval,
    validate_document_question_candidate,
)
from ai_interviewer_api.services.interview_state_transition import (
    apply_background_state_proposal,
    commit_foreground_provisional_state,
    commit_interview_state,
)


STRUCTURED_PROFILES: frozenset[str] = frozenset({"fixed_form", "business_process", "system_requirement"})
logger = logging.getLogger(__name__)
_STRUCTURED_INTERVIEW_LOCKS: dict[str, Lock] = {}
_STRUCTURED_INTERVIEW_LOCKS_GUARD = Lock()
_FAST_INTERPRETER_EXECUTOR = ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="structured-fast-interpreter",
)
_FAST_BACKGROUND_EXECUTOR = ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="structured-background-validation",
)
_FAST_BACKGROUND_TAILS: dict[str, tuple[Future[Any], "_FastValidationJoin"]] = {}
_FAST_BACKGROUND_TAILS_GUARD = Lock()
_QUESTION_GENERATOR_RECENT_MESSAGE_LIMIT = 6
_LATENCY_METRIC_NAMES = (
    "interpreter_ms",
    "medium_retry_ms",
    "patch_repair_ms",
    "state_transition_ms",
    "retrieval_ms",
    "question_generation_ms",
)
_LATENCY_CALL_METRIC_NAMES = (
    "interpreter_calls",
    "medium_retry_calls",
    "patch_repair_calls",
    "retrieval_calls",
    "question_generation_calls",
)
_LATENCY_EVENT_METRIC_NAMES = (
    "interpreter_start_ms",
    "interpreter_end_ms",
    "rag_start_ms",
    "rag_end_ms",
    "question_llm_start_ms",
    "question_first_token_ms",
    "question_first_sentence_ms",
    "question_llm_end_ms",
    "fast_interpreter_start_ms",
    "fast_interpreter_end_ms",
    "background_interpreter_start_ms",
    "background_interpreter_end_ms",
)
_CANONICAL_QUESTION_DEFINITION_DYNAMIC_KEYS = frozenset(
    {"missingItems", "missingItemIds", "capturedItemIds"}
)


@dataclass(frozen=True)
class FastInterviewTurnResult:
    """Foreground result; the detailed validation is deliberately detached."""

    assessment: FastAnswerAssessment
    can_proceed: bool
    reply: str
    action: str
    question: dict[str, Any] | None
    retrieval_policy: str | None
    retrieval_executed: bool
    retrieved_sources: list[dict[str, Any]]
    latency_metrics: dict[str, Any]
    source_target_key: str
    needs_question_explanation: bool = False
    provisional_state: dict[str, Any] | None = None


class _FastValidationJoin:
    """Join Fast output and background output without delaying the Fast path."""

    def __init__(
        self,
        *,
        record_id: str,
        user: UserContext,
        source_turn_id: str,
        source_message_id: str,
        source_target: Mapping[str, Any] | None,
        knowledge: Mapping[str, Any],
        fast_can_proceed: bool,
        on_complete: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        self.record_id = record_id
        self.user = user
        self.source_turn_id = source_turn_id
        self.source_message_id = source_message_id
        self.source_target = deepcopy(dict(source_target or {}))
        self.knowledge = deepcopy(dict(knowledge))
        self.fast_can_proceed = fast_can_proceed
        self.on_complete = on_complete
        self._lock = Lock()
        self._background: dict[str, Any] | None = None
        self._provisional_ready = False
        self._provisional_question: dict[str, Any] | None = None
        self._completed = False
        self.reconciliation_event = Event()

    def set_fast_result(self, value: bool) -> None:
        with self._lock:
            self.fast_can_proceed = value

    def set_provisional_question(self, question: dict[str, Any] | None) -> None:
        with self._lock:
            self._provisional_ready = True
            self._provisional_question = deepcopy(question) if question else None
        self._try_complete()

    def set_background_result(self, result: dict[str, Any]) -> None:
        with self._lock:
            self._background = result
        self._try_complete()

    def _try_complete(self) -> None:
        with self._lock:
            if self._completed or not self._provisional_ready or self._background is None:
                return
            self._completed = True
            background = self._background
            provisional = deepcopy(self._provisional_question)
            fast_can_proceed = self.fast_can_proceed
        try:
            payload = _reconcile_fast_background_validation(
                record_id=self.record_id,
                user=self.user,
                source_turn_id=self.source_turn_id,
                source_message_id=self.source_message_id,
                source_target=self.source_target,
                knowledge=self.knowledge,
                fast_can_proceed=fast_can_proceed,
                provisional_question=provisional,
                background=background,
            )
        except Exception as exc:  # noqa: BLE001 - background failure is telemetry only
            logger.exception(
                "background_validation_reconciliation_failed source_turn_id=%s",
                self.source_turn_id,
            )
            payload = {
                "sourceTurnId": self.source_turn_id,
                "sourceMessageId": self.source_message_id,
                "sourceTargetKey": target_key(self.source_target),
                "backgroundStatus": "failed",
                "backgroundAgreesWithFast": False,
                "backgroundCanProceed": False,
                "clarificationEnqueued": False,
                "error": exc.__class__.__name__,
                "latencyMetrics": dict(background.get("backgroundLatencyMetrics") or {}),
            }
        finally:
            # Later voice turns may already be in their own Fast path. Do not
            # let their Background evaluator overtake this reconciliation.
            self.reconciliation_event.set()
        if self.on_complete is not None:
            try:
                self.on_complete(payload)
            except Exception:  # noqa: BLE001 - validation must not fail the voice turn
                logger.exception(
                    "structured_background_validation_callback_failed source_turn_id=%s",
                    self.source_turn_id,
                )


def _new_latency_metrics() -> dict[str, float]:
    return {
        **{name: 0.0 for name in _LATENCY_METRIC_NAMES},
        **{name: 0.0 for name in _LATENCY_CALL_METRIC_NAMES},
    }


def _elapsed_ms(started_at: float) -> float:
    return round((monotonic() - started_at) * 1000, 1)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _serialize_latency_metrics(metrics: Mapping[str, float]) -> dict[str, float | int]:
    serialized: dict[str, float | int] = {}
    for name in _LATENCY_METRIC_NAMES:
        serialized[name] = round(float(metrics.get(name, 0.0)), 1)
    for name in _LATENCY_CALL_METRIC_NAMES:
        serialized[name] = int(metrics.get(name, 0.0))
    for name in _LATENCY_EVENT_METRIC_NAMES:
        value = metrics.get(name)
        if value:
            serialized[name] = int(value)
    for name in (
        "speculative_retrieval_ms",
        "speculative_retrieval_wait_ms",
        "retrieval_reused",
        "retrieval_fallbacks",
    ):
        value = metrics.get(name)
        if value is not None:
            serialized[name] = int(value) if name.endswith(("reused", "fallbacks")) else round(float(value), 1)
    return serialized


def generate_structured_interview_result(
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
    *,
    persist_assistant_messages: bool = True,
    provider: StructuredInterviewProvider | None = None,
    speculative_retrieval: SpeculativeInterviewRetrieval | None = None,
    canonical_intent: StructuredDialogueAct | None = None,
    canonical_action: CanonicalAction | None = None,
    on_question_delta: Callable[[str], None] | None = None,
    persist_state: bool = True,
    state_override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one record's Structured Interview turn serially.

    Reconnects and duplicate client submissions can otherwise observe the
    same unprocessed message and invoke Question Generator twice. The lock is
    scoped to a record so unrelated interviews continue concurrently.
    """

    record_id = str(record.get("id") or "")
    lock = _structured_interview_lock(record_id)
    with lock:
        return _generate_structured_interview_result_locked(
            record,
            knowledge,
            user,
            persist_assistant_messages=persist_assistant_messages,
            provider=provider,
            speculative_retrieval=speculative_retrieval,
            canonical_intent=canonical_intent,
            canonical_action=canonical_action,
            on_question_delta=on_question_delta,
            defer_question_generation=False,
            persist_state=persist_state,
            state_override=state_override,
        )


def _structured_interview_lock(record_id: str) -> Lock:
    with _STRUCTURED_INTERVIEW_LOCKS_GUARD:
        return _STRUCTURED_INTERVIEW_LOCKS.setdefault(record_id, Lock())


def _generate_structured_interview_result_locked(
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
    *,
    persist_assistant_messages: bool,
    provider: StructuredInterviewProvider | None,
    speculative_retrieval: SpeculativeInterviewRetrieval | None,
    canonical_intent: StructuredDialogueAct | None = None,
    canonical_action: CanonicalAction | None = None,
    on_question_delta: Callable[[str], None] | None,
    defer_question_generation: bool,
    persist_state: bool = True,
    state_override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    started_at = monotonic()
    latency_metrics = _new_latency_metrics()
    result = _generate_structured_interview_result(
        record,
        knowledge,
        user,
        persist_assistant_messages=persist_assistant_messages,
        provider=provider,
        latency_metrics=latency_metrics,
        speculative_retrieval=speculative_retrieval,
        canonical_intent=canonical_intent,
        canonical_action=canonical_action,
        on_question_delta=on_question_delta,
        defer_question_generation=defer_question_generation,
        persist_state=persist_state,
        state_override=state_override,
    )
    structured_total_ms = _elapsed_ms(started_at)
    # The coordinator portion includes state reads/writes, validation,
    # target selection, and result construction.  The external calls are
    # measured separately so a slow turn can be attributed without
    # counting them twice.
    latency_metrics["state_transition_ms"] = max(
        0.0,
        structured_total_ms
        - latency_metrics["interpreter_ms"]
        - latency_metrics["medium_retry_ms"]
        - latency_metrics["patch_repair_ms"]
        - latency_metrics["retrieval_ms"]
        - latency_metrics["question_generation_ms"],
    )
    result["latencyMetrics"] = _serialize_latency_metrics(latency_metrics)
    return result


def start_speculative_retrieval_for_interview_turn(
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
) -> SpeculativeInterviewRetrieval | None:
    """Prefetch the current question's document context before interpretation.

    The current question is only a hint: ``_generate_question_text`` compares
    the final target-derived query before reusing the result.  This makes the
    prefetch useful for follow-ups that keep the same target while preventing a
    previous target's documents from leaking into a newly selected question.
    """

    fields = _list_interview_fields(knowledge, user)
    state = load_structured_interview_state(record, knowledge, user, fields=fields)
    current_question = _get_current_question(state)
    if not current_question:
        return None
    current_field = _field_for_target(
        _target_from_question(current_question) or {},
        fields,
    )
    retrieval_policy = _retrieval_policy_for_target(
        _target_from_question(current_question) or {},
        current_field,
    )
    return start_speculative_interview_document_retrieval(
        record=record,
        knowledge=knowledge,
        user=user,
        # Keep this query shape identical to _generate_question_text.  The
        # final generator deliberately omits the previous question text so a
        # reworded question cannot change retrieval semantics by accident.
        current_question=None,
        current_field=current_field,
        target=_target_from_question(current_question),
        state=state,
        messages=_list_record_messages(record, user),
        retrieval_policy=retrieval_policy,
    )


def start_fast_interview_turn(
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
    *,
    state: Mapping[str, Any],
    current_question: Mapping[str, Any],
    latest_user_message: Mapping[str, Any],
    provider: StructuredInterviewProvider | None = None,
    fast_provider: FastInterpreterProvider | None = None,
    on_background_validation: Callable[[dict[str, Any]], None] | None = None,
    on_question_delta: Callable[[str], None] | None = None,
) -> FastInterviewTurnResult:
    """Run the provisional voice path and detailed validation concurrently.

    Only the Fast result is awaited on the foreground path. The background
    future uses the existing Structured Interview service with question
    generation deferred, so all existing detailed interpretation and backend
    application rules remain in one place.
    """

    fields = _list_interview_fields(knowledge, user)
    profile = _effective_profile(state, resolve_profile(knowledge))
    model_id = resolve_structured_model_id(knowledge)
    messages = _list_record_messages(record, user)
    source_target = _target_from_question(current_question)
    source_field = _field_for_target(source_target or {}, fields)
    question_definition = _question_definition_context(
        source_target,
        source_field,
    )
    fast_question = dict(current_question)
    fast_question["questionDefinition"] = question_definition
    fast_context = build_fast_interpreter_context(
        current_question=fast_question,
        latest_answer=latest_user_message,
        messages=messages,
    )
    structured_provider = _get_structured_provider(provider, model_id=model_id)
    selected_fast_provider = fast_provider or BedrockFastInterpreterProvider()
    source_turn_id = str(latest_user_message.get("voiceTurnId") or latest_user_message.get("turnId") or "")
    source_message_id = str(latest_user_message.get("id") or "")
    source_turn_sequence = _optional_int(latest_user_message.get("voiceTurnSequence"))
    source_key = target_key(source_target)
    join = _FastValidationJoin(
        record_id=str(record.get("id") or ""),
        user=user,
        source_turn_id=source_turn_id,
        source_message_id=source_message_id,
        source_target=source_target,
        knowledge=knowledge,
        fast_can_proceed=False,
        on_complete=on_background_validation,
    )

    background_future = _submit_ordered_background_validation(
        join=join,
        record=record,
        knowledge=knowledge,
        user=user,
        source_question=dict(current_question),
        source_turn_id=source_turn_id,
        source_message_id=source_message_id,
        source_turn_sequence=source_turn_sequence,
        provider=structured_provider,
    )

    def background_done(future: Future[Any]) -> None:
        try:
            background = future.result()
        except Exception as exc:  # noqa: BLE001 - callback turns failures into telemetry
            logger.exception(
                "background_interpreter_failed record_id=%s source_turn_id=%s",
                record.get("id"),
                source_turn_id,
            )
            background = {
                "error": exc.__class__.__name__,
                "result": None,
                "backgroundLatencyMetrics": {},
            }
        join.set_background_result(background)

    background_future.add_done_callback(background_done)

    fast_started_at = monotonic()
    fast_started_ms = int(time() * 1000)
    logger.info(
        "fast_interpreter_start record_id=%s source_turn_id=%s model_id=%s reasoning_effort=%s",
        record.get("id"),
        source_turn_id,
        settings.structured_interview_fast_model_id,
        settings.structured_interview_fast_reasoning_effort,
    )
    fast_future = _FAST_INTERPRETER_EXECUTOR.submit(
        selected_fast_provider.assess,
        context=fast_context,
    )
    try:
        assessment = fast_future.result()
    except Exception as exc:  # noqa: BLE001 - fail closed without semantic retry
        logger.exception(
            "fast_interpreter_failed record_id=%s source_turn_id=%s error_type=%s",
            record.get("id"),
            source_turn_id,
            exc.__class__.__name__,
        )
        assessment = FastAnswerAssessment(
            minimumInformationPresent=False,
            understandable=False,
            clearlyIncomplete=False,
            reason="fast_interpreter_failure",
        )
    fast_elapsed_ms = _elapsed_ms(fast_started_at)
    fast_finished_ms = int(time() * 1000)
    # Intent classification is owned by the canonical Conversation Policy.
    # Keep the legacy field on the Fast schema for wire compatibility, but do
    # not let it route a turn or block the answer-only gate.
    needs_question_explanation = False
    fast_can_proceed = can_proceed(assessment)
    join.set_fast_result(fast_can_proceed)
    logger.info(
        "fast_interpreter_end record_id=%s source_turn_id=%s latency_ms=%s fast_can_proceed=%s needs_question_explanation=%s",
        record.get("id"),
        source_turn_id,
        fast_elapsed_ms,
        fast_can_proceed,
        needs_question_explanation,
    )
    latency_metrics: dict[str, Any] = {
        **_new_latency_metrics(),
        "fast_interpreter_start_ms": fast_started_ms,
        "fast_interpreter_end_ms": fast_finished_ms,
        "fast_interpreter_latency_ms": fast_elapsed_ms,
        "fast_can_proceed": fast_can_proceed,
        "fast_needs_question_explanation": needs_question_explanation,
    }

    if not fast_can_proceed:
        join.set_provisional_question(None)
        locale = resolve_interview_locale(record, knowledge)
        if assessment.clearlyIncomplete:
            reply = localized_interview_incomplete_prompt(locale)
        else:
            reply = localized_interview_unanswerable_prompt(
                locale,
                str(
                    current_question.get("targetLabel")
                    or current_question.get("label")
                    or "この項目"
                ),
            )
        return FastInterviewTurnResult(
            assessment=assessment,
            can_proceed=False,
            reply=reply,
            action="ask_follow_up",
            question=dict(current_question),
            retrieval_policy=str(current_question.get("retrievalPolicy") or "auto"),
            retrieval_executed=False,
            retrieved_sources=[],
            latency_metrics=latency_metrics,
            source_target_key=source_key,
            needs_question_explanation=needs_question_explanation,
        )

    provisional_state = _state_with_question_overlay(state, current_question)
    target = select_next_question_target(
        provisional_state,
        profile,
        fields,
        excluded_target_keys=(source_key,) if source_key else (),
    )
    if target is None:
        # A detailed completion decision still belongs to Background. Keep the
        # current question as a safe fallback if the provisional state has no
        # next target yet.
        join.set_provisional_question(None)
        return FastInterviewTurnResult(
            assessment=assessment,
            can_proceed=True,
            reply=str(current_question.get("text") or ""),
            action="ask_structured",
            question=dict(current_question),
            retrieval_policy=str(current_question.get("retrievalPolicy") or "auto"),
            retrieval_executed=False,
            retrieved_sources=[],
            latency_metrics=latency_metrics,
            source_target_key=source_key,
            needs_question_explanation=needs_question_explanation,
        )

    speculative_retrieval: SpeculativeInterviewRetrieval | None = None
    try:
        current_field = _field_for_target(target, fields)
        retrieval_policy = _retrieval_policy_for_target(target, current_field)
        speculative_retrieval = start_speculative_interview_document_retrieval(
            record=record,
            knowledge=knowledge,
            user=user,
            current_question=None,
            current_field=current_field,
            target=target,
            state=provisional_state,
            messages=messages,
            retrieval_policy=retrieval_policy,
        )
        question_text, retrieved_context, document_candidate = _generate_question_text(
            structured_provider,
            profile=profile,
            target=target,
            record=record,
            knowledge=knowledge,
            user=user,
            fields=fields,
            state=provisional_state,
            messages=messages,
            latency_metrics=latency_metrics,
            speculative_retrieval=speculative_retrieval,
            on_question_delta=on_question_delta,
        )
        if document_candidate is not None:
            target = dict(target)
            if apply_document_candidate(
                provisional_state,
                target,
                value=document_candidate.value,
                source_ids=document_candidate.source_ids,
            ):
                question_text = localized_interview_document_confirmation_question(
                    resolve_interview_locale(record, knowledge),
                    str(target.get("label") or "").strip(),
                    document_candidate.value,
                )
            else:
                document_candidate = None
        question = _build_question(
            provisional_state,
            target,
            question_text,
            question_definition=_question_definition_context(target, current_field),
            retrieval_policy=retrieval_policy,
            retrieved_sources=source_references(retrieved_context),
        )
        provisional_state.setdefault("askedQuestions", []).append(question)
        provisional_state["currentFieldId"] = question.get("fieldId")
        provisional_state["currentQuestionId"] = question["questionId"]
        provisional_state["nextQuestionTarget"] = target
        provisional_state = commit_foreground_provisional_state(
            provisional_state,
            user,
            source="fast_foreground_provisional",
        )
        join.set_provisional_question(question)
        return FastInterviewTurnResult(
            assessment=assessment,
            can_proceed=True,
            reply=question["text"],
            action="ask_structured",
            question=question,
            retrieval_policy=retrieval_policy,
            retrieval_executed=bool(retrieved_context),
            retrieved_sources=source_references(retrieved_context),
            latency_metrics=latency_metrics,
            source_target_key=source_key,
            needs_question_explanation=needs_question_explanation,
            provisional_state=deepcopy(provisional_state),
        )
    except Exception:
        logger.exception(
            "fast_path_question_generation_failed record_id=%s source_turn_id=%s",
            record.get("id"),
            source_turn_id,
        )
        join.set_provisional_question(None)
        return FastInterviewTurnResult(
            assessment=assessment,
            can_proceed=True,
            reply=str(current_question.get("text") or ""),
            action="ask_structured",
            question=dict(current_question),
            retrieval_policy=str(current_question.get("retrievalPolicy") or "auto"),
            retrieval_executed=False,
            retrieved_sources=[],
            latency_metrics=latency_metrics,
            source_target_key=source_key,
            needs_question_explanation=needs_question_explanation,
        )
    finally:
        if speculative_retrieval is not None and not speculative_retrieval.future.done():
            speculative_retrieval.cancel()


def _submit_ordered_background_validation(
    *,
    join: _FastValidationJoin,
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
    source_question: Mapping[str, Any],
    source_turn_id: str,
    source_message_id: str,
    source_turn_sequence: int | None,
    provider: StructuredInterviewProvider,
) -> Future[Any]:
    record_id = str(record.get("id") or "")
    with _FAST_BACKGROUND_TAILS_GUARD:
        previous = _FAST_BACKGROUND_TAILS.get(record_id)
        previous_join = previous[1] if previous is not None else None
        future = _FAST_BACKGROUND_EXECUTOR.submit(
            _run_ordered_background_validation,
            previous_join=previous_join,
            record=record,
            knowledge=knowledge,
            user=user,
            source_question=source_question,
            source_turn_id=source_turn_id,
            source_message_id=source_message_id,
            source_turn_sequence=source_turn_sequence,
            provider=provider,
        )
        _FAST_BACKGROUND_TAILS[record_id] = (future, join)

    def remove_tail(done: Future[Any]) -> None:
        with _FAST_BACKGROUND_TAILS_GUARD:
            current = _FAST_BACKGROUND_TAILS.get(record_id)
            if current is not None and current[0] is done:
                _FAST_BACKGROUND_TAILS.pop(record_id, None)

    future.add_done_callback(remove_tail)
    return future


def _run_ordered_background_validation(
    *,
    previous_join: _FastValidationJoin | None,
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
    source_question: Mapping[str, Any],
    source_turn_id: str,
    source_message_id: str,
    source_turn_sequence: int | None,
    provider: StructuredInterviewProvider,
) -> dict[str, Any]:
    if previous_join is not None:
        previous_join.reconciliation_event.wait()
    return _run_fast_background_validation(
        record=record,
        knowledge=knowledge,
        user=user,
        source_question=source_question,
        source_turn_id=source_turn_id,
        source_message_id=source_message_id,
        source_turn_sequence=source_turn_sequence,
        provider=provider,
    )


def _run_fast_background_validation(
    *,
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
    source_question: Mapping[str, Any],
    source_turn_id: str,
    source_message_id: str,
    source_turn_sequence: int | None,
    provider: StructuredInterviewProvider,
) -> dict[str, Any]:
    started_at = monotonic()
    started_ms = int(time() * 1000)
    record_id = str(record.get("id") or "")
    logger.info(
        "background_interpreter_start record_id=%s source_turn_id=%s model_id=%s reasoning_effort=%s",
        record_id,
        source_turn_id,
        getattr(provider, "model_id", settings.structured_interview_model_id),
        settings.structured_interview_reasoning_effort,
    )
    # Capture an immutable base snapshot only.  The background evaluator is a
    # proposal producer and must not hold the record's state-writer lock while
    # waiting on the model.
    lock = _structured_interview_lock(record_id)
    with lock:
        fields = _list_interview_fields(knowledge, user)
        base_state = load_structured_interview_state(
            record,
            knowledge,
            user,
            fields=fields,
            persist=False,
        )
        base_state_version = int(base_state.get("stateVersion", 0) or 0)
        state = deepcopy(base_state)
        _install_background_question_overlay(state, source_question)
    result = _generate_structured_interview_result_locked(
        record,
        knowledge,
        user,
        persist_assistant_messages=False,
        provider=provider,
        speculative_retrieval=None,
        on_question_delta=None,
        defer_question_generation=True,
        persist_state=False,
        state_override=state,
    )
    result_state = result.get("interviewState")
    if not isinstance(result_state, Mapping):
        result_state = state
    proposal_state = deepcopy(dict(result_state))
    clarification_request = _background_clarification_request(
        proposal_state,
        source_target=_target_from_question(source_question),
        source_turn_id=source_turn_id,
        source_message_id=source_message_id,
    )
    valid_evidence_ids = [
        str(message.get("id"))
        for message in (result.get("messages") or [])
        if isinstance(message, Mapping) and message.get("id")
    ]
    output_payload = proposal_state.get("lastStructuredOutput")
    proposal = {
        "sourceTurnId": source_turn_id,
        "sourceMessageId": source_message_id,
        "sourceTurnSequence": source_turn_sequence,
        "baseStateVersion": base_state_version,
        "sourceQuestion": deepcopy(dict(source_question)),
        "rawTranscript": str(
            (proposal_state.get("lastTranscriptAssessment") or {}).get("rawTranscript")
            if isinstance(proposal_state.get("lastTranscriptAssessment"), Mapping)
            else ""
        ),
        "structuredOutput": deepcopy(output_payload)
        if isinstance(output_payload, Mapping)
        else None,
        "validEvidenceIds": valid_evidence_ids,
        "clarificationProposal": deepcopy(clarification_request)
        if isinstance(clarification_request, Mapping)
        else None,
        "proposalTopics": [
            "fieldUpdates",
            "requirementUpdates",
            "processPatch",
            "applicability",
            "contradictions",
            "openIssues",
            "clarificationProposal",
        ],
    }
    elapsed_ms = _elapsed_ms(started_at)
    background_metrics = {
        "background_interpreter_start_ms": started_ms,
        "background_interpreter_end_ms": int(time() * 1000),
        "background_interpreter_latency_ms": elapsed_ms,
        "background_interpreter_calls": int(
            (result.get("latencyMetrics") or {}).get("interpreter_calls", 0)
        ),
        "background_medium_retry_calls": int(
            (result.get("latencyMetrics") or {}).get("medium_retry_calls", 0)
        ),
        "background_patch_repair_calls": int(
            (result.get("latencyMetrics") or {}).get("patch_repair_calls", 0)
        ),
    }
    logger.info(
        "background_interpreter_end record_id=%s source_turn_id=%s latency_ms=%s",
        record_id,
        source_turn_id,
        elapsed_ms,
    )
    return {
        "result": result,
        "backgroundLatencyMetrics": background_metrics,
        "backgroundProposal": proposal,
    }


def _install_background_question_overlay(
    state: dict[str, Any],
    question: Mapping[str, Any],
) -> bool:
    question_id = str(question.get("questionId") or "").strip()
    if not question_id:
        return False
    asked_questions = state.setdefault("askedQuestions", [])
    if not isinstance(asked_questions, list):
        asked_questions = []
        state["askedQuestions"] = asked_questions
    changed = False
    if not any(
        isinstance(item, Mapping)
        and str(item.get("questionId") or "") == question_id
        for item in asked_questions
    ):
        asked_questions.append(deepcopy(dict(question)))
        changed = True
    if state.get("currentQuestionId") != question_id:
        state["currentQuestionId"] = question_id
        changed = True
    field_id = question.get("fieldId")
    if state.get("currentFieldId") != field_id:
        state["currentFieldId"] = field_id
        changed = True
    target = _target_from_question(question)
    if target is not None and state.get("nextQuestionTarget") != target:
        state["nextQuestionTarget"] = target
        changed = True
    clarification = question.get("clarificationRequest")
    if isinstance(clarification, Mapping):
        request_id = str(clarification.get("requestId") or "").strip()
        if request_id:
            queue = state.get("clarificationQueue")
            queued_request = None
            if isinstance(queue, list):
                retained = []
                for item in queue:
                    if (
                        isinstance(item, Mapping)
                        and str(item.get("requestId") or "") == request_id
                    ):
                        queued_request = dict(item)
                    else:
                        retained.append(item)
                if len(retained) != len(queue):
                    state["clarificationQueue"] = retained
                    changed = True
            active = state.get("activeClarificationRequest")
            if not isinstance(active, Mapping) or str(active.get("requestId") or "") != request_id:
                state["activeClarificationRequest"] = queued_request or {
                    "requestId": request_id,
                    "reason": clarification.get("reason"),
                    "sourceTurnId": clarification.get("sourceTurnId"),
                    "sourceMessageId": clarification.get("sourceMessageId"),
                    "sourceTarget": deepcopy(target or {}),
                    "priority": 2,
                    "status": "active",
                }
                changed = True
    return changed


def _state_with_question_overlay(
    state: Mapping[str, Any],
    question: Mapping[str, Any],
) -> dict[str, Any]:
    result = deepcopy(dict(state))
    _install_background_question_overlay(result, question)
    provisional_keys = result.get("provisionalAnsweredTargetKeys")
    if not isinstance(provisional_keys, list):
        provisional_keys = []
    pending_validations = result.get("pendingBackgroundValidations")
    if not isinstance(pending_validations, list):
        pending_validations = []
    for pending in pending_validations:
        if not isinstance(pending, Mapping):
            continue
        pending_question_id = str(pending.get("questionId") or "").strip()
        pending_question = next(
            (
                item
                for item in result.get("askedQuestions", [])
                if isinstance(item, Mapping)
                and str(item.get("questionId") or "").strip() == pending_question_id
            ),
            None,
        )
        pending_key = target_key(_target_from_question(pending_question))
        if pending_key and pending_key not in provisional_keys:
            provisional_keys.append(pending_key)
    source_target = _target_from_question(question)
    if source_target is not None:
        source_key = target_key(source_target)
        if source_key and source_key not in provisional_keys:
            provisional_keys.append(source_key)
    if provisional_keys:
        result["provisionalAnsweredTargetKeys"] = provisional_keys
    return result


def _reconcile_fast_background_validation(
    *,
    record_id: str,
    user: UserContext,
    source_turn_id: str,
    source_message_id: str,
    source_target: Mapping[str, Any] | None,
    knowledge: Mapping[str, Any],
    fast_can_proceed: bool,
    provisional_question: Mapping[str, Any] | None,
    background: Mapping[str, Any],
) -> dict[str, Any]:
    result = background.get("result")
    background_metrics = dict(background.get("backgroundLatencyMetrics") or {})
    proposal = background.get("backgroundProposal")
    if not isinstance(result, Mapping) or not isinstance(proposal, Mapping):
        payload = {
            "sourceTurnId": source_turn_id,
            "sourceMessageId": source_message_id,
            "sourceTargetKey": target_key(source_target),
            "backgroundStatus": "failed",
            "backgroundAgreesWithFast": False,
            "backgroundCanProceed": False,
            "clarificationEnqueued": False,
            "backgroundMerge": {
                "mergeDecision": "failed_without_proposal",
                "sourceTurnId": source_turn_id,
                "appliedFields": [],
                "discardedFields": [],
                "clarificationEnqueued": False,
            },
            "latencyMetrics": background_metrics,
        }
        logger.info(
            "background_agrees_with_fast source_turn_id=%s value=false status=failed",
            source_turn_id,
        )
        return payload

    output_state = result.get("interviewState")
    if not isinstance(output_state, Mapping):
        output_state = {}
    clarification_request = (
        proposal.get("clarificationProposal")
        if isinstance(proposal.get("clarificationProposal"), Mapping)
        else None
    )
    background_can_proceed = _background_can_proceed(
        output_state,
        clarification_request=clarification_request,
    )
    lock = _structured_interview_lock(record_id)
    with lock:
        merge_result = apply_background_state_proposal(
            record_id=record_id,
            user=user,
            proposal=proposal,
            fields=_list_interview_fields(knowledge, user),
            profile=resolve_profile(knowledge),
            source_question=(
                proposal.get("sourceQuestion")
                if isinstance(proposal.get("sourceQuestion"), Mapping)
                else None
            )
            or (
                dict(provisional_question)
                if isinstance(provisional_question, Mapping)
                else None
            ),
        )
    merge_payload = merge_result.as_dict()
    clarification_applied = merge_result.clarification_enqueued
    agrees = bool(fast_can_proceed and background_can_proceed)
    payload = {
        "sourceTurnId": source_turn_id,
        "sourceMessageId": source_message_id,
        "sourceTargetKey": target_key(source_target),
        "backgroundStatus": "completed",
        "backgroundAgreesWithFast": agrees,
        "backgroundCanProceed": background_can_proceed,
        "clarificationEnqueued": clarification_applied,
        "clarificationRequest": clarification_request,
        "backgroundMerge": merge_payload,
        "backgroundProposal": dict(proposal),
        "latencyMetrics": background_metrics,
        "transcriptAssessment": output_state.get("lastTranscriptAssessment"),
        "answerAssessment": output_state.get("lastAnswerAssessment"),
        "result": dict(result),
    }
    logger.info(
        "background_agrees_with_fast source_turn_id=%s value=%s background_can_proceed=%s",
        source_turn_id,
        agrees,
        background_can_proceed,
    )
    if clarification_request is not None:
        logger.info(
            "clarification_enqueued source_turn_id=%s request_id=%s priority=%s",
            source_turn_id,
            clarification_request.get("requestId"),
            clarification_request.get("priority"),
        )
    return payload


def _background_clarification_request(
    state: dict[str, Any],
    *,
    source_target: Mapping[str, Any] | None,
    source_turn_id: str,
    source_message_id: str,
) -> dict[str, Any] | None:
    if not source_target:
        return None
    output = state.get("lastStructuredOutput")
    if not isinstance(output, Mapping):
        return None
    transcript_assessment = output.get("transcriptAssessment")
    answer_assessment = output.get("answerAssessment")
    correction_status = (
        transcript_assessment.get("correctionStatus")
        if isinstance(transcript_assessment, Mapping)
        else None
    )
    if correction_status == "UNCERTAIN":
        return enqueue_clarification_request(
            state,
            source_turn_id=source_turn_id,
            source_message_id=source_message_id,
            source_target=source_target,
            reason="発話内容の解釈確認が必要",
            priority=1,
        )
    if output.get("contradictions") or output.get("openIssues"):
        reason = "既存回答との矛盾または未解決事項があるため確認が必要"
        return enqueue_clarification_request(
            state,
            source_turn_id=source_turn_id,
            source_message_id=source_message_id,
            source_target=source_target,
            reason=reason,
            priority=1,
        )
    target_type = str(source_target.get("targetType") or "")
    target_id = str(source_target.get("targetId") or "")
    if target_type == "field":
        target_state = state.get("fieldStates", {}).get(target_id, {})
        requires_confirmation = target_state.get("answerState") == "AWAITING_CONFIRMATION"
    elif target_type in {"requirement", "process"}:
        target_state = state.get("requirementStates", {}).get(target_id, {})
        requires_confirmation = target_state.get("status") == "AWAITING_CONFIRMATION"
    else:
        target_state = {}
        requires_confirmation = False
    if requires_confirmation:
        return enqueue_clarification_request(
            state,
            source_turn_id=source_turn_id,
            source_message_id=source_message_id,
            source_target=source_target,
            reason="回答候補の確認が必要",
            priority=2,
        )
    sufficiency = (
        answer_assessment.get("sufficiency")
        if isinstance(answer_assessment, Mapping)
        else None
    )
    reasons = {
        "INCOMPLETE": ("回答が途中のため補足が必要", 1),
        "AMBIGUOUS": ("意味が変わる曖昧さの確認が必要", 1),
        "PARTIAL": ("必須観点の補足が必要", 2),
        "REASON_MISSING": ("重要な判断理由の補足が必要", 2),
        "CRITERIA_MISSING": ("重要な判断基準の補足が必要", 2),
    }
    reason_and_priority = reasons.get(str(sufficiency or ""))
    if reason_and_priority is None:
        # EXAMPLE_MISSING is intentionally left to optional deepening.
        return None
    reason, priority = reason_and_priority
    return enqueue_clarification_request(
        state,
        source_turn_id=source_turn_id,
        source_message_id=source_message_id,
        source_target=source_target,
        reason=reason,
        priority=priority,
    )


def _background_can_proceed(
    state: Mapping[str, Any],
    *,
    clarification_request: Mapping[str, Any] | None,
) -> bool:
    if clarification_request is not None:
        return False
    output = state.get("lastStructuredOutput")
    if not isinstance(output, Mapping):
        return False
    transcript = output.get("transcriptAssessment")
    answer = output.get("answerAssessment")
    if isinstance(transcript, Mapping) and transcript.get("correctionStatus") == "UNCERTAIN":
        return False
    if isinstance(answer, Mapping) and answer.get("sufficiency") in {
        "INCOMPLETE",
        "AMBIGUOUS",
        "PARTIAL",
        "REASON_MISSING",
        "CRITERIA_MISSING",
    }:
        return False
    return not bool(output.get("contradictions") or output.get("openIssues"))


def _generate_structured_interview_result(
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    user: UserContext,
    *,
    persist_assistant_messages: bool = True,
    provider: StructuredInterviewProvider | None = None,
    latency_metrics: dict[str, float] | None = None,
    speculative_retrieval: SpeculativeInterviewRetrieval | None = None,
    canonical_intent: StructuredDialogueAct | None = None,
    canonical_action: CanonicalAction | None = None,
    on_question_delta: Callable[[str], None] | None = None,
    defer_question_generation: bool = False,
    persist_state: bool = True,
    state_override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    latency_metrics = latency_metrics if latency_metrics is not None else _new_latency_metrics()
    fields = _list_interview_fields(knowledge, user)
    state = (
        deepcopy(dict(state_override))
        if state_override is not None
        else load_structured_interview_state(
            record,
            knowledge,
            user,
            fields=fields,
            persist=persist_state,
        )
    )
    messages = _list_record_messages(record, user)
    if not persist_state:
        messages = deepcopy(messages)
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
        _persist_state(state, user, persist=persist_state)

    current_question = _get_current_question(state)
    if _repair_current_confirmation_question(state, locale=interview_locale):
        _persist_state(state, user, persist=persist_state)
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
            _persist_state(state, user, persist=persist_state)
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
                interpreter_started_at = monotonic()
                latency_metrics["interpreter_start_ms"] = int(time() * 1000)
                output = structured_provider.interpret(
                    profile=profile,
                    context=interpreter_context,
                    reasoning_effort=initial_reasoning_effort,
                )
                latency_metrics["interpreter_ms"] += _elapsed_ms(interpreter_started_at)
                latency_metrics["interpreter_calls"] += 1
                latency_metrics["interpreter_end_ms"] = int(time() * 1000)
                if (
                    initial_reasoning_effort != settings.structured_interview_medium_reasoning_effort
                    and _requires_medium_reasoning(state, output)
                ):
                    medium_context = {
                        **interpreter_context,
                        "preliminaryOutput": output.model_dump(),
                    }
                    medium_retry_started_at = monotonic()
                    output = structured_provider.interpret(
                        profile=profile,
                        context=medium_context,
                        reasoning_effort=settings.structured_interview_medium_reasoning_effort,
                    )
                    latency_metrics["medium_retry_ms"] += _elapsed_ms(medium_retry_started_at)
                    latency_metrics["medium_retry_calls"] += 1
                    selected_reasoning_effort = settings.structured_interview_medium_reasoning_effort
            valid_evidence_ids = {
                str(message.get("id"))
                for message in messages
                if message.get("id")
            }
            patch_repair_started_at = monotonic()
            output, selected_reasoning_effort, patch_repair_attempted = _repair_invalid_process_patch(
                output=output,
                provider=structured_provider,
                profile=profile,
                state=state,
                context=interpreter_context,
                record_id=str(record.get("id") or ""),
                valid_evidence_ids=valid_evidence_ids,
                selected_reasoning_effort=selected_reasoning_effort,
            )
            patch_repair_elapsed_ms = _elapsed_ms(patch_repair_started_at)
            if patch_repair_attempted:
                latency_metrics["patch_repair_ms"] += patch_repair_elapsed_ms
                latency_metrics["patch_repair_calls"] += 1
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
                    persist_state=persist_state,
                    reply=localized_interview_transcript_retry(interview_locale),
                )
            if canonical_intent is not None:
                actual_dialogue_act = output.dialogueAct
                state["lastCanonicalIntent"] = canonical_intent
                state["lastCanonicalAction"] = canonical_action
                state["lastStructuredValidationDialogueAct"] = actual_dialogue_act
                if canonical_action in {
                    "EXPLAIN_CURRENT_QUESTION",
                    "KEEP_CURRENT_QUESTION",
                }:
                    if canonical_action == "EXPLAIN_CURRENT_QUESTION":
                        reply = _render_question_explanation(
                            record_id=str(record.get("id") or ""),
                            current_question=current_question,
                            fields=fields,
                            locale=interview_locale,
                        )
                    elif canonical_intent == "CONFIRMATION":
                        reply = localized_interview_confirmation_clarification_prompt(
                            interview_locale,
                        )
                    else:
                        reply = localized_interview_hesitation_prompt(interview_locale)
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
                        persist_state=persist_state,
                        reply=reply,
                    )
                # The structured interpreter remains useful for extraction and
                # validation, but it must not replace the foreground intent.
                output = output.model_copy(update={"dialogueAct": canonical_intent})
                if canonical_action in {
                    "CONFIRM_PENDING_CANDIDATE",
                    "REJECT_PENDING_CANDIDATE",
                }:
                    # Confirmation/rejection is an action-specific state
                    # transition.  A validation model's sufficiency or stray
                    # field update must not turn it back into an answer.
                    output = output.model_copy(
                        update={
                            "utteranceCompleteness": "COMPLETE",
                            "answerAssessment": output.answerAssessment.model_copy(
                                update={
                                    "sufficiency": "SUFFICIENT",
                                    "probeType": "NONE",
                                }
                            ),
                            "fieldUpdates": [],
                            "requirementUpdates": [],
                            "processPatch": ProcessPatch(),
                            "applicability": [],
                            "contradictions": [],
                            "resolvedContradictionIds": [],
                            "openIssues": [],
                        }
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
                    persist_state=persist_state,
                    reply=_render_question_explanation(
                        record_id=str(record.get("id") or ""),
                        current_question=current_question,
                        fields=fields,
                        locale=interview_locale,
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
                    persist_state=persist_state,
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
                    persist_state=persist_state,
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
                    persist=persist_state,
                )
                state["lastStructuredOutput"] = output.model_dump()
                state["lastStructuredModelId"] = model_id
                state["lastStructuredReasoningEffort"] = selected_reasoning_effort
                _persist_state(state, user, persist=persist_state)
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
                        persist=persist_state,
                    )
                    state["lastStructuredOutput"] = output.model_dump()
                    state["lastStructuredModelId"] = model_id
                    state["lastStructuredReasoningEffort"] = selected_reasoning_effort
                    _persist_state(state, user, persist=persist_state)
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
                        state,
                    ):
                        confirm_tentative_target(state, tentative_target_before)
                    if answer_sufficiency in {"UNANSWERABLE", "REFUSAL"}:
                        if current_question.get("optionalDeepening"):
                            # Optional deepening is deliberately bounded to one
                            # attempt. A refusal or no-detail response ends the
                            # field's optional probe and keeps the interview moving.
                            confirm_tentative_target(state, current_target)
                            clear_probe(state, current_target)
                        else:
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
                        optional_target = select_optional_deepening_target(
                            state,
                            current_target,
                        )
                        if optional_target is not None:
                            register_probe(
                                state,
                                target=optional_target,
                                probe_type=output.answerAssessment.probeType,
                            )
                        elif current_question.get("optionalDeepening"):
                            # Do not turn one optional probe into a loop when
                            # the answer remains vague or contains no new detail.
                            confirm_tentative_target(state, current_target)
                            clear_probe(state, current_target)
                        elif _field_target_is_complete(state, current_target) and _follow_up_count(
                            state,
                            current_target,
                        ) >= 1:
                            # Legacy fields without optionalItems still get one
                            # useful probe, but never an unbounded deepening loop.
                            confirm_tentative_target(state, current_target)
                            clear_probe(state, current_target)
                        else:
                            register_probe(
                                state,
                                target=current_target,
                                probe_type=output.answerAssessment.probeType,
                            )
                    else:
                        clear_probe(state, current_target)
                        if current_question.get("optionalDeepening"):
                            confirm_tentative_target(state, current_target)
                    if persist_state:
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
                        persist=persist_state,
                    )
                else:
                    _persist_transcript_assessment(
                        latest_user_message,
                        state.get("lastTranscriptAssessment"),
                        persist=persist_state,
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
                _persist_state(state, user, persist=persist_state)
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
                _persist_state(state, user, persist=persist_state)
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
            clarification_completed = complete_clarification_request(
                state,
                current_question,
            )
            if clarification_completed:
                logger.info(
                    "clarification_completed record_id=%s question_id=%s",
                    record.get("id"),
                    current_question.get("questionId"),
                )
            completion = evaluate_completion(state, profile, fields)
            if completion["complete"]:
                state["status"] = "completed"
                state["currentFieldId"] = None
                state["currentQuestionId"] = None
                state["nextQuestionTarget"] = None
                _persist_state(state, user, persist=persist_state)
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
            _persist_state(state, user, persist=persist_state)

    if defer_question_generation:
        # Background validation has already applied the detailed output above.
        # The foreground voice path owns the provisional next question, so do
        # not select or generate another question here.
        return _build_result(
            record=record,
            state=state,
            messages=messages,
            fields=fields,
            reply="",
            question=_get_current_question(state),
            action="background_validation",
            status="completed" if state.get("status") == "completed" else "in_progress",
        )

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
            _persist_state(state, user, persist=persist_state)
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
    _persist_state(state, user, persist=persist_state)
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
        latency_metrics=latency_metrics,
        speculative_retrieval=speculative_retrieval,
        on_question_delta=on_question_delta,
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
    state["lastQuestionReasoningEffort"] = (
        settings.structured_interview_question_reasoning_effort
    )
    question = _build_question(
        state,
        target,
        question_text,
        question_definition=_question_definition_context(
            target,
            _field_for_target(target, fields),
        ),
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
    _persist_state(state, user, persist=persist_state)
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
    persist_state: bool = True,
) -> dict[str, Any]:
    """Persist a non-answer without replacing the active question.

    Fillers, question-help requests, qualified confirmations, and unsafe STT
    results must consume the user turn for idempotency while preserving the
    same question ID and target.  They intentionally skip target selection,
    retrieval, and Question Generator.
    """

    question_snapshot = _question_snapshot_with_definition(current_question, fields)
    if isinstance(current_question, dict):
        current_question.update(deepcopy(question_snapshot))
    record_interpretation_assessment(
        state,
        output,
        latest_message_id=latest_message_id,
        raw_transcript=raw_transcript,
    )
    if persist_state:
        _persist_transcript_assessment(
            latest_user_message,
            state.get("lastTranscriptAssessment"),
            persist=persist_state,
        )
    state["lastStructuredOutput"] = output.model_dump()
    state["lastStructuredDialogueAct"] = output.dialogueAct
    state["lastStructuredModelId"] = model_id
    state["lastStructuredReasoningEffort"] = reasoning_effort
    _persist_state(state, user, persist=persist_state)
    updated_messages = _replace_message(messages, latest_message_id, latest_user_message)
    return _build_result(
        record=record,
        state=state,
        messages=updated_messages,
        fields=fields,
        reply=reply,
        question=question_snapshot,
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
        commit_interview_state(state, user, source="state_initialization")
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
                "aiQuestionExamples": field.get("aiQuestionExamples"),
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


def _question_generator_state_context(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return only state that can affect wording for the selected target.

    Target selection and interview-state mutation remain backend-owned. The
    question generator needs the last assessment, active probe, and pending
    candidate to phrase the already-selected target safely; it does not need
    the complete accumulated field/process state.
    """

    return {
        "status": state.get("status"),
        "closingState": state.get("closingState"),
        "answerAssessment": state.get("lastAnswerAssessment"),
        "activeProbe": state.get("activeProbeTarget"),
        "tentativeCandidates": _list_tentative_candidates(state),
    }


def _question_generator_field_context(
    field: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "id": field.get("id"),
        "name": field.get("name"),
        "description": field.get("description"),
        "questionText": field.get("questionText") or field.get("question"),
        "aiQuestionExamples": field.get("aiQuestionExamples"),
        "questionPlan": field.get("questionPlan"),
        "required": field.get("required"),
        "optional": field.get("optional"),
    }


def _question_definition_context(
    target: Mapping[str, Any] | None,
    field: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the immutable definition for the already-selected target.

    The Question Generator may receive a small state/history context for
    latency, but it must still receive the configured wording and extraction
    contract.  In particular, a field title alone is not enough to recover a
    profile question such as name, department, and role.
    """

    target = target or {}
    field = field or {}
    provided_definition = target.get("questionDefinition")
    if isinstance(provided_definition, Mapping):
        # A rendered question carries the definition it was created from.
        # Never rebuild it from a later explanation or mutable progress data.
        definition = {
            key: deepcopy(value)
            for key, value in provided_definition.items()
            if key not in _CANONICAL_QUESTION_DEFINITION_DYNAMIC_KEYS
        }
        if "originalQuestion" not in definition:
            definition["originalQuestion"] = definition.get("canonicalQuestion")
        if "canonicalQuestion" not in definition:
            definition["canonicalQuestion"] = definition.get("originalQuestion")
        return definition
    raw_plan = target.get("questionPlan")
    if not isinstance(raw_plan, Mapping):
        raw_plan = field.get("questionPlan")
    plan = dict(raw_plan) if isinstance(raw_plan, Mapping) else {}

    def normalized_items(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return []
        return [
            deepcopy(dict(item))
            for item in value
            if isinstance(item, Mapping)
            and str(item.get("itemId") or item.get("label") or "").strip()
        ]

    required_items = normalized_items(plan.get("requiredItems"))
    optional_items = normalized_items(plan.get("optionalItems"))
    if not required_items:
        field_id = str(field.get("id") or target.get("targetId") or "").strip()
        field_name = str(field.get("name") or target.get("label") or field_id).strip()
        if field_id or field_name:
            required_items = [
                {
                    "itemId": field_id,
                    "label": field_name,
                    "description": field.get("description"),
                }
            ]

    original_question: str | None = None
    for value in (
        target.get("sourceQuestion"),
        target.get("questionText"),
        field.get("questionText"),
        field.get("question"),
    ):
        if isinstance(value, Mapping):
            value = value.get("text") or value.get("questionText") or value.get("question")
        if isinstance(value, str) and value.strip():
            original_question = value.strip()
            break
    if original_question is None:
        examples = field.get("aiQuestionExamples")
        if isinstance(examples, Sequence) and not isinstance(examples, (str, bytes)):
            for example in examples:
                if isinstance(example, str) and example.strip():
                    original_question = example.strip()
                    break

    description: str | None = None
    for value in (
        target.get("sourceDescription"),
        target.get("targetDescription"),
        field.get("description"),
    ):
        if isinstance(value, str) and value.strip():
            description = value.strip()
            break

    required = target.get("required") if "required" in target else field.get("required")
    optional = target.get("optional") if "optional" in target else field.get("optional")
    if optional is None and required is not None:
        optional = not bool(required)

    target_type = target.get("targetType") or target.get("kind")
    field_definition: dict[str, Any] = {}
    if field:
        for key in (
            "id",
            "name",
            "description",
            "inputType",
            "required",
            "askByAi",
            "retrievalPolicy",
            "questionText",
            "aiQuestionExamples",
            "questionPlan",
            "options",
            "displayOrder",
        ):
            if key in field:
                field_definition[key] = deepcopy(field.get(key))

    return {
        "targetType": target_type,
        "targetId": target.get("targetId") or field.get("id"),
        "title": target.get("label") or field.get("name"),
        "canonicalQuestion": original_question,
        "originalQuestion": original_question,
        "description": description,
        "purpose": plan.get("purpose"),
        "required": required,
        "optional": optional,
        "requiredItems": required_items,
        "optionalItems": optional_items,
        "completionCriteria": deepcopy(plan.get("completionCriteria")),
        "questionPlan": deepcopy(plan),
        "fieldDefinition": field_definition,
    }


def _question_progress_context(target: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return mutable completion progress separately from the definition."""

    target = target or {}
    return {
        "missingItemIds": list(target.get("missingItemIds") or []),
        "missingItems": deepcopy(target.get("missingItems") or []),
        "capturedItemIds": list(target.get("capturedItemIds") or []),
    }


def _question_definition_hash(definition: Mapping[str, Any]) -> str:
    serialized = json.dumps(
        definition,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _question_definition_for_question(
    question: Mapping[str, Any],
    fields: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    provided = question.get("questionDefinition")
    if isinstance(provided, Mapping):
        return _question_definition_context({"questionDefinition": provided})
    target = _target_from_question(question) or {}
    return _question_definition_context(target, _field_for_target(target, fields))


def _question_snapshot_with_definition(
    question: Mapping[str, Any],
    fields: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return a question with its immutable definition/progress split.

    Older persisted questions may predate the definition snapshot. Backfill
    only the missing representation from the configured field; rendered text
    remains ephemeral and is never used as the definition.
    """

    snapshot = deepcopy(dict(question))
    definition = _question_definition_for_question(snapshot, fields)
    snapshot["questionDefinition"] = definition
    snapshot["questionDefinitionHash"] = _question_definition_hash(definition)
    snapshot.setdefault("renderedQuestionText", snapshot.get("text"))
    snapshot["questionProgress"] = _question_progress_context(
        _target_from_question(snapshot)
    )
    return snapshot


def _render_question_explanation(
    *,
    record_id: str,
    current_question: Mapping[str, Any],
    fields: Sequence[Mapping[str, Any]],
    locale: InterviewLocale,
) -> str:
    """Render an explanation without a state/definition write path."""

    definition_before = _question_definition_for_question(current_question, fields)
    hash_before = _question_definition_hash(definition_before)
    reply = localized_interview_question_help(
        locale,
        str(
            current_question.get("targetLabel")
            or current_question.get("label")
            or definition_before.get("title")
            or "この項目"
        ),
        question_text=definition_before.get("originalQuestion"),
        description=definition_before.get("description"),
        required_items=definition_before.get("requiredItems") or [],
    )
    definition_after = _question_definition_for_question(current_question, fields)
    hash_after = _question_definition_hash(definition_after)
    logger.info(
        "question_explanation_rendered record_id=%s question_id=%s "
        "question_definition_hash_before=%s question_definition_hash_after=%s "
        "current_target=%s rendered_explanation=%s",
        record_id,
        current_question.get("questionId"),
        hash_before,
        hash_after,
        current_question.get("targetId"),
        reply,
    )
    if definition_before != definition_after:
        raise RuntimeError("question_definition_mutated_during_explanation")
    return reply


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
) -> tuple[StructuredInterviewOutput, str, bool]:
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
        return output, selected_reasoning_effort, False

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
        return output, selected_reasoning_effort, True

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
        return output, selected_reasoning_effort, True

    logger.info(
        "structured_process_patch_repaired record_id=%s reasoning_effort=%s",
        record_id,
        settings.structured_interview_medium_reasoning_effort,
    )
    return (
        output.model_copy(update={"processPatch": repaired_output.processPatch}),
        settings.structured_interview_medium_reasoning_effort,
        True,
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
    state: Mapping[str, Any],
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
    if tentative_type == "field":
        field_state = state.get("fieldStates", {}).get(tentative_id, {})
        if isinstance(field_state, Mapping) and field_state.get("missingRequiredItemIds"):
            # A field with an explicit item plan remains open even when the
            # next utterance contains a different field's answer.
            return False
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
    latency_metrics: dict[str, float] | None = None,
    speculative_retrieval: SpeculativeInterviewRetrieval | None = None,
    on_question_delta: Callable[[str], None] | None = None,
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
    if str(retrieval_policy or "auto").strip().lower() == "never":
        # Do not even enter the retrieval path for a target that prohibits
        # document context. This keeps the request trace unambiguous and
        # avoids needless query construction on every voice turn.
        retrieved_context: list[RetrievedKnowledgeContext] = []
    else:
        retrieval_query = build_interview_document_query(
            record=record,
            knowledge=knowledge,
            current_question=None,
            current_field=current_field,
            target=target,
            state=state,
            messages=messages,
        )
        knowledge_id = str(knowledge.get("id") or record.get("knowledgeId") or "")
        tenant_id = str(user.tenant_id or "")
        bounded_limit = MAX_INTERVIEW_DOCUMENT_CONTEXT
        retrieval_started_wall_ms = int(time() * 1000)
        retrieval_started_at = monotonic()
        reused_speculative = False
        if speculative_retrieval is not None:
            try:
                speculative_context = speculative_retrieval.resolve(
                    query=retrieval_query,
                    knowledge_id=knowledge_id,
                    tenant_id=tenant_id,
                    limit=bounded_limit,
                )
            except Exception:  # noqa: BLE001 - speculative retrieval must not fail the turn
                logger.warning(
                    "interview_speculative_rag_failed record_id=%s",
                    record.get("id"),
                    exc_info=True,
                )
                speculative_context = None
            if speculative_context is not None:
                retrieved_context = speculative_context
                reused_speculative = True
                if latency_metrics is not None:
                    latency_metrics["retrieval_reused"] = 1.0
                    latency_metrics["speculative_retrieval_ms"] = (
                        speculative_retrieval.latency_ms or 0.0
                    )
                    latency_metrics["rag_start_ms"] = float(
                        speculative_retrieval.started_at_ms
                    )
                    latency_metrics["rag_end_ms"] = float(
                        speculative_retrieval.ended_at_ms or int(time() * 1000)
                    )
            else:
                if latency_metrics is not None:
                    latency_metrics["retrieval_fallbacks"] = (
                        latency_metrics.get("retrieval_fallbacks", 0.0) + 1.0
                    )
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
        else:
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
        retrieval_elapsed_ms = _elapsed_ms(retrieval_started_at)
        if latency_metrics is not None:
            latency_metrics["retrieval_ms"] += retrieval_elapsed_ms
            latency_metrics["retrieval_calls"] += 1
            if speculative_retrieval is not None:
                latency_metrics["speculative_retrieval_wait_ms"] = retrieval_elapsed_ms
            if not reused_speculative:
                latency_metrics["rag_start_ms"] = float(retrieval_started_wall_ms)
                latency_metrics["rag_end_ms"] = float(int(time() * 1000))
    question_state = _question_generator_state_context(state)
    question_definition = _question_definition_context(target, current_field)
    context = {
        "knowledgeName": knowledge.get("name"),
        "recordTitle": record.get("title"),
        "customPrompt": knowledge.get("systemPrompt"),
        "interviewLocale": resolve_interview_locale(record, knowledge),
        "languageInstruction": interview_language_instruction(
            resolve_interview_locale(record, knowledge)
        ),
        # The interpreter still receives the complete state. The question
        # generator only needs the decision-relevant state that explains the
        # selected target; sending the whole state adds latency without
        # changing the target that the backend already chose.
        "currentState": question_state,
        "recentConversation": [
            {"role": message.get("role"), "content": message.get("content")}
            for message in messages[-_QUESTION_GENERATOR_RECENT_MESSAGE_LIMIT:]
            if message.get("isActualUtterance") is not False
        ],
        # A question is generated for one backend-selected target. Keep the
        # selected field's wording/plan, but do not resend unrelated fields.
        "fields": [_question_generator_field_context(current_field)]
        if current_field is not None
        else [],
        "questionDefinition": question_definition,
        "questionProgress": _question_progress_context(target),
        "tentativeCandidates": question_state["tentativeCandidates"],
        "answerAssessment": question_state["answerAssessment"],
        "activeProbe": question_state["activeProbe"],
        "retrieved_knowledge": [item.model_dump() for item in retrieved_context],
    }
    question_generation_started_at = monotonic()
    if latency_metrics is not None:
        latency_metrics["question_llm_start_ms"] = float(int(time() * 1000))
    streamed_delta_count = 0
    streamed_text = ""

    def emit_question_delta(delta: str) -> None:
        nonlocal streamed_delta_count, streamed_text
        value = str(delta or "")
        if not value:
            return
        streamed_delta_count += 1
        streamed_text += value
        if latency_metrics is not None:
            now_ms = float(int(time() * 1000))
            latency_metrics.setdefault("question_first_token_ms", now_ms)
            if (
                "question_first_sentence_ms" not in latency_metrics
                and re.search(r"[。！？!?]\s*$", streamed_text)
            ):
                latency_metrics["question_first_sentence_ms"] = now_ms
        if on_question_delta is not None:
            on_question_delta(value)

    stream_method = getattr(provider, "generate_question_stream", None)
    stream_allowed = (
        bool(on_question_delta)
        and bool(getattr(settings, "structured_interview_question_streaming_enabled", True))
        and callable(stream_method)
        and (
            str(retrieval_policy or "auto").strip().lower() == "never"
            or not retrieved_context
        )
        and not _is_awaiting_confirmation_target(target, state)
        and not _candidate_value_for_target(target, state)
    )
    if stream_allowed:
        try:
            generated = stream_method(
                profile=profile,
                context=context,
                target=target,
                reasoning_effort=settings.structured_interview_question_reasoning_effort,
                on_delta=emit_question_delta,
            )
        except Exception:
            if streamed_delta_count:
                raise
            logger.warning(
                "structured_question_stream_failed_before_delta record_id=%s; falling back",
                record.get("id"),
                exc_info=True,
            )
            generated = provider.generate_question(
                profile=profile,
                context=context,
                target=target,
                reasoning_effort=settings.structured_interview_question_reasoning_effort,
            )
    else:
        generated = provider.generate_question(
            profile=profile,
            context=context,
            target=target,
            reasoning_effort=settings.structured_interview_question_reasoning_effort,
        )
    if latency_metrics is not None:
        latency_metrics["question_generation_ms"] += _elapsed_ms(
            question_generation_started_at
        )
        latency_metrics["question_generation_calls"] += 1
        latency_metrics["question_llm_end_ms"] = float(int(time() * 1000))
    logger.info(
        "structured_question_generated model_id=%s target_type=%s target_id=%s reasoning_effort=%s elapsed_ms=%s",
        getattr(provider, "model_id", None),
        target.get("targetType") or target.get("kind"),
        target.get("targetId"),
        settings.structured_interview_question_reasoning_effort,
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
        if "renderedQuestionText" in current_question:
            current_question["renderedQuestionText"] = question_text
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
    if "renderedQuestionText" in current_question:
        current_question["renderedQuestionText"] = question_text
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
    target = {
        "targetType": target_type,
        "targetId": target_id,
        "label": str(question.get("targetLabel") or question.get("label") or target_id),
    }
    if isinstance(question.get("questionDefinition"), Mapping):
        target["questionDefinition"] = deepcopy(question["questionDefinition"])
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
        "clarificationRequest",
    ):
        if key in question:
            target[key] = deepcopy(question[key])
    return target


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


def _field_target_is_complete(
    state: Mapping[str, Any],
    target: Mapping[str, Any] | None,
) -> bool:
    if not target:
        return False
    target_type = str(target.get("targetType") or target.get("kind") or "")
    target_id = str(target.get("targetId") or "")
    if target_type != "field" or not target_id:
        return False
    field_state = state.get("fieldStates", {}).get(target_id)
    return isinstance(field_state, Mapping) and not field_state.get("missingRequiredItemIds")


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
    *,
    persist: bool = True,
) -> None:
    if not persist or not assessment:
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
    question_definition: Mapping[str, Any] | None = None,
    retrieval_policy: str = "auto",
    retrieved_sources: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    target_kind = str(target.get("targetType") or target.get("kind") or "issue")
    target_id = str(target.get("targetId") or "")
    field_id = target_id if target_kind == "field" else None
    immutable_definition = (
        deepcopy(dict(question_definition))
        if isinstance(question_definition, Mapping)
        else _question_definition_context(target)
    )
    question = {
        "questionId": f"q-{len(state.get('askedQuestions', [])) + 1:03d}",
        "questionType": "structured",
        "fieldId": field_id,
        "text": text,
        "renderedQuestionText": text,
        "questionDefinition": immutable_definition,
        "questionDefinitionHash": _question_definition_hash(immutable_definition),
        "questionProgress": _question_progress_context(target),
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
        "clarificationRequest",
    ):
        if key in target:
            question[key] = deepcopy(target[key])
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
        "canonicalIntent": state.get("lastCanonicalIntent"),
        "canonicalAction": state.get("lastCanonicalAction"),
        "structuredDialogueAct": state.get("lastStructuredValidationDialogueAct")
        or state.get("lastStructuredDialogueAct"),
        "dialogueActMismatch": bool(
            state.get("lastCanonicalIntent")
            and state.get("lastStructuredValidationDialogueAct")
            and state.get("lastCanonicalIntent")
            != state.get("lastStructuredValidationDialogueAct")
        ),
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


def _persist_state(
    state: dict[str, Any],
    user: UserContext,
    *,
    persist: bool = True,
) -> None:
    if not persist:
        return
    commit_interview_state(state, user, source="structured_interview")


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
