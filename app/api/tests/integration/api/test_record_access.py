import pytest
from fastapi import HTTPException

from ai_interviewer_api.auth.deps import DEV_TOKENS, UserContext
from ai_interviewer_api.models.domain import InterviewRecord
from ai_interviewer_api.models.interview_plan import InterviewPlan
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.knowledge_dbs import create_knowledge_db, list_knowledge_dbs
from ai_interviewer_api.routers.knowledges import create_knowledge
from ai_interviewer_api.routers.records import (
    create_record,
    create_record_message,
    delete_record,
    get_record,
    get_record_interview_state,
    list_records,
    list_accessible_records,
    update_record,
)
from ai_interviewer_api.schemas.requests import (
    ChatMessageCreate,
    KnowledgeCreate,
    KnowledgeDbCreate,
    RecordCreate,
    RecordUpdate,
)


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.tables.clear()


def _create_knowledge(user: UserContext) -> dict:
    knowledge_db = create_knowledge_db(KnowledgeDbCreate(name="アクセス制御テストDB"), user)
    return create_knowledge(
        knowledge_db["id"],
        KnowledgeCreate(
            name="アクセス制御テスト",
            purpose="記録の権限を確認する",
            interviewPlan=InterviewPlan(profile="fixed_form", modelId="global.openai.gpt-5.6-terra"),
        ),
        user,
    )


def _create_record(
    user: UserContext,
    *,
    owner_user_id: str | None = None,
    viewer_user_ids: list[str] | None = None,
) -> dict:
    knowledge = _create_knowledge(user)
    return create_record(
        knowledge["id"],
        RecordCreate(
            title="アクセス制御対象記録",
            ownerUserId=owner_user_id,
            viewerUserIds=viewer_user_ids or [],
        ),
        user,
    )


def _approve_record(record: dict, user: UserContext) -> dict:
    update_record(record["id"], RecordUpdate(status="submitted"), user)
    return update_record(record["id"], RecordUpdate(status="approved"), user)


def test_record_creation_persists_selected_interview_locale() -> None:
    manager = DEV_TOKENS["dev-manager"]
    knowledge = _create_knowledge(manager)

    record = create_record(
        knowledge["id"],
        RecordCreate(title="ポルトガル語インタビュー", interviewLocale="pt-BR"),
        manager,
    )

    assert record["interviewLocale"] == "pt-BR"


def test_assigned_interviewer_can_select_locale_before_interview_starts() -> None:
    manager = DEV_TOKENS["dev-manager"]
    interviewer = DEV_TOKENS["dev-interviewer"]
    record = _create_record(manager, owner_user_id=interviewer.user_id)

    updated = update_record(record["id"], RecordUpdate(interviewLocale="pt-BR"), interviewer)

    assert updated["interviewLocale"] == "pt-BR"

    store.upsert(
        "messages",
        {
            "id": "started-message",
            "tenantId": interviewer.tenant_id,
            "recordId": record["id"],
        },
    )
    with pytest.raises(HTTPException) as exc_info:
        update_record(record["id"], RecordUpdate(interviewLocale="en-US"), interviewer)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "interview_locale_locked_after_start"


def test_locale_can_be_selected_after_pristine_interview_state_was_loaded() -> None:
    manager = DEV_TOKENS["dev-manager"]
    interviewer = DEV_TOKENS["dev-interviewer"]
    record = _create_record(manager, owner_user_id=interviewer.user_id)

    snapshot = get_record_interview_state(record["id"], interviewer)
    assert snapshot["interviewState"]["currentQuestionId"] is None

    updated = update_record(record["id"], RecordUpdate(interviewLocale="en-US"), interviewer)

    assert updated["interviewLocale"] == "en-US"


def test_record_creation_requires_saved_interview_configuration() -> None:
    manager = DEV_TOKENS["dev-manager"]
    knowledge_db = create_knowledge_db(KnowledgeDbCreate(name="設定未完了テストDB"), manager)
    knowledge = create_knowledge(
        knowledge_db["id"],
        KnowledgeCreate(name="設定未完了のナレッジ"),
        manager,
    )

    with pytest.raises(HTTPException) as exc_info:
        create_record(knowledge["id"], RecordCreate(title="作成できない記録"), manager)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "interview_configuration_required"

    legacy_record = InterviewRecord(
        tenantId=manager.tenant_id,
        createdByUserId=manager.user_id,
        updatedByUserId=manager.user_id,
        knowledgeId=knowledge["id"],
        knowledgeName=knowledge["name"],
        ownerUserId=manager.user_id,
        title="設定前に作成された記録",
    ).model_dump()
    store.upsert("records", legacy_record)

    with pytest.raises(HTTPException) as transition_exc_info:
        update_record(legacy_record["id"], RecordUpdate(status="in_progress"), manager)

    assert transition_exc_info.value.status_code == 409
    assert transition_exc_info.value.detail == "interview_configuration_required"


def test_completed_interview_is_submitted_automatically() -> None:
    manager = DEV_TOKENS["dev-manager"]
    record = _create_record(manager)
    store.upsert(
        "interview_states",
        {
            "id": f"interview-state-{record['id']}",
            "tenantId": manager.tenant_id,
            "recordId": record["id"],
            "status": "completed",
            "fieldStates": {},
        },
    )

    snapshot = get_record_interview_state(record["id"], manager)

    assert snapshot["status"] == "completed"
    assert store.get("records", record["id"])["status"] == "submitted"
    assert store.list("audit_logs", manager.tenant_id)[-1]["action"] == "record_status_change"


