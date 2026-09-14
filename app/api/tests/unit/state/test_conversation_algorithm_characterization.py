from __future__ import annotations

import json

import pytest

from ai_interviewer_api.agents.interview_knowledge.schemas import StructuredDialogueAct
from ai_interviewer_api.services.conversation_policy import (
    CanonicalIntentOutput,
    resolve_canonical_action,
    resolve_canonical_intent,
)

from .conversation_algorithm_harness import (
    ConversationAlgorithmHarness,
    clear_characterization_store,
    output_for,
)


@pytest.fixture(autouse=True)
def clear_store() -> None:
    clear_characterization_store()


def test_current_algorithm_emits_a_comparable_provider_independent_trace() -> None:
    harness = ConversationAlgorithmHarness()
    trace = harness.run_turn(
        output_for(
            "宮崎です。スマート技術開発部でエンジニアをしています。",
            field_id="field-profile",
            item_values=(
                ("name", "宮崎"),
                ("department", "スマート技術開発部"),
                ("role_or_responsibility", "エンジニア"),
            ),
            message_id="message-normal-001",
        )
    )

    assert trace["routerIntent"] == "N/A"
    assert trace["fastDecision"] == "N/A"
    assert trace["structuredDialogueAct"] == "ANSWER"
    assert trace["currentTarget"]["targetId"] == "field-profile"
    assert trace["nextTarget"]["targetId"] == "field-work"
    assert trace["generatedQuestion"] == "現在の担当業務を教えてください。"
    assert trace["questionDefinitionSeenByGenerator"]["originalQuestion"] == (
        "現在の担当業務を教えてください。"
    )
    json.dumps(trace, ensure_ascii=False)


def test_confirmation_is_observable_as_a_state_transition() -> None:
    harness = ConversationAlgorithmHarness()
    harness.set_confirmation_pending(candidate="宮崎")

    trace = harness.run_turn(
        output_for("大丈夫です。", message_id="message-confirmation-001")
    )

    assert trace["structuredDialogueAct"] == "CONFIRMATION"
    assert trace["stateBefore"]["fieldStates"]["field-profile"]["answerState"] == (
        "AWAITING_CONFIRMATION"
    )
    assert trace["stateAfter"]["fieldStates"]["field-profile"]["answerState"] == "CONFIRMED"
    assert trace["nextTarget"]["targetId"] == "field-work"


@pytest.mark.parametrize(
    ("utterance", "dialogue_act", "expected_action"),
    [
        ("担当領域ってどの範囲のこと？", "QUESTION_TO_ASSISTANT", "ask_follow_up"),
        ("違います。", "REJECTION", "ask_structured"),
        ("えーっと……", "HESITATION", "ask_follow_up"),
        ("訂正します。開発部です。", "CORRECTION", "ask_structured"),
    ],
)
def test_current_dialogue_acts_are_recorded_without_a_new_router(
    utterance: str,
    dialogue_act: str,
    expected_action: str,
) -> None:
    harness = ConversationAlgorithmHarness()
    if dialogue_act == "REJECTION":
        harness.set_confirmation_pending()
    trace = harness.run_turn(
        output_for(
            utterance,
            dialogue_act=dialogue_act,
            field_id="field-profile" if dialogue_act == "CORRECTION" else None,
            message_id=f"message-{dialogue_act.lower()}",
        )
    )

    assert trace["routerIntent"] == "N/A"
    assert trace["structuredDialogueAct"] == dialogue_act
    assert trace["action"] == expected_action
    assert trace["stateAfter"]["nextQuestionTarget"]["targetId"] == "field-profile"


def test_voice_source_item_reuses_one_canonical_turn() -> None:
    harness = ConversationAlgorithmHarness()

    trace = harness.characterize_duplicate_identity()

    assert trace["sameCanonicalTurn"] is True
    assert trace["storedTurnCount"] == 1
    assert trace["sourceItemId"] == "realtime-item-001"


class DeterministicIntentProvider:
    """Small provider double for the canonical intent contract tests."""

    def __init__(self, dialogue_act: StructuredDialogueAct) -> None:
        self.dialogue_act = dialogue_act
        self.contexts: list[dict] = []

    def classify(self, *, context: dict) -> CanonicalIntentOutput:
        self.contexts.append(context)
        return CanonicalIntentOutput(dialogueAct=self.dialogue_act)


@pytest.mark.parametrize(
    ("utterance", "dialogue_act", "expected_action", "pending_confirmation"),
    [
        ("大丈夫です。", "CONFIRMATION", "CONFIRM_PENDING_CANDIDATE", True),
        ("違います。", "REJECTION", "REJECT_PENDING_CANDIDATE", True),
        (
            "担当領域ってどの範囲のこと？",
            "CLARIFICATION_REQUEST",
            "EXPLAIN_CURRENT_QUESTION",
            False,
        ),
        ("えーっと……", "HESITATION", "KEEP_CURRENT_QUESTION", False),
        (
            "宮崎です。スマート技術開発部でエンジニアをしています。",
            "ANSWER",
            "PROCESS_ANSWER",
            False,
        ),
    ],
)
def test_canonical_intent_and_action_do_not_depend_on_fast_path_setting(
    utterance: str,
    dialogue_act: StructuredDialogueAct,
    expected_action: str,
    pending_confirmation: bool,
) -> None:
    # Fast Path is a downstream ANSWER-only gate. It must not be another
    # classifier or alter the canonical intent/action selected by this policy.
    provider = DeterministicIntentProvider(dialogue_act)
    decision = resolve_canonical_intent(
        utterance=utterance,
        current_question={
            "questionId": "q-profile",
            "text": "お名前、所属部署、現在の役職または担当領域を教えてください。",
            "targetId": "field-profile",
            "targetLabel": "基本プロフィール",
        },
        current_target={"targetType": "field", "targetId": "field-profile"},
        pending_confirmation=pending_confirmation,
        recent_conversation=(),
        provider=provider,
    )
    action = resolve_canonical_action(
        decision.dialogueAct,
        pending_confirmation=pending_confirmation,
    )

    assert decision.dialogueAct == dialogue_act
    assert action == expected_action
    assert provider.contexts[0]["latestUserUtterance"] == utterance
    assert "fullInterviewState" not in provider.contexts[0]
    assert "rag" not in provider.contexts[0]


def test_canonical_action_policy_does_not_mutate_inputs() -> None:
    current_target = {"targetType": "field", "targetId": "field-profile"}

    action = resolve_canonical_action(
        "CONFIRMATION",
        current_target=current_target,
        pending_confirmation=True,
    )

    assert action == "CONFIRM_PENDING_CANDIDATE"
    assert current_target == {"targetType": "field", "targetId": "field-profile"}
