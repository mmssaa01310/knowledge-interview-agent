from __future__ import annotations

import pytest
import time

from ai_interviewer_api.agents.interview_knowledge import service as structured_service
from ai_interviewer_api.auth.deps import DEV_TOKENS
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.services import voice_interview as voice_interview_service
from ai_interviewer_api.services.conversation_policy import CanonicalIntentDecision

from .support import FakeStructuredProvider


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.tables.clear()
    for user in DEV_TOKENS.values():
        user.auth_session_id = f"test-session-{user.user_id}"
        store.upsert("users", {"id": user.user_id, "tenantId": user.tenant_id, "displayName": user.display_name, "role": user.role, "active": True, "sessionVersion": 0})
        store.upsert("auth_sessions", {"id": user.auth_session_id, "tenantId": user.tenant_id, "userId": user.user_id, "sessionVersion": 0, "expiresAt": time.time() + 3600})


@pytest.fixture(autouse=True)
def stub_structured_provider(monkeypatch: pytest.MonkeyPatch) -> FakeStructuredProvider:
    provider = FakeStructuredProvider()
    monkeypatch.setattr(
        structured_service,
        "_get_structured_provider",
        lambda *_args, **_kwargs: provider,
    )
    return provider


@pytest.fixture(autouse=True)
def stub_canonical_router(monkeypatch: pytest.MonkeyPatch) -> None:
    def resolve(
        *,
        utterance: str,
        pending_confirmation: bool,
        **_: object,
    ) -> CanonicalIntentDecision:
        normalized = utterance.strip()
        if pending_confirmation and normalized in {
            "はい",
            "大丈夫です。",
            "大丈夫です",
            "合っています",
        }:
            dialogue_act = "CONFIRMATION"
        elif pending_confirmation and normalized in {"違います。", "違います", "いいえ"}:
            dialogue_act = "REJECTION"
        elif any(token in normalized for token in ("どの範囲", "何を聞", "どういう意味")):
            dialogue_act = "QUESTION_TO_ASSISTANT"
        elif any(token in normalized for token in ("えーっと", "えっと", "ちょっと待")):
            dialogue_act = "HESITATION"
        elif normalized.startswith(("訂正", "違う", "間違")):
            dialogue_act = "CORRECTION"
        else:
            dialogue_act = "ANSWER"
        return CanonicalIntentDecision(
            dialogueAct=dialogue_act,
            latency_ms=0.1,
            provider="test",
        )

    monkeypatch.setattr(voice_interview_service, "resolve_canonical_intent", resolve)
