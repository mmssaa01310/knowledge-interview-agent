import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any
from uuid import uuid4

from ai_interviewer_api.agents.interview.adapter import AdaptedInterviewTurnResult
from ai_interviewer_api.agents.interview.adapter import run_adapted_interview_turn as _run_adapted_interview_turn
from ai_interviewer_api.agents.interview.schemas import InterviewAgentResult, InterviewQuestion, InterviewState
from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.core.config import settings
from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.models.domain import AiProposal
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.services.dialogue_interpreter import (
    DialogueInterpretation,
    interpret_dialogue_act,
    should_route_to_answer_processor,
)
from ai_interviewer_api.services.interview_answer_processor import (
    AnswerEvaluation,
    ConfirmationEvaluation,
    InterviewAnswerProcessor,
    compose_record_answer,
)


logger = logging.getLogger(__name__)
_SAFE_INTERVIEW_ERROR_REPLY = "一時的にAI応答を生成できませんでした。少し時間をおいて再度送信してください。"
DIRECT_CAPTURE_TYPES = {
    "name",
    "department",
    "role",
    "date",
    "time",
    "location",
    "number",
    "yes_no",
    "choice",
    "free_text_capture",
}
DIRECT_CAPTURE_KEYWORDS = (
    "氏名",
    "名前",
    "お名前",
    "所属",
    "部署",
    "担当",
    "役割",
    "日時",
    "日付",
    "時刻",
    "場所",
    "設備",
    "回数",
)


class RetrievalPolicy(str, Enum):
    NEVER = "never"
    AUTO = "auto"
    REQUIRED = "required"


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


def generate_interview_reply(
    record: dict,
    user: UserContext,
    *,
    persist_assistant_messages: bool = True,
) -> InterviewStreamResult:
    knowledge = store.get("knowledges", record["knowledgeId"])
    try:
        logger.info(
            "Using Strands interview agent record_id=%s knowledge_id=%s",
            record["id"],
            record["knowledgeId"],
        )
        return _generate_interview_stream_result_with_strands(
            record,
            knowledge,
            user,
            persist_assistant_messages=persist_assistant_messages,
        )
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
    *,
    persist_assistant_messages: bool = True,
) -> InterviewStreamResult:
    messages = _list_record_messages(record, user)
    knowledge_fields = _list_interview_fields(knowledge, user)
    interview_state = _load_or_initialize_interview_state(record, knowledge_fields, user)
    field_lookup = _build_field_lookup(knowledge_fields)

    if interview_state["status"] == "completed":
        _migrate_formal_record_answers(interview_state, messages, user)
        result = _build_finish_result(record, interview_state, messages, field_lookup)
        return InterviewStreamResult(reply_chunks=_split_reply_chunks(result["reply"]), metadata=result)

    last_processed_user_message_id = interview_state.get("lastProcessedUserMessageId")
    current_question = _get_current_question(interview_state)
    latest_user_turn = _latest_user_turn(messages)
    latest_user_message = _latest_user_message(
        messages,
        current_question.get("questionId") if current_question else None,
    )

    if (
        current_question is not None
        and latest_user_turn is not None
        and latest_user_turn.get("turnType") == "CONTROL"
        and latest_user_turn.get("id") != last_processed_user_message_id
    ):
        result = _acknowledge_control_turn(
            record,
            interview_state,
            current_question,
            messages,
            latest_user_turn,
            user,
            persist_assistant_message=persist_assistant_messages,
        )
        return InterviewStreamResult(reply_chunks=_split_reply_chunks(result["reply"]), metadata=result)

    if current_question is None or latest_user_message is None or latest_user_message.get("id") == last_processed_user_message_id:
        _migrate_formal_record_answers(interview_state, messages, user)
        result = _ask_next_configured_field(
            record,
            knowledge_fields,
            interview_state,
            messages,
            user,
            persist_assistant_message=persist_assistant_messages,
        )
        return InterviewStreamResult(reply_chunks=_split_reply_chunks(result["reply"]), metadata=result)

    retrieval_policy = _resolve_retrieval_policy(current_question, knowledge_fields)
    logger.info(
        "voice_retrieval_decision_completed record_id=%s question_id=%s retrieval_policy=%s retrieval_executed=%s",
        record["id"],
        current_question.get("questionId"),
        retrieval_policy.value,
        retrieval_policy == RetrievalPolicy.REQUIRED,
    )
    result = _process_text_answer_turn(
        record,
        knowledge,
        knowledge_fields,
        messages,
        interview_state,
        current_question,
        latest_user_message,
        user,
        persist_assistant_message=persist_assistant_messages,
        retrieval_policy=retrieval_policy.value,
    )
    return InterviewStreamResult(reply_chunks=_split_reply_chunks(result["reply"]), metadata=result)


