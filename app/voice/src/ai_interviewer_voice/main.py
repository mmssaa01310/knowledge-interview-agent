import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ai_interviewer_voice.config import settings
from ai_interviewer_voice.routers.health import router as health_router
from ai_interviewer_voice.routers.webrtc import router as webrtc_router

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s", force=True)

app = FastAPI(title=settings.app_name, version="0.1.0")


@app.middleware("http")
async def validate_cookie_origin(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.cookies.get("kikiori_session") and not request.headers.get("authorization", "").startswith("Bearer "):
        defaults = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174"
        allowed = {item.strip().rstrip("/") for item in os.getenv("AUTH_ALLOWED_ORIGINS", defaults).split(",") if item.strip()}
        if request.headers.get("origin", "").rstrip("/") not in allowed:
            return JSONResponse({"detail": "invalid_origin"}, status_code=403)
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health_router)
app.include_router(webrtc_router)
