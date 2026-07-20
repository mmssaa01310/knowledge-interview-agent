import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ai_interviewer_api.auth.deps import UserContext, get_current_user
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.models.domain import InterviewRecord
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.routers.common import get_scoped_item
from ai_interviewer_api.schemas.requests import ChatMessageCreate, InterviewAnswerUpdate, RecordCreate, RecordUpdate
from ai_interviewer_api.services.ai_interview import (
    build_mock_proposal,
    build_record_summary_proposal,
    generate_interview_reply,
    get_interview_state_snapshot,
)

router = APIRouter(prefix="/api")


@router.post("/knowledges/{knowledge_id}/records")
def create_record(
    knowledge_id: str,
    payload: RecordCreate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    knowledge = get_scoped_item("knowledges", knowledge_id, user, "knowledge_not_found")
    item = InterviewRecord(
        tenantId=user.tenant_id,
        createdByUserId=user.user_id,
        updatedByUserId=user.user_id,
        knowledgeId=knowledge_id,
        knowledgeName=knowledge["name"],
        **payload.model_dump(),
    )
    store.upsert("records", item.model_dump())
    return item.model_dump()


@router.get("/knowledges/{knowledge_id}/records")
def list_records(knowledge_id: str, user: UserContext = Depends(get_current_user)) -> list[dict]:
    get_scoped_item("knowledges", knowledge_id, user, "knowledge_not_found")
    return [row for row in store.list("records", user.tenant_id) if row["knowledgeId"] == knowledge_id]


@router.get("/records/{record_id}")
def get_record(record_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    return get_scoped_item("records", record_id, user, "record_not_found")


@router.patch("/records/{record_id}")
def update_record(
    record_id: str,
    payload: RecordUpdate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    item = get_record(record_id, user)
    for key, value in payload.model_dump(exclude_unset=True).items():
        item[key] = value
    item["updatedByUserId"] = user.user_id
    item["updatedAt"] = utc_now()
    store.upsert("records", item)
    return item


@router.delete("/records/{record_id}")
def delete_record(record_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    get_record(record_id, user)
    store.delete("records", record_id)
    return {"deleted": True}


@router.post("/records/{record_id}/messages")
def create_record_message(
    record_id: str,
    payload: ChatMessageCreate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    record = get_scoped_item("records", record_id, user, "record_not_found")
    proposal = build_mock_proposal(user, record_id, record["knowledgeId"], payload.content)
    store.upsert("proposals", proposal.model_dump())
    interview_state = store.get("interview_states", f"interview-state-{record_id}") or {}
    current_question_id = payload.answerToQuestionId or interview_state.get("currentQuestionId")
    current_field_id = interview_state.get("currentFieldId")
    question_type = None
    if current_question_id:
        for question in interview_state.get("askedQuestions", []):
            if question.get("questionId") == current_question_id:
                question_type = question.get("questionType")
                current_field_id = question.get("fieldId") or current_field_id
                break
    message = {
        "id": f"msg-{len(store.tables['messages']) + 1}",
        "tenantId": user.tenant_id,
        "recordId": record_id,
        "content": payload.content,
        "role": "user",
        "isActualUtterance": True,
        "createdAt": utc_now(),
        "updatedAt": utc_now(),
        "answerToQuestionId": current_question_id,
        "answerToFieldId": current_field_id,
        "questionType": question_type,
    }
    store.upsert(
        "messages",
        message,
    )
    return {"message": "accepted", "proposalId": proposal.id, "recordMessage": message}


@router.get("/records/{record_id}/interview-state")
def get_record_interview_state(
    record_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    record = get_scoped_item("records", record_id, user, "record_not_found")
    return get_interview_state_snapshot(record, user)


@router.patch("/records/{record_id}/interview-answers/{field_id}")
def update_record_interview_answer(
    record_id: str,
    field_id: str,
    payload: InterviewAnswerUpdate,
    user: UserContext = Depends(get_current_user),
) -> dict:
    record = get_scoped_item("records", record_id, user, "record_not_found")
    interview_state = store.get("interview_states", f"interview-state-{record_id}")
    if not interview_state:
        raise HTTPException(status_code=404, detail="interview_state_not_found")

    field_state = interview_state.get("fieldStates", {}).get(field_id)
    if not field_state:
        raise HTTPException(status_code=404, detail="interview_field_state_not_found")
    if field_state.get("answerState") != "CONFIRMED":
        raise HTTPException(status_code=409, detail="interview_answer_not_confirmed")

    answer_summary = payload.answerSummary.strip()
    if not answer_summary:
        raise HTTPException(status_code=422, detail="interview_answer_required")

    field_state["answerSummary"] = answer_summary
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
        confirmed_message["content"] = answer_summary
        confirmed_message["updatedByUserId"] = user.user_id
        confirmed_message["updatedAt"] = utc_now()
        store.upsert("messages", confirmed_message)

    return {
        "recordId": record["id"],
        "fieldId": field_id,
        "answerState": field_state["answerState"],
        "answerSummary": answer_summary,
    }


@router.post("/records/{record_id}/summary-proposals")
def create_summary_proposal(
    record_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    record = get_scoped_item("records", record_id, user, "record_not_found")
    proposal = build_record_summary_proposal(user, record)
    store.upsert("proposals", proposal.model_dump())
    return proposal.model_dump()


@router.get("/records/{record_id}/stream")
async def stream_record(record_id: str, user: UserContext = Depends(get_current_user)) -> StreamingResponse:
    record = get_scoped_item("records", record_id, user, "record_not_found")
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
