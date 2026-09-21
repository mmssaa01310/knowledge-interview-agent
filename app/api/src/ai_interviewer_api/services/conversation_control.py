"""Shared conversation assessment rules for every interview input path.

The Structured Interpreter remains responsible for semantic judgement.  This
module only normalizes that judgement into a small, auditable contract and
applies backend safety precedence where two model fields disagree.  Runtime
providers must pass the same transcript through this contract instead of
reclassifying a semantic follow-up as an audio failure.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ai_interviewer_api.agents.interview_knowledge.schemas import (
    AnswerEvaluation,
    ConversationNextAction,
    StructuredInterviewOutput,
)


_PARTIAL_SUFFICIENCIES = frozenset(
    {
        "PARTIAL",
        "AMBIGUOUS",
        "EXAMPLE_MISSING",
        "REASON_MISSING",
        "CRITERIA_MISSING",
        "INCOMPLETE",
    }
)


def normalize_conversation_output(
    output: StructuredInterviewOutput,
) -> StructuredInterviewOutput:
    """Return an output with a trustworthy, explicit answer evaluation.

    The model owns the semantic decision.  The backend only prevents an
    internally contradictory combination such as ``recognitionStatus=CLEAR``
    with ``answerEvaluation=NOT_HEARD`` or a complete utterance labelled as
    ``INCOMPLETE_UTTERANCE``.
    """

    evaluation = derive_answer_evaluation(output)
    if (
        evaluation == "ANSWERED"
        and output.missingInformation
        and output.answerAssessment.sufficiency == "SUFFICIENT"
    ):
        # ``missingInformation`` is an explicit semantic claim from the
        # interpreter.  Do not let a contradictory SUFFICIENT label advance
        # the interview as if all required aspects had been answered.
        evaluation = "PARTIALLY_ANSWERED"
    recognition = output.transcriptAssessment.recognitionStatus
    if evaluation == "NOT_HEARD" and recognition == "CLEAR":
        assessment = output.transcriptAssessment.model_copy(
            update={
                "recognitionStatus": "AMBIGUOUS",
                "recognitionEvidence": [
                    *output.transcriptAssessment.recognitionEvidence,
                    "correction_or_transcript_uncertain",
                ][:5],
            }
        )
        return output.model_copy(
            update={"answerEvaluation": evaluation, "transcriptAssessment": assessment}
        )
    updates: dict[str, Any] = {"answerEvaluation": evaluation}
    if (
        evaluation == "PARTIALLY_ANSWERED"
        and output.answerAssessment.sufficiency == "SUFFICIENT"
    ):
        updates["answerAssessment"] = output.answerAssessment.model_copy(
            update={"sufficiency": "PARTIAL", "probeType": "CLARIFY"}
        )
    return output.model_copy(update=updates)


def derive_answer_evaluation(output: StructuredInterviewOutput) -> AnswerEvaluation:
    """Map the interpreter's independent judgements to one conversation label."""

    dialogue_act = output.dialogueAct
    if dialogue_act == "CLARIFICATION_REQUEST":
        return "CLARIFICATION_REQUEST"
    if dialogue_act == "QUESTION_TO_ASSISTANT" and not _contains_assistant_proposal(output):
        return "CLARIFICATION_REQUEST"
    if dialogue_act == "REPEAT_REQUEST":
        return "REPEAT_REQUEST"

    recognition = output.transcriptAssessment.recognitionStatus
    if (
        recognition in {"PARTIAL", "MISSING", "AMBIGUOUS"}
        or output.transcriptAssessment.correctionStatus == "UNCERTAIN"
    ):
        return "NOT_HEARD"
    if dialogue_act == "CORRECTION":
        return "CORRECTION"
    if dialogue_act == "IRRELEVANT":
        return "OFF_TARGET"
    if output.utteranceCompleteness == "INCOMPLETE":
        return "INCOMPLETE_UTTERANCE"
    if output.answerAssessment.sufficiency in _PARTIAL_SUFFICIENCIES:
        return "PARTIALLY_ANSWERED"
    return "ANSWERED"


def _contains_assistant_proposal(output: StructuredInterviewOutput) -> bool:
    if any(
        update.candidateSource == "assistant_proposal"
        for update in [*output.fieldUpdates, *output.requirementUpdates]
    ):
        return True
    patch = output.processPatch
    for name in (
        "addParticipants",
        "updateParticipants",
        "addNodes",
        "updateNodes",
        "addEdges",
        "updateEdges",
        "addInteractions",
        "updateInteractions",
    ):
        if any(
            item.candidateSource == "assistant_proposal"
            for item in getattr(patch, name)
        ):
            return True
    return False


def next_action_for_evaluation(
    evaluation: AnswerEvaluation,
    *,
    has_pending_confirmation: bool = False,
    has_missing_information: bool = False,
) -> ConversationNextAction:
    """Return the safe high-level action before target selection is applied."""

    if evaluation == "CLARIFICATION_REQUEST":
        return "EXPLAIN_QUESTION"
    if evaluation == "REPEAT_REQUEST":
        return "REPEAT_CURRENT_QUESTION"
    if evaluation == "NOT_HEARD":
        return "ASK_REPEAT"
    if evaluation == "INCOMPLETE_UTTERANCE":
        return "WAIT_FOR_CONTINUATION"
    if evaluation == "CORRECTION":
        return "APPLY_CORRECTION"
    if evaluation == "PARTIALLY_ANSWERED" or has_missing_information:
        return "ASK_MISSING_INFORMATION"
    if evaluation == "OFF_TARGET":
        return "KEEP_CURRENT_QUESTION"
    if has_pending_confirmation:
        return "FOLLOW_UP"
    return "MOVE_NEXT"


