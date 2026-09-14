from __future__ import annotations

from copy import deepcopy

import pytest

from ai_interviewer_api.agents.interview_knowledge.coordinator import (
    build_initial_structured_state,
)
from ai_interviewer_api.agents.interview_knowledge.schemas import (
    AnswerAssessment,
    FieldUpdate,
    StructuredInterviewOutput,
    TranscriptAssessment,
)
from ai_interviewer_api.auth.deps import DEV_TOKENS
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.services.interview_state_transition import (
    apply_background_state_proposal,
)


@pytest.fixture(autouse=True)
def clear_store() -> None:
    store.tables.clear()


def _seed_state() -> tuple[object, dict, list[dict]]:
    user = DEV_TOKENS["dev-interviewer"]
    fields = [
        {
            "id": "field-1",
            "label": "担当業務",
            "required": True,
            "description": "担当している業務",
        },
        {
            "id": "field-2",
            "label": "利用者",
            "required": True,
            "description": "利用者",
        },
    ]
    state = build_initial_structured_state("fixed_form", fields)
    state.update(
        {
            "id": "interview-state-record-transition-1",
            "tenantId": user.tenant_id,
            "recordId": "record-transition-1",
            "createdByUserId": user.user_id,
            "updatedByUserId": user.user_id,
            "currentFieldId": "field-2",
            "currentQuestionId": "q-002",
            "nextQuestionTarget": {
                "targetType": "field",
                "targetId": "field-2",
                "label": "利用者",
            },
            "askedQuestions": [
                {
                    "questionId": "q-001",
                    "targetType": "field",
                    "targetId": "field-1",
                    "fieldId": "field-1",
                    "text": "担当業務を教えてください。",
                },
                {
                    "questionId": "q-002",
                    "targetType": "field",
                    "targetId": "field-2",
                    "fieldId": "field-2",
                    "text": "利用者を教えてください。",
                },
            ],
            "stateVersion": 11,
            "lastCanonicalIntent": "ANSWER",
            "lastCanonicalAction": "PROCESS_ANSWER",
            "lastProcessedUserMessageId": None,
        }
    )
    store.upsert("interview_states", deepcopy(state))
    return user, state, fields


def _proposal(
    *,
    output: StructuredInterviewOutput,
    base_version: int = 10,
    source_turn_id: str = "turn-transition-1",
    source_message_id: str = "message-transition-1",
    source_turn_sequence: int | None = None,
    valid_evidence_ids: list[str] | None = None,
) -> dict:
    return {
        "sourceTurnId": source_turn_id,
        "sourceMessageId": source_message_id,
        "sourceTurnSequence": source_turn_sequence,
        "baseStateVersion": base_version,
        "sourceQuestion": {
            "questionId": "q-001",
            "targetType": "field",
            "targetId": "field-1",
            "fieldId": "field-1",
            "targetLabel": "担当業務",
            "text": "担当業務を教えてください。",
        },
        "rawTranscript": "社内システムの開発です。",
        "structuredOutput": output.model_dump(),
        "validEvidenceIds": valid_evidence_ids or [source_message_id],
        "clarificationProposal": None,
        "proposalTopics": ["fieldUpdates", "clarificationProposal"],
    }


