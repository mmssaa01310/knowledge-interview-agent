from fastapi import APIRouter, Depends

from ai_interviewer_api.auth.deps import UserContext, get_current_user
from ai_interviewer_api.schemas.live import LiveSessionCreate, LiveSessionResponse
from ai_interviewer_api.services.live_session import create_live_session


router = APIRouter(prefix="/api/live")


@router.post("/sessions", response_model=LiveSessionResponse)
def create_live_web_session(
    payload: LiveSessionCreate,
    _: UserContext = Depends(get_current_user),
) -> LiveSessionResponse:
    return create_live_session(payload)
