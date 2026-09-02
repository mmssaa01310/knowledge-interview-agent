from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from fastapi import HTTPException

from ai_interviewer_api.agents.interview_knowledge.schemas import (
    ProcessModelEditOutput,
    ProcessInteraction,
    ProcessNode,
    ProcessParticipant,
    ProcessPatch,
    RequirementEdit,
    RequirementPatch,
)
from ai_interviewer_api.auth.deps import DEV_TOKENS, UserContext
from ai_interviewer_api.models.interview_plan import InterviewPlan
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.knowledge_dbs import create_knowledge_db
from ai_interviewer_api.routers.knowledges import create_knowledge
from ai_interviewer_api.routers.records import create_record
from ai_interviewer_api.schemas.requests import (
    KnowledgeCreate,
    KnowledgeDbCreate,
    ProcessModelCommand,
    ProcessModelUpdate,
    RecordCreate,
)
from ai_interviewer_api.services.ai_interview import get_interview_state_snapshot
from ai_interviewer_api.services.process_model import edit_process_model, save_process_model


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.tables.clear()


def _create_process_record(user: UserContext) -> dict[str, Any]:
    knowledge_db = create_knowledge_db(KnowledgeDbCreate(name="ProcessModelテストDB"), user)
    knowledge = create_knowledge(
        knowledge_db["id"],
        KnowledgeCreate(
            name="ProcessModelテスト",
            purpose="処理モデルの編集を確認する",
            interviewPlan=InterviewPlan(profile="business_process", modelId="global.openai.gpt-5.6-luna"),
        ),
        user,
    )
    return create_record(knowledge["id"], RecordCreate(title="ProcessModel編集対象"), user)


def _seed_process_state(record: dict[str, Any], user: UserContext) -> dict[str, Any]:
    process_state = {
        "version": 0,
        "sourceMessageIds": [],
        "participants": [],
        "nodes": [
            ProcessNode(
                nodeId="node-start",
                label="申請を受け付ける",
                nodeType="activity",
            ).model_dump(),
            ProcessNode(
                nodeId="node-end",
                label="処理を完了する",
                nodeType="end",
            ).model_dump(),
        ],
        "edges": [],
        "interactions": [],
    }
    state = {
        "id": f"interview-state-{record['id']}",
        "tenantId": user.tenant_id,
        "recordId": record["id"],
        "status": "in_progress",
        "interviewProfile": "business_process",
        "processState": process_state,
        "processVersion": 0,
        "stateVersion": 0,
    }
    store.upsert("interview_states", state)
    return state


def test_management_user_can_save_manual_process_model_correction() -> None:
    manager = DEV_TOKENS["dev-manager"]
    record = _create_process_record(manager)
    state = _seed_process_state(record, manager)
    process_state = deepcopy(state["processState"])
    process_state["nodes"][0]["label"] = "申請内容を受け付ける"

    result = save_process_model(
        record,
        ProcessModelUpdate(baseProcessVersion=0, processState=process_state),
        manager,
    )

    saved_state = result["interviewState"]
    assert saved_state["processState"]["nodes"][0]["label"] == "申請内容を受け付ける"
    assert saved_state["processState"]["version"] == 1
    assert saved_state["processVersion"] == 1
    assert saved_state["processState"]["nodes"][0]["confirmationStatus"] == "confirmed"
    assert store.list("audit_logs", manager.tenant_id)[-1]["action"] == "process_model_manual_save"


