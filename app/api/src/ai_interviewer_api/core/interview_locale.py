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


def localized_interview_greeting(locale: InterviewLocale) -> str:
    return {
        "ja-JP": "これからインタビューを開始します。",
        "en-US": "We are about to start the interview.",
        "zh-CN": "现在开始访谈。",
        "pt-BR": "Vamos começar a entrevista.",
    }[locale]


def localized_interview_confirmation_question(locale: InterviewLocale, candidate: str) -> str:
    candidate_text = candidate.strip()
    return {
        "ja-JP": f"「{candidate_text}」でよろしいですか？",
        "en-US": f"To confirm, is your answer “{candidate_text}”?",
        "zh-CN": f"请确认，您的回答是“{candidate_text}”吗？",
        "pt-BR": f"Para confirmar, sua resposta é “{candidate_text}”?",
    }[locale]


def localized_interview_proposal_question(locale: InterviewLocale, candidate: str) -> str:
    """Render one consistent confirmation prompt for an AI-generated proposal."""

    candidate_text = candidate.strip()
    return {
        "ja-JP": f"AIの案です。{candidate_text}という内容でよいですか。修正や拒否もできます。",
        "en-US": f"This is an AI suggestion: {candidate_text}. Is this acceptable? You can modify or reject it.",
        "zh-CN": f"这是AI的建议：{candidate_text}。这样可以吗？您也可以修改或拒绝。",
        "pt-BR": f"Esta é uma sugestão da IA: {candidate_text}. Está de acordo? Você também pode alterar ou rejeitar.",
    }[locale]


def localized_interview_document_confirmation_question(
    locale: InterviewLocale,
    field_label: str,
    candidate: str,
) -> str:
    """Ask the user to verify a value found in the uploaded prior knowledge."""

    label_text = field_label.strip() or {
        "ja-JP": "この項目",
        "en-US": "this item",
        "zh-CN": "这一项",
        "pt-BR": "este item",
    }[locale]
    candidate_text = candidate.strip()
    return {
        "ja-JP": f"事前知識では{label_text}は「{candidate_text}」となっています。この内容で合っていますか？",
        "en-US": f"The prior knowledge lists {label_text} as “{candidate_text}”. Is that correct?",
        "zh-CN": f"根据现有资料，{label_text}是“{candidate_text}”。这个内容正确吗？",
        "pt-BR": f"O conhecimento prévio indica {label_text} como “{candidate_text}”. Está correto?",
    }[locale]


def localized_interview_tentative_transition(
    locale: InterviewLocale,
    candidate: str,
    next_topic: str,
) -> str:
    """Bridge a tentative interpretation into the next question without asking for yes/no."""

    candidate_text = candidate.strip()
    topic_text = next_topic.strip() or {
        "ja-JP": "次の項目",
        "en-US": "the next topic",
        "zh-CN": "下一个问题",
        "pt-BR": "o próximo tópico",
    }[locale]
    return {
        "ja-JP": f"「{candidate_text}」なんですね。では、{topic_text}について教えてください。",
        "en-US": f"So, it tends to be “{candidate_text}”. Now, please tell me about {topic_text}.",
        "zh-CN": f"也就是说是“{candidate_text}”。那么，请告诉我关于{topic_text}的信息。",
        "pt-BR": f"Entendi, tende a ser “{candidate_text}”. Agora, fale-me sobre {topic_text}.",
    }[locale]


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
