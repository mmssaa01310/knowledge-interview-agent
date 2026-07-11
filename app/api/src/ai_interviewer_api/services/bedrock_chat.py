from __future__ import annotations

import logging
from dataclasses import dataclass

from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.core.config import settings
from ai_interviewer_api.models.domain import ChatAnswer
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.schemas.requests import ChatMessageCreate

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatContext:
    knowledge_dbs: list[dict]
    knowledges: list[dict]
    documents: list[dict]
    citations: list[str]


def build_chat_context(payload: ChatMessageCreate, user: UserContext) -> ChatContext:
    requested_db_ids = set(payload.referenceKnowledgeDbIds)
    requested_knowledge_ids = set(payload.referenceKnowledgeIds)
    requested_document_ids = set(payload.referenceDocumentIds) - set(payload.excludedDocumentIds)

    knowledge_dbs = [
        db
        for db in store.list("knowledge_dbs", user.tenant_id)
        if db["id"] in requested_db_ids and db.get("status") == "active"
    ][: payload.searchLimit]
    knowledges = [
        knowledge
        for knowledge in store.list("knowledges", user.tenant_id)
        if knowledge["id"] in requested_knowledge_ids and knowledge.get("status") == "active"
    ][: payload.searchLimit]
    documents = [
        doc
        for doc in store.list("documents", user.tenant_id)
        if doc["id"] in requested_document_ids and doc.get("ingestionStatus") == "completed"
    ][: payload.searchLimit]

    citations = [
        f"ナレッジDB: {db['name']}"
        for db in knowledge_dbs
    ] + [
        f"ナレッジ: {knowledge['name']}"
        for knowledge in knowledges
    ] + [
        f"文書: {doc['fileName']}"
        for doc in documents
    ]

    return ChatContext(knowledge_dbs=knowledge_dbs, knowledges=knowledges, documents=documents, citations=citations)


def answer_with_bedrock(payload: ChatMessageCreate, user: UserContext) -> ChatAnswer:
    context = build_chat_context(payload, user)
    if not settings.bedrock_enabled:
        return _fallback_answer(context)

    model_id = payload.modelId or settings.bedrock_model_id

    try:
        return ChatAnswer(
            answer=_invoke_bedrock_converse(model_id, payload.content, context),
            citations=context.citations,
        )
    except Exception:
        logger.exception("Failed to answer chat with Amazon Bedrock")
        return _fallback_answer(context)


def _invoke_bedrock_converse(model_id: str, user_message: str, context: ChatContext) -> str:
    import boto3

    client = boto3.client("bedrock-runtime", region_name=settings.bedrock_aws_region)
    response = client.converse(
        modelId=model_id,
        system=[
            {
                "text": (
                    "あなたは製造業の暗黙知を参照する業務支援AIです。"
                    "承認済みナレッジと取り込み完了ドキュメントだけを根拠に、"
                    "不明な点は推測せず日本語で簡潔に回答してください。"
                )
            }
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "text": (
                            f"質問:\n{user_message}\n\n"
                            f"参照コンテキスト:\n{_format_context(context)}"
                        )
                    }
                ],
            }
        ],
        inferenceConfig={
            "maxTokens": settings.bedrock_max_tokens,
            "temperature": settings.bedrock_temperature,
        },
    )
    content = response.get("output", {}).get("message", {}).get("content", [])
    return "\n".join(part["text"] for part in content if "text" in part).strip()


def _format_context(context: ChatContext) -> str:
    if not context.knowledge_dbs and not context.knowledges and not context.documents:
        return "参照可能な承認済みナレッジまたは取り込み完了ドキュメントはありません。"

    lines: list[str] = []
    for db in context.knowledge_dbs:
        lines.append(
            f"- ナレッジDB: {db['name']} / "
            f"説明: {db.get('description') or '説明未設定'}"
        )
    for knowledge in context.knowledges:
        lines.append(
            f"- ナレッジ: {knowledge['name']} / 用途: {knowledge.get('purpose') or knowledge.get('category') or '未分類'} / "
            f"対象設備: {knowledge.get('targetEquipment') or '未設定'} / "
            f"説明: {knowledge.get('description') or '説明未設定'}"
        )
    for doc in context.documents:
        lines.append(f"- 文書: {doc['fileName']} / 状態: {doc.get('ingestionStatus')}")
    return "\n".join(lines)


def _fallback_answer(context: ChatContext) -> ChatAnswer:
    if not context.citations:
        return ChatAnswer(
            answer="参照可能な承認済みナレッジまたは取り込み完了ドキュメントがないため、回答根拠を提示できません。",
            citations=[],
        )
    return ChatAnswer(
        answer="承認済みナレッジと取り込み完了ドキュメントを参照すると、一次対応は治具清掃と位置決め確認です。",
        citations=context.citations,
    )
