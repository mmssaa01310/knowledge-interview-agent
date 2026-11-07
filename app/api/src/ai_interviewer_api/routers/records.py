import asyncio
from copy import deepcopy
import json
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ai_interviewer_api.auth.deps import UserContext, get_current_user
from ai_interviewer_api.core.interview_configuration import (
    require_interview_configuration,
)
from ai_interviewer_api.core.interview_locale import resolve_interview_locale
from ai_interviewer_api.core.permissions import (
    accessible_records,
    require_admin_role,
    require_management_role,
    require_record_creation_role,
    require_record_action,
)
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.models.domain import InterviewRecord
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.common import (
    ensure_interviewer_knowledge_access,
    get_scoped_item,
    interview_context_fields,
    interview_context_knowledge,
)
from ai_interviewer_api.schemas.requests import (
    ChatMessageCreate,
    InterviewAnswerUpdate,
    ManualQuestionAnswerUpdate,
    ProcessModelCommand,
    ProcessModelUpdate,
    RequirementStatesManualUpdate,
    RecordCreate,
    RecordUpdate,
)
from ai_interviewer_api.services.ai_interview import (
    build_mock_proposal,
    generate_interview_reply,
    get_interview_state_snapshot,
)
from ai_interviewer_api.services.audit import write_audit_log
from ai_interviewer_api.services.process_model import (
    edit_process_model,
    save_process_model,
)
from ai_interviewer_api.services.manual_corrections import (
    commit_manual_correction_state,
    require_manual_correction_access,
)
from ai_interviewer_api.services.record_lifecycle import (
    sync_record_status_after_interview,
)

router = APIRouter(prefix="/api")


def _has_started_interview(record_id: str, tenant_id: str) -> bool:
    """Return whether the record has progressed past its persisted empty state."""
    if any(
        row.get("recordId") == record_id
        for row in store.list("messages", tenant_id)
    ):
        return True

    state = store.get("interview_states", f"interview-state-{record_id}")
    if not state:
        return False
    return bool(
        state.get("status") == "completed"
        or state.get("currentQuestionId")
        or state.get("askedQuestions")
        or state.get("lastProcessedUserMessageId")
        or state.get("completedFieldIds")
        or any(
            field_state.get("answerState") not in {None, "UNANSWERED"}
            for field_state in (state.get("fieldStates") or {}).values()
        )
        or any(
            requirement_state.get("status") not in {None, "UNANSWERED"}
            for requirement_state in (state.get("requirementStates") or {}).values()
        )
    )


