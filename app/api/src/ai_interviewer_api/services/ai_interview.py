import logging
from dataclasses import dataclass
from typing import Any

from ai_interviewer_api.agents.interview.adapter import run_adapted_interview_turn as _run_adapted_interview_turn
from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.core.config import settings
from ai_interviewer_api.models.domain import AiProposal
from ai_interviewer_api.repositories.store import store


logger = logging.getLogger(__name__)
_SAFE_INTERVIEW_ERROR_REPLY = "一時的にAI応答を生成できませんでした。少し時間をおいて再度送信してください。"


@dataclass(frozen=True)
class InterviewStreamResult:
    reply_chunks: list[str]
    metadata: dict[str, Any] | None = None


def build_mock_proposal(user: UserContext, record_id: str, knowledge_id: str, content: str) -> AiProposal:
    symptom = "圧入荷重が不安定" if "荷重" in content or "圧入" in content else "症状要確認"
    return AiProposal(
        tenantId=user.tenant_id,
        createdByUserId=user.user_id,
        updatedByUserId=user.user_id,
        recordId=record_id,
        knowledgeId=knowledge_id,
        structuredData={
            "equipment": "圧入機A",
            "symptom": symptom,
            "actions": ["治具清掃", "位置決めピン確認"],
        },
    )


def build_record_summary_proposal(user: UserContext, record: dict) -> AiProposal:
    summary = summarize_record(record, user)
    return AiProposal(
        tenantId=user.tenant_id,
        createdByUserId=user.user_id,
        updatedByUserId=user.user_id,
        recordId=record["id"],
        knowledgeId=record["knowledgeId"],
        proposalType="record_summary",
        status="needs_review",
        structuredData={"summary": summary},
        confidence=0.74,
    )


def summarize_record(record: dict, user: UserContext) -> str:
    if settings.bedrock_enabled:
        try:
            return _summarize_with_bedrock(record, user)
        except Exception:
            pass
    return _fallback_summary(record, user)


def _summarize_with_bedrock(record: dict, user: UserContext) -> str:
    import boto3

    client = boto3.client("bedrock-runtime", region_name=settings.bedrock_aws_region)
    response = client.converse(
        modelId=settings.bedrock_model_id,
        system=[
            {
                "text": (
                    "あなたは製造業のAIインタビュー記録を要約する補助AIです。"
                    "記録された内容だけを根拠に、未確認事項は断定せず、"
                    "日本語で80文字から160文字程度に要約してください。"
                )
            }
        ],
        messages=[
            {
                "role": "user",
                "content": [{"text": _format_record_for_summary(record, user)}],
            }
        ],
        inferenceConfig={
            "maxTokens": min(settings.bedrock_max_tokens, 500),
            "temperature": 0.1,
        },
    )
    content = response.get("output", {}).get("message", {}).get("content", [])
    text = "\n".join(part["text"] for part in content if "text" in part).strip()
    return text or _fallback_summary(record, user)


def _format_record_for_summary(record: dict, user: UserContext) -> str:
    messages = [
        row
        for row in store.list("messages", user.tenant_id)
        if row.get("recordId") == record["id"]
    ]
    proposals = [
        row
        for row in store.list("proposals", user.tenant_id)
        if row.get("recordId") == record["id"] and row.get("proposalType") == "field_update"
    ]
    lines = [
        f"記録タイトル: {record.get('title') or '未設定'}",
        f"既存要約: {record.get('summary') or '未作成'}",
        "会話:",
    ]
    lines.extend(f"- {message.get('role', 'user')}: {message.get('content', '')}" for message in messages[-10:])
    lines.append("構造化候補:")
    lines.extend(f"- {proposal.get('structuredData', {})}" for proposal in proposals[-5:])
    return "\n".join(lines)


def _fallback_summary(record: dict, user: UserContext) -> str:
    messages = [
        row
        for row in store.list("messages", user.tenant_id)
        if row.get("recordId") == record["id"]
    ]
    latest_message = messages[-1]["content"] if messages else ""
    if latest_message:
        return f"{record.get('title', '記録')}について、{latest_message[:90]}を中心に確認した記録です。"
    return f"{record.get('title', '記録')}の要約候補です。詳細内容を確認してから保存してください。"


def summarize_knowledge_records(knowledge: dict, user: UserContext) -> str:
    if settings.bedrock_enabled:
        try:
            return _summarize_knowledge_with_bedrock(knowledge, user)
        except Exception:
            pass
    return _fallback_knowledge_summary(knowledge, user)


