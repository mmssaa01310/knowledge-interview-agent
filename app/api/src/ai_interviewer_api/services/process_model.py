from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from ai_interviewer_api.agents.interview_knowledge.provider import (
    BedrockResponsesStructuredProvider,
    ProcessModelEditProvider,
    StructuredInterviewProviderError,
)
from ai_interviewer_api.agents.interview_knowledge.schemas import (
    ProcessEdge,
    ProcessInteraction,
    ProcessModelEditOutput,
    ProcessNode,
    ProcessParticipant,
    ProcessPatch,
    RequirementPatch,
)
from ai_interviewer_api.agents.interview_knowledge.service import resolve_structured_model_id
from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.core.permissions import require_record_action
from ai_interviewer_api.core.config import settings
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.schemas.requests import ProcessModelCommand, ProcessModelUpdate
from ai_interviewer_api.services.audit import write_audit_log
from ai_interviewer_api.services.ai_interview import get_interview_state_snapshot


_PROCESS_PROFILES = {"business_process", "system_requirement"}
_COLLECTIONS = ("participants", "nodes", "edges", "interactions")
_ENTITY_ID_KEYS = {
    "participants": "participantId",
    "nodes": "nodeId",
    "edges": "edgeId",
    "interactions": "interactionId",
}
_ENTITY_MODELS = {
    "participants": ProcessParticipant,
    "nodes": ProcessNode,
    "edges": ProcessEdge,
    "interactions": ProcessInteraction,
}
_EDITABLE_FIELDS = {
    "participants": {"name", "role", "kind"},
    "nodes": {"label", "nodeType", "participantIds"},
    "edges": {"sourceNodeId", "targetNodeId", "label", "condition"},
    "interactions": {
        "sequence",
        "sourceParticipantId",
        "targetParticipantId",
        "action",
        "data",
    },
}


def save_process_model(
    record: Mapping[str, Any],
    payload: ProcessModelUpdate,
    user: UserContext,
) -> dict[str, Any]:
    """Save management-user corrections to the existing ProcessModel.

    The editor intentionally keeps the entity identity set unchanged. New
    entities and removals are handled by the structured command path so that
    those higher-impact changes have an explicit instruction and audit trail.
    """

    require_record_action(dict(record), user, "manage")
    _require_process_model_editable_record(record)
    state = _load_process_state(record, user)
    current_process = _copy_process_state(state)
    current_version = _process_version(current_process)
    _require_version(payload.baseProcessVersion, current_version)
    _require_state_version(payload.baseStateVersion, _state_version(state))

    next_process, changed_ids = _merge_manual_state(current_process, payload.processState)
    changed = bool(changed_ids)
    if changed:
        next_process["version"] = current_version + 1
        next_process["sourceMessageIds"] = list(current_process.get("sourceMessageIds") or [])
        state["processState"] = next_process
        state["processVersion"] = next_process["version"]
        _persist_state(state, user)

    write_audit_log(
        user,
        "process_model_manual_save",
        "record",
        str(record["id"]),
        {
            "changed": changed,
            "changedIds": changed_ids,
            "processVersion": current_version + 1 if changed else current_version,
        },
    )
    return _snapshot(record, user)


