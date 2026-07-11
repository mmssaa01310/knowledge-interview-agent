from dataclasses import dataclass


@dataclass
class IngestionResult:
    document_id: str
    status: str
    progress_percent: int


def process_document(document_id: str) -> list[IngestionResult]:
    return [
        IngestionResult(document_id, "processing", 25),
        IngestionResult(document_id, "text_extracted", 50),
        IngestionResult(document_id, "chunked", 70),
        IngestionResult(document_id, "embedding", 90),
        IngestionResult(document_id, "completed", 100),
    ]
