from ai_interviewer_api.repositories.store import store


def queue_document(document_id: str) -> dict:
    document = store.get("documents", document_id)
    if not document:
        raise KeyError(document_id)
    document["ingestionStatus"] = "queued"
    document["progressPercent"] = 10
    store.upsert("documents", document)
    return document
