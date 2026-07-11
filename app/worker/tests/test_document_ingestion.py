from ai_interviewer_worker.jobs.document_ingestion import process_document


def test_process_document_reaches_completed() -> None:
    results = process_document("doc-1")
    assert results[-1].status == "completed"
