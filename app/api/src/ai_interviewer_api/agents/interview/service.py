from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ai_interviewer_api.agents.common.tools import READ_ONLY_TOOL_NAMES
from ai_interviewer_api.agents.interview.agent import build_interview_agent
from ai_interviewer_api.agents.interview.schemas import (
    InterviewFieldEvaluation,
    InterviewTurnInput,
    InterviewTurnOutput,
)
from ai_interviewer_api.core.interview_locale import localized_interview_fallbacks

DEFAULT_FOLLOW_UP_REPLY = "もう少し詳しく確認させてください。"
DEFAULT_FOLLOW_UP_QUESTION = "その点をもう少し詳しく教えてください。"


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
        retrieval_policy = (
            interview_input.current_field.retrievalPolicy
            if interview_input.current_field
            else "auto"
        )
        agent = factory(
            model_id=model_id,
            region_name=region_name,
            temperature=temperature,
            allow_retrieval=retrieval_policy != "never",
        )
        agent_runner = agent

    result = agent_runner(
        prompt,
        invocation_state=invocation_state,
        structured_output_model=InterviewTurnOutput,
    )
    return _normalize_output(_coerce_output(result, interview_input), invocation_state, interview_input)


def _build_turn_prompt(interview_input: InterviewTurnInput) -> str:
    history_lines = [
        f"- {message.role}: {message.content}"
        for message in interview_input.conversation_history
    ]
    field_lines = [
        f"- {field.name} | field_id: {field.fieldId or 'none'} | question_examples: {', '.join(field.aiQuestionExamples) if field.aiQuestionExamples else 'none'} | question_plan: {_format_question_plan(field.questionPlan)}"
        for field in interview_input.approved_fields
    ]
    field_state_lines = []
    if interview_input.interview_state:
        for field_id, field_state in interview_input.interview_state.fieldStates.items():
            field_state_lines.append(
                f"- {field_id}: status={field_state.status}, answer_state={field_state.answerState}, "
                f"candidate={field_state.candidateAnswer or 'none'}, "
                f"record_answer={field_state.recordAnswer or 'none'}, "
                f"answer_summary={field_state.answerSummary or 'none'}, "
                f"missing={', '.join(field_state.missingInformation) or 'none'}"
            )

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
        f"conversation_language: {interview_input.interview_locale}",
        "language_instruction: Generate all user-facing interview replies and follow-up questions in conversation_language.",
        f"knowledge_id: {interview_input.knowledge_id or 'none'}",
        "knowledge_context:",
        *(knowledge_context_lines or ["- none"]),
        "runtime_custom_prompt:",
        interview_input.custom_prompt or "none",
        "interview_plan:",
        _format_interview_plan(interview_input.interview_plan),
        "current_field:",
        f"- id: {interview_input.current_field.fieldId if interview_input.current_field else 'none'}",
        f"- name: {interview_input.current_field.name if interview_input.current_field else 'none'}",
        f"- description: {interview_input.current_field.description if interview_input.current_field and interview_input.current_field.description else 'none'}",
        f"- retrieval_policy: {interview_input.current_field.retrievalPolicy if interview_input.current_field else 'auto'}",
        f"- question_examples: {', '.join(interview_input.current_field.aiQuestionExamples) if interview_input.current_field and interview_input.current_field.aiQuestionExamples else 'none'}",
        f"- question_plan: {_format_question_plan(interview_input.current_field.questionPlan if interview_input.current_field else None)}",
        "current_question:",
        f"- id: {interview_input.current_question.questionId if interview_input.current_question else 'none'}",
        f"- type: {interview_input.current_question.questionType if interview_input.current_question else 'none'}",
        f"- text: {interview_input.current_question.text if interview_input.current_question else 'none'}",
        "interview_state:",
        f"- status: {interview_input.interview_state.status if interview_input.interview_state else 'none'}",
        f"- completed_field_ids: {', '.join(interview_input.interview_state.completedFieldIds) if interview_input.interview_state else 'none'}",
        f"- pending_field_ids: {', '.join(interview_input.interview_state.pendingFieldIds) if interview_input.interview_state else 'none'}",
        f"- follow_up_count_for_current_field: {interview_input.follow_up_count}",
        f"- max_follow_up_questions_per_field: {interview_input.max_follow_up_questions_per_field}",
        "field_states:",
        *(field_state_lines or ["- none"]),
        "approved_fields:",
        *(field_lines or ["- none"]),
        "conversation_history:",
        *(history_lines or ["- none"]),
        "latest_expert_message:",
        interview_input.user_message or "none",
        "evaluation_contract:",
        "- Extract only capturedItems from the latest expert message and classify its meaning as ANSWERED, UNCLEAR, or IRRELEVANT.",
        "- Generate recordAnswer as the natural answer text that should be recorded for the current question. Never use a meta explanation such as '回答されました'.",
        "- When answer_state is AWAITING_CONFIRMATION, classify the latest expert message in context using confirmationOutcome. For a correction, recordAnswer must contain only the corrected answer, not confirmation language.",
        "- For CONFIRM, return the current candidate as recordAnswer. For REVISE_WITH_CONTENT, return the latest consistent corrected answer and its capturedItems.",
        "- Do not merge with prior field state, calculate missing required items, or decide COMPLETE/NEEDS_FOLLOWUP. The backend does those deterministically from question_plan.",
        "- If a question_plan exists, capturedItems.itemId must be one of its requiredItems or optionalItems. Preserve the value exactly enough for the interview record.",
        "- Keep follow_up_question as a compatibility field; the backend decides whether it is needed and which required items are missing.",
        "Return the interview field evaluation response using the structured output contract.",
    ]
    return "\n".join(sections)