def _list_record_messages(record: dict, user: UserContext) -> list[dict]:
    return sorted(
        [
            row
            for row in store.list("messages", user.tenant_id)
            if row.get("recordId") == record["id"]
        ],
        key=lambda row: (row.get("createdAt") or "", row.get("id") or ""),
    )


def _list_interview_fields(knowledge: dict, user: UserContext) -> list[dict]:
    return sorted(
        [
            row
            for row in store.list("knowledge_fields", user.tenant_id)
            if row.get("knowledgeId") == knowledge["id"] and row.get("askByAi")
        ],
        key=lambda row: int(row.get("displayOrder") or 0),
    )


def _load_or_initialize_interview_state(record: dict, knowledge_fields: list[dict], user: UserContext) -> dict[str, Any]:
    state_id = _build_interview_state_id(record["id"])
    existing = store.get("interview_states", state_id)
    if existing:
        _sync_interview_state_fields(existing, knowledge_fields, user)
        return existing

    pending_field_ids = [field["id"] for field in knowledge_fields if field.get("id")]
    field_states = {
        field_id: {
            "fieldId": field_id,
            "status": "pending",
            "answerSummary": None,
            "rawAnswer": None,
            "rawAnswerHistory": [],
            "recordAnswer": None,
            "capturedItems": [],
            "missingInformation": [],
            "answerState": "UNANSWERED",
            "candidateAnswer": None,
        }
        for field_id in pending_field_ids
    }
    state = InterviewState(
        status="in_progress",
        currentFieldId=pending_field_ids[0] if pending_field_ids else None,
        currentQuestionId=None,
        completedFieldIds=[],
        pendingFieldIds=pending_field_ids,
        askedQuestions=[],
        followUpCounts={field_id: 0 for field_id in pending_field_ids},
        fieldStates=field_states,
        lastProcessedUserMessageId=None,
    ).model_dump()
    state.update(
        {
            "id": state_id,
            "tenantId": user.tenant_id,
            "recordId": record["id"],
            "createdByUserId": user.user_id,
            "updatedByUserId": user.user_id,
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
        }
    )
    store.upsert("interview_states", state)
    return state


def _sync_interview_state_fields(
    interview_state: dict[str, Any],
    knowledge_fields: list[dict],
    user: UserContext,
) -> None:
    changed = _migrate_legacy_answer_states(interview_state)
    field_ids = [field["id"] for field in knowledge_fields if field.get("id")]
    if not field_ids:
        if changed:
            _persist_interview_state(interview_state, user)
        return

    pending_field_ids = interview_state.setdefault("pendingFieldIds", [])
    completed_field_ids = interview_state.setdefault("completedFieldIds", [])
    field_states = interview_state.setdefault("fieldStates", {})
    follow_up_counts = interview_state.setdefault("followUpCounts", {})
    known_field_ids = set(pending_field_ids) | set(completed_field_ids) | set(field_states.keys())
    missing_field_ids = [field_id for field_id in field_ids if field_id not in known_field_ids]
    if not missing_field_ids:
        if changed:
            _persist_interview_state(interview_state, user)
        return

    pending_field_ids.extend(missing_field_ids)
    for field_id in missing_field_ids:
        field_states[field_id] = {
            "fieldId": field_id,
            "status": "pending",
            "answerSummary": None,
            "rawAnswer": None,
            "rawAnswerHistory": [],
            "recordAnswer": None,
            "capturedItems": [],
            "missingInformation": [],
            "answerState": "UNANSWERED",
            "candidateAnswer": None,
        }
        follow_up_counts.setdefault(field_id, 0)

    if interview_state.get("status") == "completed":
        interview_state["status"] = "in_progress"
    if not interview_state.get("currentFieldId"):
        interview_state["currentFieldId"] = missing_field_ids[0]
    interview_state["updatedByUserId"] = user.user_id
    interview_state["updatedAt"] = utc_now()
    store.upsert("interview_states", interview_state)


def _migrate_legacy_answer_states(interview_state: dict[str, Any]) -> bool:
    changed = False
    completed = interview_state.setdefault("completedFieldIds", [])
    pending = interview_state.setdefault("pendingFieldIds", [])
    for field_id, field_state in interview_state.setdefault("fieldStates", {}).items():
        if field_state.get("answerState"):
            continue
        legacy_answer = str(field_state.get("answerSummary") or "").strip()
        field_state["answerState"] = "CANDIDATE_PENDING" if legacy_answer else "UNANSWERED"
        field_state["candidateAnswer"] = legacy_answer or None
        field_state["answerSummary"] = None
        field_state["status"] = "asking" if legacy_answer else "pending"
        if field_id in completed:
            completed.remove(field_id)
        if field_id not in pending:
            pending.append(field_id)
        changed = True
    if changed:
        interview_state["status"] = "in_progress"
        interview_state["currentFieldId"] = next(iter(interview_state.get("fieldStates", {})), None)
        interview_state["currentQuestionId"] = None
    return changed


