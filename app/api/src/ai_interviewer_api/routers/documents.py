from fastapi import APIRouter, Depends

from ai_interviewer_api.auth.deps import UserContext, get_current_user
from ai_interviewer_api.core.permissions import require_roles
from ai_interviewer_api.models.domain import Document, DocumentReadStatus
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.common import get_scoped_item
from ai_interviewer_api.schemas.requests import DocumentCreate, ReadStatusUpdate
from ai_interviewer_api.services.document_ingestion_dispatcher import queue_document

router = APIRouter(prefix="/api")


@router.post("/knowledges/{knowledge_id}/documents")
def create_document(
    knowledge_id: str,
    payload: DocumentCreate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_roles(user, {"admin", "knowledge_manager"})
    get_scoped_item("knowledges", knowledge_id, user, "knowledge_not_found")
    item = Document(
        tenantId=user.tenant_id,
        createdByUserId=user.user_id,
        updatedByUserId=user.user_id,
        knowledgeId=knowledge_id,
        **payload.model_dump(),
    )
    store.upsert("documents", item.model_dump())
    queue_document(item.id)
    return store.get("documents", item.id)


@router.get("/knowledges/{knowledge_id}/documents")
def list_documents(knowledge_id: str, user: UserContext = Depends(get_current_user)) -> list[dict]:
    get_scoped_item("knowledges", knowledge_id, user, "knowledge_not_found")
    return [row for row in store.list("documents", user.tenant_id) if row["knowledgeId"] == knowledge_id]


@router.post("/documents/{document_id}/read")
def update_read_status(
    document_id: str,
    payload: ReadStatusUpdate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    get_scoped_item("documents", document_id, user, "document_not_found")
    item = DocumentReadStatus(
        tenantId=user.tenant_id,
        createdByUserId=user.user_id,
        updatedByUserId=user.user_id,
        documentId=document_id,
        userId=user.user_id,
        readStatus=payload.readStatus,
        readProgress=payload.readProgress,
    )
    store.upsert("document_read_status", item.model_dump())
    return item.model_dump()


@router.post("/documents/{document_id}/acknowledge")
def acknowledge_document(document_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    get_scoped_item("documents", document_id, user, "document_not_found")
    item = DocumentReadStatus(
        tenantId=user.tenant_id,
        createdByUserId=user.user_id,
        updatedByUserId=user.user_id,
        documentId=document_id,
        userId=user.user_id,
        readStatus="acknowledged",
        readProgress=100,
        acknowledged=True,
    )
    store.upsert("document_read_status", item.model_dump())
    return item.model_dump()
