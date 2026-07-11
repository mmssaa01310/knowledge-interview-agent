from fastapi import APIRouter, Depends

from ai_interviewer_api.auth.deps import UserContext, get_current_user
from ai_interviewer_api.models.domain import ChatAnswer
from ai_interviewer_api.schemas.requests import ChatMessageCreate
from ai_interviewer_api.services.bedrock_chat import answer_with_bedrock

router = APIRouter(prefix="/api")


@router.post("/chats")
def create_chat(user: UserContext = Depends(get_current_user)) -> dict:
    return {"chatId": "chat-demo", "tenantId": user.tenant_id}


@router.post("/chats/{chat_id}/messages")
def answer_chat(chat_id: str, payload: ChatMessageCreate, user: UserContext = Depends(get_current_user)) -> ChatAnswer:
    _ = chat_id
    return answer_with_bedrock(payload, user)