def _summarize_knowledge_with_bedrock(knowledge: dict, user: UserContext) -> str:
    import boto3

    client = boto3.client("bedrock-runtime", region_name=settings.bedrock_aws_region)
    response = client.converse(
        modelId=knowledge.get("defaultModelId") or settings.bedrock_model_id,
        system=[
            {
                "text": (
                    "あなたは製造業のナレッジ概要を要約する補助AIです。"
                    "記録済み内容だけを根拠に、未確認事項は断定せず、"
                    "概要画面に表示する日本語の要約を120文字から240文字程度で作成してください。"
                )
            }
        ],
        messages=[{"role": "user", "content": [{"text": _format_knowledge_for_summary(knowledge, user)}]}],
        inferenceConfig={
            "maxTokens": min(settings.bedrock_max_tokens, 700),
            "temperature": 0.1,
        },
    )
    content = response.get("output", {}).get("message", {}).get("content", [])
    text = "\n".join(part["text"] for part in content if "text" in part).strip()
    return text or _fallback_knowledge_summary(knowledge, user)


def _format_knowledge_for_summary(knowledge: dict, user: UserContext) -> str:
    records = [
        row
        for row in store.list("records", user.tenant_id)
        if row.get("knowledgeId") == knowledge["id"]
    ]
    messages = [
        row
        for row in store.list("messages", user.tenant_id)
        if row.get("recordId") in {record["id"] for record in records}
    ]
    lines = [
        f"ナレッジ名: {knowledge.get('name')}",
        f"用途: {knowledge.get('purpose') or knowledge.get('category') or '未設定'}",
        f"既存概要要約: {knowledge.get('summary') or '未作成'}",
        "記録:",
    ]
    lines.extend(
        f"- {record.get('title')}: {record.get('summary') or '要約未作成'}"
        for record in records[-10:]
    )
    lines.append("直近チャット内容:")
    lines.extend(f"- {message.get('content', '')}" for message in messages[-10:])
    return "\n".join(lines)


def _fallback_knowledge_summary(knowledge: dict, user: UserContext) -> str:
    records = [
        row
        for row in store.list("records", user.tenant_id)
        if row.get("knowledgeId") == knowledge["id"]
    ]
    if not records:
        return ""
    titled = "、".join(record.get("title", "記録") for record in records[-3:])
    return f"{knowledge.get('name', 'ナレッジ')}では、{titled}などの記録をもとに現場ノウハウを整理しています。内容を確認してから保存してください。"


def generate_interview_reply(record: dict, user: UserContext) -> InterviewStreamResult:
    knowledge = store.get("knowledges", record["knowledgeId"])
    try:
        logger.info(
            "Using Strands interview agent record_id=%s knowledge_id=%s",
            record["id"],
            record["knowledgeId"],
        )
        return _generate_interview_stream_result_with_strands(record, knowledge, user)
    except Exception:
        logger.exception("Strands interview agent failed; returning safe error response")
        return InterviewStreamResult(
            reply_chunks=[_SAFE_INTERVIEW_ERROR_REPLY],
            metadata={"error": "strands_interview_failed"},
        )


def _generate_interview_stream_result_with_strands(
    record: dict,
    knowledge: dict,
    user: UserContext,
) -> InterviewStreamResult:
    result = run_adapted_interview_turn(
        record,
        knowledge,
        _list_record_messages(record, user),
        _list_interview_fields(knowledge, user),
    )
    if result.reply_chunks:
        return InterviewStreamResult(
            reply_chunks=result.reply_chunks,
            metadata={
                "answer_status": result.answer_status,
                "reask_question": result.reask_question,
                "next_questions": list(result.next_questions),
                "draft_updates": dict(result.draft_updates),
                "used_tools": list(result.used_tools),
            },
        )
    logger.warning(
        "Strands interview agent returned empty reply; using safe error response record_id=%s knowledge_id=%s",
        record["id"],
        record["knowledgeId"],
    )
    return InterviewStreamResult(
        reply_chunks=[_SAFE_INTERVIEW_ERROR_REPLY],
        metadata={"error": "strands_interview_empty_reply"},
    )


def _list_record_messages(record: dict, user: UserContext) -> list[dict]:
    return [
        row
        for row in store.list("messages", user.tenant_id)
        if row.get("recordId") == record["id"]
    ]


def _list_interview_fields(knowledge: dict, user: UserContext) -> list[dict]:
    return [
        row
        for row in store.list("knowledge_fields", user.tenant_id)
        if row.get("knowledgeId") == knowledge["id"] and row.get("askByAi")
    ]
def run_adapted_interview_turn(
    record: dict,
    knowledge: dict,
    messages: list[dict],
    knowledge_fields: list[dict],
):
    return _run_adapted_interview_turn(record, knowledge, messages, knowledge_fields)
