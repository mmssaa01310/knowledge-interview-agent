from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from strands import Agent
from strands.hooks import AfterToolCallEvent

from ai_interviewer_api.agents.common.strands_runtime import create_agent, create_bedrock_model
from ai_interviewer_api.agents.common.tools import (
    READ_ONLY_TOOL_NAMES,
    search_equipment_master,
    search_existing_fields,
    search_past_knowledge,
)

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


@lru_cache(maxsize=1)
def load_interview_agent_prompt() -> str:
    return (_PROMPTS_DIR / "base.md").read_text(encoding="utf-8").strip()


def _record_used_tool(event: AfterToolCallEvent) -> None:
    used_tools = event.invocation_state.setdefault("used_tools", [])
    tool_name = event.tool_use.get("name")
    if isinstance(tool_name, str) and tool_name in READ_ONLY_TOOL_NAMES and tool_name not in used_tools:
        used_tools.append(tool_name)


def get_interview_tools() -> list[Any]:
    return [
        search_equipment_master,
        search_existing_fields,
        search_past_knowledge,
    ]


def build_interview_agent(
    *,
    model_id: str | None = None,
    region_name: str | None = None,
    temperature: float | None = None,
    allow_retrieval: bool = True,
) -> Agent:
    model = create_bedrock_model(
        model_id=model_id,
        region_name=region_name,
        temperature=temperature,
    )
    return create_agent(
        model=model,
        system_prompt=load_interview_agent_prompt(),
        tools=get_interview_tools() if allow_retrieval else [],
        hooks=[_record_used_tool],
        name="Interview Agent",
        description="Conducts expert interviews and proposes the next questions.",
    )
