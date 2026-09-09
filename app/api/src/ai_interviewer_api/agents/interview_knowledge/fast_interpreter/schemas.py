from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FastAnswerAssessment(BaseModel):
    """Minimal provisional screening result.

    This contract intentionally contains no extracted answer, confirmation,
    state transition, process, contradiction, or target-selection fields.
    ``needsQuestionExplanation`` is retained for compatibility with older
    provider responses, but canonical intent routing no longer reads it.
    """

    model_config = ConfigDict(extra="forbid")

    minimumInformationPresent: bool
    understandable: bool
    clearlyIncomplete: bool
    needsQuestionExplanation: bool = False
    reason: str | None = Field(default=None, max_length=160)
