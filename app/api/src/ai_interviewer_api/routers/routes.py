from fastapi import APIRouter

from ai_interviewer_api.routers.documents import (
    acknowledge_document,
    create_document,
    delete_document,
    get_document_content,
    list_documents,
    router as documents_router,
    upload_document,
    update_read_status,
)
from ai_interviewer_api.routers.admin_dashboard import router as admin_dashboard_router
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
from ai_interviewer_api.routers.knowledge_tags import (
    create_knowledge_tag,
    delete_knowledge_tag,
    list_knowledge_tags,
    router as knowledge_tags_router,
    update_knowledge_tag,
)
from ai_interviewer_api.routers.knowledges import (
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
    delete_record,
    get_record_interview_state,
    get_record_interview_context,
    get_record,
    list_records,
    list_accessible_records,
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
router.include_router(knowledge_tags_router)
router.include_router(records_router)
router.include_router(voice_sessions_router)
router.include_router(proposals_router)
router.include_router(documents_router)
router.include_router(internal_voice_router)
router.include_router(admin_dashboard_router)

__all__ = [
    "acknowledge_document",
    "approve_all",
    "approve_proposal",
    "bulk_approve",
    "create_document",
    "delete_document",
    "get_document_content",
    "upload_document",
    "create_field",
    "create_internal_assistant_event",
    "create_internal_connection_event",
    "create_internal_voice_turn",
    "create_knowledge_db",
    "create_knowledge_tag",
    "create_knowledge",
    "create_record",
    "create_record_message",
    "create_record_voice_session",
    "delete_field",
    "delete_knowledge_db",
    "delete_knowledge",
    "delete_knowledge_tag",
    "delete_record",
    "generate_fields",
    "get_knowledge_db",
    "get_knowledge",
    "get_record",
    "get_record_interview_context",
    "get_record_interview_state",
    "get_record_voice_session",
    "health",
    "create_interview_prompt_profile",
    "delete_interview_prompt_profile",
    "list_documents",
    "list_fields",
    "list_interview_prompt_profiles",
    "list_knowledge_dbs",
    "list_knowledge_tags",
    "list_knowledges",
    "list_proposals",
    "list_records",
    "list_accessible_records",
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
    "update_knowledge_tag",
    "update_read_status",
    "update_record",
]