def edit_process_model(
    record: Mapping[str, Any],
    payload: ProcessModelCommand,
    user: UserContext,
    *,
    provider: ProcessModelEditProvider | None = None,
) -> dict[str, Any]:
    """Turn a management user's natural-language edit into a validated patch."""

    require_record_action(dict(record), user, "manage")
    _require_process_model_editable_record(record)
    instruction = payload.instruction.strip()
    if not instruction:
        raise HTTPException(status_code=422, detail="process_model_instruction_required")
    state = _load_process_state(record, user)
    current_process = _copy_process_state(state)
    current_version = _process_version(current_process)
    _require_version(payload.baseProcessVersion, current_version)
    _require_state_version(payload.baseStateVersion, _state_version(state))

    command_id = f"process-edit-{uuid4().hex}"
    _store_process_command(record, user, command_id, instruction)
    knowledge = store.get("knowledges", str(record["knowledgeId"])) or {}
    model_id = resolve_structured_model_id(knowledge)
    structured_provider = provider or BedrockResponsesStructuredProvider(model_id=model_id)
    try:
        output = structured_provider.edit_process_model(
            context={
                "instruction": instruction,
                "commandMessageId": command_id,
                "profile": state.get("interviewProfile"),
                "record": {
                    "id": record.get("id"),
                    "title": record.get("title"),
                },
                "requirementStates": _copy_requirement_states(state),
                "processModel": current_process,
            },
            reasoning_effort=_select_edit_reasoning_effort(current_process, state),
        )
    except (StructuredInterviewProviderError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="process_model_edit_failed") from exc

    if not isinstance(output, ProcessModelEditOutput):
        try:
            output = ProcessModelEditOutput.model_validate(output)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="process_model_edit_failed") from exc

    # The version is checked again after the model call. The state may have
    # changed while the provider was responding.
    latest_state = _load_process_state(record, user)
    latest_process = _copy_process_state(latest_state)
    latest_version = _process_version(latest_process)
    _require_version(payload.baseProcessVersion, latest_version)
    _require_state_version(payload.baseStateVersion, _state_version(latest_state))
    if _has_process_operations(output.processPatch):
        next_process, changed_process_ids = _apply_command_patch(
            latest_process,
            output.processPatch,
        )
    else:
        next_process, changed_process_ids = latest_process, []
    changed_requirement_ids = _apply_requirement_patch(
        latest_state,
        output.requirementPatch,
        command_id,
    )
    changed_ids = sorted(set(changed_process_ids + changed_requirement_ids))
    if changed_ids:
        if changed_process_ids:
            next_process["version"] = latest_version + 1
            source_message_ids = list(latest_process.get("sourceMessageIds") or [])
            if command_id not in source_message_ids:
                source_message_ids.append(command_id)
            next_process["sourceMessageIds"] = source_message_ids
        else:
            next_process["version"] = latest_version
        latest_state["processState"] = next_process
        latest_state["processVersion"] = next_process["version"]
        _persist_state(latest_state, user)

    reply = output.reply.strip() or "ProcessModelを更新しました。"
    _store_process_reply(record, user, command_id, reply)
    write_audit_log(
        user,
        "process_model_command",
        "record",
        str(record["id"]),
        {
            "commandId": command_id,
            "instruction": instruction,
            "changedIds": changed_ids,
            "changedProcessIds": changed_process_ids,
            "changedRequirementIds": changed_requirement_ids,
            "processVersion": _process_version(next_process),
            "stateVersion": _state_version(latest_state),
            "modelId": model_id,
        },
    )
    result = _snapshot(record, user)
    result["reply"] = reply
    return result


def _load_process_state(record: Mapping[str, Any], user: UserContext) -> dict[str, Any]:
    state_id = f"interview-state-{record['id']}"
    existing = store.get("interview_states", state_id)
    if existing is None:
        # This also creates the normal structured state shape when a manager
        # opens the editor before the first answer has been submitted.
        _snapshot(record, user)
        existing = store.get("interview_states", state_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="interview_state_not_found")
    state = deepcopy(existing)
    profile = state.get("interviewProfile")
    if profile not in _PROCESS_PROFILES:
        raise HTTPException(status_code=409, detail="process_model_not_available")
    if profile == "system_requirement":
        process_status = (state.get("applicabilityState") or {}).get("process", {}).get("status")
        if process_status != "present":
            raise HTTPException(status_code=409, detail="process_model_not_available")
    process_state = state.get("processState")
    if not isinstance(process_state, Mapping):
        raise HTTPException(status_code=409, detail="process_model_not_available")
    return state


def _require_process_model_editable_record(record: Mapping[str, Any]) -> None:
    if record.get("status") == "approved":
        raise HTTPException(
            status_code=409,
            detail="process_model_edit_not_allowed_after_approval",
        )


def _snapshot(record: Mapping[str, Any], user: UserContext) -> dict[str, Any]:
    return get_interview_state_snapshot(dict(record), user, persist=False)


def _copy_process_state(state: Mapping[str, Any]) -> dict[str, Any]:
    process_state = state.get("processState")
    if not isinstance(process_state, Mapping):
        raise HTTPException(status_code=409, detail="process_model_not_available")
    result = deepcopy(dict(process_state))
    for collection in _COLLECTIONS:
        value = result.get(collection, [])
        if not isinstance(value, list):
            raise HTTPException(status_code=422, detail="invalid_process_model")
        result[collection] = deepcopy(value)
    return result