def _migrate_formal_record_answers(
    interview_state: dict[str, Any],
    messages: list[dict],
    user: UserContext,
) -> bool:
    """Backfill formal answers from actual user utterances, never from LLM summaries."""
    changed = False
    for field_id, field_state in interview_state.setdefault("fieldStates", {}).items():
        raw_history = [
            str(answer).strip()
            for answer in field_state.get("rawAnswerHistory") or []
            if str(answer).strip()
        ]
        if not raw_history:
            field_questions = [
                question
                for question in interview_state.get("askedQuestions", [])
                if question.get("fieldId") == field_id
            ]
            raw_history = [
                str(message.get("content") or "").strip()
                for message in messages
                if message.get("role") == "user"
                and message.get("isActualUtterance") is True
                and message.get("answerToFieldId") == field_id
                and _is_legacy_or_answer_turn(
                    message,
                    {
                        question.get("questionId") for question in field_questions
                    },
                    allow_unscoped_legacy=not field_questions,
                )
                and str(message.get("content") or "").strip()
            ]
            if raw_history:
                field_state["rawAnswerHistory"] = raw_history
                field_state["rawAnswer"] = raw_history[-1]
                changed = True

        if field_state.get("answerState") != "CONFIRMED":
            continue
        record_answer = str(field_state.get("recordAnswer") or "").strip()
        if not record_answer and raw_history:
            record_answer = compose_record_answer(raw_history)
            field_state["recordAnswer"] = record_answer
            changed = True
        if not record_answer:
            continue

        if field_state.get("answerSummary") is not None:
            field_state["answerSummary"] = None
            changed = True
        for message in messages:
            if (
                message.get("messageType") == "confirmed_answer"
                and message.get("answerToFieldId") == field_id
                and message.get("content") != record_answer
            ):
                message["content"] = record_answer
                message["updatedByUserId"] = user.user_id
                message["updatedAt"] = utc_now()
                store.upsert("messages", message)
                changed = True

    if changed:
        _persist_interview_state(interview_state, user)
    return changed


def _build_interview_state_id(record_id: str) -> str:
    return f"interview-state-{record_id}"


def _latest_user_message(
    messages: list[dict],
    current_question_id: str | None,
) -> dict[str, Any] | None:
    for message in reversed(messages):
        if (
            message.get("role") == "user"
            and _is_legacy_or_answer_turn(message, {current_question_id})
        ):
            return message
    return None


def _latest_user_turn(messages: list[dict]) -> dict[str, Any] | None:
    for message in reversed(messages):
        if message.get("role") == "user":
            return message
    return None


def _latest_assistant_message(messages: list[dict]) -> dict[str, Any] | None:
    for message in reversed(messages):
        if message.get("role") == "assistant":
            return message
    return None


def _acknowledge_control_turn(
    record: dict,
    interview_state: dict[str, Any],
    current_question: dict[str, Any],
    messages: list[dict],
    control_turn: dict[str, Any],
    user: UserContext,
    *,
    persist_assistant_message: bool,
) -> dict[str, Any]:
    """Keep control turns outside answer processing while preserving the UI flow."""
    interview_state["lastProcessedUserMessageId"] = control_turn.get("id")
    assistant_message = (
        _save_assistant_message(
            user,
            record["id"],
            str(current_question.get("text") or "").strip(),
            question=current_question,
        )
        if persist_assistant_message
        else None
    )
    _persist_interview_state(interview_state, user)
    all_messages = [*messages, assistant_message] if assistant_message else messages
    return _build_agent_result_payload(
        action="ask_configured_field",
        reply=str(current_question.get("text") or "").strip(),
        question=current_question,
        completed_field_id=None,
        current_field_id=current_question.get("fieldId"),
        answer_summary=None,
        missing_information=[],
        used_tools=[],
        interview_state=interview_state,
        assistant_message=assistant_message,
        structured_draft=_build_structured_draft(
            interview_state,
            _build_field_lookup(
                _list_interview_fields(
                    store.get("knowledges", record["knowledgeId"]) or {},
                    user,
                )
            ),
        ),
        messages=all_messages,
        retrieval_policy=str(current_question.get("retrievalPolicy") or RetrievalPolicy.AUTO.value),
        retrieval_executed=False,
    )


def _is_legacy_or_answer_turn(
    message: dict[str, Any],
    question_ids: set[Any],
    *,
    allow_unscoped_legacy: bool = False,
) -> bool:
    """Accept pre-turnType rows only at the scoped legacy boundary.

    Newly created interview turns always carry ANSWER/CONTROL. Existing rows
    predate that field, so an explicitly question-linked legacy row remains
    readable without reintroducing content-based filtering.
    """
    dialogue_act = message.get("dialogueAct")
    if dialogue_act and dialogue_act not in {"ANSWER", "CORRECTION", "REJECTION", "CONFIRMATION"}:
        return False
    if message.get("turnType") == "CONTROL":
        return False
    if message.get("turnType") == "ANSWER":
        return message.get("answerToQuestionId") in question_ids
    return (
        message.get("turnType") is None
        and (
            message.get("answerToQuestionId") in question_ids
            or (allow_unscoped_legacy and not question_ids)
        )
    )


