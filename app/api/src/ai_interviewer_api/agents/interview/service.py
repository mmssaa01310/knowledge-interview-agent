from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ai_interviewer_api.agents.common.tools import READ_ONLY_TOOL_NAMES
from ai_interviewer_api.agents.interview.agent import build_interview_agent
from ai_interviewer_api.agents.interview.schemas import (
    InterviewTurnInput,
    InterviewTurnOutput,
)


def run_interview_turn(
    interview_input: InterviewTurnInput,
    *,
    agent_factory: Callable[..., Any] | None = None,
    agent_runner: Callable[..., Any] | None = None,
    model_id: str | None = None,
    region_name: str | None = None,
    temperature: float | None = None,
) -> InterviewTurnOutput:
    invocation_state = {
        "knowledge_id": interview_input.knowledge_id,
        "used_tools": [],
    }
    prompt = _build_turn_prompt(interview_input)

    if agent_runner is None:
        factory = agent_factory or build_interview_agent
        agent = factory(model_id=model_id, region_name=region_name, temperature=temperature)
        agent_runner = agent

    result = agent_runner(
        prompt,
        invocation_state=invocation_state,
        structured_output_model=InterviewTurnOutput,
    )
    return _normalize_output(_coerce_output(result), invocation_state)


def _build_turn_prompt(interview_input: InterviewTurnInput) -> str:
    history_lines = [
        f"- {message.role}: {message.content}"
        for message in interview_input.conversation_history
    ]
    field_lines = [
        f"- {field.name}: {field.description or '説明未設定'}"
        for field in interview_input.approved_fields
    ]
    knowledge_context_items = [
        ("ナレッジ名", interview_input.knowledge_name),
        ("ナレッジ説明", interview_input.knowledge_description),
        ("対象テーマ", interview_input.target_business),
        ("関連情報", interview_input.target_equipment),
        ("記録タイトル", interview_input.record_title),
    ]
    knowledge_context_lines = [
        f"- {label}: {value.strip()}"
        for label, value in knowledge_context_items
        if isinstance(value, str) and value.strip()
    ]

    sections = [
        f"knowledge_id: {interview_input.knowledge_id or 'none'}",
        "knowledge_context:",
        *(knowledge_context_lines or ["- none"]),
        "runtime_custom_prompt:",
        interview_input.custom_prompt or "none",
        "turn_rules:",
        "- 質問する場合は、このターンの質問を1つだけにする。",
        "- 質問が不要な場合は、質問を含めなくてよい。",
        "- reply で複数の確認事項をまとめて聞かない。",
        "- next_questions は最大1件にする。",
        "current_interviewer_question:",
        _resolve_latest_assistant_message(interview_input.conversation_history) or "none",
        "approved_fields:",
        *(field_lines or ["- none"]),
        "conversation_history:",
        *(history_lines or ["- none"]),
        "latest_expert_message:",
        interview_input.user_message,
        "Return the interview response using the structured output contract.",
    ]
    return "\n".join(sections)


def _resolve_latest_assistant_message(conversation_history: list[InterviewMessage]) -> str:
    for message in reversed(conversation_history):
        if message.role == "assistant":
            return message.content
    return ""


def _coerce_output(result: Any) -> InterviewTurnOutput:
    if isinstance(result, InterviewTurnOutput):
        return result

    structured_output = getattr(result, "structured_output", None)
    if isinstance(structured_output, InterviewTurnOutput):
        return structured_output
    if structured_output is not None:
        try:
            return InterviewTurnOutput.model_validate(structured_output)
        except Exception:
            pass

    text = _extract_result_text(result)
    if text:
        try:
            return InterviewTurnOutput.model_validate_json(text)
        except Exception:
            try:
                return InterviewTurnOutput.model_validate(json.loads(text))
            except Exception:
                pass

    return InterviewTurnOutput(
        reply="回答を受け取りました。次に確認する質問を整理しながら続けます。",
    )


def _extract_result_text(result: Any) -> str:
    if isinstance(result, str):
        return result.strip()
    text = str(result).strip()
    return text


def _filter_used_tools(tool_names: list[Any]) -> list[str]:
    filtered: list[str] = []
    for tool_name in tool_names:
        if isinstance(tool_name, str) and tool_name in READ_ONLY_TOOL_NAMES and tool_name not in filtered:
            filtered.append(tool_name)
    return filtered


def _normalize_output(output: InterviewTurnOutput, invocation_state: dict[str, Any]) -> InterviewTurnOutput:
    answer_status = output.answer_status
    reask_question = output.reask_question.strip() if isinstance(output.reask_question, str) and output.reask_question.strip() else None
    answer_evaluation_reason = (
        output.answer_evaluation_reason.strip()
        if isinstance(output.answer_evaluation_reason, str) and output.answer_evaluation_reason.strip()
        else None
    )
    next_questions = output.next_questions[:1]
    draft_updates = dict(output.draft_updates)
    merged_used_tools = _filter_used_tools([*output.used_tools, *invocation_state.get("used_tools", [])])

    reply = output.reply.strip()
    if answer_status == "not_answered":
        next_questions = []
        draft_updates = {}
        if not reply:
            reply = reask_question or "現在の質問への回答がまだ確認できませんでした。もう少し具体的に教えてください。"
    else:
        reply = reply or "回答を受け取りました。次に確認する質問を整理しながら続けます。"

    return output.model_copy(
        update={
            "reply": reply,
            "answer_status": answer_status,
            "reask_question": reask_question,
            "answer_evaluation_reason": answer_evaluation_reason,
            "next_questions": next_questions,
            "draft_updates": draft_updates,
            "used_tools": merged_used_tools,
        }
    )
