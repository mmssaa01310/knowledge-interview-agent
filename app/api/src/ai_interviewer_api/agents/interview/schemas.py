from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class InterviewMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class InterviewField(BaseModel):
    fieldId: str | None = None
    name: str
    description: str | None = None
    inputType: str | None = None
    required: bool = False


class InterviewTurnInput(BaseModel):
    knowledge_id: str | None = None
    knowledge_name: str | None = None
    knowledge_description: str | None = None
    target_business: str | None = None
    target_equipment: str | None = None
    record_title: str | None = None
    custom_prompt: str | None = None
    user_message: str
    conversation_history: list[InterviewMessage] = Field(default_factory=list)
    approved_fields: list[InterviewField] = Field(default_factory=list)


class InterviewTurnOutput(BaseModel):
    reply: str
    answer_status: Literal["answered", "not_answered"] = "answered"
    reask_question: str | None = None
    answer_evaluation_reason: str | None = None
    next_questions: list[str] = Field(default_factory=list)
    draft_updates: dict[str, Any] = Field(default_factory=dict)
    used_tools: list[str] = Field(default_factory=list)