@router.post("/knowledges/{knowledge_id}/records")
def create_record(
    knowledge_id: str,
    payload: RecordCreate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_record_creation_role(user)
    knowledge = get_scoped_item("knowledges", knowledge_id, user, "knowledge_not_found")
    ensure_interviewer_knowledge_access(knowledge, user)
    require_interview_configuration(knowledge)
    payload_data = payload.model_dump()
    requested_interview_locale = payload_data.pop("interviewLocale", None)
    requested_owner_user_id = payload_data.pop("ownerUserId", None)
    owner_user_id = requested_owner_user_id or user.user_id
    if user.role == "interviewer":
        if requested_owner_user_id and requested_owner_user_id != user.user_id:
            raise HTTPException(status_code=403, detail="record_owner_must_be_current_user")
        if payload_data.get("viewerUserIds"):
            raise HTTPException(status_code=403, detail="record_viewer_assignment_forbidden")
        payload_data["viewerUserIds"] = []
        owner_user_id = user.user_id
    item = InterviewRecord(
        tenantId=user.tenant_id,
        createdByUserId=user.user_id,
        updatedByUserId=user.user_id,
        knowledgeId=knowledge_id,
        knowledgeName=knowledge["name"],
        ownerUserId=owner_user_id,
        status="in_progress",
        interviewLocale=requested_interview_locale or resolve_interview_locale(knowledge=knowledge),
        **payload_data,
    )
    store.upsert("records", item.model_dump())
    write_audit_log(
        user,
        "create",
        "record",
        item.id,
        {"knowledgeId": knowledge_id, "status": item.status},
    )
    return item.model_dump()


@router.get("/records")
def list_accessible_records(user: UserContext = Depends(get_current_user)) -> list[dict]:
    return accessible_records(store.list("records", user.tenant_id), user)


@router.get("/knowledges/{knowledge_id}/records")
def list_records(knowledge_id: str, user: UserContext = Depends(get_current_user)) -> list[dict]:
    require_management_role(user)
    get_scoped_item("knowledges", knowledge_id, user, "knowledge_not_found")
    return [
        row
        for row in store.list("records", user.tenant_id)
        if row["knowledgeId"] == knowledge_id and not row.get("deletedAt")
    ]


@router.get("/records/{record_id}/interview-context")
def get_record_interview_context(record_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    record = get_scoped_item("records", record_id, user, "record_not_found")
    knowledge = get_scoped_item("knowledges", record["knowledgeId"], user, "knowledge_not_found")
    fields = [
        row
        for row in store.list("knowledge_fields", user.tenant_id)
        if row.get("knowledgeId") == record["knowledgeId"]
    ]
    return {
        "record": record,
        "knowledge": interview_context_knowledge(knowledge, user),
        "fields": interview_context_fields(fields, user),
    }


@router.get("/records/{record_id}")
def get_record(record_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    return get_scoped_item("records", record_id, user, "record_not_found")


@router.patch("/records/{record_id}/process-model")
def update_record_process_model(
    record_id: str,
    payload: ProcessModelUpdate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    record = get_scoped_item("records", record_id, user, "record_not_found")
    return save_process_model(record, payload, user)


@router.post("/records/{record_id}/process-model/commands")
def command_record_process_model(
    record_id: str,
    payload: ProcessModelCommand,
    user: UserContext = Depends(get_current_user),
) -> dict:
    record = get_scoped_item("records", record_id, user, "record_not_found")
    return edit_process_model(record, payload, user)


@router.patch("/records/{record_id}")
def update_record(
    record_id: str,
    payload: RecordUpdate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    item = get_record(record_id, user)
    previous_status = item.get("status", "draft")
    requested_updates = payload.model_dump(exclude_unset=True)
    requested_status = requested_updates.pop("status", None)

    if user.role == "interviewer":
        if set(requested_updates) - {"interviewLocale"}:
            raise HTTPException(status_code=403, detail="record_management_forbidden")
        if requested_status is None:
            require_record_action(item, user, "answer")
    else:
        require_management_role(user)

    if "interviewLocale" in requested_updates:
        if _has_started_interview(record_id, user.tenant_id):
            raise HTTPException(status_code=409, detail="interview_locale_locked_after_start")

    if requested_status is not None:
        _transition_record_status(
            item,
            requested_status,
            user,
            review_note=requested_updates.get("reviewNote"),
        )

    for key, value in requested_updates.items():
        item[key] = value
    item["updatedByUserId"] = user.user_id
    item["updatedAt"] = utc_now()
    store.upsert("records", item)
    if requested_status is not None:
        write_audit_log(
            user,
            "record_status_change",
            "record",
            record_id,
            {"from": previous_status, "to": item["status"]},
        )
    return item


@router.delete("/records/{record_id}")
def delete_record(record_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    require_admin_role(user)
    record = get_record(record_id, user)
    store.delete("records", record_id)
    write_audit_log(
        user,
        "delete",
        "record",
        record_id,
        {"title": record.get("title"), "knowledgeId": record.get("knowledgeId")},
    )
    return {"deleted": True}


@router.post("/records/{record_id}/messages")
def create_record_message(
    record_id: str,
    payload: ChatMessageCreate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    record = get_scoped_item("records", record_id, user, "record_not_found")
    require_record_action(record, user, "answer")
    require_interview_configuration(_get_record_knowledge(record, user))
    if payload.clientMessageId:
        existing_message = next(
            (
                row
                for row in store.list("messages", user.tenant_id)
                if row.get("recordId") == record_id
                and row.get("clientMessageId") == payload.clientMessageId
            ),
            None,
        )
        if existing_message:
            same_payload = (
                existing_message.get("content") == payload.content
                and existing_message.get("answerToQuestionId") == payload.answerToQuestionId
                and (payload.turnType is None or existing_message.get("turnType") == payload.turnType)
                and (payload.targetType is None or existing_message.get("targetType") == payload.targetType)
                and (payload.targetId is None or existing_message.get("targetId") == payload.targetId)
            )
            if not same_payload:
                raise HTTPException(status_code=409, detail="client_message_id_payload_mismatch")
            return {
                "message": "accepted",
                "proposalId": None,
                "recordMessage": existing_message,
                "duplicate": True,
            }
    interview_state = store.get("interview_states", f"interview-state-{record_id}") or {}
    if (
        payload.stateVersion is not None
        and int(interview_state.get("stateVersion", 0) or 0) != payload.stateVersion
    ):
        raise HTTPException(status_code=409, detail="interview_state_version_conflict")
    current_question_id = payload.answerToQuestionId
    interview_is_active = bool(
        interview_state.get("currentQuestionId")
        or interview_state.get("currentFieldId")
        or interview_state.get("askedQuestions")
        or interview_state.get("fieldStates")
    )
    turn_type = payload.turnType
    if turn_type is None:
        if current_question_id:
            turn_type = "ANSWER"
        elif interview_is_active:
            turn_type = "CONTROL"
        else:
            # Preserve the existing non-interview record-message behavior.
            turn_type = "ANSWER"
    if turn_type == "ANSWER" and interview_is_active and not current_question_id:
        raise HTTPException(status_code=422, detail="answer_turn_missing_question_id")
    if (
        turn_type == "ANSWER"
        and interview_is_active
        and current_question_id != interview_state.get("currentQuestionId")
    ):
        raise HTTPException(status_code=409, detail="answer_question_not_current")
    if turn_type == "CONTROL":
        current_question_id = None

    proposal = None
    if turn_type == "ANSWER" and not interview_is_active:
        proposal = build_mock_proposal(user, record_id, record["knowledgeId"], payload.content)
        store.upsert("proposals", proposal.model_dump())

    current_field_id = None
    question_type = None
    target_type = None
    target_id = None
    if current_question_id:
        for question in interview_state.get("askedQuestions", []):
            if question.get("questionId") == current_question_id:
                question_type = question.get("questionType")
                current_field_id = question.get("fieldId") or current_field_id
                target_type = question.get("targetType")
                target_id = question.get("targetId")
                break
    if payload.targetType and payload.targetType != target_type:
        raise HTTPException(status_code=409, detail="answer_target_mismatch")
    if payload.targetId and payload.targetId != target_id:
        raise HTTPException(status_code=409, detail="answer_target_mismatch")
    message = {
        "id": (
            f"msg-client-{user.tenant_id}-{payload.clientMessageId}"
            if payload.clientMessageId
            else f"msg-{uuid4()}"
        ),
        "tenantId": user.tenant_id,
        "recordId": record_id,
        "content": payload.content,
        "role": "user",
        "isActualUtterance": True,
        "createdAt": utc_now(),
        "updatedAt": utc_now(),
        "turnType": turn_type,
        "answerToQuestionId": current_question_id,
        "answerToFieldId": current_field_id,
        "questionType": question_type,
        "targetType": target_type,
        "targetId": target_id,
        "clientMessageId": payload.clientMessageId,
    }
    store.upsert(
        "messages",
        message,
    )
    return {
        "message": "accepted",
        "proposalId": proposal.id if proposal else None,
        "recordMessage": message,
        "duplicate": False,
    }


@router.get("/records/{record_id}/interview-state")
def get_record_interview_state(
    record_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    record = get_scoped_item("records", record_id, user, "record_not_found")
    require_record_action(record, user, "interview_read")
    snapshot = get_interview_state_snapshot(record, user, persist=user.role != "viewer")
    sync_record_status_after_interview(record, snapshot.get("status"), user)
    return snapshot


@router.patch("/records/{record_id}/interview-answers/{field_id}")
def update_record_interview_answer(
    record_id: str,
    field_id: str,
    payload: InterviewAnswerUpdate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    record = get_scoped_item("records", record_id, user, "record_not_found")
    require_manual_correction_access(record, user)
    existing_state = store.get("interview_states", f"interview-state-{record_id}")
    if not existing_state:
        raise HTTPException(status_code=404, detail="interview_state_not_found")
    interview_state = deepcopy(existing_state)
    current_state_version = int(interview_state.get("stateVersion", 0) or 0)
    if payload.baseStateVersion != current_state_version:
        raise HTTPException(status_code=409, detail="interview_state_version_conflict")

    field_state = interview_state.get("fieldStates", {}).get(field_id)
    if not field_state:
        raise HTTPException(status_code=404, detail="interview_field_state_not_found")
    if field_state.get("answerState") != "CONFIRMED":
        raise HTTPException(status_code=409, detail="interview_answer_not_confirmed")

    record_answer = (payload.recordAnswer or payload.answerSummary or "").strip()
    if not record_answer:
        raise HTTPException(status_code=422, detail="interview_answer_required")

    field_state["recordAnswer"] = record_answer
    field_state["answerSummary"] = None
    field_state["answer"] = record_answer
    field_state["answerSource"] = "MANUAL"
    # Keep conversational evidence immutable.  A management edit changes only
    # the adopted/integrated answer in the parent question record.
    question_answers = interview_state.get("questionAnswers")
    if isinstance(question_answers, dict):
        matching = [
            value
            for value in question_answers.values()
            if isinstance(value, dict) and value.get("fieldId") == field_id
        ]
        if matching:
            question_answer = matching[-1]
            question_answer["answer"] = record_answer
            question_answer["answerSource"] = "MANUAL"
            question_answer["updatedAt"] = utc_now()
    commit_manual_correction_state(
        interview_state,
        record,
        user,
        source="record_answer_manual_correction",
        expected_state_version=payload.baseStateVersion,
    )
    write_audit_log(
        user,
        "interview_result_manual_correction",
        "record",
        record_id,
        {
            "artifact": "question_answer",
            "fieldId": field_id,
            "recordStatus": record.get("status"),
        },
    )

    return {
        "recordId": record["id"],
        "fieldId": field_id,
        "answerState": field_state["answerState"],
        "recordAnswer": record_answer,
        "answerSource": "MANUAL",
        "answerSummary": None,
        "recordStatus": record.get("status"),
    }


@router.patch("/records/{record_id}/requirement-states")
def update_record_requirement_states(
    record_id: str,
    payload: RequirementStatesManualUpdate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    record = get_scoped_item("records", record_id, user, "record_not_found")
    require_manual_correction_access(record, user)
    existing_state = store.get("interview_states", f"interview-state-{record_id}")
    if not existing_state:
        raise HTTPException(status_code=404, detail="interview_state_not_found")
    state = deepcopy(existing_state)
    if state.get("interviewProfile") != "system_requirement":
        raise HTTPException(status_code=409, detail="requirement_state_not_available")
    if int(state.get("stateVersion", 0) or 0) != payload.baseStateVersion:
        raise HTTPException(status_code=409, detail="interview_state_version_conflict")
    requirement_states = state.get("requirementStates")
    if not isinstance(requirement_states, dict):
        raise HTTPException(status_code=409, detail="requirement_state_not_available")

    changed_ids: list[str] = []
    for requirement_id, raw_value in payload.changes.items():
        value = raw_value.strip()
        if not requirement_id or len(requirement_id) > 128 or not value or len(value) > 10000:
            raise HTTPException(status_code=422, detail="invalid_requirement_correction")
        current = requirement_states.get(requirement_id)
        if not isinstance(current, dict):
            raise HTTPException(status_code=404, detail="requirement_entity_not_found")
        if current.get("status") != "CONFIRMED":
            raise HTTPException(status_code=409, detail="requirement_not_confirmed")
        if current.get("value") != value:
            current["value"] = value
            changed_ids.append(requirement_id)

    if changed_ids:
        commit_manual_correction_state(
            state,
            record,
            user,
            source="requirement_state_manual_correction",
            expected_state_version=payload.baseStateVersion,
        )
        write_audit_log(
            user,
            "interview_result_manual_correction",
            "record",
            record_id,
            {
                "artifact": "requirement_state",
                "requirementIds": sorted(changed_ids),
                "recordStatus": record.get("status"),
            },
        )
    result = get_interview_state_snapshot(record, user, persist=False)
    result["recordStatus"] = record.get("status")
    return result


@router.patch("/records/{record_id}/question-answers/{question_id}")
def update_record_question_answer(
    record_id: str,
    question_id: str,
    payload: ManualQuestionAnswerUpdate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    record = get_scoped_item("records", record_id, user, "record_not_found")
    require_manual_correction_access(record, user)
    existing_state = store.get("interview_states", f"interview-state-{record_id}")
    if not existing_state:
        raise HTTPException(status_code=404, detail="interview_state_not_found")
    state = deepcopy(existing_state)
    if int(state.get("stateVersion", 0) or 0) != payload.baseStateVersion:
        raise HTTPException(status_code=409, detail="interview_state_version_conflict")
    question_answers = state.get("questionAnswers")
    if not isinstance(question_answers, dict):
        raise HTTPException(status_code=404, detail="question_answer_not_found")
    question_answer = question_answers.get(question_id)
    if not isinstance(question_answer, dict):
        raise HTTPException(status_code=404, detail="question_answer_not_found")
    if question_answer.get("answerStatus") not in {"ANSWERED", "PARTIAL"}:
        raise HTTPException(status_code=409, detail="question_answer_not_final")
    answer = payload.answer.strip()
    if not answer:
        raise HTTPException(status_code=422, detail="question_answer_required")
    changed = answer != str(question_answer.get("answer") or "").strip()
    if changed:
        question_answer["answer"] = answer
        question_answer["answerSource"] = "MANUAL"
        question_answer["updatedAt"] = utc_now()
        field_id = question_answer.get("fieldId")
        field_states = state.get("fieldStates")
        field_state = (
            field_states.get(field_id)
            if isinstance(field_states, dict) and isinstance(field_id, str)
            else None
        )
        if isinstance(field_state, dict) and field_state.get("answerState") == "CONFIRMED":
            field_state["recordAnswer"] = answer
            field_state["answerSummary"] = None
            field_state["answer"] = answer
            field_state["answerSource"] = "MANUAL"
        commit_manual_correction_state(
            state,
            record,
            user,
            source="question_answer_manual_correction",
            expected_state_version=payload.baseStateVersion,
        )
        write_audit_log(
            user,
            "interview_result_manual_correction",
            "record",
            record_id,
            {
                "artifact": "question_answer",
                "questionId": question_id,
                "recordStatus": record.get("status"),
            },
        )
    return {
        "recordId": record_id,
        "questionId": question_id,
        "answer": answer,
        "answerStatus": question_answer.get("answerStatus"),
        "recordStatus": record.get("status"),
    }


@router.get("/records/{record_id}/stream")
async def stream_record(record_id: str, user: UserContext = Depends(get_current_user)) -> StreamingResponse:
    record = get_scoped_item("records", record_id, user, "record_not_found")
    require_record_action(record, user, "interview")
    require_interview_configuration(_get_record_knowledge(record, user))
    proposals = [row for row in store.list("proposals", user.tenant_id) if row["recordId"] == record_id]
    proposal_id = proposals[-1]["id"] if proposals else "pending"
    stream_result = generate_interview_reply(record, user)

    async def event_generator():
        stream_end_data = {"metadata": stream_result.metadata} if stream_result.metadata is not None else {}
        events = [("stream_start", {})]
        events.extend(("delta", {"text": chunk}) for chunk in stream_result.reply_chunks)
        events.extend([
            ("stream_end", stream_end_data),
            ("proposal_created", {"proposalId": proposal_id}),
        ])
        for event, data in events:
            yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.15)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


def _transition_record_status(
    item: dict,
    requested_status: str,
    user: UserContext,
    *,
    review_note: object = None,
) -> None:
    current_status = item.get("status", "draft")
    if current_status == requested_status:
        return

    allowed_transitions = {
        "draft": {"in_progress"},
        "in_progress": {"submitted"},
        "submitted": {"returned", "approved"},
        "returned": {"in_progress"},
        "approved": set(),
    }
    if requested_status not in allowed_transitions.get(current_status, set()):
        raise HTTPException(
            status_code=409,
            detail=f"invalid_record_status_transition_{current_status}_to_{requested_status}",
        )

    if requested_status == "in_progress":
        require_interview_configuration(_get_record_knowledge(item, user))
        if user.role == "interviewer":
            require_record_action(item, user, "answer")
        else:
            require_management_role(user)
    elif requested_status == "submitted":
        require_record_action(item, user, "submit")
        if user.role == "interviewer":
            interview_state = store.get("interview_states", f"interview-state-{item['id']}")
            if not interview_state or interview_state.get("status") != "completed":
                raise HTTPException(status_code=409, detail="interview_not_completed")
    elif requested_status in {"returned", "approved"}:
        require_record_action(item, user, "review")

    if requested_status == "returned":
        effective_review_note = review_note if review_note is not None else item.get("reviewNote")
        if not str(effective_review_note or "").strip():
            raise HTTPException(status_code=422, detail="review_note_required")
    elif requested_status == "in_progress" and current_status == "returned":
        item["reviewNote"] = None

    item["status"] = requested_status


def _get_record_knowledge(record: dict, user: UserContext) -> dict:
    return get_scoped_item("knowledges", record["knowledgeId"], user, "knowledge_not_found")
