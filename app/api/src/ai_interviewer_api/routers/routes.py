from fastapi import APIRouter

from ai_interviewer_api.routers.chats import answer_chat, create_chat, router as chats_router
from ai_interviewer_api.routers.documents import (
    acknowledge_document,
    create_document,
    list_documents,
    router as documents_router,
    update_read_status,
)
from ai_interviewer_api.routers.dev_tools import router as dev_tools_router
from ai_interviewer_api.routers.health import health, me, router as health_router
from ai_interviewer_api.routers.interview_prompt_profiles import (
    create_interview_prompt_profile,
    delete_interview_prompt_profile,
    list_interview_prompt_profiles,
    router as interview_prompt_profiles_router,
    update_interview_prompt_profile,
)
from ai_interviewer_api.routers.internal_voice import (
    create_internal_assistant_event,
    create_internal_connection_event,
    create_internal_voice_turn,
    process_internal_voice_turn,
    router as internal_voice_router,
)
from ai_interviewer_api.routers.knowledge_dbs import (
    create_knowledge_db,
    delete_knowledge_db,
    get_knowledge_db,
    list_knowledge_dbs,
    router as knowledge_dbs_router,
    update_knowledge_db,
)
from ai_interviewer_api.routers.knowledge_fields import (
    create_field,
    delete_field,
    generate_fields,
    list_fields,
    router as knowledge_fields_router,
    suggest_fields,
    update_field,
)
from ai_interviewer_api.routers.knowledges import (
    create_record_summary_draft,
    create_knowledge,
    delete_knowledge,
    get_knowledge,
    list_knowledges,
    router as knowledges_router,
    update_knowledge,
)
from ai_interviewer_api.routers.proposals import (
    approve_all,
    approve_proposal,
    bulk_approve,
    list_proposals,
    router as proposals_router,
)
from ai_interviewer_api.routers.records import (
    create_record,
    create_record_message,
    create_summary_proposal,
    delete_record,
    get_record_interview_state,
    get_record,
    list_records,
    router as records_router,
    stream_record,
    update_record,
)
from ai_interviewer_api.routers.voice_sessions import (
    create_record_voice_session,
    get_record_voice_session,
    router as voice_sessions_router,
    stop_record_voice_session,
)

router = APIRouter()
router.include_router(health_router)
router.include_router(dev_tools_router)
router.include_router(interview_prompt_profiles_router)
router.include_router(knowledge_dbs_router)
router.include_router(knowledges_router)
router.include_router(knowledge_fields_router)
router.include_router(records_router)
router.include_router(voice_sessions_router)
router.include_router(proposals_router)
router.include_router(documents_router)
router.include_router(chats_router)
router.include_router(internal_voice_router)

__all__ = [
    "acknowledge_document",
    "answer_chat",
    "approve_all",
    "approve_proposal",
    "bulk_approve",
    "create_chat",
    "create_document",
    "create_field",
    "create_internal_assistant_event",
    "create_internal_connection_event",
    "create_internal_voice_turn",
    "create_knowledge_db",
    "create_knowledge",
    "create_record_summary_draft",
    "create_record",
    "create_record_message",
    "create_record_voice_session",
    "create_summary_proposal",
    "delete_field",
    "delete_knowledge_db",
    "delete_knowledge",
    "delete_record",
    "generate_fields",
    "get_knowledge_db",
    "get_knowledge",
    "get_record",
    "get_record_interview_state",
    "get_record_voice_session",
    "health",
    "create_interview_prompt_profile",
    "delete_interview_prompt_profile",
    "list_documents",
    "list_fields",
    "list_interview_prompt_profiles",
    "list_knowledge_dbs",
    "list_knowledges",
    "list_proposals",
    "list_records",
    "me",
    "process_internal_voice_turn",
    "router",
    "stream_record",
    "stop_record_voice_session",
    "suggest_fields",
    "update_interview_prompt_profile",
    "update_field",
    "update_knowledge_db",
    "update_knowledge",
    "update_read_status",
    "update_record",
]
