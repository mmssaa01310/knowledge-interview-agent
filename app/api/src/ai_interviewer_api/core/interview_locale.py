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


def localized_interview_prior_knowledge_confirmation_question(
    locale: InterviewLocale,
    values: Sequence[tuple[str, str]],
) -> str:
    """Ask the participant to verify registered values by their target labels."""

    normalized = [
        (str(label or "").strip(), str(value or "").strip())
        for label, value in values
        if str(label or "").strip() and str(value or "").strip()
    ]
    if not normalized:
        return ""
    if locale == "ja-JP":
        details = "、".join(f"{label}は「{value}」" for label, value in normalized)
        return f"{details}で合っていますか？"
    if locale == "en-US":
        details = " and ".join(f"{label} is “{value}”" for label, value in normalized)
        return f"Is it correct that {details}?"
    if locale == "zh-CN":
        details = "、".join(f"{label}是“{value}”" for label, value in normalized)
        return f"{details}，对吗？"
    details = " e ".join(f"{label} é “{value}”" for label, value in normalized)
    return f"Está correto que {details}?"


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


def _localized_clarification_role(
    locale: InterviewLocale,
    semantic_role: str | None,
) -> str:
    """Turn a structured ASR role into a short user-facing label."""

    role = (semantic_role or "").strip()
    compact = role.casefold().replace(" ", "_")
    if locale == "ja-JP":
        for tokens, label in (
            (
                ("equipment", "asset", "facility", "machine", "device", "設備", "機器", "装置"),
                "設備名",
            ),
            (("technology", "tech", "技術"), "技術名"),
            (("person", "人名", "氏名"), "人名"),
            (("department", "所属", "部署"), "所属"),
            (("organization", "company", "組織", "会社", "所属"), "組織名"),
            (("number", "数字", "件数", "金額"), "数字"),
            (("date", "時期", "日付", "年月"), "時期"),
            (("location", "場所", "地域", "拠点"), "場所"),
        ):
            if any(token in compact or token in role for token in tokens):
                return label
        return role or "回答の不明瞭な部分"
    if locale == "en-US":
        for tokens, label in (
            (
                ("equipment", "asset", "facility", "machine", "device"),
                "the equipment name",
            ),
            (("technology", "tech"), "the technology name"),
            (("person",), "the person's name"),
            (("organization", "company"), "the organization name"),
            (("number",), "the number"),
            (("date",), "the time or date"),
            (("location",), "the location"),
        ):
            if any(token in compact for token in tokens):
                return label
        return role or "the unclear part of your answer"
    if locale == "zh-CN":
        for tokens, label in (
            (
                ("equipment", "asset", "facility", "machine", "device"),
                "设备名称",
            ),
            (("technology", "tech"), "技术名称"),
            (("person",), "人名"),
            (("organization", "company"), "组织名称"),
            (("number",), "数字"),
            (("date",), "时间"),
            (("location",), "地点"),
        ):
            if any(token in compact for token in tokens):
                return label
        return role or "回答中没有听清的部分"
    for tokens, label in (
        (
            ("equipment", "asset", "facility", "machine", "device"),
            "o nome do equipamento",
        ),
        (("technology", "tech"), "o nome da tecnologia"),
        (("person",), "o nome da pessoa"),
        (("organization", "company"), "o nome da organização"),
        (("number",), "o número"),
        (("date",), "a data ou período"),
        (("location",), "o local"),
    ):
        if any(token in compact for token in tokens):
            return label
    return role or "a parte pouco clara da resposta"