def _get_current_question(interview_state: dict[str, Any]) -> dict[str, Any] | None:
    current_question_id = interview_state.get("currentQuestionId")
    if not current_question_id:
        return None
    for question in interview_state.get("askedQuestions", []):
        if question.get("questionId") == current_question_id:
            return question
    return None


def _ask_next_configured_field(
    record: dict,
    knowledge_fields: list[dict],
    interview_state: dict[str, Any],
    messages: list[dict],
    user: UserContext,
    *,
    persist_assistant_message: bool = True,
) -> dict[str, Any]:
    next_field = _resolve_current_field(knowledge_fields, interview_state)
    if next_field is None:
        interview_state["status"] = "completed"
        interview_state["currentFieldId"] = None
        interview_state["currentQuestionId"] = None
        interview_state["pendingFieldIds"] = []
        _persist_interview_state(interview_state, user)
        return _build_finish_result(record, interview_state, messages, _build_field_lookup(knowledge_fields))

    question = _build_configured_question(next_field, interview_state)
    reply_text = _compose_assistant_content("", question["text"])
    assistant_message = (
        _save_assistant_message(
            user,
            record["id"],
            reply_text,
            question=question,
        )
        if persist_assistant_message
        else None
    )
    interview_state["currentFieldId"] = next_field["id"]
    interview_state["currentQuestionId"] = question["questionId"]
    interview_state["status"] = "in_progress"
    interview_state.setdefault("askedQuestions", []).append(question)
    interview_state.setdefault("fieldStates", {}).setdefault(
        next_field["id"],
        {
            "fieldId": next_field["id"],
            "status": "pending",
            "answerSummary": None,
            "rawAnswer": None,
            "rawAnswerHistory": [],
            "recordAnswer": None,
            "capturedItems": [],
            "missingInformation": [],
            "answerState": "UNANSWERED",
            "candidateAnswer": None,
        },
    )
    interview_state["fieldStates"][next_field["id"]]["status"] = "asking"
    _persist_interview_state(interview_state, user)
    all_messages = [*messages, assistant_message] if assistant_message else messages
    return _build_agent_result_payload(
        action="ask_configured_field",
        reply=reply_text,
        question=question,
        completed_field_id=None,
        current_field_id=next_field["id"],
        answer_summary=interview_state["fieldStates"][next_field["id"]].get("answerSummary"),
        missing_information=[],
        used_tools=[],
        retrieval_policy=question["retrievalPolicy"],
        retrieval_executed=False,
        interview_state=interview_state,
        assistant_message=assistant_message,
        structured_draft=_build_structured_draft(interview_state, _build_field_lookup(knowledge_fields)),
        messages=all_messages,
    )


