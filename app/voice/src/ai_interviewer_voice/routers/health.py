from fastapi import APIRouter

from ai_interviewer_voice.config import settings

router = APIRouter(prefix="/voice")


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "voice",
        "provider": settings.runtime_provider,
    }
