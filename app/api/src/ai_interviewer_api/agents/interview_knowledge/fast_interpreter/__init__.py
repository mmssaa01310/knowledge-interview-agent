"""Fast, provisional answer screening for voice interview turns."""

from ai_interviewer_api.agents.interview_knowledge.fast_interpreter.provider import (
    BedrockFastInterpreterProvider,
    FastInterpreterProvider,
)
from ai_interviewer_api.agents.interview_knowledge.fast_interpreter.schemas import (
    FastAnswerAssessment,
)
from ai_interviewer_api.agents.interview_knowledge.fast_interpreter.service import (
    build_fast_interpreter_context,
    can_proceed,
)

__all__ = [
    "BedrockFastInterpreterProvider",
    "FastAnswerAssessment",
    "FastInterpreterProvider",
    "build_fast_interpreter_context",
    "can_proceed",
]
