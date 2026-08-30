from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast


InterviewLocale = Literal["ja-JP", "en-US", "zh-CN", "pt-BR"]
DEFAULT_INTERVIEW_LOCALE: InterviewLocale = "ja-JP"
SUPPORTED_INTERVIEW_LOCALES = frozenset({"ja-JP", "en-US", "zh-CN", "pt-BR"})


def normalize_interview_locale(value: object) -> InterviewLocale | None:
    if isinstance(value, str) and value in SUPPORTED_INTERVIEW_LOCALES:
        return cast(InterviewLocale, value)
    return None


def resolve_interview_locale(
    record: Mapping[str, object] | None = None,
    knowledge: Mapping[str, object] | None = None,
) -> InterviewLocale:
    """Resolve one stable conversation locale for an interview record.

    The record value wins so a running interview is not silently changed when
    its knowledge settings are edited. Older records fall back to the
    knowledge-level default and then to the existing Japanese default.
    """

    record_locale = normalize_interview_locale((record or {}).get("interviewLocale"))
    if record_locale:
        return record_locale

    plan = (knowledge or {}).get("interviewPlan")
    if isinstance(plan, Mapping):
        plan_locale = normalize_interview_locale(plan.get("interviewLocale"))
        if plan_locale:
            return plan_locale

    knowledge_language = (knowledge or {}).get("language")
    if knowledge_language == "en":
        return "en-US"
    return DEFAULT_INTERVIEW_LOCALE


def interview_locale_language_name(locale: InterviewLocale) -> str:
    return {
        "ja-JP": "Japanese",
        "en-US": "English",
        "zh-CN": "Simplified Chinese",
        "pt-BR": "Portuguese (Brazilian)",
    }[locale]


def interview_language_instruction(locale: InterviewLocale) -> str:
    return (
        f"The interview conversation language is {interview_locale_language_name(locale)} ({locale}). "
        "Generate every user-facing assistant question, confirmation, clarification, progress, "
        "and completion message in this language. Preserve names, technical terms, and recorded "
        "values as provided unless the user explicitly asks for translation."
    )


def localized_interview_fallbacks(locale: InterviewLocale) -> dict[str, str]:
    """Small set of backend fallback replies used when an AI reply is unavailable."""

    return {
        "ja-JP": {
            "follow_up": "もう少し詳しく確認させてください。",
            "completion": "インタビューが完了しました。回答内容を確認してください。",
            "completion_full": "以上で、設定されているすべての質問項目へのインタビューが完了しました。ご協力ありがとうございました。",
            "control_ack": "承知しました。",
            "error": "一時的にAI応答を生成できませんでした。少し時間をおいて再度送信してください。",
        },
        "en-US": {
            "follow_up": "Please tell me a little more so I can confirm the answer.",
            "completion": "The interview is complete. Please review the answers.",
            "completion_full": "You have completed all configured interview questions. Thank you for your cooperation.",
            "control_ack": "Understood.",
            "error": "I could not generate an AI response temporarily. Please wait a moment and try again.",
        },
        "zh-CN": {
            "follow_up": "请再详细说明一些，以便我确认您的回答。",
            "completion": "访谈已完成。请确认回答内容。",
            "completion_full": "您已完成所有设定的问题。感谢您的配合。",
            "control_ack": "明白了。",
            "error": "暂时无法生成 AI 回复。请稍后再试。",
        },
        "pt-BR": {
            "follow_up": "Conte-me um pouco mais para que eu possa confirmar a resposta.",
            "completion": "A entrevista foi concluída. Verifique as respostas.",
            "completion_full": "Você concluiu todas as perguntas configuradas da entrevista. Obrigado pela sua colaboração.",
            "control_ack": "Entendido.",
            "error": "Não foi possível gerar uma resposta da IA no momento. Aguarde um pouco e tente novamente.",
        },
    }[locale]
