"""Recoverable receipt consumer, owned by API lifespan rather than browser requests."""

import asyncio
import logging
from time import time

from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.core.permissions import require_record_action
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.common import ensure_interviewer_knowledge_access
from ai_interviewer_api.schemas.live import LiveCaptureCreate
from ai_interviewer_api.services.live_capture import process_live_capture

logger = logging.getLogger(__name__)


def consume_pending_captures() -> None:
    visited = set()
    for receipt in store.pending_live_captures():
        session_key = (receipt["tenantId"], receipt["recordId"], receipt["liveCaptureId"])
        if session_key in visited:
            continue
        visited.add(session_key)
        # Replica-safe serialization. Receipt ingestion uses a different lock,
        # so an LLM request cannot delay saving the next transcript batch.
        with store.live_capture_lock(f"live-process:{receipt['tenantId']}:{receipt['recordId']}"):
            latest = store.get("messages", receipt["id"])
            if not latest or latest.get("liveCaptureApplied") or latest.get("liveCaptureRetryAt", 0) > time():
                continue
            try:
                user = UserContext(**latest["liveCaptureUser"])
                record = store.get("records", latest["recordId"])
                if not record or record.get("deletedAt") or record.get("tenantId") != user.tenant_id:
                    raise ValueError("live_capture_record_unavailable")
                require_record_action(record, user, "interview_read")
                if record.get("status") == "approved":
                    raise ValueError("live_capture_record_approved")
                knowledge = store.get("knowledges", record["knowledgeId"])
                if not knowledge or knowledge.get("tenantId") != user.tenant_id:
                    raise ValueError("live_capture_knowledge_unavailable")
                ensure_interviewer_knowledge_access(knowledge, user)
                # Interpret all receipts already available for this session in
                # one cumulative observation, without making audio wait.
                session_receipts = [row for row in store.list("messages", user.tenant_id)
                                    if row.get("recordId") == record["id"]
                                    and row.get("liveCaptureId") == latest["liveCaptureId"]]
                if any(row.get("liveCaptureRetryAt", 0) > time() for row in session_receipts
                       if not row.get("liveCaptureApplied")):
                    continue
                latest = max(session_receipts, key=lambda row: row["liveCaptureRevision"])
                payload = LiveCaptureCreate(
                    record_id=record["id"], capture_id=latest["liveCaptureId"],
                    revision=latest["liveCaptureRevision"],
                    fragments=[{key: value for key, value in fragment.items() if key != "id"}
                               for fragment in latest["liveTranscriptFragments"]],
                )
                process_live_capture(payload, record=record, knowledge=knowledge, user=user, coalesce=True)
            except Exception:
                # Raw evidence stays durable across failures and API restarts.
                latest = dict(store.get("messages", receipt["id"]) or receipt)
                attempts = latest.get("liveCaptureAttempts", 0) + 1
                latest.update(liveCaptureAttempts=attempts,
                              liveCaptureRetryAt=time() + min(60, 2 ** min(attempts, 6)))
                store.upsert("messages", latest)
                logger.warning("live_capture_retry_scheduled receipt_id=%s attempts=%s", receipt["id"], attempts)


async def run_capture_consumer(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(consume_pending_captures)
        except Exception:
            logger.exception("live_capture_consumer_failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except TimeoutError:
            pass
