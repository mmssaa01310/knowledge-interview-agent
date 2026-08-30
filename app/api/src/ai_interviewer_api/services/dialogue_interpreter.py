from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from time import monotonic
from typing import Any, Literal

from pydantic import BaseModel, Field

from ai_interviewer_api.agents.common.strands_runtime import (
    create_agent,
    create_voice_evaluation_bedrock_model,
)
from ai_interviewer_api.core.config import settings
from ai_interviewer_api.core.interview_locale import (
    InterviewLocale,
    interview_language_instruction,
    localized_interview_fallbacks,
)

logger = logging.getLogger(__name__)

DialogueAct = Literal[
    "ANSWER",
    "CLARIFICATION_REQUEST",
    "QUESTION_TO_ASSISTANT",
    "CONVERSATION_REQUEST",
    "BACKCHANNEL",
    "HESITATION",
    "CORRECTION",
    "REJECTION",
    "CONFIRMATION",
    "IRRELEVANT",
    "OTHER",
]

_PROCESSOR_DIALOGUE_ACTS = {
    "ANSWER",
    "CORRECTION",
    "REJECTION",
    "CONFIRMATION",
}


@dataclass(frozen=True)
class DialogueInterpretation:
    act: DialogueAct
    response_text: str | None = None
    reason: str | None = None
    evaluation_status: Literal["OK", "EVALUATION_ERROR", "DISABLED"] = "OK"


class DialogueInterpretationOutput(BaseModel):
    act: DialogueAct
    response_text: str | None = None
    reason: str | None = None


_DIALOGUE_INTERPRETER_SYSTEM_PROMPT = """
あなたはAIインタビューの Dialogue Act / Conversation Interpreter です。
ユーザー発話を、回答評価へ渡すべき発話か、会話として応答して現在質問を維持すべき発話かに分類してください。

分類:
- ANSWER: 現在質問への回答。回答内容・追加情報を含む。
- CLARIFICATION_REQUEST: 現在質問の意味や答え方を確認している。
- QUESTION_TO_ASSISTANT: 直前のAI発話、候補、確認文について質問している。
- CONVERSATION_REQUEST: もう少し会話したい、雑談したい、補助的に話してほしい。
- BACKCHANNEL: 短い相槌。
- HESITATION: 考え中、言い淀み。
- CORRECTION: 現在候補や直前回答を具体的に訂正している。
- REJECTION: 現在候補を否定しているが、訂正内容が十分ではない。
- CONFIRMATION: 確認待ち候補を承認している。
- IRRELEVANT: 現在質問や候補と関係が薄い。
- OTHER: 上記に明確に分類できない。

重要:
- 文字列一致や特定フレーズではなく、現在質問、質問計画、候補、直前AI発話、会話履歴を踏まえた意味で判断してください。
- ANSWER/CORRECTION/REJECTION/CONFIRMATION は後段の回答状態機械へ渡されるため、response_text は不要です。
- それ以外は、現在質問を不用意に繰り返さず、直前のユーザー発話に自然に応答する response_text を返してください。
- 「この内容」などの参照表現は candidateAnswer や直前AI発話から具体化してください。
- CLARIFICATION_REQUEST は、現在質問の意図を短く説明してください。
- BACKCHANNEL/HESITATION は、必要なら「少し考えてからで大丈夫です。」のように待つ応答にしてください。
結果は指定された構造化スキーマだけで返してください。
""".strip()


def should_route_to_answer_processor(
    interpretation: DialogueInterpretation,
    *,
    awaiting_confirmation: bool,
) -> bool:
    if interpretation.act == "ANSWER":
        return True
    if awaiting_confirmation and interpretation.act in _PROCESSOR_DIALOGUE_ACTS:
        return True
    return False


def interpret_dialogue_act(
    *,
    transcript: str,
    current_question: dict[str, Any] | None,
    current_field: dict[str, Any] | None,
    field_state: dict[str, Any],
    recent_messages: list[dict[str, Any]],
    last_assistant_message: dict[str, Any] | None = None,
    interview_locale: InterviewLocale = "ja-JP",
) -> DialogueInterpretation:
    if not settings.bedrock_enabled:
        return DialogueInterpretation(act="ANSWER", evaluation_status="DISABLED")

    prompt = _build_dialogue_interpreter_prompt(
        transcript=transcript,
        current_question=current_question,
        current_field=current_field,
        field_state=field_state,
        recent_messages=recent_messages,
        last_assistant_message=last_assistant_message,
        interview_locale=interview_locale,
    )
    started_at = monotonic()
    try:
        result = _run_dialogue_structured_output(prompt, interview_locale)
    except Exception as exc:  # noqa: BLE001 - classification failures must not pollute answers
        logger.exception(
            "dialogue_act_interpretation_failed question_id=%s error_type=%s",
            (current_question or {}).get("questionId"),
            exc.__class__.__name__,
        )
        return DialogueInterpretation(
            act="OTHER",
            response_text={
                "ja-JP": "すみません。いまの内容をうまく解釈できませんでした。もう一度、言い換えて教えてください。",
                "en-US": "Sorry, I could not understand that. Please rephrase your answer.",
                "zh-CN": "抱歉，我没能理解刚才的内容。请换一种方式说明。",
                "pt-BR": "Desculpe, não consegui entender. Reformule sua resposta, por favor.",
            }.get(interview_locale, localized_interview_fallbacks(interview_locale)["error"]),
            reason="dialogue_interpretation_failed",
            evaluation_status="EVALUATION_ERROR",
        )
    logger.info(
        "dialogue_act_interpreted question_id=%s act=%s interpretation_ms=%s",
        (current_question or {}).get("questionId"),
        result.act,
        round((monotonic() - started_at) * 1000, 1),
    )
    return DialogueInterpretation(
        act=result.act,
        response_text=_clean_response_text(result.response_text),
        reason=result.reason,
    )


