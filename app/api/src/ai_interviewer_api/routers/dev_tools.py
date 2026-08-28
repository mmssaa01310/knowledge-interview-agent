"""
Role:
    ローカル開発専用の操作APIを公開する。

Summary:
    音声会話テスト用データの再作成と状態リセットを提供する。

Relations:
    Uses dev_voice_demo and settings. Included by the aggregate API router.
"""

from fastapi import APIRouter, Depends, HTTPException

from ai_interviewer_api.auth.deps import UserContext, get_current_user
from ai_interviewer_api.core.config import settings
from ai_interviewer_api.core.permissions import require_management_role
from ai_interviewer_api.services.dev_system_requirement_demo import reset_dev_system_requirement_demo
from ai_interviewer_api.services.dev_voice_demo import reset_dev_voice_demo

router = APIRouter(prefix="/api/dev")


@router.post("/voice-demo/reset")
def reset_voice_demo(user: UserContext = Depends(get_current_user)) -> dict[str, str]:
    if not settings.dev_auto_seed_voice_demo:
        raise HTTPException(status_code=404, detail="dev_voice_demo_disabled")
    require_management_role(user)
    return reset_dev_voice_demo()


@router.post("/system-requirement-demo/reset")
def reset_system_requirement_demo(user: UserContext = Depends(get_current_user)) -> dict[str, str]:
    if not settings.dev_auto_seed_system_requirement_demo:
        raise HTTPException(status_code=404, detail="dev_system_requirement_demo_disabled")
    require_management_role(user)
    return reset_dev_system_requirement_demo()