def localized_interview_ambiguous_part_prompt(
    locale: InterviewLocale,
    semantic_role: str | None = None,
    understood: str | None = None,
) -> str:
    """Ask for only an important, ambiguous ASR span."""

    normalized_understood = (understood or "").strip()
    if not (semantic_role or "").strip():
        return {
            "ja-JP": "音声を正しく認識できませんでした。回答がうまく聞き取れませんでしたが、聞き取れなかった部分だけ、もう一度お願いします。",
            "en-US": "I could not recognize part of the audio. Please repeat only the unclear part.",
            "zh-CN": "有一部分语音没有听清。请只重复没有听清的部分。",
            "pt-BR": "Não consegui reconhecer uma parte do áudio. Repita apenas a parte pouco clara, por favor.",
        }[locale]
    label = _localized_clarification_role(locale, semantic_role)
    if normalized_understood:
        if locale == "en-US":
            return f"I understood {normalized_understood}. Please repeat only {label}."
        if locale == "zh-CN":
            return f"我听懂了{normalized_understood}。请只重复{label}。"
        if locale == "pt-BR":
            return f"Entendi {normalized_understood}. Repita apenas {label}, por favor."
        return f"{normalized_understood}は分かりました。{label}だけもう一度お願いします。"
    if locale == "en-US":
        return f"Please repeat only {label}."
    if locale == "zh-CN":
        return f"请只重复{label}。"
    if locale == "pt-BR":
        return f"Repita apenas {label}, por favor."
    return f"{label}の部分だけ、もう一度教えてください。"


def localized_interview_clarification_target_reply(
    locale: InterviewLocale,
    target_type: str,
    label: str | None = None,
    semantic_role: str | None = None,
    span: str | None = None,
) -> str:
    """Explain the last clarification target without restating the question."""

    if target_type == "ASR_AMBIGUOUS":
        normalized_span = (span or "").strip()
        if normalized_span:
            if locale == "en-US":
                return f'The unclear part was "{normalized_span}".'
            if locale == "zh-CN":
                return f"是被识别为“{normalized_span}”的部分。"
            if locale == "pt-BR":
                return f'Foi a parte reconhecida como "{normalized_span}".'
            return f"「{normalized_span}」と認識された部分です。"
        target_label = _localized_clarification_role(locale, semantic_role)
        if locale == "en-US":
            return f"The unclear part was {target_label}."
        if locale == "zh-CN":
            return f"是{target_label}的部分。"
        if locale == "pt-BR":
            return f"Foi {target_label}."
        return f"{target_label}の部分です。"

    normalized_label = (label or "").strip()
    if locale == "en-US":
        return f"The missing part is {normalized_label or 'one specific aspect'}."
    if locale == "zh-CN":
        return f"不足的是{normalized_label or '其中一个具体方面'}。"
    if locale == "pt-BR":
        return f"Faltou {normalized_label or 'um aspecto específico'}."
    return f"不足的是「{normalized_label or '具体的一个方面'}」的部分。"


def localized_interview_missing_information_prompt(
    locale: InterviewLocale,
    label: str,
) -> str:
    """Ask one concise question about one missing semantic aspect."""

    normalized_label = label.strip() or {
        "ja-JP": "不足している点",
        "en-US": "the missing point",
        "zh-CN": "缺少的部分",
        "pt-BR": "o ponto que falta",
    }[locale]
    if locale == "en-US":
        return f"Could you tell me a little more about {normalized_label}?"
    if locale == "zh-CN":
        return f"请只再说明一下{normalized_label}。"
    if locale == "pt-BR":
        return f"Você poderia falar um pouco mais sobre {normalized_label}?"
    return f"{normalized_label}についてだけ、もう少し教えてください。"


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
    """Render one focused question clarification in at most two sentences.

    ``perspectives`` remains accepted for compatibility with older providers,
    but a question clarification must not become a long answer guide.  An
    example is optional and is always limited to one item, including when an
    older provider still returns ``exampleMode=additional``.
    """

    normalized_topic = topic.strip() or {
        "ja-JP": "このテーマ",
        "en-US": "this topic",
        "zh-CN": "这个主题",
        "pt-BR": "este tema",
    }[locale]
    normalized_examples = [item.strip() for item in examples if item.strip()][:1]
    # ``none`` is the default.  A provider may still return ``single`` for a
    # useful example, but never more than one is spoken to the user.
    example_text = normalized_examples[0].rstrip("。.!！") if normalized_examples else ""

    if locale == "ja-JP":
        reply = f"{normalized_topic}について、何か一つ教えてください。"
        if example_mode != "none" and example_text:
            reply += f"例えば、{example_text}といった経験です。"
        return reply
    if locale == "en-US":
        reply = f"Please tell me about {normalized_topic}, focusing on one example."
        if example_mode != "none" and example_text:
            reply += f" For example, {example_text}."
        return reply
    if locale == "zh-CN":
        reply = f"请只说一个关于{normalized_topic}的例子。"
        if example_mode != "none" and example_text:
            reply += f"例如，{example_text}。"
        return reply
    reply = f"Fale sobre {normalized_topic}, com apenas um exemplo."
    if example_mode != "none" and example_text:
        reply += f" Por exemplo, {example_text}."
    return reply


