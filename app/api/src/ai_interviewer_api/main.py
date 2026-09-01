import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ai_interviewer_api.core.config import settings
from ai_interviewer_api.repositories.document_knowledge import (
    document_knowledge_repository,
)
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.routes import router
from ai_interviewer_api.services.dev_maintenance_demo import ensure_dev_maintenance_demo
from ai_interviewer_api.services.dev_system_requirement_demo import ensure_dev_system_requirement_demo
from ai_interviewer_api.services.dev_voice_demo import ensure_dev_voice_demo

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s", force=True)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.ensure_schema()
    document_knowledge_repository.ensure_ready()
    logger.info("database_ready backend=%s", store.health()["backend"])
    logger.info(
        "document_knowledge_backend_ready backend=%s",
        document_knowledge_repository.backend_name,
    )
    logger.info(
        "ai_interview_model_configuration interview_engine=structured_interview structured_interview_model_id=%s question_design_model_id=%s",
        settings.structured_interview_model_id,
        settings.question_design_model_id,
    )
    if settings.dev_auto_seed_voice_demo:
        identifiers = ensure_dev_voice_demo()
        logger.info(
            "dev_voice_demo_ready knowledge_db_id=%s knowledge_id=%s record_id=%s",
            identifiers["knowledgeDbId"],
            identifiers["knowledgeId"],
            identifiers["recordId"],
        )
    if settings.dev_auto_seed_maintenance_demo:
        identifiers = ensure_dev_maintenance_demo()
        logger.info(
            "dev_maintenance_demo_ready knowledge_db_id=%s knowledge_id=%s record_id=%s",
            identifiers["knowledgeDbId"],
            identifiers["knowledgeId"],
            identifiers["recordId"],
        )
    if settings.dev_auto_seed_system_requirement_demo:
        identifiers = ensure_dev_system_requirement_demo()
        logger.info(
            "dev_system_requirement_demo_ready knowledge_db_id=%s knowledge_id=%s record_id=%s",
            identifiers["knowledgeDbId"],
            identifiers["knowledgeId"],
            identifiers["recordId"],
        )
    yield


app = FastAPI(title="KIKIORI API", version="0.1.0", lifespan=lifespan)
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
