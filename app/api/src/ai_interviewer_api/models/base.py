from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BaseEntity(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    tenantId: str
    createdByUserId: str
    updatedByUserId: str
    ownerUserId: str | None = None
    createdAt: str = Field(default_factory=utc_now)
    updatedAt: str = Field(default_factory=utc_now)
    deletedAt: str | None = None


class AuditLog(BaseEntity):
    actorUserId: str
    action: str
    resourceType: str
    resourceId: str
    result: str
    detail: dict