def _coerce_output(result: Any, interview_input: InterviewTurnInput) -> InterviewTurnOutput:
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

    field_id = interview_input.current_field.fieldId if interview_input.current_field else "unknown"
    return InterviewTurnOutput(
        reply=localized_interview_fallbacks(interview_input.interview_locale)["follow_up"],
        field_evaluation=InterviewFieldEvaluation(
            fieldId=field_id or "unknown",
            isComplete=False,
            answerSummary="",
            missingInformation=[],
            nextAction="follow_up",
            evaluationStatus="EVALUATION_ERROR",
        ),
        follow_up_question=None,
        used_tools=[],
    )


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
    output: InterviewTurnOutput,
    invocation_state: dict[str, Any],
    interview_input: InterviewTurnInput,
) -> InterviewTurnOutput:
    reply = output.reply.strip() or localized_interview_fallbacks(interview_input.interview_locale)["follow_up"]
    follow_up_question = output.follow_up_question.strip() if isinstance(output.follow_up_question, str) and output.follow_up_question.strip() else None
    evaluation = output.field_evaluation
    answer_summary = evaluation.answerSummary.strip() if isinstance(evaluation.answerSummary, str) else ""
    record_answer = evaluation.recordAnswer.strip() if isinstance(evaluation.recordAnswer, str) else ""
    missing_information = [
        item.strip()
        for item in evaluation.missingInformation
        if isinstance(item, str) and item.strip()
    ]
    field_id = evaluation.fieldId or (
        interview_input.current_field.fieldId if interview_input.current_field else "unknown"
    )
    has_question_plan = bool(
        (interview_input.current_question and interview_input.current_question.questionPlan)
        or (interview_input.current_field and interview_input.current_field.questionPlan)
    )
    next_action = evaluation.nextAction if has_question_plan else ("next_field" if evaluation.isComplete else evaluation.nextAction)
    decision = evaluation.decision
    if decision is None:
        if evaluation.isComplete and (answer_summary or record_answer):
            decision = "CONFIRMABLE"
        elif answer_summary:
            decision = "NEEDS_MORE_INFORMATION"
        else:
            decision = "NOT_ANSWER"
    if next_action == "follow_up" and not follow_up_question:
        follow_up_question = DEFAULT_FOLLOW_UP_QUESTION
    merged_used_tools = _filter_used_tools([*output.used_tools, *invocation_state.get("used_tools", [])])

    return output.model_copy(
        update={
            "reply": reply,
            "follow_up_question": follow_up_question,
            "field_evaluation": evaluation.model_copy(
                update={
                    "fieldId": field_id or "unknown",
                    "answerSummary": answer_summary,
                    "recordAnswer": record_answer,
                    "missingInformation": missing_information,
                    "nextAction": next_action,
                    "decision": decision,
                    "isRelevant": evaluation.isRelevant if evaluation.isRelevant is not None else decision not in {"NOT_ANSWER", "UNCLEAR"},
                    "isSufficient": evaluation.isSufficient if evaluation.isSufficient is not None else decision == "CONFIRMABLE",
                }
            ),
            "used_tools": merged_used_tools,
        }
    )


def _format_question_plan(plan: Any) -> str:
    if plan is None:
        return "none"
    if hasattr(plan, "model_dump"):
        plan = plan.model_dump()
    if not isinstance(plan, dict):
        return "none"
    required = ", ".join(
        f"{item.get('itemId')}: {item.get('label')}"
        for item in plan.get("requiredItems", [])
        if isinstance(item, dict)
    ) or "none"
    optional = ", ".join(
        f"{item.get('itemId')}: {item.get('label')}"
        for item in plan.get("optionalItems", [])
        if isinstance(item, dict)
    ) or "none"
    return f"purpose={plan.get('purpose') or 'none'}; required=[{required}]; optional=[{optional}]"


def _format_interview_plan(plan: Any) -> str:
    if plan is None:
        return "none"
    if hasattr(plan, "model_dump"):
        plan = plan.model_dump()
    if not isinstance(plan, dict):
        return "none"
    return f"version={plan.get('version', 1)}; purpose={plan.get('purpose') or 'none'}"
