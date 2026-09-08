from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ai_interviewer_api.agents.interview_knowledge.fast_interpreter.schemas import (
    FastAnswerAssessment,
)


def build_fast_interpreter_context(
    *,
    current_question: Mapping[str, Any],
    latest_answer: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build the intentionally narrow input contract for Fast Interpreter."""

    question_plan = current_question.get("questionPlan")
    required_items: list[dict[str, str]] = []
    if isinstance(question_plan, Mapping):
        raw_items = question_plan.get("requiredItems")
        if isinstance(raw_items, Sequence) and not isinstance(raw_items, (str, bytes)):
            for item in raw_items:
                if not isinstance(item, Mapping):
                    continue
                item_id = str(item.get("itemId") or "").strip()
                label = str(item.get("label") or "").strip()
                description = str(item.get("description") or "").strip()
                if item_id or label or description:
                    required_items.append(
                        {
                            "itemId": item_id,
                            "label": label,
                            "description": description,
                        }
                    )

    missing_items = current_question.get("missingItems")
    if isinstance(missing_items, Sequence) and not isinstance(missing_items, (str, bytes)):
        required_items = []
        for item in missing_items:
            if not isinstance(item, Mapping):
                continue
            required_items.append(
                {
                    "itemId": str(item.get("itemId") or "").strip(),
                    "label": str(item.get("label") or "").strip(),
                    "description": str(item.get("description") or "").strip(),
                }
            )

    current_question_context = {
        "questionId": current_question.get("questionId"),
        "text": str(current_question.get("text") or "").strip(),
        "targetType": current_question.get("targetType"),
        "targetId": current_question.get("targetId"),
        "targetLabel": current_question.get("targetLabel"),
        "purpose": current_question.get("sourceDescription")
        or current_question.get("targetLabel")
        or current_question.get("label"),
        "minimumRequiredItems": required_items,
    }
    answer_text = str(
        latest_answer.get("rawTranscript")
        or latest_answer.get("content")
        or latest_answer.get("transcript")
        or ""
    ).strip()
    previous_turns = []
    latest_id = latest_answer.get("id")
    for message in messages[-4:]:
        if message.get("id") == latest_id:
            continue
        content = str(message.get("content") or message.get("rawTranscript") or "").strip()
        if not content:
            continue
        previous_turns.append(
            {
                "role": message.get("role"),
                "content": content,
            }
        )

    return {
        "currentQuestion": current_question_context,
        "latestUserAnswer": {
            "text": answer_text,
            "sttConfidence": latest_answer.get("sttConfidence"),
        },
        "previousTurns": previous_turns,
    }


def can_proceed(assessment: FastAnswerAssessment) -> bool:
    return bool(
        assessment.minimumInformationPresent
        and assessment.understandable
        and not assessment.clearlyIncomplete
    )