def _run_dialogue_structured_output(
    prompt: str,
    interview_locale: InterviewLocale = "ja-JP",
) -> DialogueInterpretationOutput:
    agent = create_agent(
        model=create_voice_evaluation_bedrock_model(),
        system_prompt=(
            f"{_DIALOGUE_INTERPRETER_SYSTEM_PROMPT}\n\n"
            f"{interview_language_instruction(interview_locale)}"
        ),
        tools=[],
        hooks=[],
        name="Interview Dialogue Interpreter",
        description="Classifies interview dialogue acts before answer evaluation.",
    )
    result = agent(
        prompt,
        invocation_state={},
        structured_output_model=DialogueInterpretationOutput,
    )
    structured_output = getattr(result, "structured_output", None)
    if isinstance(structured_output, DialogueInterpretationOutput):
        return structured_output
    if structured_output is not None:
        return DialogueInterpretationOutput.model_validate(structured_output)
    if isinstance(result, DialogueInterpretationOutput):
        return result
    text = str(result).strip()
    if text:
        try:
            return DialogueInterpretationOutput.model_validate_json(text)
        except Exception:  # noqa: BLE001
            return DialogueInterpretationOutput.model_validate(json.loads(text))
    raise ValueError("dialogue interpretation output missing")


def _build_dialogue_interpreter_prompt(
    *,
    transcript: str,
    current_question: dict[str, Any] | None,
    current_field: dict[str, Any] | None,
    field_state: dict[str, Any],
    recent_messages: list[dict[str, Any]],
    last_assistant_message: dict[str, Any] | None,
    interview_locale: InterviewLocale,
) -> str:
    payload = {
        "currentQuestion": {
            "questionId": (current_question or {}).get("questionId"),
            "text": (current_question or {}).get("text"),
            "questionType": (current_question or {}).get("questionType"),
            "questionPlan": (current_question or {}).get("questionPlan"),
        },
        "currentField": {
            "id": (current_field or {}).get("id"),
            "name": (current_field or {}).get("name"),
            "description": (current_field or {}).get("description"),
            "aiAssistPrompt": (current_field or {}).get("aiAssistPrompt"),
            "questionPlan": (current_field or {}).get("questionPlan"),
        },
        "fieldState": {
            "answerState": field_state.get("answerState"),
            "candidateAnswer": field_state.get("candidateAnswer"),
            "recordAnswer": field_state.get("recordAnswer"),
            "capturedItems": field_state.get("capturedItems") or [],
            "missingInformation": field_state.get("missingInformation") or [],
            "pendingQuestionId": field_state.get("pendingQuestionId"),
            "pendingFieldId": field_state.get("pendingFieldId"),
        },
        "lastAssistantMessage": _message_digest(last_assistant_message),
        "recentMessages": [_message_digest(message) for message in recent_messages[-8:]],
        "userTranscript": transcript,
        "interviewLocale": interview_locale,
        "languageInstruction": interview_language_instruction(interview_locale),
        "instructions": {
            "routeOnlyAnswerLikeActsToAnswerEvaluation": True,
            "doNotAdvanceInterviewStateForConversationActs": True,
            "doNotUsePhraseMatching": True,
            "responseTextRequiredUnlessActIsRoutedToProcessor": True,
            "currentQuestionShouldBeMaintained": True,
        },
        "examples": [
            {
                "question": "現在の担当業務を教えてください。",
                "transcript": "私の仕事？",
                "act": "CLARIFICATION_REQUEST",
                "response_text": "はい。普段どんな仕事を担当しているか、という意味です。",
            },
            {
                "candidateAnswer": "清掃員",
                "lastAssistantMessage": "清掃員でよろしいですか？",
                "transcript": "この内容とは？",
                "act": "QUESTION_TO_ASSISTANT",
                "response_text": "先ほどの「清掃員」という回答のことです。",
            },
            {
                "candidateAnswer": "設備保全",
                "transcript": "いや、保全管理です",
                "act": "CORRECTION",
            },
            {
                "candidateAnswer": "設備保全",
                "transcript": "はい、そうです",
                "act": "CONFIRMATION",
            },
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _message_digest(message: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return None
    return {
        "role": message.get("role"),
        "content": message.get("content"),
        "questionId": message.get("questionId") or message.get("answerToQuestionId"),
        "fieldId": message.get("fieldId") or message.get("answerToFieldId"),
        "messageType": message.get("messageType"),
        "turnType": message.get("turnType"),
        "dialogueAct": message.get("dialogueAct"),
    }


def _clean_response_text(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None
