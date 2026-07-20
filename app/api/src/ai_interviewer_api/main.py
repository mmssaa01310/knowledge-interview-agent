import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ai_interviewer_api.core.config import settings
from ai_interviewer_api.routers.routes import router
from ai_interviewer_api.services.dev_maintenance_demo import ensure_dev_maintenance_demo
from ai_interviewer_api.services.dev_voice_demo import ensure_dev_voice_demo

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s", force=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.dev_auto_seed_voice_demo:
        identifiers = ensure_dev_voice_demo()
        logging.getLogger(__name__).info(
            "dev_voice_demo_ready knowledge_db_id=%s knowledge_id=%s record_id=%s",
            identifiers["knowledgeDbId"],
            identifiers["knowledgeId"],
            identifiers["recordId"],
        )
    if settings.dev_auto_seed_maintenance_demo:
        identifiers = ensure_dev_maintenance_demo()
        logging.getLogger(__name__).info(
            "dev_maintenance_demo_ready knowledge_db_id=%s knowledge_id=%s record_id=%s",
            identifiers["knowledgeDbId"],
            identifiers["knowledgeId"],
            identifiers["recordId"],
        )
    yield


app = FastAPI(title="AI Interviewer API", version="0.1.0", lifespan=lifespan)
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
app.include_router(router)
