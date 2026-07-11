"""Reusable read-only Strands tools for agents."""

from ai_interviewer_api.agents.common.tools.equipment_master import search_equipment_master
from ai_interviewer_api.agents.common.tools.existing_fields import search_existing_fields
from ai_interviewer_api.agents.common.tools.past_knowledge import search_past_knowledge

READ_ONLY_TOOL_NAMES = {
    "search_equipment_master",
    "search_existing_fields",
    "search_past_knowledge",
}

__all__ = [
    "READ_ONLY_TOOL_NAMES",
    "search_equipment_master",
    "search_existing_fields",
    "search_past_knowledge",
]
