from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class InterviewPlan(BaseModel):
    """Interview-wide purpose kept separate from per-question requirements."""

    version: int = 1
    purpose: str | None = None


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