def test_stale_background_result_merges_extraction_without_rolling_back_question() -> None:
    user, _, fields = _seed_state()
    output = StructuredInterviewOutput(
        transcriptAssessment=TranscriptAssessment(
            rawTranscript="社内システムの開発です。",
            normalizedTranscript="社内システムの開発です。",
        ),
        answerAssessment=AnswerAssessment(sufficiency="SUFFICIENT"),
        fieldUpdates=[
            FieldUpdate(
                fieldId="field-1",
                value="社内システムの開発",
                evidenceTranscriptIds=["message-transition-1"],
                answerResolution="AUTO_CONFIRM",
            )
        ],
    )

    result = apply_background_state_proposal(
        record_id="record-transition-1",
        user=user,
        proposal=_proposal(output=output),
        fields=fields,
        profile="fixed_form",
        source_question=_proposal(output=output)["sourceQuestion"],
    )

    assert result.decision == "stale_safe_merge"
    stored = store.get("interview_states", "interview-state-record-transition-1")
    assert stored is not None
    assert stored["currentQuestionId"] == "q-002"
    assert stored["nextQuestionTarget"]["targetId"] == "field-2"
    assert stored["fieldStates"]["field-1"]["answerState"] == "CONFIRMED"
    assert stored["lastProcessedUserMessageId"] == "message-transition-1"
    assert stored["stateVersion"] == 11
    assert stored["analysisVersion"] == 1
    assert result.resulting_state_version == 11
    assert result.resulting_analysis_version == 1
    assert "currentQuestionId" in result.discarded_fields
    assert "field:field-1" in result.applied_fields


def test_background_confirmation_proposal_cannot_apply_confirmation_or_target_change() -> None:
    user, state, fields = _seed_state()
    state["pendingTranscriptConfirmation"] = {
        "messageId": "previous-message",
        "targetRefs": [{"targetType": "field", "targetId": "field-1"}],
    }
    store.upsert("interview_states", deepcopy(state))
    output = StructuredInterviewOutput(
        dialogueAct="CONFIRMATION",
        transcriptAssessment=TranscriptAssessment(
            rawTranscript="大丈夫です。",
            normalizedTranscript="大丈夫です。",
        ),
        answerAssessment=AnswerAssessment(sufficiency="SUFFICIENT"),
    )

    result = apply_background_state_proposal(
        record_id="record-transition-1",
        user=user,
        proposal=_proposal(output=output),
        fields=fields,
        profile="fixed_form",
        source_question=_proposal(output=output)["sourceQuestion"],
    )

    assert result.decision == "stale_safe_merge"
    stored = store.get("interview_states", "interview-state-record-transition-1")
    assert stored is not None
    assert stored["currentQuestionId"] == "q-002"
    assert stored["stateVersion"] == 11
    assert stored["analysisVersion"] == 1
    assert stored["nextQuestionTarget"]["targetId"] == "field-2"
    assert stored["lastCanonicalIntent"] == "ANSWER"
    assert stored["lastCanonicalAction"] == "PROCESS_ANSWER"
    assert stored["fieldStates"]["field-1"]["answerState"] != "CONFIRMED"


def test_stale_background_clarification_is_queued_without_changing_current_question() -> None:
    user, _, fields = _seed_state()
    output = StructuredInterviewOutput(
        transcriptAssessment=TranscriptAssessment(
            rawTranscript="回答です。",
            normalizedTranscript="回答です。",
        ),
        answerAssessment=AnswerAssessment(sufficiency="SUFFICIENT"),
    )
    proposal = _proposal(output=output)
    proposal["clarificationProposal"] = {
        "requestId": "clarification-turn-transition-1-field-field-1",
        "sourceTurnId": "turn-transition-1",
        "sourceMessageId": "message-transition-1",
        "sourceTarget": proposal["sourceQuestion"],
        "reason": "回答候補の確認が必要",
        "priority": 2,
    }

    result = apply_background_state_proposal(
        record_id="record-transition-1",
        user=user,
        proposal=proposal,
        fields=fields,
        profile="fixed_form",
        source_question=proposal["sourceQuestion"],
    )

    assert result.clarification_enqueued is True
    stored = store.get("interview_states", "interview-state-record-transition-1")
    assert stored is not None
    assert stored["currentQuestionId"] == "q-002"
    assert stored["stateVersion"] == 11
    assert stored["analysisVersion"] == 1
    assert stored["clarificationQueue"][0]["requestId"] == (
        "clarification-turn-transition-1-field-field-1"
    )


