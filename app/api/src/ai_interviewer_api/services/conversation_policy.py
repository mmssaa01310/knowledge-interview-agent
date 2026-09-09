"""Canonical conversation intent and action policy.

This module is deliberately small and side-effect free. Providers submit a
user utterance here, and the returned StructuredDialogueAct is the single
conversation-intent contract used by the interview policy. State mutation,
retrieval, and question generation remain in their existing services.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from ai_interviewer_api.agents.interview_knowledge.provider import (
    BedrockResponsesStructuredProvider,
    StructuredInterviewProviderError,
)
from ai_interviewer_api.agents.interview_knowledge.schemas import StructuredDialogueAct
from ai_interviewer_api.core.config import settings


logger = logging.getLogger(__name__)

CanonicalAction = Literal[
    "PROCESS_ANSWER",
    "CONFIRM_PENDING_CANDIDATE",
    "REJECT_PENDING_CANDIDATE",
    "EXPLAIN_CURRENT_QUESTION",
    "KEEP_CURRENT_QUESTION",
    "APPLY_CORRECTION",
    "HANDLE_CONTROL",
]


class CanonicalIntentOutput(BaseModel):
    """The minimal structured output accepted from the intent resolver."""

    model_config = ConfigDict(extra="forbid")

    dialogueAct: StructuredDialogueAct


class CanonicalIntentProvider(Protocol):
    def classify(self, *, context: Mapping[str, Any]) -> CanonicalIntentOutput: ...


class BedrockCanonicalIntentProvider:
    """Bedrock adapter for the foreground, intent-only classification call."""

    def __init__(
        self,
        *,
        provider: BedrockResponsesStructuredProvider | None = None,
    ) -> None:
        self._provider = provider or BedrockResponsesStructuredProvider(
            model_id=settings.structured_interview_fast_model_id,
        )

    def classify(self, *, context: Mapping[str, Any]) -> CanonicalIntentOutput:
        payload = self._provider.request_structured_output(
            schema_name="canonical_conversation_intent",
            schema=CanonicalIntentOutput.model_json_schema(),
            system_prompt=_canonical_intent_system_prompt(),
            user_payload=context,
            reasoning_effort=settings.structured_interview_fast_reasoning_effort,
            max_output_tokens=settings.structured_interview_fast_max_output_tokens,
        )
        return CanonicalIntentOutput.model_validate(payload)


@dataclass(frozen=True)
class CanonicalIntentDecision:
    """Internal decision with timing metadata kept outside the model schema."""

    dialogueAct: StructuredDialogueAct
    latency_ms: float
    provider: str

    def model_dump(self) -> dict[str, Any]:
        return {
            "dialogueAct": self.dialogueAct,
            "latencyMs": self.latency_ms,
            "provider": self.provider,
        }


def build_canonical_intent_context(
    *,
    utterance: str,
    current_question: Mapping[str, Any] | None,
    current_target: Mapping[str, Any] | None,
    pending_confirmation: bool,
    recent_conversation: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build the intentionally narrow, provider-independent router input."""

    question = current_question or {}
    target = current_target or {}
    recent: list[dict[str, str]] = []
    for message in recent_conversation[-4:]:
        content = str(
            message.get("content")
            or message.get("rawTranscript")
            or message.get("transcript")
            or ""
        ).strip()
        if content:
            recent.append(
                {
                    "role": str(message.get("role") or "unknown"),
                    "content": content,
                }
            )

    return {
        "latestUserUtterance": str(utterance).strip(),
        "currentQuestion": {
            "questionId": question.get("questionId"),
            "text": str(question.get("text") or question.get("questionText") or "").strip(),
            "targetType": question.get("targetType"),
            "targetId": question.get("targetId"),
            "targetLabel": question.get("targetLabel") or question.get("label"),
        },
        "currentTarget": {
            "targetType": target.get("targetType"),
            "targetId": target.get("targetId"),
            "label": target.get("label") or target.get("targetLabel"),
        },
        "pendingConfirmation": bool(pending_confirmation),
        "recentConversation": recent,
    }


def resolve_canonical_intent(
    *,
    utterance: str,
    current_question: Mapping[str, Any] | None,
    current_target: Mapping[str, Any] | None,
    pending_confirmation: bool,
    recent_conversation: Sequence[Mapping[str, Any]] = (),
    provider: CanonicalIntentProvider | None = None,
) -> CanonicalIntentDecision:
    """Resolve one user utterance without changing interview state."""

    context = build_canonical_intent_context(
        utterance=utterance,
        current_question=current_question,
        current_target=current_target,
        pending_confirmation=pending_confirmation,
        recent_conversation=recent_conversation,
    )
    started_at = monotonic()
    provider_name = provider.__class__.__name__ if provider is not None else "bedrock"
    logger.info(
        "conversation_router_start utterance_chars=%s question_id=%s target_id=%s "
        "pending_confirmation=%s provider=%s",
        len(str(utterance).strip()),
        context["currentQuestion"].get("questionId"),
        context["currentTarget"].get("targetId"),
        context["pendingConfirmation"],
        provider_name,
    )
    try:
        selected_provider = provider or BedrockCanonicalIntentProvider()
        output = selected_provider.classify(context=context)
        dialogue_act = output.dialogueAct
    except (StructuredInterviewProviderError, ValueError, TypeError) as exc:
        # A resolver failure must not mutate state or accidentally enter the
        # answer path. OTHER maps to the safe keep-current-question action.
        logger.exception(
            "conversation_router_failed provider=%s error_type=%s",
            provider_name,
            exc.__class__.__name__,
        )
        dialogue_act = "OTHER"
    elapsed_ms = round((monotonic() - started_at) * 1000, 1)
    logger.info(
        "conversation_router_end dialogue_act=%s conversation_router_ms=%s",
        dialogue_act,
        elapsed_ms,
    )
    return CanonicalIntentDecision(
        dialogueAct=dialogue_act,
        latency_ms=elapsed_ms,
        provider=provider_name,
    )


