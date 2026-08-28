"""Helpers for high-confidence confirmation replies."""

from __future__ import annotations

import unicodedata


# These are intentionally limited to replies whose meaning is unambiguous when
# the interviewer is explicitly waiting for confirmation. Longer or mixed
# replies continue through the LLM because they may contain a correction.
_EXPLICIT_CONFIRMATIONS = frozenset(
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
)


def is_unambiguous_confirmation(text: str | None) -> bool:
    """Return whether *text* is an unambiguous affirmative acknowledgement.

    This is a narrow state-machine safety check, not a general dialogue
    classifier. It is only called while a candidate is already awaiting
    confirmation. Punctuation and whitespace produced by speech recognition
    are ignored; mixed replies are deliberately left to the LLM.
    """

    normalized = _normalize_confirmation_text(text)
    return bool(normalized and normalized in _EXPLICIT_CONFIRMATIONS)


def _normalize_confirmation_text(text: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or "")).strip()
    return "".join(
        character
        for character in normalized
        if not character.isspace() and character not in "、。,.，．!?！？"
    )
