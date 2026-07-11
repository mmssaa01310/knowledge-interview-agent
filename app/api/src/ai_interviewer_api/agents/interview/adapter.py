from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ai_interviewer_api.agents.interview.schemas import (
    InterviewField,
    InterviewMessage,
    InterviewTurnInput,
    InterviewTurnOutput,
)
from ai_interviewer_api.agents.interview.service import run_interview_turn


@dataclass(frozen=True)
class AdaptedInterviewTurnResult:
    reply_text: str
    reply_chunks: list[str]
    next_questions: list[str]
    draft_updates: dict[str, Any]
    used_tools: list[str]
    answer_status: str = "answered"
    reask_question: str | None = None
    answer_evaluation_reason: str | None = None


def build_interview_turn_input(
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    knowledge_fields: Sequence[Mapping[str, Any]],
) -> InterviewTurnInput:
    conversation_history = _build_conversation_history(messages)
    approved_fields = _build_approved_fields(knowledge_fields)

    return InterviewTurnInput(
        knowledge_id=_normalize_text(knowledge.get("id") or record.get("knowledgeId")),
        knowledge_name=_normalize_text(knowledge.get("name")),
        knowledge_description=_normalize_text(knowledge.get("description")),
        target_business=_normalize_text(knowledge.get("targetBusiness")),
        target_equipment=_normalize_text(record.get("targetEquipment") or knowledge.get("targetEquipment")),
        record_title=_normalize_text(record.get("title")),
        custom_prompt=_normalize_text(knowledge.get("systemPrompt")),
        user_message=_resolve_latest_user_message(conversation_history),
        conversation_history=conversation_history,
        approved_fields=approved_fields,
    )


def adapt_interview_turn_output(output: InterviewTurnOutput) -> AdaptedInterviewTurnResult:
    reply_text = output.reply.strip()
    return AdaptedInterviewTurnResult(
        reply_text=reply_text,
        reply_chunks=_split_reply_chunks(reply_text),
        answer_status=output.answer_status,
        reask_question=output.reask_question,
        answer_evaluation_reason=output.answer_evaluation_reason,
        next_questions=list(output.next_questions),
        draft_updates=dict(output.draft_updates),
        used_tools=list(output.used_tools),
    )


def run_adapted_interview_turn(
    record: Mapping[str, Any],
    knowledge: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    knowledge_fields: Sequence[Mapping[str, Any]],
    **kwargs: Any,
) -> AdaptedInterviewTurnResult:
    interview_input = build_interview_turn_input(record, knowledge, messages, knowledge_fields)
    output = run_interview_turn(interview_input, **kwargs)
    return adapt_interview_turn_output(output)


def _build_conversation_history(messages: Sequence[Mapping[str, Any]]) -> list[InterviewMessage]:
    history: list[InterviewMessage] = []
    for message in messages:
        content = _normalize_text(message.get("content"))
        if not content:
            continue
        history.append(
            InterviewMessage(
                role=_normalize_message_role(message.get("role")),
                content=content,
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
                inputType=_normalize_text(field.get("inputType")),
                required=bool(field.get("required", False)),
            )
        )
    return items


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


def _split_reply_chunks(reply_text: str) -> list[str]:
    if not reply_text:
        return []

    lines = [line.strip() for line in reply_text.replace("\r\n", "\n").split("\n") if line.strip()]
    if len(lines) >= 2:
        return lines

    chunks = [part.strip() for part in reply_text.replace("。", "。\n").split("\n") if part.strip()]
    return chunks or [reply_text]