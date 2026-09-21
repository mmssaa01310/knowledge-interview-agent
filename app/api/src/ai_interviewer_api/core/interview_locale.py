from __future__ import annotations

from collections.abc import Mapping, Sequence
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


def localized_interview_not_heard_prompt(
    locale: InterviewLocale,
    subject: str | None = None,
    understood: str | None = None,
) -> str:
    """Ask for a repeat only when the transcript has recognition evidence."""

    normalized_subject = subject.strip() if subject else ""
    normalized_understood = understood.strip() if understood else ""
    if locale == "en-US":
        if normalized_understood and normalized_subject:
            return (
                f"I understood {normalized_understood}, but I could not hear "
                f"{normalized_subject}. Please repeat only that part."
            )
        return f"I could not hear that clearly. Please repeat the part about {normalized_subject or 'the current question'}."
    if locale == "zh-CN":
        if normalized_understood and normalized_subject:
            return f"我听懂了{normalized_understood}，但没有听清{normalized_subject}。请只重复这一部分。"
        return f"这部分没有听清。请再说明{normalized_subject or '当前问题'}。"
    if locale == "pt-BR":
        if normalized_understood and normalized_subject:
            return (
                f"Entendi {normalized_understood}, mas não consegui ouvir "
                f"{normalized_subject}. Repita apenas essa parte, por favor."
            )
        return f"Não consegui ouvir com clareza. Repita a parte sobre {normalized_subject or 'a pergunta atual'}, por favor."
    if normalized_understood and normalized_subject:
        return (
            f"{normalized_understood}ということは分かりましたが、{normalized_subject}を認識できませんでした。"
            f"その部分だけ、もう一度お願いします。"
        )
    return f"音声を正しく認識できませんでした。{normalized_subject or '先ほどの質問'}について、もう一度お願いします。"


def localized_interview_off_target_prompt(
    locale: InterviewLocale,
    target_label: str,
) -> str:
    """Return to the current target without pretending the answer was missing from audio."""

    label = target_label.strip() or {
        "ja-JP": "現在の項目",
        "en-US": "the current item",
        "zh-CN": "当前项目",
        "pt-BR": "o item atual",
    }[locale]
    return {
        "ja-JP": f"その内容も確認しました。今は{label}について、話せる範囲で教えてください。",
        "en-US": f"I noted that. For now, please tell me what you can about {label}.",
        "zh-CN": f"我记下了这部分内容。现在请尽可能说明{label}。",
        "pt-BR": f"Anotei esse conteúdo. Agora, fale o que puder sobre {label}.",
    }[locale]


def localized_interview_question_guidance(
    locale: InterviewLocale,
    *,
    topic: str,
    examples: Sequence[str],
    perspectives: Sequence[str],
    example_mode: str = "single",
) -> str:
    """Turn LLM-generated guidance into a natural, concise spoken reply."""

    normalized_topic = topic.strip() or {
        "ja-JP": "このテーマ",
        "en-US": "this topic",
        "zh-CN": "这个主题",
        "pt-BR": "este tema",
    }[locale]
    normalized_examples = [item.strip() for item in examples if item.strip()]
    if example_mode != "additional":
        normalized_examples = normalized_examples[:1]
    else:
        normalized_examples = normalized_examples[:3]
    normalized_perspectives = [item.strip() for item in perspectives if item.strip()][:3]
    examples_text = {
        "ja-JP": "、".join(normalized_examples),
        "en-US": ", ".join(normalized_examples),
        "zh-CN": "、".join(normalized_examples),
        "pt-BR": ", ".join(normalized_examples),
    }[locale]
    perspectives_text = {
        "ja-JP": "、".join(normalized_perspectives),
        "en-US": ", ".join(normalized_perspectives),
        "zh-CN": "、".join(normalized_perspectives),
        "pt-BR": ", ".join(normalized_perspectives),
    }[locale]

    if locale == "ja-JP":
        return (
            f"お聞きしたいのは、{normalized_topic}です。"
            f"例えば、{examples_text}などが考えられます。"
            f"回答するときは、{perspectives_text}といった点を意識して、話せる範囲で教えてください。"
        )
    if locale == "en-US":
        return (
            f"I would like to hear about {normalized_topic}. "
            f"For example, you could talk about {examples_text}. "
            f"As you answer, consider {perspectives_text}."
        )
    if locale == "zh-CN":
        return (
            f"我想了解的是{normalized_topic}。"
            f"例如，可以谈谈{examples_text}。"
            f"回答时可以从{perspectives_text}这些方面来说明。"
        )
    return (
        f"Gostaria de saber sobre {normalized_topic}. "
        f"Por exemplo, você pode falar sobre {examples_text}. "
        f"Ao responder, considere {perspectives_text}."
    )


def localized_interview_hesitation_prompt(locale: InterviewLocale) -> str:
    """Respond briefly to a filler without replacing the current question."""

    return {
        "ja-JP": "急がなくて大丈夫です。続けられるところからお願いします。",
        "en-US": "No rush. Please continue whenever you are ready.",
        "zh-CN": "不用着急，准备好后请继续。",
        "pt-BR": "Sem pressa. Continue quando estiver pronto.",
    }[locale]


def localized_interview_confirmation_clarification_prompt(locale: InterviewLocale) -> str:
    """Ask for the difference before committing a qualified confirmation."""

    return {
        "ja-JP": "少し違う部分があれば、その点だけ教えてください。",
        "en-US": "Please tell me only which part is different.",
        "zh-CN": "如果有不一样的地方，请只说明那一点。",
        "pt-BR": "Conte apenas qual parte está diferente.",
    }[locale]


def localized_interview_unanswerable_prompt(locale: InterviewLocale, target_label: str) -> str:
    """Offer one neutral, concrete angle when the answer does not come to mind."""

    label = target_label.strip() or {
        "ja-JP": "この項目",
        "en-US": "this topic",
        "zh-CN": "这一项",
        "pt-BR": "este tema",
    }[locale]
    return {
        "ja-JP": f"思い出せる範囲で大丈夫です。{label}に関して、具体的な出来事や困ったことを一つ教えてください。",
        "en-US": f"Whatever you can recall is fine. Please share one concrete event or difficulty related to {label}.",
        "zh-CN": f"能想起多少都可以。请说一件与{label}有关的具体事情或困难。",
        "pt-BR": f"Tudo bem responder apenas o que lembrar. Conte um acontecimento ou dificuldade concreta sobre {label}.",
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
