import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from ai_interviewer_api.auth.deps import UserContext, get_current_user
from ai_interviewer_api.core.permissions import require_management_role
from ai_interviewer_api.models.domain import Document, DocumentReadStatus
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.repositories.document_knowledge import (
    INDEXED_STATUSES,
    DocumentKnowledgeBackendError,
    document_knowledge_repository,
)
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.common import get_scoped_item
from ai_interviewer_api.schemas.requests import (
    PriorKnowledgeCreate,
    PriorKnowledgeUpdate,
    ReadStatusUpdate,
)
from ai_interviewer_api.services.document_ingestion import (
    DocumentIngestionError,
    ingest_text_document,
)
from ai_interviewer_api.services.audit import write_audit_log

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


def _prior_knowledge_document(
    *,
    knowledge_id: str,
    user: UserContext,
    payload: PriorKnowledgeCreate,
) -> dict:
    title = payload.title.strip()
    content = payload.content
    if not title:
        raise HTTPException(status_code=422, detail="prior_knowledge_title_required")
    if not content.strip():
        raise HTTPException(status_code=422, detail="prior_knowledge_content_required")
    item = Document(
        id=str(uuid4()),
        tenantId=user.tenant_id,
        createdByUserId=user.user_id,
        updatedByUserId=user.user_id,
        knowledgeId=knowledge_id,
        fileName=title,
        contentType="text/plain",
        sourceType="prior_knowledge",
        title=title,
        knowledgeType=payload.knowledgeType,
        contentFormat=None,
        content=content,
        ingestionStatus="processing",
        progressPercent=20,
    )
    return item.model_dump()


def _require_prior_knowledge(document: dict) -> None:
    if document.get("sourceType") != "prior_knowledge":
        raise HTTPException(status_code=404, detail="prior_knowledge_not_found")


def _index_prior_knowledge(item: dict, *, content: str) -> dict:
    try:
        result = ingest_text_document(item, content)
    except DocumentKnowledgeBackendError as error:
        logger.exception(
            "prior_knowledge_backend_unavailable document_id=%s", item["id"]
        )
        raise HTTPException(status_code=503, detail="document_backend_unavailable") from error
    except DocumentIngestionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _document_summary(result.document)


@router.post("/knowledges/{knowledge_id}/prior-knowledge")
def create_prior_knowledge(
    knowledge_id: str,
    payload: PriorKnowledgeCreate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Save one directly entered text/Markdown knowledge entry and index it."""

    require_management_role(user)
    get_scoped_item("knowledges", knowledge_id, user, "knowledge_not_found")
    item = _prior_knowledge_document(
        knowledge_id=knowledge_id,
        user=user,
        payload=payload,
    )
    store.upsert("documents", item)
    return _index_prior_knowledge(item, content=str(item.get("content") or ""))


@router.get("/knowledges/{knowledge_id}/prior-knowledge")
def list_prior_knowledge(
    knowledge_id: str,
    user: UserContext = Depends(get_current_user),
) -> list[dict]:
    require_management_role(user)
    get_scoped_item("knowledges", knowledge_id, user, "knowledge_not_found")
    return [
        _document_summary(row)
        for row in store.list("documents", user.tenant_id)
        if row.get("knowledgeId") == knowledge_id
        and row.get("sourceType") == "prior_knowledge"
        and row.get("deletedAt") is None
    ]


@router.patch("/prior-knowledge/{document_id}")
def update_prior_knowledge(
    document_id: str,
    payload: PriorKnowledgeUpdate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_management_role(user)
    item = get_scoped_item(
        "documents",
        document_id,
        user,
        "prior_knowledge_not_found",
    )
    _require_prior_knowledge(item)
    if not payload.title.strip():
        raise HTTPException(status_code=422, detail="prior_knowledge_title_required")
    if not payload.content.strip():
        raise HTTPException(status_code=422, detail="prior_knowledge_content_required")
    item.update(
        {
            "updatedByUserId": user.user_id,
            "updatedAt": utc_now(),
            "title": payload.title.strip(),
            "fileName": payload.title.strip(),
            "knowledgeType": payload.knowledgeType,
            "contentFormat": None,
            "contentType": "text/plain",
            "content": payload.content,
            "ingestionStatus": "processing",
            "progressPercent": 20,
            "errorMessage": None,
        }
    )
    store.upsert("documents", item)
    return _index_prior_knowledge(item, content=str(item.get("content") or ""))


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
