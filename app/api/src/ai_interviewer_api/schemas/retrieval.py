from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

DocumentSourceType = Literal["document", "document_chunk"]


class RetrievedKnowledgeContext(BaseModel):
    """A backend-selected, read-only piece of knowledge for an AI prompt."""

    source_type: Literal[
        "knowledge_field",
        "approved_record",
        "approved_proposal",
        "document",
        "document_chunk",
    ]
    source_id: str
    title: str
    content: str
    score: float = 0.0


class RetrievedSourceReference(BaseModel):
    """The small source reference persisted with an interview question."""

    sourceType: DocumentSourceType
    sourceId: str
    title: str
    score: float = 0.0


@dataclass(frozen=True)
class DocumentQuestionCandidate:
    """A document-grounded value proposed for confirmation before saving."""

    value: str
    source_ids: tuple[str, ...]


def source_references(
    contexts: list[RetrievedKnowledgeContext],
) -> list[dict[str, object]]:
    return [
        RetrievedSourceReference(
            sourceType=_document_source_type(item.source_type),
            sourceId=item.source_id,
            title=item.title,
            score=item.score,
        ).model_dump()
        for item in contexts
        if item.source_type in {"document", "document_chunk"}
    ]

def _document_source_type(source_type: str) -> DocumentSourceType:
    return "document_chunk" if source_type == "document_chunk" else "document"
