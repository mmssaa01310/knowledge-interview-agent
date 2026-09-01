from __future__ import annotations

from dataclasses import dataclass

from ai_interviewer_api.agents.question_design.schemas import (
    ExistingQuestionField,
    QuestionDesignInput,
    QuestionDesignMessage,
    QuestionDesignOutput,
    RetrievedKnowledgeContext,
)
from ai_interviewer_api.agents.question_design.service import DEFAULT_CLARIFICATION
from ai_interviewer_api.models.interview_plan import InterviewPlan
from ai_interviewer_api.schemas.requests import FieldSuggestionRequest, KnowledgeFieldCreate

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


@dataclass(frozen=True)
class AdaptedQuestionDesignResult:
    reply: str
    fields: list[KnowledgeFieldCreate]
    interview_plan: InterviewPlan | None = None


def build_question_design_input(
    payload: FieldSuggestionRequest,
    *,
    knowledge_id: str | None = None,
    retrieved_context: list[RetrievedKnowledgeContext] | None = None,
) -> QuestionDesignInput:
    return QuestionDesignInput(
        knowledge_id=knowledge_id,
        knowledge_name=_normalize_text(payload.context.name),
        knowledge_description=_normalize_text(payload.context.description),
        category=_normalize_text(payload.context.category),
        target_business=_normalize_text(payload.context.targetBusiness),
        target_equipment=_normalize_text(payload.context.targetEquipment),
        language=_normalize_text(payload.context.language) or "ja",
        custom_prompt=_normalize_text(payload.context.systemPrompt),
        user_instruction=_normalize_text(payload.content),
        desired_count=payload.maxFields,
        existing_fields=[
            ExistingQuestionField(
                name=field.name,
                description=field.description,
                input_type=field.inputType,
                required=field.required,
                ai_question_examples=list(field.aiQuestionExamples),
            )
            for field in payload.existingFields
            if field.name.strip()
        ],
        recent_messages=[
            QuestionDesignMessage(
                role="assistant" if message.role in {"ai", "assistant"} else "user",
                content=message.content.strip(),
            )
            for message in payload.recentMessages
            if message.content.strip()
        ],
        retrieved_context=list(retrieved_context or []),
    )


def adapt_question_design_output(output: QuestionDesignOutput) -> AdaptedQuestionDesignResult:
    if output.design_status == "needs_info":
        return AdaptedQuestionDesignResult(
            reply=DEFAULT_CLARIFICATION,
            fields=[],
            interview_plan=output.interview_plan,
        )

    fields: list[KnowledgeFieldCreate] = []
    for index, suggestion in enumerate(output.suggestions, start=1):
        input_type = suggestion.input_type if suggestion.input_type in ALLOWED_INPUT_TYPES else "long_text"
        fields.append(
            KnowledgeFieldCreate(
                name=suggestion.label,
                description=suggestion.description,
                inputType=input_type,
                required=suggestion.required,
                askByAi=True,
                aiQuestionExamples=[suggestion.question],
                options=list(suggestion.options),
                displayOrder=index,
                questionPlan=suggestion.question_plan,
            )
        )
    return AdaptedQuestionDesignResult(
        reply=output.reply.strip(),
        fields=fields,
        interview_plan=output.interview_plan,
    )


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None