def test_late_background_result_preserves_newer_processed_message_and_is_idempotent() -> None:
    user, state, fields = _seed_state()
    state["lastProcessedUserMessageId"] = "message-transition-2"
    state["lastStructuredDialogueAct"] = "ANSWER"
    store.upsert("interview_states", deepcopy(state))
    output = StructuredInterviewOutput(
        transcriptAssessment=TranscriptAssessment(
            rawTranscript="社内システムの開発です。",
            normalizedTranscript="社内システムの開発です。",
        ),
        answerAssessment=AnswerAssessment(sufficiency="SUFFICIENT"),
        fieldUpdates=[
            FieldUpdate(
                fieldId="field-1",
                value="社内システムの開発",
                evidenceTranscriptIds=["message-transition-1"],
                answerResolution="AUTO_CONFIRM",
            )
        ],
    )
    proposal = _proposal(output=output)

    result = apply_background_state_proposal(
        record_id="record-transition-1",
        user=user,
        proposal=proposal,
        fields=fields,
        profile="fixed_form",
        source_question=proposal["sourceQuestion"],
    )
    stored = store.get("interview_states", "interview-state-record-transition-1")
    assert stored is not None
    assert result.decision == "stale_safe_merge"
    assert stored["lastProcessedUserMessageId"] == "message-transition-2"
    assert stored["lastStructuredDialogueAct"] == "ANSWER"
    assert "message-transition-1" in stored["backgroundAppliedSourceMessageIds"]

    duplicate = apply_background_state_proposal(
        record_id="record-transition-1",
        user=user,
        proposal=proposal,
        fields=fields,
        profile="fixed_form",
        source_question=proposal["sourceQuestion"],
    )
    assert duplicate.decision == "duplicate_background_proposal"
    unchanged = store.get("interview_states", "interview-state-record-transition-1")
    assert unchanged is not None
    assert unchanged["stateVersion"] == stored["stateVersion"]
    assert unchanged["analysisVersion"] == stored["analysisVersion"]


def test_background_proposals_apply_per_topic_in_source_sequence_order() -> None:
    user, _, fields = _seed_state()

    newer_output = StructuredInterviewOutput(
        transcriptAssessment=TranscriptAssessment(
            rawTranscript="新しい回答",
            normalizedTranscript="新しい回答",
        ),
        answerAssessment=AnswerAssessment(sufficiency="SUFFICIENT"),
        fieldUpdates=[
            FieldUpdate(
                fieldId="field-1",
                itemId="field-1",
                value="新しい回答",
                evidenceTranscriptIds=["message-transition-new"],
                answerResolution="AUTO_CONFIRM",
            )
        ],
    )
    older_output = newer_output.model_copy(
        update={
            "transcriptAssessment": TranscriptAssessment(
                rawTranscript="古い回答",
                normalizedTranscript="古い回答",
            ),
            "fieldUpdates": [
                FieldUpdate(
                    fieldId="field-1",
                    itemId="field-1",
                    value="古い回答",
                    evidenceTranscriptIds=["message-transition-old"],
                    answerResolution="AUTO_CONFIRM",
                )
            ],
        }
    )

    newer = _proposal(
        output=newer_output,
        base_version=11,
        source_turn_id="turn-transition-new",
        source_message_id="message-transition-new",
        source_turn_sequence=11,
    )
    older = _proposal(
        output=older_output,
        base_version=11,
        source_turn_id="turn-transition-old",
        source_message_id="message-transition-old",
        source_turn_sequence=10,
    )

    apply_background_state_proposal(
        record_id="record-transition-1",
        user=user,
        proposal=newer,
        fields=fields,
        profile="fixed_form",
        source_question=newer["sourceQuestion"],
    )
    result = apply_background_state_proposal(
        record_id="record-transition-1",
        user=user,
        proposal=older,
        fields=fields,
        profile="fixed_form",
        source_question=older["sourceQuestion"],
    )

    stored = store.get("interview_states", "interview-state-record-transition-1")
    assert stored is not None
    assert stored["fieldStates"]["field-1"]["recordAnswer"] == "新しい回答"
    assert stored["backgroundAppliedSourceSequences"]["field:field-1:item:field-1"] == 11
    assert "field:field-1" in result.discarded_fields
