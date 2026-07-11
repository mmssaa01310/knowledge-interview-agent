from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ai_interviewer_api.agents.common.tools import READ_ONLY_TOOL_NAMES
from ai_interviewer_api.agents.question_design.agent import build_question_design_agent
from ai_interviewer_api.agents.question_design.schemas import (
    QuestionDesignInput,
    QuestionDesignOutput,
    QuestionFieldSuggestion,
)

ALLOWED_INPUT_TYPES = {
    "short_text",
    "long_text",
    "number",
    "date",
    "single_select",
    "multi_select",
    "checklist",
    "related_entity",
}


def run_question_design(
    question_input: QuestionDesignInput,
    *,
    agent_factory: Callable[..., Any] | None = None,
    agent_runner: Callable[..., Any] | None = None,
    model_id: str | None = None,
    region_name: str | None = None,
    temperature: float | None = None,
) -> QuestionDesignOutput:
    invocation_state = {
        "knowledge_id": question_input.knowledge_id,
        "used_tools": [],
    }
    prompt = _build_turn_prompt(question_input)

    if agent_runner is None:
        factory = agent_factory or build_question_design_agent
        agent = factory(model_id=model_id, region_name=region_name, temperature=temperature)
        agent_runner = agent

    result = agent_runner(
        prompt,
        invocation_state=invocation_state,
        structured_output_model=QuestionDesignOutput,
    )
    return _normalize_output(_coerce_output(result), invocation_state, question_input.desired_count)


def _build_turn_prompt(question_input: QuestionDesignInput) -> str:
    knowledge_context_items = [
        ("ナレッジ名", question_input.knowledge_name),
        ("ナレッジ説明", question_input.knowledge_description),
        ("カテゴリ", question_input.category),
        ("対象テーマ", question_input.target_business),
        ("関連対象", question_input.target_equipment),
        ("言語", question_input.language),
    ]
    knowledge_context_lines = [
        f"- {label}: {value.strip()}"
        for label, value in knowledge_context_items
        if isinstance(value, str) and value.strip()
    ]
    existing_field_lines = [
        f"- {field.name}: {field.description or '説明未設定'}"
        for field in question_input.existing_fields
    ]
    recent_message_lines = [
        f"- {message.role}: {message.content}"
        for message in question_input.recent_messages
    ]
    sections = [
        f"knowledge_id: {question_input.knowledge_id or 'none'}",
        "knowledge_context:",
        *(knowledge_context_lines or ["- none"]),
        "runtime_custom_prompt:",
        question_input.custom_prompt or "none",
        "existing_fields:",
        *(existing_field_lines or ["- none"]),
        "recent_messages:",
        *(recent_message_lines or ["- none"]),
        "user_instruction:",
        (question_input.user_instruction or "none"),
        "desired_count:",
        str(question_input.desired_count or 8),
        "Return the question design response using the structured output contract.",
    ]
    return "\n".join(sections)


def _coerce_output(result: Any) -> QuestionDesignOutput:
    if isinstance(result, QuestionDesignOutput):
        return result

    structured_output = getattr(result, "structured_output", None)
    if isinstance(structured_output, QuestionDesignOutput):
        return structured_output
    if structured_output is not None:
        try:
            return QuestionDesignOutput.model_validate(structured_output)
        except Exception:
            pass

    text = _extract_result_text(result)
    if text:
        try:
            return QuestionDesignOutput.model_validate_json(text)
        except Exception:
            try:
                return QuestionDesignOutput.model_validate(json.loads(text))
            except Exception:
                pass

    return QuestionDesignOutput(reply="質問項目候補をまだ整理しきれていません。")


def _extract_result_text(result: Any) -> str:
    if isinstance(result, str):
        return result.strip()
    return str(result).strip()


def _filter_used_tools(tool_names: list[Any]) -> list[str]:
    filtered: list[str] = []
    for tool_name in tool_names:
        if isinstance(tool_name, str) and tool_name in READ_ONLY_TOOL_NAMES and tool_name not in filtered:
            filtered.append(tool_name)
    return filtered


def _normalize_output(
    output: QuestionDesignOutput,
    invocation_state: dict[str, Any],
    desired_count: int | None,
) -> QuestionDesignOutput:
    limit = max(1, min(desired_count or 8, 20))
    normalized_suggestions: list[QuestionFieldSuggestion] = []
    seen_labels: set[str] = set()
    for suggestion in output.suggestions:
        label = suggestion.label.strip()
        question = suggestion.question.strip()
        if not label or not question or label in seen_labels:
            continue
        seen_labels.add(label)
        input_type = suggestion.input_type.strip() if suggestion.input_type.strip() in ALLOWED_INPUT_TYPES else "long_text"
        normalized_suggestions.append(
            suggestion.model_copy(
                update={
                    "label": label,
                    "question": question,
                    "description": suggestion.description.strip() if isinstance(suggestion.description, str) and suggestion.description.strip() else None,
                    "reason": suggestion.reason.strip() if isinstance(suggestion.reason, str) and suggestion.reason.strip() else None,
                    "input_type": input_type,
                    "options": [str(option).strip() for option in suggestion.options if str(option).strip()],
                }
            )
        )
        if len(normalized_suggestions) >= limit:
            break

    reply = output.reply.strip()
    if not reply and normalized_suggestions:
        reply = "ヒアリング前に確認しておきたい質問項目を提案します。"
    elif not reply:
        reply = "質問項目候補をまだ整理しきれていません。"

    merged_used_tools = _filter_used_tools([*output.used_tools, *invocation_state.get("used_tools", [])])
    return output.model_copy(
        update={
            "reply": reply,
            "suggestions": normalized_suggestions,
            "used_tools": merged_used_tools,
        }
    )
