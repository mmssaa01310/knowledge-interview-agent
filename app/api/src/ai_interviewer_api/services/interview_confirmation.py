"""Helpers for high-confidence confirmation replies."""

from __future__ import annotations

import unicodedata

from ai_interviewer_api.core.interview_locale import (
    DEFAULT_INTERVIEW_LOCALE,
    InterviewLocale,
)

# These are intentionally limited to replies whose meaning is unambiguous when
# the interviewer is explicitly waiting for confirmation. Longer or mixed
# replies continue through the LLM because they may contain a correction.
_EXPLICIT_CONFIRMATIONS_BY_LOCALE: dict[InterviewLocale, frozenset[str]] = {
    "ja-JP": frozenset(
        {
            "はい",
            "はいそうです",
            "はい大丈夫",
            "はい大丈夫です",
            "はい合っています",
            "はい合ってます",
            "はい正しいです",
            "はいその通りです",
            "はいそのとおりです",
            "そうです",
            "大丈夫",
            "大丈夫です",
            "問題ありません",
            "問題ないです",
            "合っています",
            "合ってます",
            "正しいです",
            "その通りです",
            "そのとおりです",
            "うん",
            "うんそう",
            "うんそうです",
        }
    ),
    "en-US": frozenset(
        {
            "yes",
            "yep",
            "yeah",
            "yup",
            "ok",
            "okay",
            "correct",
            "right",
            "exactly",
            "sure",
            "absolutely",
            "yescorrect",
            "yesitscorrect",
            "yesitiscorrect",
            "yesthatscorrect",
            "yesthatiscorrect",
            "yesright",
            "yesitsright",
            "yesitisright",
            "yesthatsright",
            "yesthatisright",
            "thatscorrect",
            "thatiscorrect",
            "thatsright",
            "thatisright",
            "yesitis",
        }
    ),
    "zh-CN": frozenset(
        {
            "是",
            "是的",
            "对",
            "对的",
            "正确",
            "没错",
            "没问题",
            "可以",
            "好的",
        }
    ),
    "pt-BR": frozenset(
        {
            "sim",
            "correto",
            "correta",
            "certo",
            "certa",
            "isso",
            "issomesmo",
            "exatamente",
            "estacorreto",
            "estacorreta",
            "estacerto",
            "simcorreto",
            "simcorreta",
            "simestacorreto",
            "simestacorreta",
            "simestacerto",
        }
    ),
}


def is_unambiguous_confirmation(
    text: str | None,
    *,
    locale: InterviewLocale = DEFAULT_INTERVIEW_LOCALE,
) -> bool:
    """Return whether *text* is an unambiguous affirmative acknowledgement.

    This is a narrow state-machine safety check, not a general dialogue
    classifier. It is only called while a candidate is already awaiting
    confirmation. Punctuation, case, and whitespace produced by speech
    recognition are ignored; mixed replies are deliberately left to the LLM.
    """

    normalized = _normalize_confirmation_text(text)
    confirmations = _EXPLICIT_CONFIRMATIONS_BY_LOCALE.get(
        locale,
        _EXPLICIT_CONFIRMATIONS_BY_LOCALE[DEFAULT_INTERVIEW_LOCALE],
    )
    return bool(normalized and normalized in confirmations)


def _normalize_confirmation_text(text: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or "")).casefold().strip()
    return "".join(
        character
        for character in normalized
        if not character.isspace() and character not in "、。,.，．!?！？'’-"
    )
