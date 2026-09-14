from __future__ import annotations

import asyncio
import json
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any

import pytest

TESTS_ROOT = Path(__file__).resolve().parents[2]
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from ai_interviewer_api.agents.interview_knowledge.fast_interpreter.provider import (  # noqa: E402
    BedrockFastInterpreterProvider,
)
from ai_interviewer_api.agents.interview_knowledge.fast_interpreter.schemas import (  # noqa: E402
    FastAnswerAssessment,
)
from ai_interviewer_api.repositories.store import store  # noqa: E402
from ai_interviewer_api.services import voice_interview  # noqa: E402
from support.voice_scenario import (  # noqa: E402
    async_test,
    apply_background_proposal,
    create_profile_voice_session,
    install_deterministic_interview_provider,
    persisted_voice_turn,
    seed_fast_path_regression_state,
    start_runtime_scenario,
)


FIXTURES = TESTS_ROOT / "fixtures"


def _seeded_state_fixture() -> dict[str, Any]:
    return json.loads(
        (FIXTURES / "interview_states" / "fast_path_v7.json").read_text(
            encoding="utf-8"
        )
    )


def _field_has_item(field_state: dict[str, Any], item_id: str) -> bool:
    return any(
        item.get("itemId") == item_id
        for item in field_state.get("candidateItems", [])
        if isinstance(item, dict)
    )


@pytest.mark.integration
@pytest.mark.parametrize("fast_enabled", [False, True], ids=["fast-off", "fast-on"])
@async_test
async def test_runtime_background_commit_does_not_lose_the_next_turn(
    monkeypatch: pytest.MonkeyPatch,
    clean_interview_store: None,
    fast_enabled: bool,
) -> None:
    """Exercise Runtime -> internal Voice API -> real State Transition/persistence."""

    del clean_interview_store
    provider = install_deterministic_interview_provider(
        monkeypatch,
        intent_by_text={"はい": "BACKCHANNEL"},
        block_background=fast_enabled,
    )
    monkeypatch.setattr(
        voice_interview,
        "settings",
        replace(
            voice_interview.settings,
            structured_interview_fast_path_enabled=fast_enabled,
        ),
    )
    monkeypatch.setattr(
        BedrockFastInterpreterProvider,
        "assess",
        lambda _self, *, context: FastAnswerAssessment(
            minimumInformationPresent=True,
            understandable=True,
            clearlyIncomplete=False,
        ),
    )

    background_committed = Event()
    original_background_handler = voice_interview._handle_fast_background_validation

    def track_background_commit(*args: Any, **kwargs: Any) -> None:
        try:
            original_background_handler(*args, **kwargs)
        finally:
            background_committed.set()

    monkeypatch.setattr(
        voice_interview,
        "_handle_fast_background_validation",
        track_background_commit,
    )

    user, record, knowledge, field, session = create_profile_voice_session(
        extra_required_field=True,
    )
    seeded = _seeded_state_fixture()
    source_question = seed_fast_path_regression_state(
        record=record,
        field=field,
        voice_session=session,
        state_version=int(seeded["stateVersion"]),
        question_id=str(seeded["currentQuestionId"]),
    )

    question_observations: list[dict[str, Any]] = []
    original_generate_question = provider.generate_question

    def observe_question_generation(*, target: dict[str, Any], **kwargs: Any) -> Any:
        current = store.get("interview_states", f"interview-state-{record['id']}") or {}
        field_state = current.get("fieldStates", {}).get(field["id"], {})
        question_observations.append(
            {
                "targetId": target.get("targetId"),
                "questionIdBefore": current.get("currentQuestionId"),
                "rolePersistedBeforeQuestion": _field_has_item(
                    field_state,
                    "role_or_responsibility",
                ),
            }
        )
        return original_generate_question(target=target, **kwargs)

    monkeypatch.setattr(provider, "generate_question", observe_question_generation)
    scenario = await start_runtime_scenario(
        user=user,
        record=record,
        knowledge=knowledge,
        field=field,
        voice_session=session,
        structured_provider=provider,
    )
    failures: list[str] = []
    try:
        await scenario.submit_transcript_turn("プロジェクトリードです", "critical-turn-1")
        first_turn = persisted_voice_turn(user, "transcribe-critical-turn-1")
        after_foreground = store.get("interview_states", f"interview-state-{record['id']}")
        if first_turn is None or after_foreground is None:
            raise AssertionError("Turn 1 did not reach durable VoiceTurn and Interview State")

        field_after_foreground = after_foreground["fieldStates"][field["id"]]
        role_saved_before_background = _field_has_item(
            field_after_foreground,
            "role_or_responsibility",
        )
        progressed_before_answer = any(
            item["targetId"] != field["id"] and not item["rolePersistedBeforeQuestion"]
            for item in question_observations
        )

        # In the fixed implementation Fast ON is deliberately fail-closed, so
        # no speculative worker is started. On the historical active path this
        # event is raised by the real Background Structured worker and held
        # until the foreground snapshot above has been observed.
        if provider.background_started.is_set():
            provider.release_background.set()
            assert await asyncio.to_thread(background_committed.wait, 5)
            background_result = None  # real Fast/Background path already merged it
        else:
            background_result = await asyncio.to_thread(
                apply_background_proposal,
                scenario=scenario,
                source_question=source_question,
                client_turn_id="critical-turn-1",
                clarification=True,
            )

        after_background = store.get("interview_states", f"interview-state-{record['id']}")
        assert after_background is not None
        clarification_queue = after_background.get("clarificationQueue") or []
        if not clarification_queue and background_result is not None:
            failures.append("Background clarification was not durably enqueued")
        if background_result is not None and after_background["stateVersion"] != after_foreground[
            "stateVersion"
        ]:
            failures.append(
                "Background clarification changed conversation stateVersion "
                f"{after_foreground['stateVersion']} -> {after_background['stateVersion']}"
            )
        if scenario.runtime._state_version != after_background["stateVersion"]:
            failures.append(
                "Runtime snapshot became stale after Background merge: "
                f"runtime={scenario.runtime._state_version}, persisted={after_background['stateVersion']}"
            )

        current_question_after_background = after_background["currentQuestionId"]
        await scenario.submit_transcript_turn("はい", "critical-turn-2")
        second_turn = persisted_voice_turn(user, "transcribe-critical-turn-2")
        final_state = store.get("interview_states", f"interview-state-{record['id']}")
        if second_turn is None or second_turn.get("lifecycleStatus") != "COMMITTED":
            failures.append("Runtime dropped Turn 2 instead of committing the BACKCHANNEL")
        if second_turn is not None and second_turn.get("canonicalIntent") != "BACKCHANNEL":
            failures.append(
                f"Turn 2 intent was {second_turn.get('canonicalIntent')!r}, expected BACKCHANNEL"
            )
        if final_state is None:
            failures.append("Interview State disappeared after Turn 2")
        else:
            final_field_state = final_state["fieldStates"][field["id"]]
            if not _field_has_item(final_field_state, "role_or_responsibility"):
                failures.append("Turn 1 role answer was lost from candidateItems")
            if final_field_state.get("candidateAnswer") is None and final_field_state.get(
                "recordAnswer"
            ) is None:
                failures.append("Turn 1 role answer is absent from both candidate and record answer")
            if second_turn is not None and second_turn.get("answerToQuestionId") != (
                current_question_after_background
            ):
                failures.append("Turn 2 was associated with a different current question")
            if final_state["currentQuestionId"] != current_question_after_background:
                failures.append("BACKCHANNEL unexpectedly advanced the current question")
            if not final_state.get("clarificationQueue"):
                failures.append("Clarification queue was lost while processing Turn 2")

        if not role_saved_before_background:
            failures.append("Turn 1 answer was not committed before Background completion")
        if progressed_before_answer:
            failures.append("Question generation/progression ran before its answer was persisted")
        if first_turn.get("processingStatus") != "completed":
            failures.append("Turn 1 did not complete through the Voice Interview API")
        if fast_enabled and first_turn.get("fastCheckExecuted"):
            # This codebase intentionally defers the unsafe Fast Path until its
            # answer commit shares the ordinary transition boundary.
            failures.append("Fast Path executed despite the fail-closed production contract")

        assert not failures, "\n".join(failures)
    finally:
        provider.release_background.set()
        if provider.background_started.is_set():
            await asyncio.to_thread(background_committed.wait, 5)
        await scenario.close()


