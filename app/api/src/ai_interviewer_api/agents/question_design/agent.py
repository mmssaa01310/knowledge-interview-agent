from __future__ import annotations

from typing import Any

from strands import Agent
from strands.hooks import AfterToolCallEvent

from ai_interviewer_api.agents.common.strands_runtime import create_agent, create_bedrock_model
from ai_interviewer_api.agents.common.tools import READ_ONLY_TOOL_NAMES, search_existing_fields, search_past_knowledge
from ai_interviewer_api.agents.question_design.prompt_loader import (
    load_question_design_prompt,
    load_question_design_validation_prompt,
)


def _record_used_tool(event: AfterToolCallEvent) -> None:
    used_tools = event.invocation_state.setdefault("used_tools", [])
    tool_name = event.tool_use.get("name")
    if isinstance(tool_name, str) and tool_name in READ_ONLY_TOOL_NAMES and tool_name not in used_tools:
        used_tools.append(tool_name)


def get_question_design_tools() -> list[Any]:
    return [
        search_existing_fields,
        search_past_knowledge,
    ]


def build_question_design_agent(
    *,
    model_id: str | None = None,
    region_name: str | None = None,
    temperature: float | None = None,
) -> Agent:
    model = create_bedrock_model(
        model_id=model_id,
        region_name=region_name,
        temperature=temperature,
    )
    return create_agent(
        model=model,
        system_prompt=load_question_design_prompt(),
        tools=get_question_design_tools(),
        hooks=[_record_used_tool],
        name="Question Design Agent",
        description="Designs question field suggestions before interviews start.",
    )


def build_question_design_validator(
    *,
    model_id: str | None = None,
    region_name: str | None = None,
    temperature: float | None = None,
) -> Agent:
    model = create_bedrock_model(
        model_id=model_id,
        region_name=region_name,
        temperature=temperature,
    )
    return create_agent(
        model=model,
        system_prompt=load_question_design_validation_prompt(),
        tools=[],
        hooks=[],
        name="Question Design Validator",
        description="Validates whether question design suggestions match the user's intent.",
    )
