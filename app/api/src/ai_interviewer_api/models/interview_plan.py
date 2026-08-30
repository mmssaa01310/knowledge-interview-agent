from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ai_interviewer_api.core.interview_locale import InterviewLocale


InterviewProfile = Literal["fixed_form", "business_process", "system_requirement"]
StructuredInterviewModelId = Literal[
    "global.openai.gpt-5.6-terra",
    "global.openai.gpt-5.6-luna",
]
STRUCTURED_INTERVIEW_MODEL_IDS = frozenset(
    {"global.openai.gpt-5.6-terra", "global.openai.gpt-5.6-luna"}
)


class InterviewPlan(BaseModel):
    """Interview-wide purpose kept separate from per-question requirements."""

    version: int = 1
    purpose: str | None = None
    # Existing plans without a profile remain fixed-form interviews.
    profile: InterviewProfile = "fixed_form"
    # None keeps existing plans on the backend default model.
    modelId: StructuredInterviewModelId | None = None
    # The interview conversation language is independent from the UI locale.
    interviewLocale: InterviewLocale | None = None


class InterviewPlanItem(BaseModel):
    itemId: str
    label: str
    description: str | None = None


class InterviewCompletionCriteria(BaseModel):
    mode: Literal["all_required_items"] = "all_required_items"


class InterviewQuestionPlan(BaseModel):
    """The immutable evaluation contract for one configured interview question."""

    version: int = 1
    purpose: str | None = None
    requiredItems: list[InterviewPlanItem] = Field(default_factory=list)
    optionalItems: list[InterviewPlanItem] = Field(default_factory=list)
    completionCriteria: InterviewCompletionCriteria = Field(default_factory=InterviewCompletionCriteria)


class CapturedInterviewItem(BaseModel):
    itemId: str
    value: str
    evidenceTranscriptIds: list[str] = Field(default_factory=list)
