import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ai_interviewer_api.auth.deps import UserContext, get_current_user
from ai_interviewer_api.core.interview_configuration import require_interview_configuration
from ai_interviewer_api.core.permissions import (
    accessible_records,
    require_admin_role,
    require_management_role,
    require_record_action,
)
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.models.domain import InterviewRecord
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.common import get_scoped_item
from ai_interviewer_api.schemas.requests import (
    ChatMessageCreate,
    InterviewAnswerUpdate,
    ProcessModelCommand,
    ProcessModelUpdate,
    RecordCreate,
    RecordUpdate,
)
from ai_interviewer_api.services.audit import write_audit_log
from ai_interviewer_api.services.ai_interview import (
    build_mock_proposal,
    generate_interview_reply,
    get_interview_state_snapshot,
)
from ai_interviewer_api.services.record_lifecycle import sync_record_status_after_interview
from ai_interviewer_api.services.process_model import edit_process_model, save_process_model

router = APIRouter(prefix="/api")


@router.post("/knowledges/{knowledge_id}/records")
def create_record(
    knowledge_id: str,
    payload: RecordCreate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    require_management_role(user)
    knowledge = get_scoped_item("knowledges", knowledge_id, user, "knowledge_not_found")
    require_interview_configuration(knowledge)
    payload_data = payload.model_dump()
    owner_user_id = payload_data.pop("ownerUserId", None) or user.user_id
    item = InterviewRecord(
        tenantId=user.tenant_id,
        createdByUserId=user.user_id,
        updatedByUserId=user.user_id,
        knowledgeId=knowledge_id,
        knowledgeName=knowledge["name"],
        ownerUserId=owner_user_id,
        status="in_progress",
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
    return [row for row in store.list("records", user.tenant_id) if row["knowledgeId"] == knowledge_id]


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
        "knowledge": _interview_context_knowledge(knowledge, user),
        "fields": _interview_context_fields(fields, user),
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
        if requested_updates:
            raise HTTPException(status_code=403, detail="record_management_forbidden")
        if requested_status is None:
            raise HTTPException(status_code=403, detail="record_management_forbidden")
    else:
        require_management_role(user)

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
            else f"msg-{len(store.tables['messages']) + 1}"
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
    require_record_action(record, user, "answer")
    interview_state = store.get("interview_states", f"interview-state-{record_id}")
    if not interview_state:
        raise HTTPException(status_code=404, detail="interview_state_not_found")

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
    interview_state["stateVersion"] = int(interview_state.get("stateVersion", 0) or 0) + 1
    interview_state["updatedByUserId"] = user.user_id
    interview_state["updatedAt"] = utc_now()
    store.upsert("interview_states", interview_state)

    confirmed_messages = [
        message
        for message in store.list("messages", user.tenant_id)
        if message.get("recordId") == record_id
        and message.get("messageType") == "confirmed_answer"
        and message.get("answerToFieldId") == field_id
    ]
    if confirmed_messages:
        confirmed_message = confirmed_messages[-1]
        confirmed_message["content"] = record_answer
        confirmed_message["updatedByUserId"] = user.user_id
        confirmed_message["updatedAt"] = utc_now()
        store.upsert("messages", confirmed_message)

    return {
        "recordId": record["id"],
        "fieldId": field_id,
        "answerState": field_state["answerState"],
        "recordAnswer": record_answer,
        "answerSummary": None,
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


def _interview_context_knowledge(knowledge: dict, user: UserContext) -> dict:
    if user.role in {"admin", "knowledge_manager"}:
        return knowledge

    result = dict(knowledge)
    result.pop("systemPrompt", None)
    result.pop("defaultModelId", None)
    plan = result.get("interviewPlan")
    if isinstance(plan, dict):
        result["interviewPlan"] = {
            "profile": plan.get("profile"),
            "modelId": plan.get("modelId"),
        }
    return result


def _interview_context_fields(fields: list[dict], user: UserContext) -> list[dict]:
    if user.role in {"admin", "knowledge_manager"}:
        return fields

    public_keys = {
        "id",
        "tenantId",
        "createdByUserId",
        "updatedByUserId",
        "knowledgeId",
        "name",
        "description",
        "inputType",
        "required",
        "askByAi",
        "aiQuestionExamples",
        "options",
        "displayOrder",
    }
    return [{key: value for key, value in field.items() if key in public_keys} for field in fields]