def resolve_canonical_action(
    dialogue_act: StructuredDialogueAct,
    *,
    pending_confirmation: bool,
    current_target: Mapping[str, Any] | None = None,
    turn_type: str | None = None,
) -> CanonicalAction:
    """Map one canonical intent to one existing action family.

    This function intentionally has no store access and performs no mutation.
    turn_type is only an operational boundary for an explicit control turn; it
    is not a second semantic classifier.
    """

    del current_target  # Reserved for future policy guards without side effects.
    if turn_type == "CONTROL":
        return "HANDLE_CONTROL"
    if dialogue_act == "ANSWER":
        return "PROCESS_ANSWER"
    if dialogue_act == "CONFIRMATION":
        return "CONFIRM_PENDING_CANDIDATE" if pending_confirmation else "KEEP_CURRENT_QUESTION"
    if dialogue_act == "REJECTION":
        return "REJECT_PENDING_CANDIDATE" if pending_confirmation else "KEEP_CURRENT_QUESTION"
    if dialogue_act in {"CLARIFICATION_REQUEST", "QUESTION_TO_ASSISTANT"}:
        return "EXPLAIN_CURRENT_QUESTION"
    if dialogue_act == "CORRECTION":
        return "APPLY_CORRECTION"
    if dialogue_act == "CONVERSATION_REQUEST":
        return "HANDLE_CONTROL"
    return "KEEP_CURRENT_QUESTION"


def _canonical_intent_system_prompt() -> str:
    return (
        "あなたはKIKIORIの会話意図ルーターです。\n\n"
        "最新のユーザー発話が、現在の質問に対して何をしているかだけを分類してください。\n"
        "回答内容の抽出、回答十分性の判定、状態更新、次の質問の決定はしません。\n\n"
        "判定順序:\n"
        "1. まず発話の目的を、現在の質問・pendingConfirmation・直前のassistant発話を含む文脈で判定します。\n"
        "2. 現在の質問の意味、対象範囲、答え方を尋ねている場合は、回答内容が含まれていてもCLARIFICATION_REQUESTを優先します。\n"
        "3. 提示された候補や理解への肯定・否定は、pendingConfirmation=trueの場合だけCONFIRMATION/REJECTIONです。\n"
        "4. 現在の質問への事実・経験・判断・追加情報を述べている場合だけANSWERです。短い相づちや制御発話をANSWERにしません。\n\n"
        "分類候補:\n"
        "- ANSWER: 現在の質問に対応する事実、経験、判断、追加情報を述べる発話\n"
        "- QUESTION_TO_ASSISTANT: 現在の質問の定義ではなく、AI・インタビューの目的・進行・表示についてAIへ尋ねる発話\n"
        "- CLARIFICATION_REQUEST: 現在の質問の意味、対象範囲、必要な回答内容、答え方を明確にしてほしい発話\n"
        "- CONFIRMATION: 提示された候補や理解への肯定\n"
        "- REJECTION: 提示された候補や理解への否定\n"
        "- CORRECTION: 直前の発言や理解の訂正\n"
        "- HESITATION: 考え中、言いよどみ、短い沈黙を言語化した発話\n"
        "- BACKCHANNEL: 相づちなど、回答や状態変更を伴わない発話\n"
        "- CONVERSATION_REQUEST: 一時停止、終了、再開、最初からやり直す、速度変更など、セッション操作を依頼する発話。挨拶や呼びかけも、回答ではなく会話を開始・再開する目的ならここです\n"
        "- IRRELEVANT: セッション操作でもなく、現在の質問にも答えていない無関係な話題\n"
        "- OTHER: 上記に明確に該当しない発話\n\n"
        "境界例:\n"
        "- 現在の質問が「担当領域を教えてください。」のとき、「担当領域ってどの範囲？」「何を答えればいい？」はCLARIFICATION_REQUESTです。\n"
        "- 「今、何を知りたいの？」「なぜそれを聞くの？」「質問文が表示されていない」はQUESTION_TO_ASSISTANTです。\n"
        "- pendingConfirmation=trueで「大丈夫です」「はい、合っています」はCONFIRMATION、「違います」はREJECTIONです。\n"
        "- pendingConfirmation=falseの単独の「はい」は、直前assistantの説明への相づちならBACKCHANNEL、現在質問への返答ならANSWERです。\n"
        "- 「おーい」「こんにちは」は回答ではなく呼びかけ・会話開始なのでCONVERSATION_REQUESTです。\n"
        "- 「えーっと」「少し考えます」はHESITATIONです。\n"
        "CLARIFICATION_REQUESTとQUESTION_TO_ASSISTANTが迷わしい場合、現在の質問の意味・範囲・答え方を聞いているならCLARIFICATION_REQUEST、AIやインタビューの進行・目的・表示を聞いているならQUESTION_TO_ASSISTANTです。\n"
        "迷う場合は発話の目的を優先し、内容の十分性を推測してANSWERにしないでください。\n"
        "指定されたJSON Schemaだけを返してください。"
    ).strip()
