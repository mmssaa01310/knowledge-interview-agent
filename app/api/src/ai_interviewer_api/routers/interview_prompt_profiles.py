from fastapi import APIRouter, Depends

from ai_interviewer_api.auth.deps import UserContext, get_current_user
from ai_interviewer_api.core.permissions import require_roles
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.models.domain import InterviewPromptProfile
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.common import get_scoped_item
from ai_interviewer_api.schemas.requests import (
    InterviewPromptProfileCreate,
    InterviewPromptProfileUpdate,
)

router = APIRouter(prefix="/api")

DEFAULT_PROMPT_PROFILES = [
    {
        "slug": "maintenance-initial-triage",
        "name": "保全トラブル初動",
        "prompt": (
            "現象、発生タイミング、影響範囲、設備停止の有無、"
            "最初に確認したことを優先して聞いてください。"
        ),
    },
    {
        "slug": "maintenance-root-cause",
        "name": "原因切り分け",
        "prompt": (
            "再現条件、切り分け順序、除外した原因候補、"
            "判断根拠が分かるように深掘りしてください。"
        ),
    },
    {
        "slug": "maintenance-recurrence-prevention",
        "name": "再発防止",
        "prompt": (
            "暫定対応と恒久対策を分けて聞き、"
            "再発防止の条件、監視方法、残課題まで確認してください。"
        ),
    },
    {
        "slug": "maintenance-inspection-signs",
        "name": "点検と異常兆候",
        "prompt": (
            "普段の点検ポイント、異常の前兆、正常時との違い、"
            "見落としやすい兆候を優先して聞いてください。"
        ),
    },
    {
        "slug": "maintenance-shutdown-judgment",
        "name": "停止判断",
        "prompt": (
            "どの条件で運転継続と停止を判断するか、"
            "しきい値、リスク、現場判断の基準が残るように聞いてください。"
        ),
    },
]


def _ensure_default_profiles(user: UserContext) -> list[dict]:
    profiles = store.list("interview_prompt_profiles", user.tenant_id)
    existing_ids = {profile["id"] for profile in profiles}

    for default_profile in DEFAULT_PROMPT_PROFILES:
        profile_id = f"default-{default_profile['slug']}"
        if profile_id in existing_ids:
            continue
        item = InterviewPromptProfile(
            id=profile_id,
            tenantId=user.tenant_id,
            createdByUserId="system",
            updatedByUserId="system",
            name=default_profile["name"],
            prompt=default_profile["prompt"],
        )
        store.upsert("interview_prompt_profiles", item.model_dump())

    return store.list("interview_prompt_profiles", user.tenant_id)


@router.get("/interview-prompt-profiles")
def list_interview_prompt_profiles(user: UserContext = Depends(get_current_user)) -> list[dict]:
    return _ensure_default_profiles(user)


@router.post("/interview-prompt-profiles")
def create_interview_prompt_profile(
    payload: InterviewPromptProfileCreate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_roles(user, {"admin", "knowledge_manager"})
    _ensure_default_profiles(user)
    item = InterviewPromptProfile(
        tenantId=user.tenant_id,
        createdByUserId=user.user_id,
        updatedByUserId=user.user_id,
        **payload.model_dump(),
    )
    store.upsert("interview_prompt_profiles", item.model_dump())
    return item.model_dump()


@router.patch("/interview-prompt-profiles/{profile_id}")
def update_interview_prompt_profile(
    profile_id: str,
    payload: InterviewPromptProfileUpdate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_roles(user, {"admin", "knowledge_manager"})
    item = get_scoped_item(
        "interview_prompt_profiles",
        profile_id,
        user,
        "interview_prompt_profile_not_found",
    )
    for key, value in payload.model_dump(exclude_unset=True).items():
        item[key] = value
    item["updatedByUserId"] = user.user_id
    item["updatedAt"] = utc_now()
    store.upsert("interview_prompt_profiles", item)
    return item


@router.delete("/interview-prompt-profiles/{profile_id}")
def delete_interview_prompt_profile(
    profile_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_roles(user, {"admin", "knowledge_manager"})
    get_scoped_item(
        "interview_prompt_profiles",
        profile_id,
        user,
        "interview_prompt_profile_not_found",
    )
    store.delete("interview_prompt_profiles", profile_id)
    return {"deleted": True}
