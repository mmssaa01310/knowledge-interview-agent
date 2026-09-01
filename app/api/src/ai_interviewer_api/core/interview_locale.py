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


def localized_interview_incomplete_prompt(locale: InterviewLocale) -> str:
    """Ask the speaker to continue without repeating the partial transcript."""

    return {
        "ja-JP": "続き、お願いします。",
        "en-US": "Please continue.",
        "zh-CN": "请继续说。",
        "pt-BR": "Pode continuar, por favor.",
    }[locale]


def localized_interview_transcript_retry(locale: InterviewLocale) -> str:
    """Ask for a re-utterance when the transcript cannot be safely corrected."""

    return {
        "ja-JP": "この部分をもう一度お願いします。",
        "en-US": "Please repeat that part.",
        "zh-CN": "请再说一遍这一部分。",
        "pt-BR": "Repita essa parte, por favor.",
    }[locale]


def localized_interview_question_help(locale: InterviewLocale, target_label: str) -> str:
    """Explain the current question without asking a second question."""

    label = target_label.strip() or {
        "ja-JP": "この項目",
        "en-US": "this item",
        "zh-CN": "这一项",
        "pt-BR": "este item",
    }[locale]
    return {
        "ja-JP": f"この質問では、{label}について実際の内容や経験をお聞きしています。答えられる範囲でお話しください。",
        "en-US": f"This question asks about the actual details or experience related to {label}. Please share what you can.",
        "zh-CN": f"这个问题想了解{label}的实际内容或经历。请在您方便的范围内说明。",
        "pt-BR": f"Esta pergunta busca os detalhes ou experiências reais relacionados a {label}. Compartilhe o que puder.",
    }[locale]


def localized_interview_transcript_confirmation_question(
    locale: InterviewLocale,
    normalized_transcript: str,
) -> str:
    """Confirm a corrected transcript before it is used as an answer."""

    transcript = normalized_transcript.strip()
    return {
        "ja-JP": f"「{transcript}」という理解でよろしいですか？",
        "en-US": f"Is my understanding correct: “{transcript}”?",
        "zh-CN": f"我的理解是“{transcript}”，这样对吗？",
        "pt-BR": f"Entendi que “{transcript}”. Está correto?",
    }[locale]


def localized_interview_fallbacks(locale: InterviewLocale) -> dict[str, str]:
    """Small set of backend fallback replies used when an AI reply is unavailable."""

    return {
        "ja-JP": {
            "completion": "インタビューが完了しました。回答内容を確認してください。",
            "control_ack": "承知しました。",
            "error": "一時的にAI応答を生成できませんでした。少し時間をおいて再度送信してください。",
        },
        "en-US": {
            "completion": "The interview is complete. Please review the answers.",
            "control_ack": "Understood.",
            "error": "I could not generate an AI response temporarily. Please wait a moment and try again.",
        },
        "zh-CN": {
            "completion": "访谈已完成。请确认回答内容。",
            "control_ack": "明白了。",
            "error": "暂时无法生成 AI 回复。请稍后再试。",
        },
        "pt-BR": {
            "completion": "A entrevista foi concluída. Verifique as respostas.",
            "control_ack": "Entendido.",
            "error": "Não foi possível gerar uma resposta da IA no momento. Aguarde um pouco e tente novamente.",
        },
    }[locale]
