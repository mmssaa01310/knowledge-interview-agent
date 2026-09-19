"""Opt-in real extraction smoke test. All records use the test in-memory store."""
import os
from time import monotonic

import pytest

from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.schemas.live import LiveCaptureCreate
from ai_interviewer_api.services.live_capture import process_live_capture


@pytest.mark.skipif(os.getenv("RUN_LIVE_CAPTURE_LLM") != "1", reason="real LLM opt-in")
def test_real_llm_captures_followups_and_correction(clean_interview_store):
    user = UserContext(user_id="capture-smoke-user", tenant_id="capture-smoke", role="interviewer", display_name="test")
    record = {"id": "capture-smoke", "tenantId": user.tenant_id, "knowledgeId": "capture-smoke-k"}
    knowledge = {"id": record["knowledgeId"], "tenantId": user.tenant_id, "interviewPlan": {"profile": "fixed_form"}}
    store.upsert("knowledges", knowledge)
    store.upsert("records", record)
    for field_id, label, items in [
        ("profile", "基本プロフィール", [("name", "氏名"), ("department", "所属"), ("role", "役職")]),
        ("work", "担当業務", [("task", "主な業務内容")]),
    ]:
        store.upsert("knowledge_fields", {
            "id": field_id, "tenantId": user.tenant_id, "knowledgeId": knowledge["id"],
            "name": label, "askByAi": True, "required": True,
            "questionPlan": {"requiredItems": [{"itemId": key, "label": name} for key, name in items]},
        })
    transcripts = [
        [("assistant", "お名前、所属、役職を教えてください。"), ("user", "田中太郎です。開発部です。")],
        [("assistant", "役職を教えてください。"), ("user", "課長です。")],
        [("assistant", "担当業務を教えてください。"), ("user", "品質管理を担当しています。それと訂正します。役職は課長ではなく部長です。")],
    ]
    for revision, fragments in enumerate(transcripts, 1):
        started = monotonic()
        result = process_live_capture(LiveCaptureCreate(
            record_id=record["id"], capture_id="smoke", revision=revision,
            fragments=[{"role": role, "text": text} for role, text in fragments],
        ), record=record, knowledge=knowledge, user=user)
        print(f"live_capture_smoke revision={revision} elapsed_seconds={monotonic() - started:.2f}")
        if revision == 1:
            assert result["interviewState"]["fieldStates"]["profile"]["answerState"] != "CONFIRMED"
            assert next(field for field in result["checklist"] if field["id"] == "profile")["missing_required_items"] == ["役職"]
    fields = result["interviewState"]["fieldStates"]
    assert all(field["answerState"] == "CONFIRMED" for field in fields.values())
    assert "田中" in fields["profile"]["recordAnswer"]
    assert "開発部" in fields["profile"]["recordAnswer"]
    assert "部長" in fields["profile"]["recordAnswer"]
    assert "課長" not in fields["profile"]["recordAnswer"]
    assert "品質管理" in fields["work"]["recordAnswer"]