def localized_interview_intent_confirmation(
    locale: InterviewLocale,
    match: str,
    correction: str | None = None,
) -> str:
    """Answer a user's proposed interpretation without replaying the question."""

    normalized = (correction or "").strip().rstrip("。.!！")[:120]
    if locale == "en-US":
        if match == "MATCH":
            return "Yes, that is the right understanding."
        if match == "PARTIAL_MATCH":
            return f"Yes, that is close. The focus is {normalized or 'the specific part you mentioned'}."
        return f"Not quite. The focus is {normalized or 'the intended aspect of the question'}."
    if locale == "zh-CN":
        if match == "MATCH":
            return "是的，您的理解是对的。"
        if match == "PARTIAL_MATCH":
            return f"大致是这样，重点是{normalized or '您提到的具体部分'}。"
        return f"不完全是，重点是{normalized or '问题所指的那个方面'}。"
    if locale == "pt-BR":
        if match == "MATCH":
            return "Sim, esse entendimento está correto."
        if match == "PARTIAL_MATCH":
            return f"É bem próximo. O foco é {normalized or 'o aspecto específico mencionado'}."
        return f"Não exatamente. O foco é {normalized or 'o aspecto indicado pela pergunta'}."
    if match == "MATCH":
        return "はい、その理解で合っています。"
    if match == "PARTIAL_MATCH":
        return f"はい、近いです。焦点は{normalized or '今お尋ねの観点'}です。"
    return f"少し違います。焦点は{normalized or '質問で確認したい観点'}です。"


def localized_interview_meta_question_reply(
    locale: InterviewLocale,
    *,
    purpose: str | None = None,
) -> str:
    """Explain the interview context briefly when the user asks about the flow."""

    normalized = (purpose or "").strip().rstrip("。.!！")
    if locale == "en-US":
        return (
            f"I am asking this to organize the interview information about {normalized}."
            if normalized
            else "I am asking this to organize the information relevant to the interview."
        ) + " Please answer the current question in the way that is easiest for you."
    if locale == "zh-CN":
        return (
            f"我会通过这个问题整理与{normalized}有关的信息。"
            if normalized
            else "我会通过这个问题整理访谈所需的信息。"
        ) + "请按您方便的方式回答当前问题。"
    if locale == "pt-BR":
        return (
            f"Faço esta pergunta para organizar as informações da entrevista sobre {normalized}."
            if normalized
            else "Faço esta pergunta para organizar as informações relevantes da entrevista."
        ) + " Responda à pergunta atual da forma que for mais fácil para você."
    return (
        f"このインタビューでは、{normalized}に関する情報を整理するために伺っています。"
        if normalized
        else "このインタビューでは、必要な情報を整理するために伺っています。"
    ) + "今の質問について、話しやすいところから教えてください。"


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
    candidate_summary: str,
) -> str:
    """Confirm target-mapped candidate values before they are used as answers."""

    summary = candidate_summary.strip()
    if not summary:
        return localized_interview_transcript_retry(locale)
    return {
        "ja-JP": f"{summary}という理解でよろしいですか？",
        "en-US": f"Just to confirm, {summary}. Is that right?",
        "zh-CN": f"请确认：{summary}，对吗？",
        "pt-BR": f"Só para confirmar: {summary}. Está correto?",
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