def _process_text_answer_turn(
    record: dict,
    knowledge: dict,
    knowledge_fields: list[dict],
    messages: list[dict],
    interview_state: dict[str, Any],
    current_question: dict[str, Any],
    latest_user_message: dict[str, Any],
    user: UserContext,
    *,
    persist_assistant_message: bool,
    retrieval_policy: str,
) -> dict[str, Any]:
    current_field_id = str(current_question.get("fieldId") or interview_state.get("currentFieldId") or "")
    if not current_field_id:
        raise ValueError("current interview field is missing")
    field_lookup = _build_field_lookup(knowledge_fields)
    current_field = field_lookup.get(current_field_id) or {"id": current_field_id, "name": current_field_id}
    field_state = interview_state.setdefault("fieldStates", {}).setdefault(current_field_id, {})
    interpretation = interpret_dialogue_act(
        transcript=str(latest_user_message.get("content") or ""),
        current_question=current_question,
        current_field=current_field,
        field_state=field_state,
        recent_messages=messages,
        last_assistant_message=_latest_assistant_message(messages),
    )
    latest_user_message["dialogueAct"] = interpretation.act
    store.upsert("messages", latest_user_message)
    if not should_route_to_answer_processor(
        interpretation,
        awaiting_confirmation=field_state.get("answerState") == "AWAITING_CONFIRMATION",
    ):
        return _build_dialogue_act_response_result(
            record=record,
            knowledge_fields=knowledge_fields,
            messages=messages,
            interview_state=interview_state,
            current_question=current_question,
            current_field_id=current_field_id,
            user_message=latest_user_message,
            interpretation=interpretation,
            user=user,
            persist_assistant_message=persist_assistant_message,
            retrieval_policy=retrieval_policy,
        )
    evaluation_retrieval_executed = False

    def evaluate_text_answer(**_: Any) -> AnswerEvaluation:
        nonlocal evaluation_retrieval_executed
        adapted_result = run_adapted_interview_turn(
            record,
            knowledge,
            messages,
            knowledge_fields,
            interview_state=interview_state,
            current_question=current_question,
        )
        evaluation_retrieval_executed = bool(adapted_result.used_tools)
        evaluation = adapted_result.field_evaluation
        normalized_answer = str(evaluation.get("answerSummary") or "").strip()
        record_answer = str(evaluation.get("recordAnswer") or "").strip()
        decision = evaluation.get("decision")
        if not decision:
            if bool(evaluation.get("isComplete")) and normalized_answer:
                decision = "CONFIRMABLE"
            elif normalized_answer:
                decision = "NEEDS_MORE_INFORMATION"
            else:
                decision = "NOT_ANSWER"
        return AnswerEvaluation(
            decision=decision,
            normalized_answer=normalized_answer,
            record_answer=record_answer,
            is_relevant=evaluation.get("isRelevant"),
            is_sufficient=bool(evaluation.get("isSufficient", evaluation.get("isComplete", False))),
            missing_information=list(evaluation.get("missingInformation") or []),
            follow_up_question=adapted_result.follow_up_question,
            confirmation_question=evaluation.get("confirmationQuestion"),
            target_field_id=evaluation.get("targetFieldId"),
            retrieval_needed=bool(evaluation.get("retrievalNeeded", False)),
            evaluation_reason=evaluation.get("evaluationReason"),
            evidence_transcript_ids=[latest_user_message["id"]],
            captured_items=list(evaluation.get("capturedItems") or []),
            answer_disposition=evaluation.get("answerDisposition"),
            evaluation_status=evaluation.get("evaluationStatus", "OK"),
        )

    def evaluate_text_confirmation(**_: Any) -> ConfirmationEvaluation:
        adapted_result = run_adapted_interview_turn(
            record,
            knowledge,
            messages,
            knowledge_fields,
            interview_state=interview_state,
            current_question=current_question,
        )
        evaluation = adapted_result.field_evaluation
        outcome = evaluation.get("confirmationOutcome")
        allowed_outcomes = {
            "CONFIRM",
            "REVISE_WITH_CONTENT",
            "REJECT_WITHOUT_CONTENT",
            "UNCLEAR",
        }
        if outcome not in allowed_outcomes:
            return ConfirmationEvaluation(
                outcome="UNCLEAR",
                clarification_question="内容を確定してよいか判断できませんでした。正しければ、確認するか正しい内容を教えてください。",
            )
        record_answer = str(evaluation.get("recordAnswer") or "").strip() or None
        return ConfirmationEvaluation(
            outcome=outcome,
            record_answer=record_answer,
            revised_answer=record_answer if outcome == "REVISE_WITH_CONTENT" else None,
            clarification_question=adapted_result.follow_up_question
            or evaluation.get("confirmationQuestion"),
            captured_items=list(evaluation.get("capturedItems") or []),
        )

    turn_result = InterviewAnswerProcessor(
        evaluator=evaluate_text_answer,
        confirmation_evaluator=evaluate_text_confirmation,
    ).process_turn_sync(
        record_id=record["id"],
        question_id=str(current_question.get("questionId") or ""),
        field_id=current_field_id,
        transcript=str(latest_user_message.get("content") or ""),
        current_state=interview_state,
        question=current_question,
        field=current_field,
        evidence_transcript_id=latest_user_message["id"],
        retrieval_policy=retrieval_policy,
    )
    interview_state["lastProcessedUserMessageId"] = latest_user_message["id"]

    if turn_result.action == "confirmed":
        confirmed_field_id = turn_result.confirmed_field_id or current_field_id
        _save_confirmed_answer_message(
            record_id=record["id"],
            question_id=turn_result.question_id,
            field_id=confirmed_field_id,
            content=str(
                interview_state["fieldStates"][confirmed_field_id].get("recordAnswer")
                or compose_record_answer(
                    list(interview_state["fieldStates"][confirmed_field_id].get("rawAnswerHistory") or [])
                )
                or ""
            ),
            user=user,
        )
        interview_state["currentFieldId"] = None
        interview_state["currentQuestionId"] = None
        _persist_interview_state(interview_state, user)
        return _ask_next_configured_field(
            record,
            knowledge_fields,
            interview_state,
            messages,
            user,
            persist_assistant_message=persist_assistant_message,
        )

    follow_up_question = _build_follow_up_question(
        turn_result.field_id,
        turn_result.reply_text,
        question_plan=current_field.get("questionPlan"),
        retrieval_policy=turn_result.retrieval_policy,
    )
    assistant_message = (
        _save_assistant_message(user, record["id"], turn_result.reply_text, question=follow_up_question)
        if persist_assistant_message
        else None
    )
    interview_state["currentFieldId"] = turn_result.field_id
    interview_state["currentQuestionId"] = follow_up_question["questionId"]
    interview_state.setdefault("askedQuestions", []).append(follow_up_question)
    field_state = interview_state["fieldStates"][turn_result.field_id]
    if field_state.get("answerState") == "AWAITING_CONFIRMATION":
        field_state["pendingQuestionId"] = follow_up_question["questionId"]
        field_state["pendingFieldId"] = turn_result.field_id
    elif turn_result.decision in {"NEEDS_MORE_INFORMATION", "NEEDS_FOLLOWUP"}:
        counts = interview_state.setdefault("followUpCounts", {})
        counts[turn_result.field_id] = int(counts.get(turn_result.field_id, 0)) + 1
    _persist_interview_state(interview_state, user)
    all_messages = [*messages, assistant_message] if assistant_message else messages
    payload = _build_agent_result_payload(
        action="ask_follow_up",
        reply=turn_result.reply_text,
        question=follow_up_question,
        completed_field_id=None,
        current_field_id=turn_result.field_id,
        answer_summary=None,
        missing_information=list(field_state.get("missingInformation") or []),
        used_tools=[],
        retrieval_policy=turn_result.retrieval_policy,
        retrieval_executed=turn_result.retrieval_executed or evaluation_retrieval_executed,
        interview_state=interview_state,
        assistant_message=assistant_message,
        structured_draft=_build_structured_draft(interview_state, field_lookup),
        messages=all_messages,
    )
    payload["decision"] = turn_result.decision
    payload["completionStatus"] = turn_result.completion_status
    payload["missingRequiredItemIds"] = list(turn_result.missing_required_item_ids)
    payload["answerDisposition"] = turn_result.answer_disposition
    return payload


