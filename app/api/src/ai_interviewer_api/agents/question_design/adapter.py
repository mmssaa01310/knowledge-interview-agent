from __future__ import annotations

from dataclasses import dataclass

from ai_interviewer_api.agents.question_design.schemas import (
    ExistingQuestionField,
    QuestionDesignInput,
    QuestionDesignMessage,
    QuestionDesignOutput,
)
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
    used_tools: list[str]


def build_question_design_input(payload: FieldSuggestionRequest) -> QuestionDesignInput:
    return QuestionDesignInput(
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
    )


def adapt_question_design_output(output: QuestionDesignOutput) -> AdaptedQuestionDesignResult:
    fields: list[KnowledgeFieldCreate] = []
    for index, suggestion in enumerate(output.suggestions, start=1):
        input_type = suggestion.input_type if suggestion.input_type in ALLOWED_INPUT_TYPES else "long_text"
        description = suggestion.description or suggestion.reason
        fields.append(
            KnowledgeFieldCreate(
                name=suggestion.label,
                description=description,
                inputType=input_type,
                required=suggestion.required,
                askByAi=suggestion.ask_by_ai,
                aiQuestionExamples=[suggestion.question],
                options=list(suggestion.options),
                displayOrder=index,
            )
        )
    return AdaptedQuestionDesignResult(
        reply=output.reply.strip(),
        fields=fields,
        used_tools=list(output.used_tools),
    )


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None