@pytest.mark.integration
@pytest.mark.parametrize("fast_enabled", [False, True], ids=["fast-off", "fast-on"])
@async_test
async def test_runtime_retries_same_question_conflict_and_commits_turn(
    monkeypatch: pytest.MonkeyPatch,
    clean_interview_store: None,
    fast_enabled: bool,
) -> None:
    del clean_interview_store
    provider = install_deterministic_interview_provider(monkeypatch)
    monkeypatch.setattr(
        voice_interview,
        "settings",
        replace(
            voice_interview.settings,
            structured_interview_fast_path_enabled=fast_enabled,
        ),
    )
    user, record, knowledge, field, session = create_profile_voice_session()
    source_question = seed_fast_path_regression_state(
        record=record,
        field=field,
        voice_session=session,
    )
    scenario = await start_runtime_scenario(
        user=user,
        record=record,
        knowledge=knowledge,
        field=field,
        voice_session=session,
        structured_provider=provider,
    )
    try:
        expected_version = scenario.runtime._state_version
        # Simulate an intervening real state commit after the Runtime snapshot.
        from ai_interviewer_api.services.interview_state_transition import (
            commit_interview_state,
        )

        state_id = f"interview-state-{record['id']}"
        concurrent_state = deepcopy(store.get("interview_states", state_id))
        commit_interview_state(
            concurrent_state,
            user,
            source="integration_concurrent_state_version_change",
        )
        assert store.get("interview_states", state_id)["currentQuestionId"] == source_question[
            "questionId"
        ]

        await scenario.submit_transcript_turn("プロジェクトリードです", "conflict-retry-1")
        turn = persisted_voice_turn(user, "transcribe-conflict-retry-1")
        state = store.get("interview_states", state_id)
        assert turn is not None
        assert turn["lifecycleStatus"] == "COMMITTED"
        assert turn["expectedStateVersion"] >= expected_version + 1
        assert state is not None
        assert _field_has_item(state["fieldStates"][field["id"]], "role_or_responsibility")
        assert len(
            [
                item
                for item in store.list("voice_turns", user.tenant_id)
                if item.get("clientTurnId") == "transcribe-conflict-retry-1"
            ]
        ) == 1
    finally:
        await scenario.close()