def _build_dialogue_act_response_result(
    *,
    record: dict,
    knowledge_fields: list[dict],
    messages: list[dict],
    interview_state: dict[str, Any],
    current_question: dict[str, Any],
    current_field_id: str,
    user_message: dict[str, Any],
    interpretation: DialogueInterpretation,
    user: UserContext,
    persist_assistant_message: bool,
    retrieval_policy: str,
) -> dict[str, Any]:
    field_lookup = _build_field_lookup(knowledge_fields)
    reply_text = _dialogue_response_text(interpretation, current_question)
    interview_state["lastProcessedUserMessageId"] = user_message["id"]
    field_state = interview_state.setdefault("fieldStates", {}).setdefault(current_field_id, {})
    field_state["lastDialogueAct"] = interpretation.act
    field_state["lastDialogueResponse"] = reply_text
    _persist_interview_state(interview_state, user)
    assistant_message = (
        _save_assistant_message(user, record["id"], reply_text, question=current_question)
        if persist_assistant_message
        else None
    )
    all_messages = [*messages, assistant_message] if assistant_message else messages
    payload = _build_agent_result_payload(
        action="ask_follow_up",
        reply=reply_text,
        question=current_question,
        completed_field_id=None,
        current_field_id=current_field_id,
        answer_summary=None,
        missing_information=list(field_state.get("missingInformation") or []),
        used_tools=[],
        retrieval_policy=retrieval_policy,
        retrieval_executed=False,
        interview_state=interview_state,
        assistant_message=assistant_message,
        structured_draft=_build_structured_draft(interview_state, field_lookup),
        messages=all_messages,
    )
    payload["dialogueAct"] = interpretation.act
    payload["decision"] = "DIALOGUE_ACT"
    return payload


def _build_finish_result(
    record: dict,
    interview_state: dict[str, Any],
    messages: list[dict],
    field_lookup: dict[str, dict[str, Any]],
    *,
    completed_field_id: str | None = None,
    answer_summary: str | None = None,
    missing_information: list[str] | None = None,
    used_tools: list[str] | None = None,
) -> dict[str, Any]:
    return _build_agent_result_payload(
        action="finish",
        reply="以上で、設定されているすべての質問項目へのインタビューが完了しました。ご協力ありがとうございました。",
        question=None,
        completed_field_id=completed_field_id,
        current_field_id=None,
        answer_summary=answer_summary,
        missing_information=missing_information or [],
        used_tools=used_tools or [],
        interview_state=interview_state,
        assistant_message=None,
        structured_draft=_build_structured_draft(interview_state, field_lookup),
        messages=messages,
        status="completed",
    )


