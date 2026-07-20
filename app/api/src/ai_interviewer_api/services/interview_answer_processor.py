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
from typing import Any, Literal, Protocol


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


@dataclass(frozen=True)
class AnswerEvaluation:
    decision: AnswerDecision
    normalized_answer: str = ""
    is_relevant: bool | None = None
    is_sufficient: bool = False
    captured_information: list[str] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    follow_up_question: str | None = None
    confirmation_question: str | None = None
    target_field_id: str | None = None
    retrieval_needed: bool = False
    evaluation_reason: str | None = None
    evidence_transcript_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ConfirmationEvaluation:
    outcome: ConfirmationOutcome
    revised_answer: str | None = None
    clarification_question: str | None = None


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

        field_state["status"] = "asking"
        field_state["answerState"] = ANSWER_STATE_CANDIDATE_PENDING
        field_state["answerSummary"] = None
        evaluation = self._evaluator(
            transcript=transcript,
            question=question,
            field=field,
            field_state=field_state,
            evidence_transcript_id=evidence_transcript_id,
            knowledge_context=None,
        )
        retrieval_executed = False
        if evaluation.retrieval_needed:
            if retrieval_policy == "never" or self._retriever is None:
                evaluation = AnswerEvaluation(
                    decision="NEEDS_MORE_INFORMATION",
                    normalized_answer=evaluation.normalized_answer,
                    is_relevant=evaluation.is_relevant,
                    is_sufficient=False,
                    captured_information=evaluation.captured_information,
                    missing_information=evaluation.missing_information,
                    follow_up_question=evaluation.follow_up_question
                    or "判断に必要な情報を、もう少し具体的に教えてください。",
                    confirmation_question=evaluation.confirmation_question,
                    retrieval_needed=True,
                    evaluation_reason="knowledge_required_but_retrieval_disabled",
                    evidence_transcript_ids=evaluation.evidence_transcript_ids,
                )
            else:
                context = self._retriever(
                    field_id=field_id,
                    question_id=question_id,
                    transcript=transcript,
                )
                retrieval_executed = True
                evaluation = self._evaluator(
                    transcript=transcript,
                    question=question,
                    field=field,
                    field_state=field_state,
                    evidence_transcript_id=evidence_transcript_id,
                    knowledge_context=context,
                )

        return self._apply_evaluation(
            current_state=current_state,
            field_state=field_state,
            field_id=field_id,
            question_id=question_id,
            field_name=str(field.get("name") or "").strip(),
            question_text=str(question.get("text") or "").strip(),
            evaluation=evaluation,
            retrieval_policy=retrieval_policy,
            retrieval_executed=retrieval_executed,
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

        if evaluation.decision == "CORRECT_PREVIOUS_FIELD":
            target_field_id = evaluation.target_field_id
            if target_field_id and target_field_id in current_state.get("fieldStates", {}):
                target = _ensure_field_state(current_state, target_field_id)
                target["candidateAnswer"] = evaluation.normalized_answer or target.get("candidateAnswer")
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

        if evaluation.decision == "CONFIRMABLE" and evaluation.normalized_answer.strip():
            field_state["candidateAnswer"] = evaluation.normalized_answer.strip()
            field_state["answerState"] = ANSWER_STATE_AWAITING_CONFIRMATION
            field_state["pendingQuestionId"] = question_id
            field_state["pendingFieldId"] = field_id
            return InterviewTurnResult(
                decision=evaluation.decision,
                action="ask_confirmation",
                reply_text=evaluation.confirmation_question
                or _confirmation_prompt(field_name, evaluation.normalized_answer.strip()),
                question_id=question_id,
                field_id=field_id,
                retrieval_policy=retrieval_policy,
                retrieval_executed=retrieval_executed,
            )

        if evaluation.normalized_answer.strip() and evaluation.decision == "NEEDS_MORE_INFORMATION":
            field_state["candidateAnswer"] = evaluation.normalized_answer.strip()
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
        confirmation = _explicit_confirmation(transcript)
        if confirmation is None and self._confirmation_evaluator is not None:
            confirmation = self._confirmation_evaluator(
                candidate_answer=str(field_state.get("candidateAnswer") or "").strip(),
                user_reply=transcript,
                question=question,
                field_state=field_state,
            )
        confirmation = confirmation or ConfirmationEvaluation(
            outcome="UNCLEAR",
            clarification_question="正しければ『はい』、修正があれば正しい内容を教えてください。",
        )

        if confirmation.outcome == "CONFIRM":
            candidate = str(field_state.get("candidateAnswer") or "").strip()
            if candidate:
                field_state["answerState"] = ANSWER_STATE_CONFIRMED
                field_state["answerSummary"] = candidate
                field_state["status"] = "completed"
                field_state["candidateAnswer"] = None
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

        if confirmation.outcome == "REVISE_WITH_CONTENT" and confirmation.revised_answer:
            field_state["candidateAnswer"] = confirmation.revised_answer.strip()
            field_state["answerSummary"] = None
            field_state["answerState"] = ANSWER_STATE_AWAITING_CONFIRMATION
            return InterviewTurnResult(
                decision=confirmation.outcome,
                action="ask_confirmation",
                reply_text=_confirmation_prompt(field_name, confirmation.revised_answer.strip()),
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
    return field_state


def _explicit_confirmation(transcript: str) -> ConfirmationEvaluation | None:
    normalized = "".join(transcript.strip().lower().split()).strip("。.!！")
    if normalized in {
        "はい",
        "そうです",
        "はい、そうです",
        "そのとおりです",
        "合っています",
        "はい、合っています",
        "それで合っています",
        "はい、それで合っています",
        "問題ありません",
    }:
        return ConfirmationEvaluation(outcome="CONFIRM")
    if normalized in {"いいえ", "違います", "ダメです", "間違っています"}:
        return ConfirmationEvaluation(
            outcome="REJECT_WITHOUT_CONTENT",
            clarification_question="承知しました。どの部分が違いますか。正しい内容を教えてください。",
        )
    return None


def _confirmation_prompt(field_name: str, candidate: str) -> str:
    if field_name:
        return f"{field_name}は「{candidate}」という理解でよろしいですか？"
    return f"「{candidate}」でよろしいですか？"


def _retry_prompt(question_text: str) -> str:
    return f"回答として確認できませんでした。もう一度、{question_text}" if question_text else "回答をもう一度教えてください。"
