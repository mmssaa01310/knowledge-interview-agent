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
    AnswerStatus,
    ConversationNextAction,
    ProcessPatch,
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
    with ``answerEvaluation=NOT_HEARD``.  It does not infer utterance
    completeness from transcript spelling or punctuation.
    """

    output = _normalize_aspect_coverage(output)
    if output.userIntent in {
        "QUESTION_CLARIFICATION",
        "QUESTION_INTENT_CONFIRMATION",
        "ASR_CLARIFICATION_REQUEST",
        "META_QUESTION",
    }:
        return output.model_copy(
            update={
                "answerEvaluation": "CLARIFICATION_REQUEST",
                "answerStatus": "NOT_ANSWERED",
                "clarificationKind": (
                    "ASR_TARGET"
                    if output.userIntent == "ASR_CLARIFICATION_REQUEST"
                    else "QUESTION_MEANING"
                ),
                "probeDecision": "NONE",
                "probeDimension": None,
                "probeReason": None,
                "probeQuestion": None,
                "fieldUpdates": [],
                "requirementUpdates": [],
                "processPatch": ProcessPatch(),
                "applicability": [],
                "contradictions": [],
                "resolvedContradictionIds": [],
                "openIssues": [],
            }
        )
    evaluation = derive_answer_evaluation(output)
    if (
        evaluation == "ANSWERED"
        and output.missingInformation
        and output.answerAssessment.sufficiency == "SUFFICIENT"
        and "answerStatus" not in output.model_fields_set
    ):
        # ``missingInformation`` is an explicit semantic claim from the
        # interpreter.  Do not let a contradictory SUFFICIENT label advance
        # the interview as if all required aspects had been answered.
        evaluation = "PARTIALLY_ANSWERED"
    if evaluation == "NOT_HEARD" and not _has_concrete_asr_target(output):
        # An audio retry is only useful when the system can name the exact
        # recognition target.  Do not manufacture an AMBIGUOUS status or ask
        # the participant to repeat an unspecified "unclear part".  Keep
        # the question active as a semantic follow-up instead.
        evaluation = "PARTIALLY_ANSWERED"
    explicit_answer_status = "answerStatus" in output.model_fields_set
    explicit_probe_decision = "probeDecision" in output.model_fields_set
    answer_status = (
        output.answerStatus
        if explicit_answer_status
        else derive_answer_status(output, evaluation=evaluation)
    )
    probe_decision = (
        output.probeDecision
        if explicit_probe_decision
        else derive_probe_decision(output, answer_status=answer_status)
    )
    if evaluation in {
        "NOT_HEARD",
        "CLARIFICATION_REQUEST",
        "REPEAT_REQUEST",
        "OFF_TARGET",
        "INCOMPLETE_UTTERANCE",
    }:
        answer_status = "NOT_ANSWERED"
    if answer_status != "ANSWERED":
        # A clarification/retry/partial turn is not a valuable probe. This is
        # a backend safety rule even when a provider returns contradictory
        # fields.
        probe_decision = "NONE"
    updates: dict[str, Any] = {
        "answerEvaluation": evaluation,
        "answerStatus": answer_status,
        "clarificationKind": (
            output.clarificationKind
            if evaluation == "CLARIFICATION_REQUEST"
            else "NONE"
        ),
        "probeDecision": probe_decision,
    }
    if probe_decision != "PROBE":
        updates.update(
            {
                "probeDimension": None,
                "probeReason": None,
                "probeQuestion": None,
            }
        )
    if (
        evaluation == "PARTIALLY_ANSWERED"
        and output.answerAssessment.sufficiency == "SUFFICIENT"
    ):
        updates["answerAssessment"] = output.answerAssessment.model_copy(
            update={"sufficiency": "PARTIAL", "probeType": "CLARIFY"}
        )
    return output.model_copy(update=updates)


def derive_answer_status(
    output: StructuredInterviewOutput,
    *,
    evaluation: AnswerEvaluation | None = None,
) -> AnswerStatus:
    """Classify whether the current basic answer is usable.

    This deliberately does not ask whether another valuable fact exists. That
    independent decision is ``probeDecision``.
    """

    effective_evaluation = evaluation or output.answerEvaluation
    if effective_evaluation in {
        "NOT_HEARD",
        "CLARIFICATION_REQUEST",
        "QUESTION_TO_ASSISTANT",
        "REPEAT_REQUEST",
        "OFF_TARGET",
        "INCOMPLETE_UTTERANCE",
    }:
        return "NOT_ANSWERED"
    if effective_evaluation == "PARTIALLY_ANSWERED" or output.answerAssessment.sufficiency in {
        "PARTIAL",
        "AMBIGUOUS",
        "EXAMPLE_MISSING",
        "REASON_MISSING",
        "CRITERIA_MISSING",
        "INCOMPLETE",
        "UNANSWERABLE",
        "REFUSAL",
    }:
        return "PARTIAL"
    return "ANSWERED"


def derive_probe_decision(
    output: StructuredInterviewOutput,
    *,
    answer_status: AnswerStatus,
) -> str:
    """Preserve legacy optional deepening while keeping new decisions explicit."""

    if answer_status != "ANSWERED":
        return "NONE"
    if output.probeDecision == "PROBE":
        return "PROBE"
    # Older providers used EXAMPLE_MISSING for the one optional deepening.
    # Keep that compatibility path; new providers should return explicit
    # answerStatus=ANSWERED and probeDecision=PROBE instead.
    if output.answerAssessment.sufficiency == "EXAMPLE_MISSING":
        return "PROBE"
    return "NONE"


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
    if recognition in {"PARTIAL", "MISSING"}:
        return "NOT_HEARD"
    if recognition == "AMBIGUOUS" and _has_concrete_asr_target(output):
        return "NOT_HEARD"
    if (
        output.transcriptAssessment.correctionStatus == "UNCERTAIN"
        and _has_concrete_asr_target(output)
    ):
        return "NOT_HEARD"
    if dialogue_act == "CORRECTION":
        return "CORRECTION"
    if dialogue_act == "IRRELEVANT":
        return "OFF_TARGET"
    if (
        output.utteranceCompleteness == "INCOMPLETE"
        and not has_evaluable_answer_content(output)
    ):
        return "INCOMPLETE_UTTERANCE"
    if output.answerAssessment.sufficiency in _PARTIAL_SUFFICIENCIES:
        return "PARTIALLY_ANSWERED"
    return "ANSWERED"


def has_evaluable_answer_content(output: StructuredInterviewOutput) -> bool:
    """Return whether an incomplete transcript still contains answer evidence.

    A user can finish one semantic part of an answer and pause before starting
    another part.  The utterance is syntactically incomplete, but treating the
    whole turn as unanswerable would discard the grounded part and produce the
    generic continuation prompt.  This decision intentionally uses the
    interpreter's structured coverage/sufficiency fields rather than matching
    words in the transcript.
    """

    if any(
        aspect.status in {"ANSWERED", "NOT_APPLICABLE"}
        for aspect in output.aspectAssessments
    ):
        return True
    if not output.missingInformation:
        return False
    has_partial_semantics = (
        output.answerStatus == "PARTIAL"
        or output.answerAssessment.sufficiency in _PARTIAL_SUFFICIENCIES
    )
    return has_partial_semantics and bool(
        output.fieldUpdates
        or output.requirementUpdates
        or output.applicability
        or output.processPatch.model_dump(exclude_defaults=True)
    )


def _has_concrete_asr_target(output: StructuredInterviewOutput) -> bool:
    """Return whether an ASR retry can name one concrete ambiguous span.

    Recognition quality alone is not a user-facing retry target.  The
    structured interpreter must identify an important span in the transcript
    and its semantic role before the coordinator may say it did not hear the
    answer.  This keeps ASR ambiguity separate from semantic missing
    information and avoids vague retry prompts.
    """

    return any(
        span.important and span.span.strip() and span.semanticRole.strip()
        for span in output.transcriptAssessment.ambiguousSpans
    )


def _normalize_aspect_coverage(
    output: StructuredInterviewOutput,
) -> StructuredInterviewOutput:
    """Keep missing-information labels aligned with per-aspect decisions.

    The interpreter is still responsible for semantic judgement.  This is a
    small backend guard against asking for an aspect that the same response
    marked as answered, and against losing a required missing aspect when the
    model only returned the structured coverage list.
    """

    if not output.aspectAssessments:
        return output

    def key(value: object) -> str:
        return "".join(str(value or "").casefold().split())

    def matches(left: str, right: str) -> bool:
        return bool(left and right and left == right)

    assessed_aspects = {
        candidate: aspect
        for aspect in output.aspectAssessments
        for candidate in (key(aspect.aspectId), key(aspect.label))
        if candidate
    }
    missing_aspects = [
        aspect
        for aspect in output.aspectAssessments
        if aspect.required and aspect.status in {"MISSING", "UNCERTAIN"}
    ]
    missing_information: list[str] = []
    for item in output.missingInformation:
        normalized = key(item)
        aspect = assessed_aspects.get(normalized)
        if aspect is None:
            # Preserve an unmodeled item for auditability; the interpreter
            # prompt still requires missingInformation to use configured
            # aspects, but dropping it here would hide a provider defect.
            if normalized:
                missing_information.append(item)
            continue
        if aspect.required and aspect.status in {"MISSING", "UNCERTAIN"}:
            missing_information.append(item)
    for aspect in missing_aspects:
        if not any(
            matches(key(aspect.aspectId), key(item))
            or matches(key(aspect.label), key(item))
            for item in missing_information
        ):
            missing_information.append(aspect.label)
    missing_information = list(dict.fromkeys(missing_information))[:12]

    required_gap = bool(missing_aspects)
    updates: dict[str, Any] = {}
    if missing_information != list(output.missingInformation):
        updates["missingInformation"] = missing_information
    if required_gap:
        if output.answerStatus == "ANSWERED":
            updates["answerStatus"] = "PARTIAL"
        if output.answerAssessment.sufficiency == "SUFFICIENT":
            updates["answerAssessment"] = output.answerAssessment.model_copy(
                update={"sufficiency": "PARTIAL", "probeType": "CLARIFY"}
            )
    return output.model_copy(update=updates) if updates else output


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
    probe_decision: str = "NONE",
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
    if evaluation == "OFF_TARGET":
        return "KEEP_CURRENT_QUESTION"
    if probe_decision == "PROBE":
        return "FOLLOW_UP"
    if evaluation == "PARTIALLY_ANSWERED" or has_missing_information:
        return "ASK_MISSING_INFORMATION"
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
        "questionGoal": context.get("questionGoal"),
        "preferredProbeDimensions": context.get("preferredProbeDimensions", []),
        "acquiredInformation": context.get("acquiredInformation", []),
        "missingInformation": context.get("missingInformation", []),
        "aspectAssessments": context.get("aspectAssessments", []),
        "clarificationTarget": context.get("clarificationTarget"),
        "clarificationKind": context.get("clarificationKind", "NONE"),
        "previousAnswerEvaluation": context.get("answerEvaluation"),
        "answerStatus": context.get("answerStatus"),
        "probeDecision": context.get("probeDecision"),
        "questionAnswer": context.get("questionAnswer"),
        "sameQuestionAskedBefore": context.get("sameQuestionAskedBefore", False),
        "currentTurnType": context.get("currentTurnType"),
        "previousAssistantMessage": context.get("previousAssistantMessage"),
        "previousUserAnswer": context.get("previousUserAnswer"),
        "knownFacts": context.get("knownFacts", []),
        "coveredInformation": context.get("coveredInformation", []),
        "currentQuestionSlots": context.get("currentQuestionSlots", []),
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
        "userIntent": output.userIntent,
        "interpretationMatch": output.interpretationMatch,
        "userInterpretation": output.userInterpretation,
        "originalQuestionIntent": output.originalQuestionIntent,
        "resumeCurrentQuestion": output.resumeCurrentQuestion,
        "answerEvaluation": output.answerEvaluation,
        "answerStatus": output.answerStatus,
        "clarificationKind": output.clarificationKind,
        "probeDecision": output.probeDecision,
        "probeDimension": output.probeDimension,
        "probeReason": output.probeReason,
        "probeQuestion": output.probeQuestion,
        "utteranceCompleteness": output.utteranceCompleteness,
        "transcriptRecognitionStatus": output.transcriptAssessment.recognitionStatus,
        "transcriptCorrectionStatus": output.transcriptAssessment.correctionStatus,
        "ambiguousSpans": [
            span.model_dump() for span in output.transcriptAssessment.ambiguousSpans
        ],
        "answerSufficiency": output.answerAssessment.sufficiency,
        "probeType": output.answerAssessment.probeType,
        "aspectAssessments": [
            aspect.model_dump() for aspect in output.aspectAssessments
        ],
        "missingInformation": list(output.missingInformation),
        "extractedFields": extracted_update_summary(output),
    }


def _conversation_action_for_output(output: StructuredInterviewOutput) -> str:
    """Fallback action for direct Structured Interview calls without a router."""

    if output.userIntent in {
        "QUESTION_CLARIFICATION",
        "QUESTION_INTENT_CONFIRMATION",
        "ASR_CLARIFICATION_REQUEST",
        "META_QUESTION",
    }:
        return "ANSWER_USER_CLARIFICATION"
    if output.userIntent == "DECLINE_OR_SKIP":
        return "SKIP_AND_NEXT"
    if output.userIntent == "ANSWER_CLARIFICATION" and output.dialogueAct == "CORRECTION":
        return "APPLY_CORRECTION"
    if output.answerEvaluation == "NOT_HEARD":
        return "ASK_ASR_RETRY"
    if output.answerEvaluation == "INCOMPLETE_UTTERANCE":
        return "KEEP_CURRENT_QUESTION"
    if output.probeDecision == "PROBE":
        return "ASK_PROBE"
    if output.answerEvaluation == "PARTIALLY_ANSWERED" or output.answerStatus == "PARTIAL":
        return "ASK_FOR_MISSING_INFORMATION"
    if output.answerStatus == "ANSWERED":
        return "ACCEPT_AND_NEXT"
    return "KEEP_CURRENT_QUESTION"


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
            "answerStatus": raw_state.get("answerStatus"),
            "probeDecision": raw_state.get("probeDecision"),
            "probeCount": raw_state.get("probeCount", 0),
            "probeStatus": raw_state.get("probeStatus"),
            "originalAnswer": raw_state.get("originalAnswer"),
            "answer": raw_state.get("answer"),
            "probes": list(raw_state.get("probes") or []),
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
        probe_decision=output.probeDecision,
    )
    transcript = output.transcriptAssessment
    latest_utterance = context.get("latestUtterance")
    latest_utterance = latest_utterance if isinstance(latest_utterance, Mapping) else {}
    raw_transcript = transcript.rawTranscript or str(
        latest_utterance.get("rawTranscript") or ""
    ).strip()
    final_transcript = transcript.normalizedTranscript or raw_transcript
    user_intent = (
        state.get("lastUserIntent")
        if isinstance(state, Mapping) and state.get("lastUserIntent")
        else output.userIntent
    )
    conversation_action = (
        state.get("lastConversationAction")
        if isinstance(state, Mapping) and state.get("lastConversationAction")
        else _conversation_action_for_output(output)
    )
    return {
        "runtime": runtime,
        "rawTranscript": raw_transcript,
        "finalTranscript": final_transcript,
        "dialogueAct": output.dialogueAct,
        "userIntent": user_intent,
        "interpretationMatch": output.interpretationMatch,
        "userInterpretation": output.userInterpretation,
        "originalQuestionIntent": output.originalQuestionIntent,
        "conversationAction": conversation_action,
        "resumeCurrentQuestion": output.resumeCurrentQuestion,
        "answerEvaluation": output.answerEvaluation,
        "answerStatus": output.answerStatus,
        "clarificationKind": output.clarificationKind,
        "probeDecision": output.probeDecision,
        "probeDimension": output.probeDimension,
        "probeReason": output.probeReason,
        "probeQuestion": output.probeQuestion,
        "aspectAssessments": [
            aspect.model_dump() for aspect in output.aspectAssessments
        ],
        "clarificationTarget": (
            dict(state.get("lastClarificationTarget"))
            if isinstance(state, Mapping)
            and isinstance(state.get("lastClarificationTarget"), Mapping)
            else None
        ),
        "ambiguousSpans": [
            span.model_dump() for span in transcript.ambiguousSpans
        ],
        "extractedFields": extracted_update_summary(output),
        "fieldStates": field_state_summary(state),
        "missingInformation": list(output.missingInformation),
        "nextAction": next_action,
        "llmInput": llm_input_summary(dict(context)),
        "llmOutput": llm_output_summary(output),
    }