def _build_agent_result_payload(
    *,
    action: str,
    reply: str,
    question: dict[str, Any] | None,
    completed_field_id: str | None,
    current_field_id: str | None,
    answer_summary: str | None,
    missing_information: list[str],
    used_tools: list[str],
    interview_state: dict[str, Any],
    assistant_message: dict[str, Any] | None,
    structured_draft: dict[str, str],
    messages: list[dict],
    retrieval_policy: str = "auto",
    retrieval_executed: bool = False,
    status: str | None = None,
) -> dict[str, Any]:
    effective_status = status or interview_state.get("status") or "in_progress"
    result_field_id = completed_field_id or current_field_id
    result_record_answer = (
        str(
            interview_state.get("fieldStates", {})
            .get(result_field_id, {})
            .get("recordAnswer")
            or ""
        ).strip()
        if result_field_id
        else None
    ) or None
    agent_result = InterviewAgentResult(
        status="completed" if effective_status == "completed" else "in_progress",
        action=action,
        reply=reply,
        question=InterviewQuestion.model_validate(question) if question else None,
        completedFieldId=completed_field_id,
        currentFieldId=current_field_id,
        answerSummary=answer_summary,
        recordAnswer=result_record_answer,
        missingInformation=missing_information,
        used_tools=used_tools,
    )
    payload = agent_result.model_dump()
    payload["assistantMessage"] = assistant_message
    payload["retrievalPolicy"] = retrieval_policy
    payload["retrievalExecuted"] = retrieval_executed
    payload["interviewState"] = interview_state
    payload["structuredDraft"] = structured_draft
    payload["messages"] = messages
    return payload


def _resolve_current_field(knowledge_fields: list[dict], interview_state: dict[str, Any]) -> dict[str, Any] | None:
    current_field_id = interview_state.get("currentFieldId")
    if current_field_id:
        for field in knowledge_fields:
            if field.get("id") == current_field_id:
                return field
    return _resolve_next_pending_field(knowledge_fields, interview_state)


def _resolve_next_pending_field(knowledge_fields: list[dict], interview_state: dict[str, Any]) -> dict[str, Any] | None:
    pending_field_ids = list(interview_state.get("pendingFieldIds", []))
    for field in knowledge_fields:
        if field.get("id") in pending_field_ids:
            return field
    return None


def _build_configured_question(field: dict[str, Any], interview_state: dict[str, Any]) -> dict[str, Any]:
    examples = [
        str(example).strip()
        for example in field.get("aiQuestionExamples") or []
        if str(example).strip()
    ]
    text = examples[0] if examples else _fallback_field_question(str(field.get("name") or ""))
    return {
        "questionId": _next_question_id(interview_state),
        "questionType": "configured_field",
        "fieldId": field.get("id"),
        "text": text,
        "retrievalPolicy": _field_retrieval_policy(field).value,
        "questionPlan": field.get("questionPlan"),
    }


def _build_follow_up_question(
    field_id: str | None,
    text: str,
    *,
    question_plan: dict[str, Any] | None = None,
    retrieval_policy: str = RetrievalPolicy.AUTO.value,
) -> dict[str, Any]:
    return {
        "questionId": f"q-{uuid4().hex[:12]}",
        "questionType": "follow_up",
        "fieldId": field_id,
        "text": text.strip(),
        "retrievalPolicy": retrieval_policy,
        "questionPlan": question_plan,
    }


def _resolve_retrieval_policy(
    current_question: dict[str, Any],
    knowledge_fields: list[dict],
) -> RetrievalPolicy:
    explicit = _parse_retrieval_policy(current_question.get("retrievalPolicy"))
    if explicit != RetrievalPolicy.AUTO:
        return explicit
    field_id = current_question.get("fieldId")
    if field_id:
        for field in knowledge_fields:
            if field.get("id") == field_id:
                return _field_retrieval_policy(field)
    return RetrievalPolicy.AUTO


def _field_retrieval_policy(field: dict[str, Any]) -> RetrievalPolicy:
    explicit = _parse_retrieval_policy(field.get("retrievalPolicy"))
    if explicit != RetrievalPolicy.AUTO:
        return explicit
    input_type = str(field.get("inputType") or "").strip()
    if input_type in DIRECT_CAPTURE_TYPES or input_type in {"short_text", "number", "date"}:
        return RetrievalPolicy.NEVER
    haystack = " ".join(
        str(value or "")
        for value in [
            field.get("name"),
            field.get("description"),
            " ".join(str(item) for item in field.get("aiQuestionExamples") or []),
        ]
    )
    if any(keyword in haystack for keyword in DIRECT_CAPTURE_KEYWORDS):
        return RetrievalPolicy.NEVER
    return RetrievalPolicy.AUTO


def _parse_retrieval_policy(value: Any) -> RetrievalPolicy:
    try:
        return RetrievalPolicy(str(value or RetrievalPolicy.AUTO.value).strip())
    except ValueError:
        return RetrievalPolicy.AUTO


def _next_question_id(interview_state: dict[str, Any]) -> str:
    return f"q-{len(interview_state.get('askedQuestions', [])) + 1:03d}"


def _fallback_field_question(field_name: str) -> str:
    text = field_name.strip()
    if not text:
        return "この項目について教えてください。"
    if text.endswith(("か", "か？", "か。", "ください。", "ください")):
        return text
    return f"{text}について教えてください。"


def _compose_assistant_content(reply: str, question_text: str | None) -> str:
    parts = [part.strip() for part in [reply, question_text] if isinstance(part, str) and part.strip()]
    if not parts:
        return _SAFE_INTERVIEW_ERROR_REPLY
    if len(parts) == 1:
        return parts[0]
    return "".join(parts)


