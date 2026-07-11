from __future__ import annotations

import hashlib
import logging

from strands import tool
from strands.types.tools import ToolContext

logger = logging.getLogger(__name__)

_NOT_CONNECTED_MESSAGE = "No existing fields data source is connected yet."


def _query_digest(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]


@tool(name="search_existing_fields", context=True)
def search_existing_fields(query: str, tool_context: ToolContext) -> str:
    """Read-only tool that searches existing interview fields when a data source is connected.

    This tool never writes to a database, file, or external service.
    If no field data source is connected, it returns an explicit unavailable message.
    """

    invocation_state = tool_context.get("invocation_state", {})
    logger.info(
        "tool=%s knowledge_id=%s query_length=%s query_digest=%s read_only=true connected=false",
        "search_existing_fields",
        invocation_state.get("knowledge_id"),
        len(query),
        _query_digest(query),
    )
    return _NOT_CONNECTED_MESSAGE
