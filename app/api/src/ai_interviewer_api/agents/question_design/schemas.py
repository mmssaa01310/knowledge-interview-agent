from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ai_interviewer_api.models.interview_plan import InterviewQuestionPlan
from ai_interviewer_api.models.interview_plan import InterviewPlan
from ai_interviewer_api.schemas.retrieval import RetrievedKnowledgeContext


class ExistingQuestionField(BaseModel):
    name: str
    description: str | None = None
    input_type: str | None = None
    required: bool = False
    ai_question_examples: list[str] = Field(default_factory=list)


class QuestionDesignMessage(BaseModel):
    role: str
    content: str


class QuestionDesignInput(BaseModel):
    knowledge_id: str | None = None
    knowledge_name: str | None = None
    knowledge_description: str | None = None
    category: str | None = None
    target_business: str | None = None
    target_equipment: str | None = None
    language: str = "ja"
    custom_prompt: str | None = None
    user_instruction: str | None = None
    desired_count: int | None = None
    existing_fields: list[ExistingQuestionField] = Field(default_factory=list)
    recent_messages: list[QuestionDesignMessage] = Field(default_factory=list)
    retrieved_context: list[RetrievedKnowledgeContext] = Field(default_factory=list)


class QuestionFieldSuggestion(BaseModel):
    label: str
    question: str
    description: str | None = None
    reason: str | None = None
    input_type: str = "long_text"
    required: bool = False
    ask_by_ai: bool = True
    options: list[str] = Field(default_factory=list)
    priority: int | None = None
    question_plan: InterviewQuestionPlan | None = None


class QuestionDesignOutput(BaseModel):
    reply: str = ""
    design_status: Literal["ready", "needs_info"] = "ready"
    clarification_question: str | None = None
    reason: str | None = None
    suggestions: list[QuestionFieldSuggestion] = Field(default_factory=list)
    interview_plan: InterviewPlan | None = None
    used_tools: list[str] = Field(default_factory=list)


class QuestionDesignValidation(BaseModel):
    is_aligned: bool
    validation_reason: str | None = None
    issues: list[str] = Field(default_factory=list)
    should_retry: bool = False
    retry_instruction: str | None = None
