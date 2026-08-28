from collections.abc import Mapping

from fastapi import HTTPException

from ai_interviewer_api.models.interview_plan import STRUCTURED_INTERVIEW_MODEL_IDS


INTERVIEW_PROFILES = frozenset({"fixed_form", "business_process", "system_requirement"})


def is_interview_configuration_complete(knowledge: Mapping[str, object]) -> bool:
    """Return whether the saved knowledge can create and start an interview record."""

    plan = knowledge.get("interviewPlan")
    if not isinstance(plan, Mapping):
        return False
    return (
        plan.get("profile") in INTERVIEW_PROFILES
        and plan.get("modelId") in STRUCTURED_INTERVIEW_MODEL_IDS
    )


def require_interview_configuration(knowledge: Mapping[str, object]) -> None:
    if not is_interview_configuration_complete(knowledge):
        raise HTTPException(status_code=409, detail="interview_configuration_required")
