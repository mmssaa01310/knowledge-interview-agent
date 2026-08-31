"""Common answer-resolution contract shared by text and voice interviews."""

from __future__ import annotations

from typing import Literal


AnswerResolution = Literal[
    "AUTO_CONFIRM",
    "TENTATIVE",
    "RETRY",
    "CONFIRM_REQUIRED",
]


def normalize_answer_resolution(value: object) -> AnswerResolution | None:
    """Return a supported resolution without making a semantic decision.

    The evaluator owns the semantic judgement. This helper only protects the
    state machine from malformed or legacy values.
    """

    if value in {"AUTO_CONFIRM", "TENTATIVE", "RETRY", "CONFIRM_REQUIRED"}:
        return value  # type: ignore[return-value]
    return None