def test_new_record_is_immediately_answerable_by_assigned_interviewer() -> None:
    manager = DEV_TOKENS["dev-manager"]
    interviewer = DEV_TOKENS["dev-interviewer"]
    assigned = _create_record(manager, owner_user_id=interviewer.user_id)
    assert assigned["status"] == "in_progress"
    manager_owned = _create_record(manager)

    records = list_accessible_records(interviewer)

    assert [record["id"] for record in records] == [assigned["id"]]
    assert get_record(assigned["id"], interviewer)["id"] == assigned["id"]
    with pytest.raises(HTTPException) as exc_info:
        get_record(manager_owned["id"], interviewer)
    assert exc_info.value.status_code == 403

    knowledge_dbs = list_knowledge_dbs(interviewer)
    assert len(knowledge_dbs) == 2


def test_interviewer_can_create_a_record_for_self_from_active_knowledge() -> None:
    manager = DEV_TOKENS["dev-manager"]
    interviewer = DEV_TOKENS["dev-interviewer"]
    knowledge = _create_knowledge(manager)

    record = create_record(
        knowledge["id"],
        RecordCreate(title="対象者が開始したインタビュー"),
        interviewer,
    )

    assert record["ownerUserId"] == interviewer.user_id
    assert record["createdByUserId"] == interviewer.user_id
    assert record["status"] == "in_progress"
    assert [item["id"] for item in list_accessible_records(interviewer)] == [record["id"]]


def test_interviewer_cannot_assign_a_self_started_record_to_another_user() -> None:
    manager = DEV_TOKENS["dev-manager"]
    interviewer = DEV_TOKENS["dev-interviewer"]
    knowledge = _create_knowledge(manager)

    with pytest.raises(HTTPException) as exc_info:
        create_record(
            knowledge["id"],
            RecordCreate(title="所有者を変更する記録", ownerUserId=manager.user_id),
            interviewer,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "record_owner_must_be_current_user"


def test_only_admin_can_delete_record() -> None:
    manager = DEV_TOKENS["dev-manager"]
    admin = DEV_TOKENS["dev-admin"]
    record = _create_record(manager)

    with pytest.raises(HTTPException) as manager_exc_info:
        delete_record(record["id"], manager)

    assert manager_exc_info.value.status_code == 403
    assert delete_record(record["id"], admin) == {"deleted": True}
    assert store.get("records", record["id"]) is None
    assert list_accessible_records(admin) == []
    assert list_records(record["knowledgeId"], admin) == []


def test_deleted_records_are_excluded_from_access_and_knowledge_counts() -> None:
    manager = DEV_TOKENS["dev-manager"]
    knowledge = _create_knowledge(manager)
    record = create_record(knowledge["id"], RecordCreate(title="削除済み記録"), manager)
    record["deletedAt"] = "2026-08-31T00:00:00Z"
    store.upsert("records", record)

    assert list_accessible_records(manager) == []
    assert list_records(knowledge["id"], manager) == []

    from ai_interviewer_api.routers.knowledges import get_knowledge

    assert get_knowledge(knowledge["id"], manager)["recordCount"] == 0


def test_viewer_can_only_read_explicitly_shared_approved_records() -> None:
    manager = DEV_TOKENS["dev-manager"]
    viewer = DEV_TOKENS["dev-viewer"]
    shared = _create_record(
        manager,
        owner_user_id=DEV_TOKENS["dev-interviewer"].user_id,
        viewer_user_ids=[viewer.user_id],
    )
    _approve_record(shared, manager)
    not_shared = _create_record(
        manager,
        owner_user_id=DEV_TOKENS["dev-interviewer"].user_id,
    )

    records = list_accessible_records(viewer)

    assert [record["id"] for record in records] == [shared["id"]]
    assert get_record(shared["id"], viewer)["status"] == "approved"
    assert store.get("interview_states", f"interview-state-{shared['id']}") is None
    get_record_interview_state(shared["id"], viewer)
    assert store.get("interview_states", f"interview-state-{shared['id']}") is None
    with pytest.raises(HTTPException) as update_exc_info:
        update_record(shared["id"], RecordUpdate(title="閲覧者による変更"), viewer)
    assert update_exc_info.value.status_code == 403
    with pytest.raises(HTTPException) as unpublished_exc_info:
        get_record(not_shared["id"], viewer)
    assert unpublished_exc_info.value.status_code == 403


def test_interviewer_can_submit_returned_record_and_manager_can_review() -> None:
    manager = DEV_TOKENS["dev-manager"]
    interviewer = DEV_TOKENS["dev-interviewer"]
    record = _create_record(manager, owner_user_id=interviewer.user_id)
    store.upsert(
        "interview_states",
        {"id": f"interview-state-{record['id']}", "recordId": record["id"], "status": "completed"},
    )

    submitted = update_record(record["id"], RecordUpdate(status="submitted"), interviewer)
    assert submitted["status"] == "submitted"

    returned = update_record(
        record["id"],
        RecordUpdate(status="returned", reviewNote="期待結果を具体化してください"),
        manager,
    )
    assert returned["status"] == "returned"
    assert returned["reviewNote"] == "期待結果を具体化してください"

    reopened = update_record(record["id"], RecordUpdate(status="in_progress"), interviewer)
    assert reopened["status"] == "in_progress"
    assert reopened["reviewNote"] is None


def test_interviewer_cannot_manage_or_answer_another_users_record() -> None:
    manager = DEV_TOKENS["dev-manager"]
    interviewer = DEV_TOKENS["dev-interviewer"]
    record = _create_record(manager, owner_user_id=manager.user_id)

    with pytest.raises(HTTPException) as message_exc_info:
        create_record_message(
            record["id"],
            ChatMessageCreate(content="権限外の回答"),
            interviewer,
        )
    assert message_exc_info.value.status_code == 403
