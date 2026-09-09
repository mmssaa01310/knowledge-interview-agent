from __future__ import annotations

from collections import Counter

from .canonical_intent_cases import CASES


def test_router_evaluation_has_ten_cases_for_each_required_intent() -> None:
    counts = Counter(case.expected_intent for case in CASES)
    assert len(CASES) == 100
    assert set(counts) == {
        "ANSWER",
        "CONFIRMATION",
        "REJECTION",
        "CLARIFICATION_REQUEST",
        "QUESTION_TO_ASSISTANT",
        "CORRECTION",
        "HESITATION",
        "BACKCHANNEL",
        "IRRELEVANT",
        "CONVERSATION_REQUEST",
    }
    assert all(count == 10 for count in counts.values())


def test_router_evaluation_contains_context_sensitive_yes_cases() -> None:
    yes_cases = [case for case in CASES if case.utterance == "はい。"]
    assert {case.expected_intent for case in yes_cases} == {
        "CONFIRMATION",
        "BACKCHANNEL",
    }
    assert len(yes_cases) >= 2
