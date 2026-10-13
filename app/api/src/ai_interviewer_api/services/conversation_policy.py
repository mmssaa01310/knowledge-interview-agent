"""Canonical conversation intent and action policy.

This module is deliberately small and side-effect free. Providers submit a
user utterance plus the active conversation state here, and the returned
semantic user intent/conversation action is the shared contract used by the
interview policy. State mutation, retrieval, and question generation remain
in their existing services.
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
from ai_interviewer_api.agents.interview_knowledge.schemas import (
    ConversationAction,
    InterpretationMatch,
    StructuredDialogueAct,
    UserIntent,
)
from ai_interviewer_api.core.config import settings


logger = logging.getLogger(__name__)

CanonicalAction = Literal[
    "PROCESS_ANSWER",
    "CONFIRM_PENDING_CANDIDATE",
    "REJECT_PENDING_CANDIDATE",
    "EXPLAIN_CURRENT_QUESTION",
    "REPEAT_CURRENT_QUESTION",
    "KEEP_CURRENT_QUESTION",
    "APPLY_CORRECTION",
    "HANDLE_CONTROL",
]


class CanonicalIntentOutput(BaseModel):
    """Structured semantic intent output accepted from the intent resolver."""

    model_config = ConfigDict(extra="forbid")

    dialogueAct: StructuredDialogueAct
    userIntent: UserIntent = "USER_RESPONSE"
    interpretationMatch: InterpretationMatch = "NONE"
    userInterpretation: str | None = None
    originalQuestionIntent: str | None = None
    conversationAction: ConversationAction | None = None
    resumeCurrentQuestion: bool = True


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
    user_intent: UserIntent = "USER_RESPONSE"
    interpretation_match: InterpretationMatch = "NONE"
    user_interpretation: str | None = None
    original_question_intent: str | None = None
    conversation_action: ConversationAction = "PROCESS_USER_RESPONSE"
    resume_current_question: bool = True

    def model_dump(self) -> dict[str, Any]:
        return {
            "dialogueAct": self.dialogueAct,
            "latencyMs": self.latency_ms,
            "provider": self.provider,
            "userIntent": self.user_intent,
            "interpretationMatch": self.interpretation_match,
            "userInterpretation": self.user_interpretation,
            "originalQuestionIntent": self.original_question_intent,
            "conversationAction": self.conversation_action,
            "resumeCurrentQuestion": self.resume_current_question,
        }


def build_canonical_intent_context(
    *,
    utterance: str,
    current_question: Mapping[str, Any] | None,
    current_target: Mapping[str, Any] | None,
    pending_confirmation: bool,
    recent_conversation: Sequence[Mapping[str, Any]] = (),
    current_turn_type: str | None = None,
    previous_assistant_message: Mapping[str, Any] | None = None,
    previous_user_answer: Mapping[str, Any] | None = None,
    clarification_target: Mapping[str, Any] | None = None,
    probe_target: Mapping[str, Any] | None = None,
    known_facts: Sequence[Mapping[str, Any]] = (),
    covered_information: Sequence[Mapping[str, Any]] = (),
    missing_information: Sequence[Mapping[str, Any] | str] = (),
    current_question_slots: Sequence[Mapping[str, Any]] = (),
    interview_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the provider-independent semantic router input.

    Short utterances are interpreted against the active conversation state,
    not against their surface wording alone.
    """

    question = current_question or {}
    target = current_target or {}
    is_candidate_confirmation = pending_confirmation
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

    state = interview_state or {}
    question_definition = question.get("questionDefinition")
    if not isinstance(question_definition, Mapping):
        question_definition = {}
    slots = list(current_question_slots)
    if not slots:
        for required, key in ((True, "requiredItems"), (False, "optionalItems")):
            items = question_definition.get(key) or question.get(key) or []
            if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
                continue
            slots.extend(
                {
                    "itemId": item.get("itemId"),
                    "label": item.get("label"),
                    "description": item.get("description"),
                    "required": required,
                }
                for item in items
                if isinstance(item, Mapping) and item.get("itemId")
            )

    def compact_message(message: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(message, Mapping):
            return None
        return {
            "role": message.get("role"),
            "content": str(
                message.get("content")
                or message.get("rawTranscript")
                or message.get("transcript")
                or ""
            ).strip(),
            "questionId": message.get("questionId"),
            "answerToQuestionId": message.get("answerToQuestionId"),
        }

    resolved_previous_assistant = compact_message(previous_assistant_message)
    resolved_previous_user = compact_message(previous_user_answer)
    if resolved_previous_assistant is None:
        for message in reversed(recent_conversation):
            if message.get("role") == "assistant":
                resolved_previous_assistant = compact_message(message)
                break
    if resolved_previous_user is None:
        user_messages = [
            message for message in recent_conversation if message.get("role") == "user"
        ]
        if len(user_messages) > 1:
            resolved_previous_user = compact_message(user_messages[-2])

    question_answer = (
        state.get("questionAnswers", {}).get(question.get("questionId"))
        if isinstance(state.get("questionAnswers"), Mapping)
        else None
    )
    resolved_known_facts = list(known_facts) or list(state.get("knownFacts") or [])
    if not resolved_known_facts and isinstance(question_answer, Mapping):
        for key in ("originalAnswer", "answer"):
            value = str(question_answer.get(key) or "").strip()
            if value:
                resolved_known_facts.append({"source": key, "value": value})

    resolved_clarification_target = (
        None
        if is_candidate_confirmation
        else (
            clarification_target
            if clarification_target is not None
            else state.get("lastClarificationTarget")
        )
    )
    conversation_state = {
        "currentTurnType": current_turn_type or state.get("currentTurnType", "BASIC"),
        "pendingConfirmation": bool(pending_confirmation),
        "clarificationTarget": resolved_clarification_target,
        "probeTarget": probe_target
        if probe_target is not None
        else state.get("activeProbeTarget"),
        "knownFacts": resolved_known_facts,
        "coveredInformation": list(covered_information)
        or list(state.get("lastAspectAssessments") or []),
        "missingInformation": list(missing_information)
        or list(state.get("lastMissingInformation") or []),
        "currentQuestionSlots": slots,
        "currentQuestionAnswer": question_answer,
    }

    return {
        "latestUserUtterance": str(utterance).strip(),
        "currentQuestion": {
            "questionId": question.get("questionId"),
            "text": str(question.get("text") or question.get("questionText") or "").strip(),
            "targetType": question.get("targetType"),
            "targetId": question.get("targetId"),
            "targetLabel": question.get("targetLabel") or question.get("label"),
            "purpose": question.get("purpose") or question_definition.get("purpose"),
            "questionGoal": question.get("questionGoal")
            or question_definition.get("questionGoal"),
            "expectedInformation": slots,
        },
        "currentTarget": {
            "targetType": target.get("targetType"),
            "targetId": target.get("targetId"),
            "label": target.get("label") or target.get("targetLabel"),
        },
        "pendingConfirmation": bool(pending_confirmation),
        "currentTurnType": conversation_state["currentTurnType"],
        "previousAssistantMessage": resolved_previous_assistant,
        "previousUserAnswer": resolved_previous_user,
        "clarificationTarget": conversation_state["clarificationTarget"],
        "probeTarget": conversation_state["probeTarget"],
        "knownFacts": conversation_state["knownFacts"],
        "coveredInformation": conversation_state["coveredInformation"],
        "missingInformation": conversation_state["missingInformation"],
        "currentQuestionSlots": conversation_state["currentQuestionSlots"],
        "conversationState": conversation_state,
        "recentConversation": recent,
    }


def resolve_canonical_intent(
    *,
    utterance: str,
    current_question: Mapping[str, Any] | None,
    current_target: Mapping[str, Any] | None,
    pending_confirmation: bool,
    recent_conversation: Sequence[Mapping[str, Any]] = (),
    current_turn_type: str | None = None,
    previous_assistant_message: Mapping[str, Any] | None = None,
    previous_user_answer: Mapping[str, Any] | None = None,
    clarification_target: Mapping[str, Any] | None = None,
    probe_target: Mapping[str, Any] | None = None,
    known_facts: Sequence[Mapping[str, Any]] = (),
    covered_information: Sequence[Mapping[str, Any]] = (),
    missing_information: Sequence[Mapping[str, Any] | str] = (),
    current_question_slots: Sequence[Mapping[str, Any]] = (),
    interview_state: Mapping[str, Any] | None = None,
    provider: CanonicalIntentProvider | None = None,
) -> CanonicalIntentDecision:
    """Resolve one user utterance without changing interview state."""

    context = build_canonical_intent_context(
        utterance=utterance,
        current_question=current_question,
        current_target=current_target,
        pending_confirmation=pending_confirmation,
        recent_conversation=recent_conversation,
        current_turn_type=current_turn_type,
        previous_assistant_message=previous_assistant_message,
        previous_user_answer=previous_user_answer,
        clarification_target=clarification_target,
        probe_target=probe_target,
        known_facts=known_facts,
        covered_information=covered_information,
        missing_information=missing_information,
        current_question_slots=current_question_slots,
        interview_state=interview_state,
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
        user_intent = output.userIntent
        if (
            pending_confirmation
            and user_intent
            in {
                "QUESTION_CLARIFICATION",
                "QUESTION_INTENT_CONFIRMATION",
                "ASR_CLARIFICATION_REQUEST",
                "META_QUESTION",
            }
            and dialogue_act in {"ANSWER", "CORRECTION", "CONFIRMATION", "REJECTION"}
        ):
            # A substantive act about the active candidate outranks a
            # contradictory assistant-directed intent label.
            user_intent = (
                "ANSWER_CLARIFICATION"
                if dialogue_act == "CORRECTION"
                else "USER_RESPONSE"
            )
        interpretation_match = output.interpretationMatch
        user_interpretation = output.userInterpretation
        original_question_intent = output.originalQuestionIntent
        resume_current_question = output.resumeCurrentQuestion
    except (StructuredInterviewProviderError, ValueError, TypeError) as exc:
        # A resolver failure must not mutate state or accidentally enter the
        # answer path. OTHER maps to the safe keep-current-question action.
        logger.exception(
            "conversation_router_failed provider=%s error_type=%s",
            provider_name,
            exc.__class__.__name__,
        )
        dialogue_act = "OTHER"
        user_intent = "OTHER"
        interpretation_match = "NONE"
        user_interpretation = None
        original_question_intent = None
        resume_current_question = True
    conversation_action = resolve_conversation_action(
        user_intent,
        dialogue_act=dialogue_act,
        pending_confirmation=pending_confirmation,
        turn_type=current_turn_type,
    )
    elapsed_ms = round((monotonic() - started_at) * 1000, 1)
    logger.info(
        "conversation_router_end dialogue_act=%s user_intent=%s conversation_action=%s conversation_router_ms=%s",
        dialogue_act,
        user_intent,
        conversation_action,
        elapsed_ms,
    )
    return CanonicalIntentDecision(
        dialogueAct=dialogue_act,
        latency_ms=elapsed_ms,
        provider=provider_name,
        user_intent=user_intent,
        interpretation_match=interpretation_match,
        user_interpretation=user_interpretation,
        original_question_intent=original_question_intent,
        conversation_action=conversation_action,
        resume_current_question=resume_current_question,
    )


def resolve_conversation_action(
    user_intent: UserIntent,
    *,
    dialogue_act: StructuredDialogueAct,
    pending_confirmation: bool,
    turn_type: str | None = None,
) -> ConversationAction:
    """Choose a common conversation action from semantic intent.

    A normal user response remains ``PROCESS_USER_RESPONSE`` until the
    Structured Interview evaluator decides whether to accept, probe, or ask
    for a missing aspect.
    """

    if turn_type == "CONTROL" or dialogue_act == "CONVERSATION_REQUEST":
        return "HANDLE_CONTROL"
    if user_intent in {"QUESTION_CLARIFICATION", "QUESTION_INTENT_CONFIRMATION", "META_QUESTION"}:
        return "ANSWER_USER_CLARIFICATION"
    if user_intent == "ASR_CLARIFICATION_REQUEST":
        # The user is asking which part was unclear.  Answer that target first;
        # ASK_ASR_RETRY is selected when the assistant itself detects an
        # actionable recognition failure in the preceding answer evaluation.
        return "ANSWER_USER_CLARIFICATION"
    if user_intent == "DECLINE_OR_SKIP":
        return "SKIP_AND_NEXT"
    if user_intent == "ANSWER_CLARIFICATION":
        return "APPLY_CORRECTION" if dialogue_act == "CORRECTION" else "ANSWER_USER_CLARIFICATION"
    if dialogue_act == "CONFIRMATION":
        return "ACCEPT_AND_NEXT" if pending_confirmation else "KEEP_CURRENT_QUESTION"
    if dialogue_act == "REJECTION":
        return "SKIP_AND_NEXT" if pending_confirmation else "KEEP_CURRENT_QUESTION"
    if dialogue_act == "REPEAT_REQUEST":
        return "REPEAT_CURRENT_QUESTION"
    if dialogue_act == "CORRECTION":
        return "APPLY_CORRECTION"
    if dialogue_act == "ANSWER":
        return "PROCESS_USER_RESPONSE"
    return "KEEP_CURRENT_QUESTION"


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
    if dialogue_act == "REPEAT_REQUEST":
        return "REPEAT_CURRENT_QUESTION"
    if dialogue_act == "CORRECTION":
        return "APPLY_CORRECTION"
    if dialogue_act == "CONVERSATION_REQUEST":
        return "HANDLE_CONTROL"
    return "KEEP_CURRENT_QUESTION"


def _canonical_intent_system_prompt() -> str:
    return (
        "あなたはKIKIORIの会話意図ルーターです。\n\n"
        "最新のユーザー発話が現在の質問に対して何をしているかを、会話状態と意味から分類してください。\n"
        "文字列一致、特定語の一覧、質問ID固有の分岐は使わないでください。外国語、複数言語、くだけた表現、短い発話も同じ意味契約で判定します。\n"
        "回答内容の抽出、回答の十分性、状態更新はしません。\n\n"
        "まずuserIntentを選び、userIntentに整合するdialogueAct、interpretationMatch、conversationActionを返してください。\n"
        "直前のassistant発話、clarificationTarget、probeTarget、取得済み情報、不足情報を優先し、発話単独で判断しないでください。\n\n"
        "pendingConfirmation=trueの場合、会話の対象は現在確認中の候補です。現在のtargetTypeにかかわらず、以前のclarificationTargetやASR曖昧箇所を今回の話題として使ってはいけません。候補への同意、否定、訂正、または現在の候補についての質問として最新発話を分類してください。値を明示して現在の候補を補足・訂正する発話は回答として処理し、ASR_CLARIFICATION_REQUESTにしてはいけません。ASR_CLARIFICATION_REQUESTは、最新発話そのものが認識対象を尋ねている場合に限ります。\n"
        "userIntent:\n"
        "- USER_RESPONSE: 現在の質問への事実、経験、判断、追加情報\n"
        "- QUESTION_CLARIFICATION: 現在の質問の意味、対象、答え方の理解支援を求める\n"
        "- QUESTION_INTENT_CONFIRMATION: ユーザー自身の解釈を示し、その解釈が質問意図に合うか確認する\n"
        "- ASR_CLARIFICATION_REQUEST: 直前に音声認識上の不明箇所を示されたため、その対象を確認する\n"
        "- ANSWER_CLARIFICATION: 自分の直前回答を補足または訂正する。訂正内容がある場合はCORRECTIONを併記する\n"
        "- META_QUESTION: 質問内容ではなく、インタビューの目的、進行、AIの処理について尋ねる\n"
        "- DECLINE_OR_SKIP: 回答を控える、回答できない、スキップや次への移行を求める\n"
        "- OTHER: 上記に確実に分類できない発話\n\n"
        "QUESTION_INTENT_CONFIRMATIONではuserInterpretationを短い意味要約として記録し、originalQuestionIntentと意味比較してMATCH、PARTIAL_MATCH、MISMATCHを返してください。質問文をそのまま複製しないでください。\n"
        "ASR_CLARIFICATION_REQUESTはconversationState.clarificationTargetに音声上の対象がある場合だけ使い、対象がなければ別の意味分類に戻してください。\n"
        "QUESTION_CLARIFICATION、QUESTION_INTENT_CONFIRMATION、ASR_CLARIFICATION_REQUEST、META_QUESTIONはインタビュー回答ではありません。回答値を抽出する前提にしないでください。\n"
        "conversationActionは意味分類から選びます。通常回答はPROCESS_USER_RESPONSE、質問意図・メタ質問はANSWER_USER_CLARIFICATION、ASR対象確認はASK_ASR_RETRY、訂正はAPPLY_CORRECTION、拒否・スキップはSKIP_AND_NEXTです。通常回答をACCEPT_AND_NEXTにしてはいけません。十分性と深掘りは別のStructured Interview評価で決めます。\n"
        "resumeCurrentQuestionは、質問への応答を求める発話の処理後に同じ基本質問へ戻る場合trueにしてください。\n"
        "dialogueActは既存の互換分類として返し、質問への発話をANSWERにしないでください。\n"
        "指定されたJSON Schemaだけを返してください。"
    ).strip()