def _dialogue_response_text(
    interpretation: DialogueInterpretation,
    current_question: dict[str, Any] | None,
) -> str:
    if interpretation.response_text:
        return interpretation.response_text
    question_text = str((current_question or {}).get("text") or "").strip()
    if interpretation.act in {"BACKCHANNEL", "HESITATION"}:
        return "少し考えてからで大丈夫です。"
    if interpretation.act == "CLARIFICATION_REQUEST" and question_text:
        return f"この質問は、{question_text}という内容について伺っています。分かる範囲で教えてください。"
    if interpretation.act == "QUESTION_TO_ASSISTANT":
        return "直前に確認した内容についての質問ですね。もう少し具体的に聞きたい点を教えてください。"
    if interpretation.act == "CONVERSATION_REQUEST":
        return "少し補足しながら進めます。いまの質問について、分かる範囲で教えてください。"
    if interpretation.act in {"IRRELEVANT", "OTHER"} and question_text:
        return f"ありがとうございます。インタビューでは、いまは「{question_text}」について伺っています。"
    return "ありがとうございます。いまの質問について、分かる範囲で教えてください。"


def _save_assistant_message(
    user: UserContext,
    record_id: str,
    content: str,
    *,
    question: dict[str, Any] | None,
) -> dict[str, Any]:
    message = {
        "id": f"msg-{uuid4().hex[:12]}",
        "tenantId": user.tenant_id,
        "recordId": record_id,
        "content": content,
        "role": "assistant",
        "isActualUtterance": True,
        "createdAt": utc_now(),
        "updatedAt": utc_now(),
        "questionId": question.get("questionId") if question else None,
        "questionType": question.get("questionType") if question else None,
        "fieldId": question.get("fieldId") if question else None,
    }
    store.upsert("messages", message)
    return message


def _save_confirmed_answer_message(
    *,
    record_id: str,
    question_id: str,
    field_id: str,
    content: str,
    user: UserContext,
) -> dict[str, Any]:
    message = {
        "id": f"msg-{uuid4().hex[:12]}",
        "tenantId": user.tenant_id,
        "recordId": record_id,
        "content": content,
        "role": "user",
        "isActualUtterance": False,
        "messageType": "confirmed_answer",
        "turnType": "ANSWER",
        "createdAt": utc_now(),
        "updatedAt": utc_now(),
        "answerToQuestionId": question_id,
        "answerToFieldId": field_id,
    }
    store.upsert("messages", message)
    return message


def _persist_interview_state(interview_state: dict[str, Any], user: UserContext) -> None:
    interview_state["updatedByUserId"] = user.user_id
    interview_state["updatedAt"] = utc_now()
    store.upsert("interview_states", interview_state)


def _build_structured_draft(interview_state: dict[str, Any], field_lookup: dict[str, dict[str, Any]]) -> dict[str, str]:
    draft: dict[str, str] = {}
    for field_id, field_state in interview_state.get("fieldStates", {}).items():
        record_answer = str(field_state.get("recordAnswer") or "").strip()
        if not record_answer:
            continue
        field_name = str(field_lookup.get(field_id, {}).get("name") or field_id).strip()
        if field_name:
            draft[field_name] = record_answer
    return draft


def _build_field_lookup(knowledge_fields: list[dict]) -> dict[str, dict[str, Any]]:
    return {
        field["id"]: field
        for field in knowledge_fields
        if field.get("id")
    }


def _split_reply_chunks(reply_text: str) -> list[str]:
    if not reply_text.strip():
        return []
    lines = [line.strip() for line in reply_text.replace("\r\n", "\n").split("\n") if line.strip()]
    return lines or [reply_text.strip()]


def get_interview_state_snapshot(record: dict, user: UserContext) -> dict[str, Any]:
    knowledge = store.get("knowledges", record["knowledgeId"])
    knowledge_fields = _list_interview_fields(knowledge, user)
    interview_state = _load_or_initialize_interview_state(record, knowledge_fields, user)
    messages = _list_record_messages(record, user)
    _migrate_formal_record_answers(interview_state, messages, user)
    return {
        "status": interview_state.get("status", "in_progress"),
        "interviewState": interview_state,
        "messages": messages,
        "structuredDraft": _build_structured_draft(interview_state, _build_field_lookup(knowledge_fields)),
    }


def run_adapted_interview_turn(
    record: dict,
    knowledge: dict,
    messages: list[dict],
    knowledge_fields: list[dict],
    interview_state: dict[str, Any] | None = None,
    current_question: dict[str, Any] | None = None,
    **kwargs: Any,
):
    return _run_adapted_interview_turn(
        record,
        knowledge,
        messages,
        knowledge_fields,
        interview_state=interview_state,
        current_question=current_question,
        **kwargs,
    )
