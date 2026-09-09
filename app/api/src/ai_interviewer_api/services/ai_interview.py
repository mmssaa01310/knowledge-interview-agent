"""Structured Interview facade used by the text and voice API boundaries.

The interview domain has one supported execution path. This module keeps the
existing router-facing function names stable while delegating all semantic
interpretation, coordination, question generation, and persistence to the
Structured Interview service.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ai_interviewer_api.agents.interview_knowledge.service import (
    generate_structured_interview_result,
    get_structured_interview_state_snapshot,
)
from ai_interviewer_api.agents.interview_knowledge.coordinator import (
    is_current_question_confirmation_target,
)
from ai_interviewer_api.auth.deps import UserContext
from ai_interviewer_api.core.interview_locale import (
    localized_interview_fallbacks,
    resolve_interview_locale,
)
from ai_interviewer_api.models.domain import AiProposal
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.services.record_lifecycle import sync_record_status_after_interview
from ai_interviewer_api.services.conversation_policy import (
    resolve_canonical_action,
    resolve_canonical_intent,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InterviewStreamResult:
    reply_chunks: list[str]
    metadata: dict[str, Any] | None = None


def build_mock_proposal(
    user: UserContext,
    record_id: str,
    knowledge_id: str,
    content: str,
) -> AiProposal:
    """Build the existing local proposal used by the record API demo path."""

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


def generate_interview_reply(
    record: dict,
    user: UserContext,
    *,
    persist_assistant_messages: bool = True,
) -> InterviewStreamResult:
    """Generate one reply through the canonical Structured Interview path."""

    knowledge = store.get("knowledges", record["knowledgeId"]) or {}
    try:
        canonical_policy = _resolve_text_turn_policy(record, knowledge, user)
        policy_kwargs = (
            {
                "canonical_intent": canonical_policy[0],
                "canonical_action": canonical_policy[1],
            }
            if canonical_policy is not None
            else {}
        )
        result = generate_structured_interview_result(
            record,
            knowledge,
            user,
            persist_assistant_messages=persist_assistant_messages,
            **policy_kwargs,
        )
        sync_record_status_after_interview(record, result.get("status"), user)
        return InterviewStreamResult(
            reply_chunks=_split_reply_chunks(str(result.get("reply") or "")),
            metadata=result,
        )
    except Exception:
        logger.exception(
            "Structured Interview failed record_id=%s knowledge_id=%s",
            record.get("id"),
            record.get("knowledgeId"),
        )
        locale = resolve_interview_locale(record, knowledge)
        return InterviewStreamResult(
            reply_chunks=[localized_interview_fallbacks(locale)["error"]],
            metadata={"error": "structured_interview_failed"},
        )


def _resolve_text_turn_policy(
    record: dict,
    knowledge: dict,
    user: UserContext,
) -> tuple[str, str] | None:
    """Route text turns through the same canonical policy as voice turns."""

    snapshot = get_structured_interview_state_snapshot(
        record,
        knowledge,
        user,
        persist=False,
    )
    state = snapshot.get("interviewState") or {}
    messages = snapshot.get("messages") or []
    current_question_id = state.get("currentQuestionId")
    current_question = next(
        (
            question
            for question in state.get("askedQuestions", [])
            if question.get("questionId") == current_question_id
        ),
        None,
    )
    if not isinstance(current_question, dict):
        return None
    latest_user_message = next(
        (
            message
            for message in reversed(messages)
            if message.get("role") == "user"
            and message.get("isActualUtterance") is not False
            and message.get("answerToQuestionId") == current_question_id
            and message.get("id") != state.get("lastProcessedUserMessageId")
        ),
        None,
    )
    if not isinstance(latest_user_message, dict):
        return None
    current_target = {
        "targetType": current_question.get("targetType"),
        "targetId": current_question.get("targetId"),
        "label": current_question.get("targetLabel") or current_question.get("label"),
    }
    decision = resolve_canonical_intent(
        utterance=str(
            latest_user_message.get("rawTranscript")
            or latest_user_message.get("content")
            or ""
        ),
        current_question=current_question,
        current_target=current_target,
        pending_confirmation=is_current_question_confirmation_target(
            state,
            current_question,
        ),
        recent_conversation=messages,
    )
    action = resolve_canonical_action(
        decision.dialogueAct,
        pending_confirmation=is_current_question_confirmation_target(
            state,
            current_question,
        ),
        current_target=current_target,
    )
    return decision.dialogueAct, action


def get_interview_state_snapshot(
    record: dict,
    user: UserContext,
    *,
    persist: bool = True,
) -> dict[str, Any]:
    """Return the canonical Structured Interview state snapshot."""

    knowledge = store.get("knowledges", record["knowledgeId"]) or {}
    return get_structured_interview_state_snapshot(record, knowledge, user, persist=persist)


def _split_reply_chunks(reply_text: str) -> list[str]:
    if not reply_text.strip():
        return []
    lines = [
        line.strip()
        for line in reply_text.replace("\r\n", "\n").split("\n")
        if line.strip()
    ]
    return lines or [reply_text.strip()]
