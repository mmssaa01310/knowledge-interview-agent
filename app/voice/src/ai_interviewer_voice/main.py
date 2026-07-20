import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ai_interviewer_voice.config import settings
from ai_interviewer_voice.routers.health import router as health_router
from ai_interviewer_voice.routers.webrtc import router as webrtc_router

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s", force=True)

app = FastAPI(title=settings.app_name, version="0.1.0")
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
