from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ai_interviewer_api.agents.interview.schemas import (
    InterviewField,
    InterviewMessage,
    InterviewQuestion,
    InterviewState,
    InterviewTurnInput,
    InterviewTurnOutput,
)
from ai_interviewer_api.agents.interview.service import run_interview_turn


@dataclass(frozen=True)
class AdaptedInterviewTurnResult:
    reply_text: str
    field_evaluation: dict[str, Any]
    follow_up_question: str | None
    used_tools: list[str]


def build_interview_turn_input(
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    knowledge_fields: Sequence[Mapping[str, Any]],
    interview_state: Mapping[str, Any] | None = None,
    current_question: Mapping[str, Any] | None = None,
    *,
    max_follow_up_questions_per_field: int = 2,
) -> InterviewTurnInput:
    conversation_history = _build_conversation_history(messages)
    approved_fields = _build_approved_fields(knowledge_fields)
    state_model = InterviewState.model_validate(interview_state) if interview_state else None
    current_field = _find_current_field(approved_fields, state_model.currentFieldId if state_model else None)
    current_question_model = (
        InterviewQuestion.model_validate(current_question)
        if current_question
        else _resolve_current_question(state_model)
    )

    return InterviewTurnInput(
        knowledge_id=_normalize_text(knowledge.get("id") or record.get("knowledgeId")),
        knowledge_name=_normalize_text(knowledge.get("name")),
        knowledge_description=_normalize_text(knowledge.get("description")),
        target_business=_normalize_text(knowledge.get("targetBusiness")),
        target_equipment=_normalize_text(record.get("targetEquipment") or knowledge.get("targetEquipment")),
        record_title=_normalize_text(record.get("title")),
        custom_prompt=_normalize_text(knowledge.get("systemPrompt")),
        interview_plan=knowledge.get("interviewPlan"),
        user_message=_resolve_latest_user_message(conversation_history),
        conversation_history=conversation_history,
        approved_fields=approved_fields,
        current_field=current_field,
        current_question=current_question_model,
        interview_state=state_model,
        follow_up_count=state_model.followUpCounts.get(current_field.fieldId, 0) if state_model and current_field and current_field.fieldId else 0,
        max_follow_up_questions_per_field=max_follow_up_questions_per_field,
    )


def adapt_interview_turn_output(output: InterviewTurnOutput) -> AdaptedInterviewTurnResult:
    return AdaptedInterviewTurnResult(
        reply_text=output.reply.strip(),
        field_evaluation=output.field_evaluation.model_dump(),
        follow_up_question=output.follow_up_question,
        used_tools=list(output.used_tools),
    )


def run_adapted_interview_turn(
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    knowledge_fields: Sequence[Mapping[str, Any]],
    interview_state: Mapping[str, Any] | None = None,
    current_question: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> AdaptedInterviewTurnResult:
    interview_input = build_interview_turn_input(
        record,
        knowledge,
        messages,
        knowledge_fields,
        interview_state,
        current_question,
        max_follow_up_questions_per_field=int(kwargs.pop("max_follow_up_questions_per_field", 2)),
    )
    output = run_interview_turn(interview_input, **kwargs)
    return adapt_interview_turn_output(output)


def _build_conversation_history(messages: Sequence[Mapping[str, Any]]) -> list[InterviewMessage]:
    history: list[InterviewMessage] = []
    for message in messages:
        if message.get("turnType") == "CONTROL":
            continue
        content = _normalize_text(message.get("content"))
        if not content:
            continue
        history.append(
            InterviewMessage(
                id=_normalize_text(message.get("id")),
                role=_normalize_message_role(message.get("role")),
                content=content,
                questionId=_normalize_text(message.get("questionId")),
                questionType=message.get("questionType"),
                fieldId=_normalize_text(message.get("fieldId")),
                answerToQuestionId=_normalize_text(message.get("answerToQuestionId")),
                answerToFieldId=_normalize_text(message.get("answerToFieldId")),
                turnType=message.get("turnType"),
                isLegacy=not bool(message.get("questionId") or message.get("answerToQuestionId")),
            )
        )
    return history


def _build_approved_fields(knowledge_fields: Sequence[Mapping[str, Any]]) -> list[InterviewField]:
    items: list[InterviewField] = []
    sorted_fields = sorted(knowledge_fields, key=lambda field: int(field.get("displayOrder") or 0))
    for field in sorted_fields:
        if not field.get("askByAi"):
            continue
        name = _normalize_text(field.get("name"))
        if not name:
            continue
        items.append(
            InterviewField(
                fieldId=_normalize_text(field.get("id")),
                name=name,
                description=_normalize_text(field.get("description")),
                aiQuestionExamples=[
                    text
                    for value in field.get("aiQuestionExamples") or []
                    if (text := _normalize_text(value))
                ],
                inputType=_normalize_text(field.get("inputType")),
                required=bool(field.get("required", False)),
                retrievalPolicy=_normalize_retrieval_policy(field.get("retrievalPolicy")),
                questionPlan=field.get("questionPlan"),
            )
        )
    return items


def _find_current_field(approved_fields: Sequence[InterviewField], field_id: str | None) -> InterviewField | None:
    if field_id:
        for field in approved_fields:
            if field.fieldId == field_id:
                return field
    return approved_fields[0] if approved_fields else None


def _resolve_current_question(interview_state: InterviewState | None) -> InterviewQuestion | None:
    if not interview_state or not interview_state.currentQuestionId:
        return None
    for question in interview_state.askedQuestions:
        if question.questionId == interview_state.currentQuestionId:
            return question
    return None


def _resolve_latest_user_message(conversation_history: Sequence[InterviewMessage]) -> str:
    for message in reversed(conversation_history):
        if message.role == "user":
            return message.content
    return ""


def _normalize_message_role(value: Any) -> str:
    role = str(value or "user").strip().lower()
    if role in {"assistant", "ai"}:
        return "assistant"
    if role == "system":
        return "system"
    return "user"


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_retrieval_policy(value: Any) -> str:
    policy = str(value or "auto").strip()
    return policy if policy in {"never", "auto", "required"} else "auto"
