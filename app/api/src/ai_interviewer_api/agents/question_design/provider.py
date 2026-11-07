from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from ai_interviewer_api.agents.interview_knowledge.provider import OpenAIResponsesStructuredProvider
from ai_interviewer_api.agents.question_design.prompt_loader import (
    load_question_design_prompt,
    load_question_design_validation_prompt,
)
from ai_interviewer_api.agents.question_design.schemas import QuestionDesignOutput, QuestionDesignValidation
from ai_interviewer_api.core.config import settings


class OpenAIQuestionDesignRunner:
    """Direct Responses API runner for question design and validation.

    Retrieval is performed by the Backend before this runner is called. The
    LLM receives the retrieved context as input and returns only the requested
    Pydantic contract; it does not access or write the repository.
    """

    def __init__(
        self,
        *,
        model_id: str | None = None,
    ) -> None:
        self._provider = OpenAIResponsesStructuredProvider(model_id=model_id)

    def __call__(
        self,
        prompt: str,
        *,
        invocation_state: dict[str, Any] | None = None,
        structured_output_model: type[BaseModel] | None = None,
    ) -> BaseModel:
        del invocation_state
        output_model, schema_name, system_prompt = self._resolve_contract(structured_output_model)
        payload = self._provider.request_structured_output(
            schema_name=schema_name,
            schema=output_model.model_json_schema(),
            system_prompt=system_prompt,
            user_payload={"question_design_prompt": prompt},
            reasoning_effort=settings.question_design_reasoning_effort,
            max_output_tokens=settings.question_design_max_output_tokens,
        )
        return output_model.model_validate(payload)

    @staticmethod
    def _resolve_contract(
        output_model: type[BaseModel] | None,
    ) -> tuple[type[BaseModel], str, str]:
        if output_model is QuestionDesignOutput:
            return output_model, "question_design_output", load_question_design_prompt()
        if output_model is QuestionDesignValidation:
            return output_model, "question_design_validation", load_question_design_validation_prompt()
        raise TypeError("unsupported question design structured output model")


def build_question_design_runner(
    *,
    model_id: str | None = None,
) -> Callable[..., BaseModel]:
    return OpenAIQuestionDesignRunner(model_id=model_id)