def _copy_requirement_states(state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    requirement_states = state.get("requirementStates", {})
    if not isinstance(requirement_states, Mapping):
        return {}
    return {
        str(requirement_id): {
            "requirementId": str(requirement_id),
            "label": item.get("label"),
            "kind": item.get("kind"),
            "status": item.get("status"),
            "value": item.get("value"),
            "candidateValue": item.get("candidateValue"),
        }
        for requirement_id, item in requirement_states.items()
        if isinstance(item, Mapping)
    }


def _process_version(process_state: Mapping[str, Any]) -> int:
    value = process_state.get("version", 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value or value < 0:
        raise HTTPException(status_code=422, detail="invalid_process_model_version")
    return int(value)


def _state_version(state: Mapping[str, Any]) -> int:
    value = state.get("stateVersion", 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value or value < 0:
        raise HTTPException(status_code=422, detail="invalid_interview_state_version")
    return int(value)


def _require_version(requested: int, current: int) -> None:
    if requested != current:
        raise HTTPException(status_code=409, detail="process_model_version_conflict")


def _require_state_version(requested: int | None, current: int) -> None:
    if requested is not None and requested != current:
        raise HTTPException(status_code=409, detail="interview_state_version_conflict")


def _merge_manual_state(
    current: dict[str, Any],
    incoming: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    next_process = deepcopy(current)
    changed_ids: list[str] = []
    if not isinstance(incoming, Mapping):
        raise HTTPException(status_code=422, detail="invalid_process_model")

    for collection in _COLLECTIONS:
        current_items = _validated_items(collection, current.get(collection, []))
        incoming_value = incoming.get(collection, current_items)
        incoming_items = _validated_items(collection, incoming_value)
        current_by_id = _index_items(collection, current_items)
        incoming_by_id = _index_items(collection, incoming_items)
        if set(current_by_id) != set(incoming_by_id):
            raise HTTPException(status_code=422, detail="process_model_identity_change_forbidden")

        merged_items: list[dict[str, Any]] = []
        for incoming_item in incoming_items:
            entity_id = incoming_item[_ENTITY_ID_KEYS[collection]]
            current_item = current_by_id[entity_id]
            merged_item = deepcopy(current_item)
            for key in _EDITABLE_FIELDS[collection]:
                if key in incoming_item:
                    merged_item[key] = deepcopy(incoming_item[key])
            _validate_entity(collection, merged_item)
            if _editable_values(merged_item, collection) != _editable_values(current_item, collection):
                merged_item["confirmationStatus"] = "confirmed"
                changed_ids.append(entity_id)
            merged_items.append(merged_item)
        next_process[collection] = merged_items

    _validate_relationships(next_process)
    return next_process, sorted(set(changed_ids))


def _apply_command_patch(
    current: dict[str, Any],
    patch: ProcessPatch,
) -> tuple[dict[str, Any], list[str]]:
    current_version = _process_version(current)
    _require_version(patch.baseProcessVersion, current_version)
    next_process = deepcopy(current)
    changed_ids: list[str] = []

    _apply_entity_patch(
        next_process,
        "participants",
        patch.addParticipants,
        patch.updateParticipants,
        changed_ids,
    )
    _apply_entity_patch(
        next_process,
        "nodes",
        patch.addNodes,
        patch.updateNodes,
        changed_ids,
    )
    _apply_entity_patch(
        next_process,
        "edges",
        patch.addEdges,
        patch.updateEdges,
        changed_ids,
    )
    _apply_entity_patch(
        next_process,
        "interactions",
        patch.addInteractions,
        patch.updateInteractions,
        changed_ids,
    )
    _supersede_entities(next_process, "edges", patch.removeEdges, changed_ids)
    _supersede_entities(next_process, "interactions", patch.removeInteractions, changed_ids)
    _validate_relationships(next_process)
    return next_process, sorted(set(changed_ids))


def _apply_requirement_patch(
    state: dict[str, Any],
    patch: RequirementPatch,
    command_id: str,
) -> list[str]:
    if not patch.updateRequirements:
        return []
    requirement_states = state.get("requirementStates", {})
    if not isinstance(requirement_states, Mapping):
        raise HTTPException(status_code=422, detail="requirement_state_not_available")

    next_requirement_states = deepcopy(dict(requirement_states))
    changed_ids: list[str] = []
    for edit in patch.updateRequirements:
        requirement_id = edit.requirementId.strip()
        value = edit.value.strip()
        if not requirement_id:
            raise HTTPException(status_code=422, detail="requirement_id_required")
        if not value:
            raise HTTPException(status_code=422, detail="requirement_value_required")
        current = next_requirement_states.get(requirement_id)
        if not isinstance(current, Mapping):
            raise HTTPException(status_code=422, detail="requirement_entity_not_found")

        updated = deepcopy(dict(current))
        updated["status"] = "CONFIRMED"
        updated["value"] = value
        updated["candidateValue"] = None
        updated["candidateSource"] = None
        updated["candidateProposalMessageId"] = None
        updated["confirmedSource"] = "management_edit"
        updated["confirmedProposalMessageId"] = None
        updated["confirmationEvidenceTranscriptIds"] = []
        updated["managementEditMessageId"] = command_id
        if updated != current:
            changed_ids.append(requirement_id)
        next_requirement_states[requirement_id] = updated

    state["requirementStates"] = next_requirement_states
    return sorted(set(changed_ids))


def _has_process_operations(patch: ProcessPatch) -> bool:
    return bool(
        patch.addParticipants
        or patch.updateParticipants
        or patch.addNodes
        or patch.updateNodes
        or patch.addEdges
        or patch.updateEdges
        or patch.removeEdges
        or patch.addInteractions
        or patch.updateInteractions
        or patch.removeInteractions
    )


def _apply_entity_patch(
    process_state: dict[str, Any],
    collection: str,
    additions: Sequence[Any],
    updates: Sequence[Any],
    changed_ids: list[str],
) -> None:
    id_key = _ENTITY_ID_KEYS[collection]
    model = _ENTITY_MODELS[collection]
    items = _validated_items(collection, process_state.get(collection, []))
    by_id = _index_items(collection, items)
    for entity in additions:
        item = _entity_dump(entity, model)
        entity_id = item[id_key]
        if entity_id in by_id:
            raise HTTPException(status_code=422, detail="process_model_duplicate_entity_id")
        item["confirmationStatus"] = "confirmed"
        item["candidateSource"] = "user_statement"
        item["evidenceTranscriptIds"] = []
        _validate_entity(collection, item)
        items.append(item)
        by_id[entity_id] = item
        changed_ids.append(entity_id)

    for entity in updates:
        item = _entity_dump(entity, model)
        entity_id = item[id_key]
        if entity_id not in by_id:
            raise HTTPException(status_code=422, detail="process_model_entity_not_found")
        current_item = by_id[entity_id]
        merged_item = deepcopy(current_item)
        for key in _EDITABLE_FIELDS[collection]:
            if key in item:
                merged_item[key] = deepcopy(item[key])
        _validate_entity(collection, merged_item)
        if _editable_values(merged_item, collection) != _editable_values(current_item, collection):
            merged_item["confirmationStatus"] = "confirmed"
            by_id[entity_id] = merged_item
            changed_ids.append(entity_id)

    process_state[collection] = list(by_id.values())


def _supersede_entities(
    process_state: dict[str, Any],
    collection: str,
    entity_ids: Sequence[str],
    changed_ids: list[str],
) -> None:
    if not entity_ids:
        return
    items = _validated_items(collection, process_state.get(collection, []))
    by_id = _index_items(collection, items)
    for entity_id in entity_ids:
        if entity_id not in by_id:
            raise HTTPException(status_code=422, detail="process_model_entity_not_found")
        if by_id[entity_id].get("lifecycle") != "superseded":
            by_id[entity_id]["lifecycle"] = "superseded"
            by_id[entity_id]["confirmationStatus"] = "confirmed"
            changed_ids.append(entity_id)
    process_state[collection] = list(by_id.values())


def _validated_items(collection: str, value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise HTTPException(status_code=422, detail="invalid_process_model")
    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise HTTPException(status_code=422, detail="invalid_process_model_entity")
        item_dict = dict(item)
        _validate_entity(collection, item_dict)
        items.append(item_dict)
    return items


def _index_items(collection: str, items: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    id_key = _ENTITY_ID_KEYS[collection]
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        entity_id = str(item[id_key])
        if entity_id in indexed:
            raise HTTPException(status_code=422, detail="process_model_duplicate_entity_id")
        indexed[entity_id] = dict(item)
    return indexed


def _validate_entity(collection: str, item: Mapping[str, Any]) -> None:
    try:
        _ENTITY_MODELS[collection].model_validate(dict(item))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_process_model_entity") from exc


def _validate_relationships(process_state: Mapping[str, Any]) -> None:
    participants = _validated_items("participants", process_state.get("participants", []))
    nodes = _validated_items("nodes", process_state.get("nodes", []))
    edges = _validated_items("edges", process_state.get("edges", []))
    interactions = _validated_items("interactions", process_state.get("interactions", []))
    active_participant_ids = {
        str(item["participantId"])
        for item in participants
        if item.get("lifecycle") != "superseded"
    }
    active_node_ids = {
        str(item["nodeId"])
        for item in nodes
        if item.get("lifecycle") != "superseded"
    }
    for node in nodes:
        if node.get("lifecycle") == "superseded":
            continue
        if not set(node.get("participantIds") or []).issubset(active_participant_ids):
            raise HTTPException(status_code=422, detail="process_model_invalid_participant_reference")
    for edge in edges:
        if edge.get("lifecycle") == "superseded":
            continue
        if edge.get("sourceNodeId") not in active_node_ids or edge.get("targetNodeId") not in active_node_ids:
            raise HTTPException(status_code=422, detail="process_model_invalid_node_reference")
    for interaction in interactions:
        if interaction.get("lifecycle") == "superseded":
            continue
        if (
            interaction.get("sourceParticipantId") not in active_participant_ids
            or interaction.get("targetParticipantId") not in active_participant_ids
        ):
            raise HTTPException(status_code=422, detail="process_model_invalid_participant_reference")


def _entity_dump(entity: Any, model: Any) -> dict[str, Any]:
    if isinstance(entity, model):
        return entity.model_dump()
    try:
        return model.model_validate(entity).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_process_model_entity") from exc


def _editable_values(
    after: Mapping[str, Any],
    collection: str,
) -> tuple[Any, ...]:
    return tuple(
        deepcopy(after.get(key))
        for key in sorted(_EDITABLE_FIELDS[collection])
    )


def _persist_state(state: dict[str, Any], user: UserContext) -> None:
    state["stateVersion"] = int(state.get("stateVersion", 0) or 0) + 1
    state["updatedByUserId"] = user.user_id
    state["updatedAt"] = utc_now()
    store.upsert("interview_states", state)


def _store_process_command(
    record: Mapping[str, Any],
    user: UserContext,
    command_id: str,
    instruction: str,
) -> None:
    now = utc_now()
    store.upsert(
        "messages",
        {
            "id": command_id,
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "content": instruction,
            "role": "user",
            "isActualUtterance": False,
            "messageType": "process_model_edit_command",
            "createdAt": now,
            "updatedAt": now,
        },
    )


def _store_process_reply(
    record: Mapping[str, Any],
    user: UserContext,
    command_id: str,
    reply: str,
) -> None:
    now = utc_now()
    store.upsert(
        "messages",
        {
            "id": f"process-reply-{uuid4().hex}",
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "content": reply,
            "role": "assistant",
            "isActualUtterance": False,
            "messageType": "process_model_edit_reply",
            "processCommandId": command_id,
            "createdAt": now,
            "updatedAt": now,
        },
    )


def _select_edit_reasoning_effort(
    process_state: Mapping[str, Any],
    interview_state: Mapping[str, Any],
) -> str:
    complex_state = bool(
        interview_state.get("contradictions")
        or interview_state.get("openIssues")
        or len(process_state.get("nodes", [])) >= 10
        or len(process_state.get("edges", [])) >= 12
    )
    return (
        settings.structured_interview_medium_reasoning_effort
        if complex_state
        else settings.structured_interview_reasoning_effort
    )
