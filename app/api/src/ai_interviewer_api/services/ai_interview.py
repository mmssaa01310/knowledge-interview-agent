from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.core.config import settings
from ai_interviewer_api.models.domain import AiProposal
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.services.prompts.loader import build_interview_system_prompt


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


def generate_interview_reply(record: dict, user: UserContext) -> list[str]:
    knowledge = store.get("knowledges", record["knowledgeId"])
    if settings.bedrock_enabled:
        try:
            return _generate_interview_reply_with_bedrock(record, knowledge, user)
        except Exception:
            pass
    return _fallback_interview_reply(record, knowledge, user)


def _generate_interview_reply_with_bedrock(record: dict, knowledge: dict, user: UserContext) -> list[str]:
    import boto3

    client = boto3.client("bedrock-runtime", region_name=settings.bedrock_aws_region)
    response = client.converse(
        modelId=knowledge.get("defaultModelId") or settings.bedrock_model_id,
        system=[{"text": build_interview_system_prompt(knowledge.get("systemPrompt"))}],
        messages=[
            {
                "role": "user",
                "content": [{"text": _format_record_for_interview(record, knowledge, user)}],
            }
        ],
        inferenceConfig={
            "maxTokens": min(settings.bedrock_max_tokens, 500),
            "temperature": 0.2,
        },
    )
    content = response.get("output", {}).get("message", {}).get("content", [])
    text = "\n".join(part["text"] for part in content if "text" in part).strip()
    if not text:
        return _fallback_interview_reply(record, knowledge, user)
    return _split_interview_reply(text)


def _format_record_for_interview(record: dict, knowledge: dict, user: UserContext) -> str:
    messages = [
        row
        for row in store.list("messages", user.tenant_id)
        if row.get("recordId") == record["id"]
    ]
    fields = [
        row
        for row in store.list("knowledge_fields", user.tenant_id)
        if row.get("knowledgeId") == knowledge["id"] and row.get("askByAi")
    ]
    lines = [
        f"ナレッジ名: {knowledge.get('name') or '未設定'}",
        f"ナレッジ説明: {knowledge.get('description') or '未設定'}",
        f"対象業務: {knowledge.get('targetBusiness') or '未設定'}",
        f"対象設備: {knowledge.get('targetEquipment') or '未設定'}",
        f"記録タイトル: {record.get('title') or '未設定'}",
        "ヒアリング項目:",
    ]
    lines.extend(
        f"- {field.get('name')}: {field.get('description') or '説明未設定'}"
        for field in sorted(fields, key=lambda field: field.get("displayOrder", 0))
    )
    lines.append("直近会話:")
    lines.extend(
        f"- {message.get('role', 'user')}: {message.get('content', '')}"
        for message in messages[-8:]
    )
    lines.append("次にインタビューで返す日本語の質問だけを1文から2文で作成してください。")
    return "\n".join(lines)


def _fallback_interview_reply(record: dict, knowledge: dict, user: UserContext) -> list[str]:
    messages = [
        row
        for row in store.list("messages", user.tenant_id)
        if row.get("recordId") == record["id"]
    ]
    fields = [
        row
        for row in store.list("knowledge_fields", user.tenant_id)
        if row.get("knowledgeId") == knowledge["id"] and row.get("askByAi")
    ]
    next_field = next(
        iter(sorted(fields, key=lambda field: field.get("displayOrder", 0))),
        None,
    )
    latest_message = messages[-1]["content"] if messages else ""
    custom_prompt = (knowledge.get("systemPrompt") or "").strip()

    intro = "状況を具体化するため、まず再現条件から確認します。"
    if "停止判断" in custom_prompt:
        intro = "停止判断に関わる条件を優先して確認します。"
    elif "切り分け" in custom_prompt:
        intro = "原因の切り分けに必要な条件から順に確認します。"

    if next_field:
        question = f"{next_field.get('name')}として、{_field_question_hint(next_field, latest_message)}"
    else:
        question = "その事象が起きた条件やタイミングをもう少し具体的に教えてください。"
    return [intro, question]


def _field_question_hint(field: dict, latest_message: str) -> str:
    examples = field.get("aiQuestionExamples") or []
    if examples:
        return str(examples[0]).strip()
    field_name = field.get("name") or "確認項目"
    if latest_message and "いつ" not in latest_message:
        return f"{field_name}について、いつ・どの条件で発生したのか教えてください。"
    return f"{field_name}について、現場でどう見分けているか教えてください。"


def _split_interview_reply(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return []
    chunks = [part.strip() for part in normalized.split("\n") if part.strip()]
    if len(chunks) >= 2:
        return chunks[:2]

    sentence_like_chunks = [
        part.strip()
        for part in normalized.replace("。", "。\n").split("\n")
        if part.strip()
    ]
    return sentence_like_chunks[:2] or [normalized]
