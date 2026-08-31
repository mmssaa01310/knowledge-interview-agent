import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ai_interviewer_api.auth.deps import UserContext, get_current_user
from ai_interviewer_api.core.config import settings
from ai_interviewer_api.core.permissions import require_management_role
from ai_interviewer_api.models.domain import Document, DocumentReadStatus
from ai_interviewer_api.repositories.document_knowledge import (
    INDEXED_STATUSES,
    DocumentKnowledgeBackendError,
    document_knowledge_repository,
)
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.common import get_scoped_item
from ai_interviewer_api.schemas.requests import DocumentCreate, ReadStatusUpdate
from ai_interviewer_api.services.document_ingestion_dispatcher import queue_document
from ai_interviewer_api.services.document_ingestion import (
    DocumentIngestionError,
    document_content_type,
    ingest_document,
    safe_document_file_name,
    UnsupportedDocumentTypeError,
)
from ai_interviewer_api.services.audit import write_audit_log

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


@router.post("/knowledges/{knowledge_id}/documents")
def create_document(
    knowledge_id: str,
    payload: DocumentCreate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_management_role(user)
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
    return _document_summary(store.get("documents", item.id) or item.model_dump())


@router.post("/knowledges/{knowledge_id}/documents/upload")
async def upload_document(
    knowledge_id: str,
    file: UploadFile = File(...),
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Upload a document, extract its text, and index searchable chunks."""

    require_management_role(user)
    get_scoped_item("knowledges", knowledge_id, user, "knowledge_not_found")
    try:
        file_name = safe_document_file_name(file.filename)
    except DocumentIngestionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    try:
        raw_bytes = await file.read(settings.document_max_upload_bytes + 1)
    except Exception as error:  # noqa: BLE001 - keep file read errors user-safe
        raise HTTPException(status_code=400, detail="document_read_failed") from error
    if len(raw_bytes) > settings.document_max_upload_bytes:
        raise HTTPException(status_code=413, detail="document_size_limit_exceeded")

    item = Document(
        tenantId=user.tenant_id,
        createdByUserId=user.user_id,
        updatedByUserId=user.user_id,
        knowledgeId=knowledge_id,
        fileName=file_name,
        contentType=document_content_type(file_name, file.content_type),
        ingestionStatus="queued",
        progressPercent=10,
    )
    store.upsert("documents", item.model_dump())
    try:
        result = ingest_document(item.model_dump(), raw_bytes)
    except UnsupportedDocumentTypeError as error:
        raise HTTPException(status_code=415, detail=str(error)) from error
    except DocumentKnowledgeBackendError as error:
        logger.exception("document_backend_unavailable document_id=%s", item.id)
        raise HTTPException(status_code=503, detail="document_backend_unavailable") from error
    except DocumentIngestionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _document_summary(result.document)


@router.get("/knowledges/{knowledge_id}/documents")
def list_documents(knowledge_id: str, user: UserContext = Depends(get_current_user)) -> list[dict]:
    require_management_role(user)
    get_scoped_item("knowledges", knowledge_id, user, "knowledge_not_found")
    return [
        _document_summary(row)
        for row in store.list("documents", user.tenant_id)
        if row["knowledgeId"] == knowledge_id
    ]


@router.get("/documents/{document_id}/content")
def get_document_content(document_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    require_management_role(user)
    document = get_scoped_item("documents", document_id, user, "document_not_found")
    if document.get("ingestionStatus") not in INDEXED_STATUSES:
        raise HTTPException(status_code=409, detail="document_content_not_ready")
    try:
        content = document_knowledge_repository.get_document_content(
            document_id=document_id,
            knowledge_id=str(document["knowledgeId"]),
            tenant_id=user.tenant_id,
        )
    except DocumentKnowledgeBackendError as error:
        logger.exception("document_content_read_failed document_id=%s", document_id)
        raise HTTPException(status_code=503, detail="document_backend_unavailable") from error
    if not content:
        raise HTTPException(status_code=404, detail="document_content_not_found")
    return {"document": _document_summary(document), "content": content}


@router.delete("/documents/{document_id}")
def delete_document(document_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    require_management_role(user)
    document = get_scoped_item("documents", document_id, user, "document_not_found")
    try:
        document_knowledge_repository.delete_document(
            document_id=document_id,
            knowledge_id=str(document["knowledgeId"]),
            tenant_id=user.tenant_id,
        )
    except DocumentKnowledgeBackendError as error:
        logger.exception("document_delete_backend_failed document_id=%s", document_id)
        raise HTTPException(status_code=503, detail="document_backend_unavailable") from error
    store.delete("documents", document_id)
    write_audit_log(
        user,
        "delete",
        "document",
        document_id,
        {"fileName": document.get("fileName"), "knowledgeId": document.get("knowledgeId")},
    )
    return {"deleted": True}


@router.post("/documents/{document_id}/read")
def update_read_status(
    document_id: str,
    payload: ReadStatusUpdate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_management_role(user)
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


def _document_summary(document: dict) -> dict:
    return {
        key: value
        for key, value in document.items()
        if key not in {"content", "text", "extractedText", "body", "rawContent"}
    }


@router.post("/documents/{document_id}/acknowledge")
def acknowledge_document(document_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    require_management_role(user)
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
