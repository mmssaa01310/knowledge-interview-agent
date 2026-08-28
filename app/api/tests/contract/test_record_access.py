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
    get_record,
    get_record_interview_state,
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


def _publish_record(record: dict, user: UserContext) -> dict:
    return update_record(record["id"], RecordUpdate(status="in_progress"), user)


def _approve_record(record: dict, user: UserContext) -> dict:
    _publish_record(record, user)
    update_record(record["id"], RecordUpdate(status="submitted"), user)
    return update_record(record["id"], RecordUpdate(status="approved"), user)


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

    with pytest.raises(HTTPException) as publish_exc_info:
        update_record(legacy_record["id"], RecordUpdate(status="in_progress"), manager)

    assert publish_exc_info.value.status_code == 409
    assert publish_exc_info.value.detail == "interview_configuration_required"


def test_interviewer_can_only_access_assigned_published_records() -> None:
    manager = DEV_TOKENS["dev-manager"]
    interviewer = DEV_TOKENS["dev-interviewer"]
    assigned = _create_record(manager, owner_user_id=interviewer.user_id)
    _publish_record(assigned, manager)
    manager_owned = _create_record(manager)
    _publish_record(manager_owned, manager)

    records = list_accessible_records(interviewer)

    assert [record["id"] for record in records] == [assigned["id"]]
    assert get_record(assigned["id"], interviewer)["id"] == assigned["id"]
    with pytest.raises(HTTPException) as exc_info:
        get_record(manager_owned["id"], interviewer)
    assert exc_info.value.status_code == 403

    with pytest.raises(HTTPException) as knowledge_exc_info:
        list_knowledge_dbs(interviewer)
    assert knowledge_exc_info.value.status_code == 403


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
    _publish_record(not_shared, manager)

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
    _publish_record(record, manager)
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
    _publish_record(record, manager)

    with pytest.raises(HTTPException) as message_exc_info:
        create_record_message(
            record["id"],
            ChatMessageCreate(content="権限外の回答"),
            interviewer,
        )
    assert message_exc_info.value.status_code == 403
