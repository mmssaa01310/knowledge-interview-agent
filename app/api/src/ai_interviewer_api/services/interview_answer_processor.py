"""
Role:
    テキスト・音声共通のインタビュー回答状態機械。

Summary:
    回答評価、候補保持、確認、確定を一つの契約で処理し、検索方針と
    回答評価を分離する。呼び出し元は評価器と必要な検索Portだけを渡す。

Relations:
    Uses evaluator and retriever Ports. Used by text and voice interview services.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from typing import Any, Literal, Mapping, Protocol


AnswerDecision = Literal[
    "CONFIRMABLE",
    "NEEDS_MORE_INFORMATION",
    "NOT_ANSWER",
    "UNCLEAR",
    "REQUEST_GUIDANCE",
    "CORRECT_PREVIOUS_FIELD",
]
ConfirmationOutcome = Literal[
    "CONFIRM",
    "REVISE_WITH_CONTENT",
    "REJECT_WITHOUT_CONTENT",
    "UNCLEAR",
]

ANSWER_STATE_UNANSWERED = "UNANSWERED"
ANSWER_STATE_CANDIDATE_PENDING = "CANDIDATE_PENDING"
ANSWER_STATE_AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
ANSWER_STATE_CONFIRMED = "CONFIRMED"


def compose_record_answer(raw_answers: list[str]) -> str:
    answers: list[str] = []
    for raw_answer in raw_answers:
        text = str(raw_answer or "").strip()
        if text and (not answers or answers[-1] != text):
            answers.append(text)
    return answers[0] if len(answers) == 1 else "\n".join(answers)


@dataclass(frozen=True)
class AnswerEvaluation:
    decision: AnswerDecision
    normalized_answer: str = ""
    record_answer: str = ""
    is_relevant: bool | None = None
    is_sufficient: bool = False
    missing_information: list[str] = field(default_factory=list)
    follow_up_question: str | None = None
    confirmation_question: str | None = None
    target_field_id: str | None = None
    retrieval_needed: bool = False
    evaluation_reason: str | None = None
    evidence_transcript_ids: list[str] = field(default_factory=list)
    captured_items: list[dict[str, Any]] = field(default_factory=list)
    answer_disposition: Literal["ANSWERED", "UNCLEAR", "IRRELEVANT"] | None = None
    evaluation_status: Literal["OK", "EVALUATION_ERROR"] = "OK"


@dataclass(frozen=True)
class ConfirmationEvaluation:
    outcome: ConfirmationOutcome
    revised_answer: str | None = None
    record_answer: str | None = None
    confirmation_question: str | None = None
    clarification_question: str | None = None
    captured_items: list[dict[str, Any]] = field(default_factory=list)
    evaluation_status: Literal["OK", "EVALUATION_ERROR"] = "OK"


@dataclass(frozen=True)
class InterviewTurnResult:
    decision: str
    action: Literal["ask_confirmation", "ask_follow_up", "confirmed"]
    reply_text: str
    question_id: str
    field_id: str
    retrieval_policy: str
    retrieval_executed: bool
    confirmed_field_id: str | None = None
    completion_status: Literal["COMPLETE", "NEEDS_FOLLOWUP"] | None = None
    missing_required_item_ids: list[str] = field(default_factory=list)
    answer_disposition: Literal["ANSWERED", "UNCLEAR", "IRRELEVANT"] | None = None


class AnswerEvaluator(Protocol):
    def __call__(
        self,
        *,
        transcript: str,
        question: dict[str, Any],
        field: dict[str, Any],
        field_state: dict[str, Any],
        evidence_transcript_id: str,
        knowledge_context: list[dict[str, Any]] | None = None,
    ) -> AnswerEvaluation: ...


class ConfirmationEvaluator(Protocol):
    def __call__(
        self,
        *,
        candidate_answer: str,
        user_reply: str,
        question: dict[str, Any],
        field_state: dict[str, Any],
    ) -> ConfirmationEvaluation: ...


class KnowledgeRetriever(Protocol):
    def __call__(self, *, field_id: str, question_id: str, transcript: str) -> list[dict[str, Any]]: ...


class InterviewAnswerProcessor:
    def __init__(
        self,
        *,
        evaluator: AnswerEvaluator,
        confirmation_evaluator: ConfirmationEvaluator | None = None,
        retriever: KnowledgeRetriever | None = None,
    ) -> None:
        self._evaluator = evaluator
        self._confirmation_evaluator = confirmation_evaluator
        self._retriever = retriever

    async def process_turn(
        self,
        *,
        record_id: str,
        question_id: str,
        field_id: str,
        transcript: str,
        current_state: dict[str, Any],
        question: dict[str, Any],
        field: dict[str, Any],
        evidence_transcript_id: str,
        retrieval_policy: str,
    ) -> InterviewTurnResult:
        return self.process_turn_sync(
            record_id=record_id,
            question_id=question_id,
            field_id=field_id,
            transcript=transcript,
            current_state=current_state,
            question=question,
            field=field,
            evidence_transcript_id=evidence_transcript_id,
            retrieval_policy=retrieval_policy,
        )

    def process_turn_sync(
        self,
        *,
        record_id: str,
        question_id: str,
        field_id: str,
        transcript: str,
        current_state: dict[str, Any],
        question: dict[str, Any],
        field: dict[str, Any],
        evidence_transcript_id: str,
        retrieval_policy: str,
    ) -> InterviewTurnResult:
        del record_id
        field_state = _ensure_field_state(current_state, field_id)
        if field_state["answerState"] == ANSWER_STATE_AWAITING_CONFIRMATION:
            missing_required_item_ids = list(field_state.get("missingRequiredItemIds") or [])
            if missing_required_item_ids:
                missing_labels = _missing_required_item_labels(
                    missing_required_item_ids,
                    question,
                    field,
                )
                follow_up = _missing_items_prompt(missing_labels)
                field_state["answerState"] = ANSWER_STATE_CANDIDATE_PENDING
                field_state["status"] = "asking"
                field_state["followUpQuestion"] = follow_up
                field_state["clarificationQuestion"] = follow_up
                return InterviewTurnResult(
                    decision="NEEDS_FOLLOWUP",
                    action="ask_follow_up",
                    reply_text=follow_up,
                    question_id=question_id,
                    field_id=field_id,
                    retrieval_policy=retrieval_policy,
                    retrieval_executed=False,
                    completion_status="NEEDS_FOLLOWUP",
                    missing_required_item_ids=missing_required_item_ids,
                )
            return self._process_confirmation(
                current_state=current_state,
                field_state=field_state,
                field_id=field_id,
                question_id=question_id,
                transcript=transcript,
                question=question,
                field_name=str(field.get("name") or "").strip(),
                retrieval_policy=retrieval_policy,
            )

        previous_field_state = deepcopy(field_state)
        field_state["status"] = "asking"
        field_state["answerState"] = ANSWER_STATE_CANDIDATE_PENDING
        field_state["answerSummary"] = None
        evaluation = self._evaluate(
            transcript=transcript,
            question=question,
            field=field,
            field_state=field_state,
            evidence_transcript_id=evidence_transcript_id,
            knowledge_context=None,
        )
        retrieval_executed = False
        if evaluation.retrieval_needed and evaluation.evaluation_status != "EVALUATION_ERROR":
            if retrieval_policy == "never" or self._retriever is None:
                evaluation = AnswerEvaluation(
                    decision="NEEDS_MORE_INFORMATION",
                    normalized_answer=evaluation.normalized_answer,
                    record_answer=evaluation.record_answer,
                    is_relevant=evaluation.is_relevant,
                    is_sufficient=False,
                    missing_information=evaluation.missing_information,
                    follow_up_question=evaluation.follow_up_question
                    or "判断に必要な情報を、もう少し具体的に教えてください。",
                    confirmation_question=evaluation.confirmation_question,
                    retrieval_needed=True,
                    evaluation_reason="knowledge_required_but_retrieval_disabled",
                    evidence_transcript_ids=evaluation.evidence_transcript_ids,
                    captured_items=evaluation.captured_items,
                    answer_disposition=evaluation.answer_disposition,
                    evaluation_status=evaluation.evaluation_status,
                )
            else:
                try:
                    context = self._retriever(
                        field_id=field_id,
                        question_id=question_id,
                        transcript=transcript,
                    )
                    retrieval_executed = True
                    evaluation = self._evaluate(
                        transcript=transcript,
                        question=question,
                        field=field,
                        field_state=field_state,
                        evidence_transcript_id=evidence_transcript_id,
                        knowledge_context=context,
                    )
                except Exception as exc:  # noqa: BLE001 - retrieval failures are system errors
                    evaluation = AnswerEvaluation(
                        decision="UNCLEAR",
                        evaluation_status="EVALUATION_ERROR",
                    )

        if evaluation.evaluation_status == "EVALUATION_ERROR":
            field_state.clear()
            field_state.update(previous_field_state)
            return InterviewTurnResult(
                decision="EVALUATION_ERROR",
                action="ask_follow_up",
                reply_text="回答処理で一時的な問題が発生しました。もう一度お答えください。",
                question_id=question_id,
                field_id=field_id,
                retrieval_policy=retrieval_policy,
                retrieval_executed=retrieval_executed,
                answer_disposition=evaluation.answer_disposition,
            )

        if _should_capture_raw_answer(
            transcript=transcript,
            evaluation=evaluation,
            question=question,
            field=field,
        ):
            _append_raw_answer(field_state, transcript)

        return self._apply_evaluation(
            current_state=current_state,
            field_state=field_state,
            field_id=field_id,
            question_id=question_id,
            field_name=str(field.get("name") or "").strip(),
            question_text=str(question.get("text") or "").strip(),
            question=question,
            field=field,
            raw_answer=transcript,
            evaluation=evaluation,
            retrieval_policy=retrieval_policy,
            retrieval_executed=retrieval_executed,
        )

    def _evaluate(self, **kwargs: Any) -> AnswerEvaluation:
        try:
            return self._evaluator(**kwargs)
        except Exception as exc:  # noqa: BLE001 - evaluation failures are a separate system status
            return AnswerEvaluation(
                decision="UNCLEAR",
                evaluation_status="EVALUATION_ERROR",
            )

    def _apply_evaluation(
        self,
        *,
        current_state: dict[str, Any],
        field_state: dict[str, Any],
        field_id: str,
        question_id: str,
        field_name: str,
        question_text: str,
        question: dict[str, Any],
        field: dict[str, Any],
        raw_answer: str,
        evaluation: AnswerEvaluation,
        retrieval_policy: str,
        retrieval_executed: bool,
    ) -> InterviewTurnResult:
        field_state["answerSummary"] = None
        field_state["isRelevant"] = evaluation.is_relevant
        field_state["isSufficient"] = evaluation.is_sufficient
        field_state["missingInformation"] = list(evaluation.missing_information)
        field_state["evaluationReason"] = evaluation.evaluation_reason or evaluation.decision
        field_state["candidateEvidenceTranscriptIds"] = list(evaluation.evidence_transcript_ids)

        if evaluation.captured_items:
            captured_items = _captured_items_by_id(field_state.get("capturedItems"))
            captured_items.update(_captured_items_by_id(evaluation.captured_items))
            field_state["capturedItems"] = list(captured_items.values())
            field_state["candidateItems"] = list(captured_items.values())

        question_plan = _resolve_question_plan(question, field)
        if question_plan is not None:
            return self._apply_question_plan_evaluation(
                current_state=current_state,
                field_state=field_state,
                field_id=field_id,
                question_id=question_id,
                field_name=field_name,
                raw_answer=raw_answer,
                evaluation=evaluation,
                question_plan=question_plan,
                retrieval_policy=retrieval_policy,
                retrieval_executed=retrieval_executed,
            )

        if evaluation.decision == "CORRECT_PREVIOUS_FIELD":
            target_field_id = evaluation.target_field_id
            if target_field_id and target_field_id in current_state.get("fieldStates", {}):
                target = _ensure_field_state(current_state, target_field_id)
                corrected_raw_answer = str(raw_answer or "")
                target["rawAnswer"] = (
                    corrected_raw_answer if corrected_raw_answer.strip() else target.get("rawAnswer")
                )
                if corrected_raw_answer.strip():
                    history = target.setdefault("rawAnswerHistory", [])
                    if not history or history[-1] != corrected_raw_answer:
                        history.append(corrected_raw_answer)
                target["recordAnswer"] = None
                target["candidateAnswer"] = (
                    evaluation.record_answer.strip()
                    or raw_answer.strip()
                    or target.get("candidateAnswer")
                )
                target["answerSummary"] = None
                target["answerState"] = ANSWER_STATE_AWAITING_CONFIRMATION
                target["status"] = "asking"
                target["pendingQuestionId"] = question_id
                target["pendingFieldId"] = target_field_id
                current_state["completedFieldIds"] = [
                    item
                    for item in current_state.get("completedFieldIds", [])
                    if item != target_field_id
                ]
                pending = current_state.setdefault("pendingFieldIds", [])
                if target_field_id not in pending:
                    pending.append(target_field_id)
                return InterviewTurnResult(
                    decision=evaluation.decision,
                    action="ask_confirmation",
                    reply_text=evaluation.follow_up_question or "訂正内容を反映してよろしいですか？",
                    question_id=question_id,
                    field_id=target_field_id,
                    retrieval_policy=retrieval_policy,
                    retrieval_executed=retrieval_executed,
                )
            return InterviewTurnResult(
                decision=evaluation.decision,
                action="ask_follow_up",
                reply_text=evaluation.follow_up_question or "どの回答を訂正するか教えてください。",
                question_id=question_id,
                field_id=field_id,
                retrieval_policy=retrieval_policy,
                retrieval_executed=retrieval_executed,
            )

        if evaluation.decision == "CONFIRMABLE" and raw_answer.strip():
            field_state["candidateAnswer"] = (
                evaluation.record_answer.strip()
                or raw_answer.strip()
            )
            field_state["answerState"] = ANSWER_STATE_AWAITING_CONFIRMATION
            field_state["pendingQuestionId"] = question_id
            field_state["pendingFieldId"] = field_id
            return InterviewTurnResult(
                decision=evaluation.decision,
                action="ask_confirmation",
                reply_text=_confirmation_prompt(
                    field_name,
                    raw_answer.strip(),
                    evaluation.confirmation_question,
                ),
                question_id=question_id,
                field_id=field_id,
                retrieval_policy=retrieval_policy,
                retrieval_executed=retrieval_executed,
            )

        if raw_answer.strip() and evaluation.decision == "NEEDS_MORE_INFORMATION":
            field_state["candidateAnswer"] = (
                evaluation.record_answer.strip()
                or raw_answer.strip()
            )
        field_state["answerState"] = ANSWER_STATE_CANDIDATE_PENDING
        field_state["status"] = "asking"
        follow_up = evaluation.follow_up_question or _retry_prompt(question_text)
        field_state["followUpQuestion"] = follow_up
        field_state["clarificationQuestion"] = follow_up
        return InterviewTurnResult(
            decision=evaluation.decision,
            action="ask_follow_up",
            reply_text=follow_up,
            question_id=question_id,
            field_id=field_id,
            retrieval_policy=retrieval_policy,
            retrieval_executed=retrieval_executed,
        )

    def _apply_question_plan_evaluation(
        self,
        *,
        current_state: dict[str, Any],
        field_state: dict[str, Any],
        field_id: str,
        question_id: str,
        field_name: str,
        raw_answer: str,
        evaluation: AnswerEvaluation,
        question_plan: Mapping[str, Any],
        retrieval_policy: str,
        retrieval_executed: bool,
    ) -> InterviewTurnResult:
        required_items = _plan_items(question_plan.get("requiredItems"))
        optional_items = _plan_items(question_plan.get("optionalItems"))
        required_item_ids = [str(item["itemId"]) for item in required_items]
        item_by_id = {
            str(item["itemId"]): item
            for item in [*required_items, *optional_items]
        }
        existing_items = _captured_items_by_id(
            field_state.get("capturedItems")
            or field_state.get("candidateItems")
        )
        captured_items = {
            **existing_items,
            **{
                item_id: item
                for item_id, item in _captured_items_by_id(evaluation.captured_items).items()
                if item_id in item_by_id
            },
        }
        missing_ids = [item_id for item_id in required_item_ids if item_id not in captured_items]
        missing_labels = [str(item_by_id[item_id].get("label") or item_id) for item_id in missing_ids]

        field_state["capturedItems"] = list(captured_items.values())
        field_state["candidateItems"] = list(captured_items.values())
        field_state["missingRequiredItemIds"] = missing_ids
        field_state["missingInformation"] = missing_labels
        field_state["answerDisposition"] = evaluation.answer_disposition

        if not missing_ids:
            candidate = (
                evaluation.record_answer.strip()
                or compose_record_answer(list(field_state.get("rawAnswerHistory") or []))
                or raw_answer.strip()
                or _render_captured_items(captured_items, required_items)
                or str(field_state.get("candidateAnswer") or "").strip()
            )
            if candidate:
                field_state["candidateAnswer"] = candidate
                field_state["answerState"] = ANSWER_STATE_AWAITING_CONFIRMATION
                field_state["pendingQuestionId"] = question_id
                field_state["pendingFieldId"] = field_id
                return InterviewTurnResult(
                    decision="COMPLETE",
                    action="ask_confirmation",
                    reply_text=_confirmation_prompt(
                        field_name,
                        candidate,
                        evaluation.confirmation_question,
                    ),
                    question_id=question_id,
                    field_id=field_id,
                    retrieval_policy=retrieval_policy,
                    retrieval_executed=retrieval_executed,
                    completion_status="COMPLETE",
                    missing_required_item_ids=[],
                    answer_disposition=evaluation.answer_disposition,
                )

        field_state["candidateAnswer"] = (
            evaluation.record_answer.strip()
            or compose_record_answer(list(field_state.get("rawAnswerHistory") or []))
            or raw_answer.strip()
            or _render_captured_items(captured_items, required_items)
            or str(field_state.get("candidateAnswer") or "").strip()
            or None
        )
        field_state["answerState"] = ANSWER_STATE_CANDIDATE_PENDING
        field_state["status"] = "asking"
        follow_up = _missing_items_prompt(missing_labels)
        field_state["followUpQuestion"] = follow_up
        field_state["clarificationQuestion"] = follow_up
        return InterviewTurnResult(
            decision="NEEDS_FOLLOWUP",
            action="ask_follow_up",
            reply_text=follow_up,
            question_id=question_id,
            field_id=field_id,
            retrieval_policy=retrieval_policy,
            retrieval_executed=retrieval_executed,
            completion_status="NEEDS_FOLLOWUP",
            missing_required_item_ids=missing_ids,
            answer_disposition=evaluation.answer_disposition,
        )

    def _process_confirmation(
        self,
        *,
        current_state: dict[str, Any],
        field_state: dict[str, Any],
        field_id: str,
        question_id: str,
        transcript: str,
        question: dict[str, Any],
        field_name: str,
        retrieval_policy: str,
    ) -> InterviewTurnResult:
        confirmation = None
        if self._confirmation_evaluator is not None:
            try:
                confirmation = self._confirmation_evaluator(
                    candidate_answer=str(field_state.get("candidateAnswer") or "").strip(),
                    user_reply=transcript,
                    question=question,
                    field_state=field_state,
                )
            except Exception:  # noqa: BLE001 - confirmation failures are system errors
                confirmation = ConfirmationEvaluation(
                    outcome="UNCLEAR",
                    evaluation_status="EVALUATION_ERROR",
                )
        confirmation = confirmation or ConfirmationEvaluation(
            outcome="UNCLEAR",
            clarification_question="正しければ『はい』、修正があれば正しい内容を教えてください。",
        )

        if confirmation.evaluation_status == "EVALUATION_ERROR":
            return InterviewTurnResult(
                decision="EVALUATION_ERROR",
                action="ask_follow_up",
                reply_text="回答処理で一時的な問題が発生しました。もう一度お答えください。",
                question_id=question_id,
                field_id=field_id,
                retrieval_policy=retrieval_policy,
                retrieval_executed=False,
            )

        if confirmation.outcome == "CONFIRM":
            candidate = str(
                confirmation.record_answer
                or field_state.get("candidateAnswer")
                or field_state.get("recordAnswer")
                or compose_record_answer(list(field_state.get("rawAnswerHistory") or []))
            ).strip()
            if candidate:
                field_state["answerState"] = ANSWER_STATE_CONFIRMED
                field_state["recordAnswer"] = candidate
                field_state["status"] = "completed"
                field_state["candidateAnswer"] = None
                captured_items = _captured_items_by_id(
                    field_state.get("capturedItems")
                    or field_state.get("candidateItems")
                    or field_state.get("confirmedItems")
                )
                captured_items.update(_captured_items_by_id(confirmation.captured_items))
                field_state["capturedItems"] = list(captured_items.values())
                field_state["confirmedItems"] = list(captured_items.values())
                field_state["candidateItems"] = []
                field_state["missingRequiredItemIds"] = []
                field_state["pendingQuestionId"] = None
                field_state["pendingFieldId"] = None
                completed = current_state.setdefault("completedFieldIds", [])
                if field_id not in completed:
                    completed.append(field_id)
                current_state["pendingFieldIds"] = [
                    item for item in current_state.get("pendingFieldIds", []) if item != field_id
                ]
                return InterviewTurnResult(
                    decision="CONFIRM",
                    action="confirmed",
                    reply_text="",
                    question_id=question_id,
                    field_id=field_id,
                    confirmed_field_id=field_id,
                    retrieval_policy=retrieval_policy,
                    retrieval_executed=False,
                )

        revised_record_answer = str(
            confirmation.record_answer or confirmation.revised_answer or ""
        ).strip()
        if confirmation.outcome == "REVISE_WITH_CONTENT" and revised_record_answer:
            _append_raw_answer(field_state, transcript)
            field_state["candidateAnswer"] = revised_record_answer
            captured_items = _captured_items_by_id(field_state.get("capturedItems"))
            captured_items.update(_captured_items_by_id(confirmation.captured_items))
            field_state["capturedItems"] = list(captured_items.values())
            field_state["candidateItems"] = list(captured_items.values())
            field_state["answerSummary"] = None
            field_state["answerState"] = ANSWER_STATE_AWAITING_CONFIRMATION
            return InterviewTurnResult(
                decision=confirmation.outcome,
                action="ask_confirmation",
                reply_text=_confirmation_prompt(
                    field_name,
                    revised_record_answer,
                    confirmation.confirmation_question,
                ),
                question_id=question_id,
                field_id=field_id,
                retrieval_policy=retrieval_policy,
                retrieval_executed=False,
            )

        if confirmation.outcome == "REJECT_WITHOUT_CONTENT":
            field_state["answerState"] = ANSWER_STATE_CANDIDATE_PENDING
        field_state["answerSummary"] = None
        return InterviewTurnResult(
            decision=confirmation.outcome,
            action="ask_follow_up",
            reply_text=confirmation.clarification_question
            or "正しければ『はい』、修正があれば正しい内容を教えてください。",
            question_id=question_id,
            field_id=field_id,
            retrieval_policy=retrieval_policy,
            retrieval_executed=False,
        )


def _ensure_field_state(current_state: dict[str, Any], field_id: str) -> dict[str, Any]:
    field_state = current_state.setdefault("fieldStates", {}).setdefault(
        field_id,
        {
            "fieldId": field_id,
            "status": "asking",
            "answerSummary": None,
            "missingInformation": [],
        },
    )
    field_state.setdefault("answerState", ANSWER_STATE_UNANSWERED)
    field_state.setdefault("candidateAnswer", None)
    field_state.setdefault("rawAnswer", None)
    raw_answer_history = field_state.setdefault("rawAnswerHistory", [])
    if not raw_answer_history and field_state.get("rawAnswer"):
        raw_answer_history.append(str(field_state["rawAnswer"]))
    field_state.setdefault("recordAnswer", None)
    field_state.setdefault("capturedItems", [])
    if not field_state["capturedItems"]:
        field_state["capturedItems"] = list(
            field_state.get("candidateItems") or field_state.get("confirmedItems") or []
        )
    field_state.setdefault("candidateItems", [])
    field_state.setdefault("confirmedItems", [])
    field_state.setdefault("missingRequiredItemIds", [])
    field_state.setdefault("answerDisposition", None)
    return field_state


def _resolve_question_plan(
    question: Mapping[str, Any],
    field: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    raw_plan = question.get("questionPlan") or field.get("questionPlan")
    if raw_plan is None:
        return None
    if hasattr(raw_plan, "model_dump"):
        raw_plan = raw_plan.model_dump()
    return raw_plan if isinstance(raw_plan, Mapping) else None


def _should_capture_raw_answer(
    *,
    transcript: str,
    evaluation: AnswerEvaluation,
    question: Mapping[str, Any],
    field: Mapping[str, Any],
) -> bool:
    if not transcript.strip() or evaluation.evaluation_status != "OK":
        return False
    if evaluation.answer_disposition == "IRRELEVANT":
        return False
    if _resolve_question_plan(question, field) is not None:
        return evaluation.answer_disposition == "ANSWERED" or bool(evaluation.captured_items)
    return evaluation.decision in {"CONFIRMABLE", "NEEDS_MORE_INFORMATION"}


def _append_raw_answer(field_state: dict[str, Any], transcript: str) -> None:
    raw_answer = str(transcript or "")
    if not raw_answer.strip():
        return
    field_state["rawAnswer"] = raw_answer
    history = field_state.setdefault("rawAnswerHistory", [])
    if not history or history[-1] != raw_answer:
        history.append(raw_answer)


def _plan_items(raw_items: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []
    items: list[dict[str, Any]] = []
    for raw_item in raw_items:
        if hasattr(raw_item, "model_dump"):
            raw_item = raw_item.model_dump()
        if not isinstance(raw_item, Mapping):
            continue
        item_id = str(raw_item.get("itemId") or "").strip()
        label = str(raw_item.get("label") or item_id).strip()
        if item_id and label:
            items.append({"itemId": item_id, "label": label, "description": raw_item.get("description")})
    return items


def _captured_items_by_id(raw_items: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw_items, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for raw_item in raw_items:
        if hasattr(raw_item, "model_dump"):
            raw_item = raw_item.model_dump()
        if not isinstance(raw_item, Mapping):
            continue
        item_id = str(raw_item.get("itemId") or "").strip()
        value = str(raw_item.get("value") or "").strip()
        if item_id and value:
            result[item_id] = {
                "itemId": item_id,
                "value": value,
                "evidenceTranscriptIds": list(raw_item.get("evidenceTranscriptIds") or []),
            }
    return result


def _render_captured_items(
    captured_items: Mapping[str, Mapping[str, Any]],
    required_items: list[dict[str, Any]],
) -> str:
    values = [
        str(captured_items[item["itemId"]].get("value") or "").strip()
        for item in required_items
        if item["itemId"] in captured_items
    ]
    return " / ".join(value for value in values if value)


def _missing_items_prompt(labels: list[str]) -> str:
    if not labels:
        return "不足している情報を、わかる範囲で教えてください。"
    if len(labels) == 1:
        return f"{labels[0]}について、具体的に教えてください。"
    return f"{ '、'.join(labels) }について、わかる範囲で教えてください。"


def _missing_required_item_labels(
    item_ids: list[str],
    question: Mapping[str, Any],
    field: Mapping[str, Any],
) -> list[str]:
    question_plan = _resolve_question_plan(question, field) or {}
    items = _plan_items(
        [
            *list(question_plan.get("requiredItems") or []),
            *list(question_plan.get("optionalItems") or []),
        ]
    )
    labels_by_id = {str(item["itemId"]): str(item["label"]) for item in items}
    return [labels_by_id.get(item_id, item_id) for item_id in item_ids]


def _confirmation_prompt(
    field_name: str,
    candidate: str,
    confirmation_question: str | None = None,
) -> str:
    generated_question = str(confirmation_question or "").strip()
    if generated_question:
        return generated_question
    if field_name:
        return f"{field_name}について、この内容でよろしいですか？"
    return "この内容でよろしいですか？"


def _retry_prompt(question_text: str) -> str:
    return f"回答として確認できませんでした。もう一度、{question_text}" if question_text else "回答をもう一度教えてください。"