def extracted_update_summary(output: StructuredInterviewOutput) -> list[dict[str, Any]]:
    """Return structured update metadata suitable for a turn trace."""

    extracted: list[dict[str, Any]] = []
    for update in output.fieldUpdates:
        extracted.append(
            {
                "targetType": "field",
                "targetId": update.fieldId,
                "itemId": update.itemId,
                "answerResolution": update.answerResolution,
            }
        )
    for update in output.requirementUpdates:
        extracted.append(
            {
                "targetType": (
                    "requirement"
                    if update.requirementId.startswith("requirement.")
                    else "process"
                ),
                "targetId": update.requirementId,
                "answerResolution": update.answerResolution,
            }
        )
    if output.processPatch.model_dump(exclude_defaults=True):
        extracted.append({"targetType": "process", "targetId": "process_model"})
    extracted.extend(
        {"targetType": "applicability", "targetId": update.topic}
        for update in output.applicability
    )
    return extracted


def llm_input_summary(context: dict[str, Any]) -> dict[str, Any]:
    """Keep trace fields inspectable without logging full prompts or transcripts."""

    latest = context.get("latestUtterance")
    if not isinstance(latest, dict):
        latest = {}
    question = context.get("currentQuestion")
    if not isinstance(question, dict):
        question = {}
    return {
        "currentQuestionId": question.get("questionId"),
        "currentQuestionText": question.get("text") or question.get("questionText"),
        "currentTarget": {
            "targetType": question.get("targetType"),
            "targetId": question.get("targetId"),
        },
        "questionPurpose": context.get("questionPurpose"),
        "requiredAspects": context.get("requiredAspects", []),
        "acquiredInformation": context.get("acquiredInformation", []),
        "missingInformation": context.get("missingInformation", []),
        "previousAnswerEvaluation": context.get("answerEvaluation"),
        "sameQuestionAskedBefore": context.get("sameQuestionAskedBefore", False),
        "latestMessageId": latest.get("messageId"),
        "latestTranscriptChars": len(str(latest.get("rawTranscript") or "")),
        "currentAnswer": {
            "messageId": latest.get("messageId"),
            "transcriptChars": len(str(latest.get("rawTranscript") or "")),
        },
    }


def llm_output_summary(output: StructuredInterviewOutput) -> dict[str, Any]:
    """Return the decision-bearing part of one structured model response."""

    return {
        "dialogueAct": output.dialogueAct,
        "answerEvaluation": output.answerEvaluation,
        "utteranceCompleteness": output.utteranceCompleteness,
        "transcriptRecognitionStatus": output.transcriptAssessment.recognitionStatus,
        "transcriptCorrectionStatus": output.transcriptAssessment.correctionStatus,
        "answerSufficiency": output.answerAssessment.sufficiency,
        "probeType": output.answerAssessment.probeType,
        "missingInformation": list(output.missingInformation),
        "extractedFields": extracted_update_summary(output),
    }


def field_state_summary(state: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Return the state fields needed to audit one conversation decision."""

    if not isinstance(state, Mapping):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for field_id, raw_state in (state.get("fieldStates") or {}).items():
        if not isinstance(raw_state, Mapping):
            continue
        result[str(field_id)] = {
            "answerState": raw_state.get("answerState"),
            "answerResolution": raw_state.get("answerResolution"),
            "missingRequiredItemIds": list(raw_state.get("missingRequiredItemIds") or []),
            "capturedItemIds": list(raw_state.get("capturedItemIds") or []),
        }
    return result


def build_conversation_trace(
    output: StructuredInterviewOutput,
    context: Mapping[str, Any],
    *,
    state: Mapping[str, Any] | None = None,
    runtime: str = "structured_interview",
    has_pending_confirmation: bool = False,
) -> dict[str, Any]:
    """Build the runtime-independent trace for one interpreted transcript."""

    next_action = next_action_for_evaluation(
        output.answerEvaluation,
        has_pending_confirmation=has_pending_confirmation,
        has_missing_information=bool(output.missingInformation),
    )
    transcript = output.transcriptAssessment
    latest_utterance = context.get("latestUtterance")
    latest_utterance = latest_utterance if isinstance(latest_utterance, Mapping) else {}
    raw_transcript = transcript.rawTranscript or str(
        latest_utterance.get("rawTranscript") or ""
    ).strip()
    final_transcript = transcript.normalizedTranscript or raw_transcript
    return {
        "runtime": runtime,
        "rawTranscript": raw_transcript,
        "finalTranscript": final_transcript,
        "dialogueAct": output.dialogueAct,
        "answerEvaluation": output.answerEvaluation,
        "extractedFields": extracted_update_summary(output),
        "fieldStates": field_state_summary(state),
        "missingInformation": list(output.missingInformation),
        "nextAction": next_action,
        "llmInput": llm_input_summary(dict(context)),
        "llmOutput": llm_output_summary(output),
    }