def test_process_model_save_rejects_stale_version() -> None:
    manager = DEV_TOKENS["dev-manager"]
    record = _create_process_record(manager)
    state = _seed_process_state(record, manager)
    process_state = deepcopy(state["processState"])
    process_state["nodes"][0]["label"] = "別の名称"

    save_process_model(
        record,
        ProcessModelUpdate(baseProcessVersion=0, processState=process_state),
        manager,
    )

    with pytest.raises(HTTPException) as exc_info:
        save_process_model(
            record,
            ProcessModelUpdate(baseProcessVersion=0, processState=process_state),
            manager,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "process_model_version_conflict"


def test_only_management_users_can_edit_process_model() -> None:
    manager = DEV_TOKENS["dev-manager"]
    interviewer = DEV_TOKENS["dev-interviewer"]
    record = _create_process_record(manager)
    record["ownerUserId"] = interviewer.user_id
    store.upsert("records", record)
    state = _seed_process_state(record, manager)
    process_state = deepcopy(state["processState"])

    with pytest.raises(HTTPException) as exc_info:
        save_process_model(
            record,
            ProcessModelUpdate(baseProcessVersion=0, processState=process_state),
            interviewer,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "insufficient_role"


class FakeProcessModelProvider:
    def __init__(self, output: ProcessModelEditOutput) -> None:
        self.output = output
        self.context: dict[str, Any] | None = None

    def edit_process_model(self, *, context: dict[str, Any], reasoning_effort: str) -> ProcessModelEditOutput:
        self.context = context
        assert reasoning_effort == "low"
        return self.output


def test_process_model_command_applies_structured_patch_and_keeps_command_history() -> None:
    manager = DEV_TOKENS["dev-manager"]
    record = _create_process_record(manager)
    _seed_process_state(record, manager)
    provider = FakeProcessModelProvider(
        ProcessModelEditOutput(
            reply="申請受付の名称を変更しました。",
            processPatch=ProcessPatch(
                baseProcessVersion=0,
                updateNodes=[
                    ProcessNode(
                        nodeId="node-start",
                        label="申請内容を受け付ける",
                        nodeType="activity",
                    )
                ],
            ),
        )
    )

    result = edit_process_model(
        record,
        ProcessModelCommand(instruction="最初の処理を『申請内容を受け付ける』に変更して", baseProcessVersion=0),
        manager,
        provider=provider,
    )

    assert result["reply"] == "申請受付の名称を変更しました。"
    assert result["interviewState"]["processState"]["nodes"][0]["label"] == "申請内容を受け付ける"
    assert result["interviewState"]["processState"]["version"] == 1
    assert provider.context is not None
    assert provider.context["instruction"].startswith("最初の処理")
    messages = store.list("messages", manager.tenant_id)
    command_message = next(
        message for message in messages
        if message.get("messageType") == "process_model_edit_command"
    )
    reply_message = next(
        message for message in messages
        if message.get("messageType") == "process_model_edit_reply"
    )
    assert command_message["instructionSummary"] == "最初の処理を『申請内容を受け付ける』に変更して"
    assert command_message["updatedTargets"] == ["flowchart", "sequence"]
    assert reply_message["processCommandId"] == command_message["id"]
    assert reply_message["updatedTargets"] == ["flowchart", "sequence"]
    assert "フローチャートとシーケンス図を更新しました" in reply_message["processChangeSummary"]
    assert any("申請内容を受け付ける" in point for point in reply_message["processUpdatedPoints"])
    snapshot = get_interview_state_snapshot(record, manager, persist=False)
    snapshot_message_types = {message.get("messageType") for message in snapshot["messages"]}
    assert {"process_model_edit_command", "process_model_edit_reply"}.issubset(snapshot_message_types)


def test_process_model_command_persists_sequence_semantics_for_both_diagrams() -> None:
    manager = DEV_TOKENS["dev-manager"]
    record = _create_process_record(manager)
    _seed_process_state(record, manager)
    provider = FakeProcessModelProvider(
        ProcessModelEditOutput(
            reply="権限分岐と非同期生成を追加しました。",
            processPatch=ProcessPatch(
                baseProcessVersion=0,
                addParticipants=[
                    ProcessParticipant(
                        participantId="order-system",
                        name="受注管理システム",
                        kind="system",
                    ),
                    ProcessParticipant(
                        participantId="csv-worker",
                        name="CSV生成処理",
                        kind="system",
                    ),
                ],
                addInteractions=[
                    ProcessInteraction(
                        interactionId="system-to-worker",
                        sequence=1,
                        sourceParticipantId="order-system",
                        targetParticipantId="csv-worker",
                        action="CSV生成を依頼する",
                        interactionType="async",
                        fragmentType="alt",
                        fragmentId="export-mode",
                        fragmentLabel="件数が多い場合",
                    ),
                ],
            ),
        )
    )

    result = edit_process_model(
        record,
        ProcessModelCommand(
            instruction="件数が多い場合はバックグラウンドでCSVを生成する分岐を追加して",
            baseProcessVersion=0,
        ),
        manager,
        provider=provider,
    )

    interaction = result["interviewState"]["processState"]["interactions"][0]
    assert interaction["interactionType"] == "async"
    assert interaction["fragmentType"] == "alt"
    assert interaction["fragmentLabel"] == "件数が多い場合"
    reply_message = next(
        message for message in store.list("messages", manager.tenant_id)
        if message.get("messageType") == "process_model_edit_reply"
    )
    assert reply_message["updatedTargets"] == ["flowchart", "sequence"]


def test_process_model_command_applies_requirement_and_process_patches() -> None:
    manager = DEV_TOKENS["dev-manager"]
    record = _create_process_record(manager)
    state = _seed_process_state(record, manager)
    state["requirementStates"] = {
        "requirement.request": {
            "requirementId": "requirement.request",
            "label": "要求内容",
            "kind": "requirement",
            "status": "CONFIRMED",
            "candidateValue": None,
            "candidateSource": None,
            "candidateProposalMessageId": None,
            "confirmedSource": "user_statement",
            "confirmedProposalMessageId": None,
            "confirmationEvidenceTranscriptIds": [],
            "value": "検索結果を一覧表示する",
            "evidenceTranscriptIds": [],
        },
    }
    store.upsert("interview_states", state)
    provider = FakeProcessModelProvider(
        ProcessModelEditOutput(
            reply="要件と最初の処理を更新しました。",
            requirementPatch=RequirementPatch(
                updateRequirements=[
                    RequirementEdit(
                        requirementId="requirement.request",
                        value="検索結果に一致度スコアを表示し、スコアの高い順に一覧表示する",
                    ),
                ],
            ),
            processPatch=ProcessPatch(
                baseProcessVersion=0,
                updateNodes=[
                    ProcessNode(
                        nodeId="node-start",
                        label="検索結果をスコア順に一覧表示する",
                        nodeType="activity",
                    ),
                ],
            ),
        ),
    )

    result = edit_process_model(
        record,
        ProcessModelCommand(
            instruction="検索結果に一致度スコアを表示し、高い順に並べる",
            baseProcessVersion=0,
            baseStateVersion=0,
        ),
        manager,
        provider=provider,
    )

    saved_state = result["interviewState"]
    assert saved_state["requirementStates"]["requirement.request"]["status"] == "CONFIRMED"
    assert saved_state["requirementStates"]["requirement.request"]["value"] == (
        "検索結果に一致度スコアを表示し、スコアの高い順に一覧表示する"
    )
    assert saved_state["requirementStates"]["requirement.request"]["confirmedSource"] == "management_edit"
    assert saved_state["processState"]["nodes"][0]["label"] == "検索結果をスコア順に一覧表示する"
    assert saved_state["processState"]["version"] == 1
    assert saved_state["stateVersion"] == 1


def test_requirement_only_edit_does_not_increment_process_version() -> None:
    manager = DEV_TOKENS["dev-manager"]
    record = _create_process_record(manager)
    state = _seed_process_state(record, manager)
    state["requirementStates"] = {
        "requirement.request": {
            "requirementId": "requirement.request",
            "label": "要求内容",
            "kind": "requirement",
            "status": "UNANSWERED",
            "value": None,
        },
    }
    store.upsert("interview_states", state)
    provider = FakeProcessModelProvider(
        ProcessModelEditOutput(
            reply="要求内容を更新しました。",
            requirementPatch=RequirementPatch(
                updateRequirements=[
                    RequirementEdit(
                        requirementId="requirement.request",
                        value="検索結果を一致度スコアの高い順に表示する",
                    ),
                ],
            ),
        ),
    )

    result = edit_process_model(
        record,
        ProcessModelCommand(
            instruction="検索結果を一致度スコアの高い順に表示する",
            baseProcessVersion=0,
            baseStateVersion=0,
        ),
        manager,
        provider=provider,
    )

    assert result["interviewState"]["processState"]["version"] == 0
    assert result["interviewState"]["processVersion"] == 0
    assert result["interviewState"]["stateVersion"] == 1
