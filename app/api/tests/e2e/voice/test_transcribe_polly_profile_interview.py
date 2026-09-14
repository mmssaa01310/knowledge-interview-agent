from __future__ import annotations

import asyncio
import json
import sys
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


TRANSCRIPT_FIXTURE = TESTS_ROOT / "fixtures" / "transcripts" / "profile_three_turns.json"


def _has_item(field_state: dict[str, Any], item_id: str) -> bool:
    return any(
        item.get("itemId") == item_id
        for item in field_state.get("candidateItems", [])
        if isinstance(item, dict)
    )


def _item_value(field_state: dict[str, Any], item_id: str) -> str | None:
    for item in field_state.get("candidateItems", []):
        if isinstance(item, dict) and item.get("itemId") == item_id:
            value = item.get("value")
            return str(value) if value is not None else None
    return None


@pytest.mark.e2e
@pytest.mark.parametrize("fast_enabled", [False, True], ids=["fast-off", "fast-on"])
@pytest.mark.parametrize(
    "backchannel_text",
    ["はい", "そうそう"],
    ids=["yes-noise", "sousou-noise"],
)
@async_test
async def test_three_turn_profile_interview_keeps_partial_answers_durable(
    monkeypatch: pytest.MonkeyPatch,
    clean_interview_store: None,
    fast_enabled: bool,
    backchannel_text: str,
) -> None:
    """Run three final Transcribe results through the actual Runtime and API."""

    del clean_interview_store
    transcripts = json.loads(TRANSCRIPT_FIXTURE.read_text(encoding="utf-8"))
    transcripts[1] = {
        **transcripts[1],
        "text": backchannel_text,
        "intent": "BACKCHANNEL",
    }
    intent_by_text = {item["text"]: item["intent"] for item in transcripts}
    provider = install_deterministic_interview_provider(
        monkeypatch,
        intent_by_text=intent_by_text,
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

    failures: list[str] = []
    observed: list[dict[str, Any]] = []
    previous_version = scenario.runtime._state_version
    previous_question = source_question["questionId"]
    state_id = f"interview-state-{record['id']}"
    try:
        for index, fixture_turn in enumerate(transcripts, start=1):
            result_id = str(fixture_turn["resultId"])
            client_turn_id = f"transcribe-{result_id}"
            answer_to_question = previous_question
            await scenario.submit_transcript_turn(
                str(fixture_turn["text"]),
                result_id,
            )
            voice_turn = persisted_voice_turn(user, client_turn_id)
            state = store.get("interview_states", state_id)
            if voice_turn is None or state is None:
                failures.append(f"Turn {index} was dropped before durable persistence")
                observed.append({"turn": index, "persisted": False})
                continue

            field_state = state["fieldStates"][field["id"]]
            turn_observation = {
                "turn": index,
                "clientTurnId": client_turn_id,
                "turnLifecycle": voice_turn.get("lifecycleStatus"),
                "canonicalIntent": voice_turn.get("canonicalIntent"),
                "canonicalAction": voice_turn.get("canonicalAction"),
                "fastCheckExecuted": voice_turn.get("fastCheckExecuted"),
                "stateVersion": state.get("stateVersion"),
                "currentQuestionId": state.get("currentQuestionId"),
                "answerToQuestionId": voice_turn.get("answerToQuestionId"),
                "answerState": field_state.get("answerState"),
                "candidateAnswer": field_state.get("candidateAnswer"),
                "recordAnswer": field_state.get("recordAnswer"),
                "candidateItems": [
                    item.get("itemId") for item in field_state.get("candidateItems", [])
                ],
                "missingRequiredItemIds": field_state.get("missingRequiredItemIds", []),
                "clarificationQueue": state.get("clarificationQueue", []),
                "nextTarget": state.get("nextQuestionTarget"),
            }
            observed.append(turn_observation)

            if voice_turn.get("lifecycleStatus") != "COMMITTED":
                failures.append(f"Turn {index} lifecycle is not COMMITTED")
            if voice_turn.get("answerToQuestionId") != answer_to_question:
                failures.append(f"Turn {index} was associated with a different question")
            if int(state["stateVersion"]) < int(previous_version):
                failures.append(f"Turn {index} decreased the persisted stateVersion")
            if scenario.runtime._state_version != state["stateVersion"]:
                failures.append(
                    f"Turn {index} Runtime/state version mismatch: "
                    f"{scenario.runtime._state_version} != {state['stateVersion']}"
                )
            if voice_turn.get("processingStatus") == "failed":
                failures.append(f"Turn {index} was marked failed by the API")
            if voice_turn.get("fastCheckExecuted") is not False:
                failures.append(
                    "Fast Answer Check ran despite the current fail-closed contract"
                )
            if len(
                [
                    item
                    for item in store.list("voice_turns", user.tenant_id)
                    if item.get("clientTurnId") == client_turn_id
                ]
            ) != 1:
                failures.append(f"Turn {index} did not have exactly one durable VoiceTurn")

            if index == 1:
                if not _has_item(field_state, "role_or_responsibility"):
                    failures.append("Turn 1 did not retain role_or_responsibility")
                if field_state.get("answerState") != "CANDIDATE_PENDING":
                    failures.append("Turn 1 did not keep the partial profile as a candidate")
                if field_state.get("status") != "asking":
                    failures.append("Turn 1 field status is not asking for the missing profile items")
                if _item_value(field_state, "role_or_responsibility") != "プロジェクトリード":
                    failures.append("Turn 1 did not persist the exact role candidate value")
                if not {"name", "department"}.issubset(
                    set(field_state.get("missingRequiredItemIds", []))
                ):
                    failures.append("Turn 1 did not preserve name and department as missing")
                if voice_turn.get("canonicalAction") != "PROCESS_ANSWER":
                    failures.append("Turn 1 did not take the PROCESS_ANSWER action")
                if field_state.get("recordAnswer") is not None:
                    failures.append("Partial Turn 1 unexpectedly replaced recordAnswer")
                # Release an active historical Fast worker only after capturing
                # the foreground commit. The current fail-closed path instead
                # runs this same production merge service deterministically.
                if provider.background_started.is_set():
                    provider.release_background.set()
                    assert await asyncio.to_thread(background_committed.wait, 5)
                else:
                    await asyncio.to_thread(
                        apply_background_proposal,
                        scenario=scenario,
                        source_question=source_question,
                        client_turn_id=result_id,
                        clarification=False,
                    )
                after_background = store.get("interview_states", state_id)
                assert after_background is not None
                if after_background["stateVersion"] != state["stateVersion"]:
                    failures.append("Background analysis advanced conversation stateVersion")
                if not _has_item(
                    after_background["fieldStates"][field["id"]],
                    "role_or_responsibility",
                ):
                    failures.append("Background commit removed the role candidate")
                previous_question = after_background["currentQuestionId"]
                previous_version = after_background["stateVersion"]
            elif index == 2:
                if voice_turn.get("canonicalIntent") != "BACKCHANNEL":
                    failures.append("The short 'はい' turn was not classified as BACKCHANNEL")
                if voice_turn.get("canonicalAction") != "KEEP_CURRENT_QUESTION":
                    failures.append("The short 'はい' turn did not keep the current question")
                if not _has_item(field_state, "role_or_responsibility"):
                    failures.append("Turn 2 removed the previously captured role")
                if field_state.get("candidateAnswer") != observed[0].get("candidateAnswer"):
                    failures.append("Turn 2 changed the previously captured role answer")
                if state["currentQuestionId"] != answer_to_question:
                    failures.append("BACKCHANNEL advanced the current question")
                if not isinstance(state.get("clarificationQueue", []), list):
                    failures.append("Turn 2 corrupted clarificationQueue")
                previous_question = state["currentQuestionId"]
                previous_version = state["stateVersion"]
            else:
                if not _has_item(field_state, "role_or_responsibility"):
                    failures.append("Turn 3 removed the previously captured role")
                if not _has_item(field_state, "department"):
                    failures.append("Turn 3 did not add the department candidate")
                if "name" not in field_state.get("missingRequiredItemIds", []):
                    failures.append("Turn 3 did not leave only the name item unresolved")
                if "department" in field_state.get("missingRequiredItemIds", []):
                    failures.append("Turn 3 left the supplied department unresolved")
                if field_state.get("answerState") != "CANDIDATE_PENDING":
                    failures.append("Turn 3 did not retain the partial profile candidate")
                if field_state.get("status") != "asking":
                    failures.append("Turn 3 field status is not asking for the remaining name")
                if _item_value(field_state, "role_or_responsibility") != "プロジェクトリード":
                    failures.append("Turn 3 changed the previously persisted role value")
                if _item_value(field_state, "department") != "開発部":
                    failures.append("Turn 3 did not persist the exact department candidate value")
                if field_state.get("recordAnswer") is not None:
                    failures.append("Turn 3 unexpectedly finalized an incomplete profile")
                if voice_turn.get("canonicalAction") != "PROCESS_ANSWER":
                    failures.append("Turn 3 did not take the PROCESS_ANSWER action")
                previous_question = state["currentQuestionId"]
                previous_version = state["stateVersion"]

        assert len(observed) == 3, f"Expected three persisted turns, got {observed!r}"
        assert not failures, "\n".join((*failures, f"Turn traces: {observed!r}"))
    finally:
        provider.release_background.set()
        if provider.background_started.is_set():
            await asyncio.to_thread(background_committed.wait, 5)
        await scenario.close()
