from ai_interviewer_api.services.prompts.loader import (
    get_field_fill_system_prompt,
    get_json_repair_system_prompt,
)


# Backward-compatible aliases for tests and callers that still import this module.
FIELD_SUGGESTION_SYSTEM_PROMPT = get_field_fill_system_prompt()
FIELD_SUGGESTION_GUARD_PROMPT = ""
JSON_REPAIR_SYSTEM_PROMPT = get_json_repair_system_prompt()
