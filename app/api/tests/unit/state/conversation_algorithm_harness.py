from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from ai_interviewer_api.agents.interview_knowledge.schemas import (
    AnswerAssessment,
    FieldUpdate,
    QuestionGenerationOutput,
    StructuredInterviewOutput,
    TranscriptAssessment,
)
from ai_interviewer_api.agents.interview_knowledge.service import (
    generate_structured_interview_result,
)
from ai_interviewer_api.auth.deps import DEV_TOKENS, UserContext
from ai_interviewer_api.models.domain import VoiceSession
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.repositories.voice_session_repository import save as save_voice_session
from ai_interviewer_api.schemas.voice import VoiceTurnCreate
from ai_interviewer_api.services.voice_interview import create_voice_turn


@dataclass(frozen=True)
class CharacterizationTurn:
    """A deterministic input/output pair for the current backend algorithm."""

    utterance: str
    message_id: str
    structured_output: StructuredInterviewOutput


class DeterministicStructuredProvider:
    """Provider double that exposes the existing service's actual decisions.

    The harness deliberately does not implement a Router. ``routerIntent`` is
    reported as ``N/A`` so a future canonical router can be compared with the
    current behavior without silently inventing a second production policy.
    """

    def __init__(
        self,
        output_for_utterance: Mapping[str, StructuredInterviewOutput] | None = None,
        *,
        default_output: StructuredInterviewOutput | None = None,
    ) -> None:
        self.output_for_utterance = dict(output_for_utterance or {})
        self.default_output = default_output or StructuredInterviewOutput()
        self.interpret_calls: list[dict[str, Any]] = []
        self.question_calls: list[dict[str, Any]] = []

    def interpret(self, *, context: Mapping[str, Any], **_: Any) -> StructuredInterviewOutput:
        copied_context = deepcopy(dict(context))
        self.interpret_calls.append(copied_context)
        latest = copied_context.get("latestUtterance")
        raw = str(latest.get("rawTranscript") or "") if isinstance(latest, Mapping) else ""
        return deepcopy(self.output_for_utterance.get(raw, self.default_output))

    def generate_question(
        self,
        *,
        target: Mapping[str, Any],
        context: Mapping[str, Any],
        **_: Any,
    ) -> QuestionGenerationOutput:
        self.question_calls.append(
            {"target": deepcopy(dict(target)), "context": deepcopy(dict(context))}
        )
        definition = context.get("questionDefinition")
        if isinstance(definition, Mapping):
            canonical = str(definition.get("originalQuestion") or "").strip()
            if canonical:
                return QuestionGenerationOutput(questionText=canonical)
        return QuestionGenerationOutput(
            questionText=f"{str(target.get('label') or 'その項目').strip()}について教えてください。"
        )


def _state_projection(state: Mapping[str, Any]) -> dict[str, Any]:
    """Keep comparison output deterministic while retaining policy state."""

    keys = (
        "status",
        "interviewProfile",
        "stateVersion",
        "currentFieldId",
        "currentQuestionId",
        "nextQuestionTarget",
        "closingState",
        "lastProcessedUserMessageId",
        "lastUtteranceCompleteness",
        "lastTranscriptAssessment",
        "lastAnswerAssessment",
        "lastStructuredDialogueAct",
        "activeProbeTarget",
        "pendingTranscriptConfirmation",
        "lastTentativeTarget",
        "deferredProposalTarget",
        "activeClarificationRequest",
        "clarificationQueue",
        "clarificationHistory",
        "fieldStates",
        "requirementStates",
        "applicabilityState",
        "contradictions",
        "openIssues",
    )
    return {key: deepcopy(state.get(key)) for key in keys}


def _target_from_current_state(state: Mapping[str, Any]) -> dict[str, Any] | None:
    target = state.get("nextQuestionTarget")
    return deepcopy(dict(target)) if isinstance(target, Mapping) else None


class ConversationAlgorithmHarness:
    """Provider-independent observer for one or more current algorithm turns."""

    def __init__(self, *, fields: tuple[Mapping[str, Any], ...] | None = None) -> None:
        self.user: UserContext = DEV_TOKENS["dev-manager"]
        self.record = {
            "id": "characterization-record",
            "tenantId": self.user.tenant_id,
            "knowledgeId": "characterization-knowledge",
            "title": "Conversation Algorithm characterization",
            "status": "in_progress",
        }
        self.knowledge = {
            "id": "characterization-knowledge",
            "tenantId": self.user.tenant_id,
            "name": "Characterization interview",
            "interviewPlan": {"profile": "fixed_form"},
        }
        self.fields = tuple(fields or self._default_fields())
        self.provider = DeterministicStructuredProvider()
        self._message_sequence = 0
        self._seed()

    def _default_fields(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "id": "field-profile",
                "tenantId": self.user.tenant_id,
                "knowledgeId": self.knowledge["id"],
                "name": "基本プロフィール",
                "description": "名前、所属部署、役職または担当領域",
                "inputType": "long_text",
                "required": True,
                "askByAi": True,
                "retrievalPolicy": "never",
                "questionText": "お名前、所属部署、役職または担当領域を教えてください。",
                "aiQuestionExamples": ["お名前、所属部署、役職または担当領域を教えてください。"],
                "questionPlan": {
                    "version": 1,
                    "purpose": "基本プロフィールを確定する",
                    "requiredItems": [
                        {"itemId": "name", "label": "名前", "description": "氏名"},
                        {"itemId": "department", "label": "所属部署", "description": "所属部署"},
                        {
                            "itemId": "role_or_responsibility",
                            "label": "役職または担当領域",
                            "description": "役職または担当領域",
                        },
                    ],
                    "optionalItems": [],
                    "completionCriteria": {"mode": "all_required_items"},
                },
                "displayOrder": 1,
            },
            {
                "id": "field-work",
                "tenantId": self.user.tenant_id,
                "knowledgeId": self.knowledge["id"],
                "name": "担当業務",
                "description": "担当している業務",
                "inputType": "long_text",
                "required": True,
                "askByAi": True,
                "retrievalPolicy": "never",
                "questionText": "現在の担当業務を教えてください。",
                "aiQuestionExamples": ["現在の担当業務を教えてください。"],
                "questionPlan": {
                    "version": 1,
                    "purpose": "担当業務を確認する",
                    "requiredItems": [
                        {"itemId": "work", "label": "担当業務", "description": "担当している業務"}
                    ],
                    "optionalItems": [],
                    "completionCriteria": {"mode": "all_required_items"},
                },
                "displayOrder": 2,
            },
        )

    def _seed(self) -> None:
        store.upsert("records", deepcopy(self.record))
        store.upsert("knowledges", deepcopy(self.knowledge))
        for field in self.fields:
            store.upsert("knowledge_fields", deepcopy(dict(field)))

        from ai_interviewer_api.agents.interview_knowledge.coordinator import (
            build_initial_structured_state,
        )

        state = build_initial_structured_state("fixed_form", self.fields)
        state.update(
            {
                "id": f"interview-state-{self.record['id']}",
                "tenantId": self.user.tenant_id,
                "recordId": self.record["id"],
                "createdByUserId": self.user.user_id,
                "updatedByUserId": self.user.user_id,
                "createdAt": "2026-01-01T00:00:00+00:00",
                "updatedAt": "2026-01-01T00:00:00+00:00",
            }
        )
        first_field = dict(self.fields[0])
        question = self._question_for_field(first_field, question_id="q-001")
        state["askedQuestions"] = [question]
        state["currentFieldId"] = first_field["id"]
        state["currentQuestionId"] = question["questionId"]
        state["nextQuestionTarget"] = self._target_for_question(question)
        store.upsert("interview_states", state)

    @staticmethod
    def _target_for_question(question: Mapping[str, Any]) -> dict[str, Any]:
        keys = (
            "targetType",
            "targetId",
            "targetLabel",
            "questionPlan",
            "sourceQuestion",
            "sourceDescription",
            "questionText",
            "targetDescription",
            "required",
            "optional",
        )
        target = {
            "label": question.get("targetLabel") or question.get("label"),
            **{
                key: deepcopy(question[key])
                for key in keys
                if key in question and key not in {"targetLabel"}
            },
        }
        target["targetId"] = question.get("targetId")
        target["targetType"] = question.get("targetType")
        return target

    @staticmethod
    def _question_for_field(field: Mapping[str, Any], *, question_id: str) -> dict[str, Any]:
        examples = field.get("aiQuestionExamples")
        first_example = examples[0] if isinstance(examples, list) and examples else ""
        text = str(field.get("questionText") or first_example)
        return {
            "questionId": question_id,
            "questionType": "structured",
            "fieldId": field["id"],
            "text": text,
            "retrievalPolicy": field.get("retrievalPolicy", "never"),
            "targetType": "field",
            "targetId": field["id"],
            "targetLabel": field.get("name"),
            "questionPlan": deepcopy(field.get("questionPlan")),
            "sourceQuestion": text,
            "sourceDescription": field.get("description"),
            "questionText": text,
            "targetDescription": field.get("description"),
            "required": field.get("required"),
            "optional": not bool(field.get("required")),
        }

    def set_confirmation_pending(self, *, candidate: str = "宮崎") -> None:
        state = store.get("interview_states", f"interview-state-{self.record['id']}")
        assert state is not None
        field_id = self.fields[0]["id"]
        field_state = state["fieldStates"][field_id]
        field_state.update(
            {
                "answerState": "AWAITING_CONFIRMATION",
                "answerResolution": "TENTATIVE",
                "status": "asking",
                "candidateAnswer": candidate,
                "candidateSource": "user_statement",
                "candidateSourceIds": ["candidate-message"],
                "candidateItems": [
                    {
                        "itemId": item["itemId"],
                        "value": candidate,
                        "evidenceTranscriptIds": ["candidate-message"],
                    }
                    for item in field_state["questionPlan"]["requiredItems"]
                ],
                "candidateEvidenceTranscriptIds": ["candidate-message"],
            }
        )
        state["lastTentativeTarget"] = {
            "targetType": "field",
            "targetId": field_id,
        }
        question = deepcopy(state["askedQuestions"][0])
        question["text"] = f"{candidate}という理解でよろしいですか？"
        state["askedQuestions"][0] = question
        state["currentQuestionId"] = question["questionId"]
        state["currentFieldId"] = field_id
        state["nextQuestionTarget"] = self._target_for_question(question)
        store.upsert("interview_states", state)

    def run_turn(self, turn: CharacterizationTurn) -> dict[str, Any]:
        stored_state_before = store.get(
            "interview_states", f"interview-state-{self.record['id']}"
        )
        if stored_state_before is None:
            raise AssertionError("characterization state is missing")
        state_before = deepcopy(stored_state_before)
        current_question_id = state_before.get("currentQuestionId")
        current_question = next(
            (
                question
                for question in state_before.get("askedQuestions", [])
                if question.get("questionId") == current_question_id
            ),
            None,
        )
        self._message_sequence += 1
        store.upsert(
            "messages",
            {
                "id": turn.message_id,
                "tenantId": self.user.tenant_id,
                "recordId": self.record["id"],
                "content": turn.utterance,
                "rawTranscript": turn.utterance,
                "role": "user",
                "isActualUtterance": True,
                "turnType": "ANSWER",
                "answerToQuestionId": current_question_id,
                "answerToFieldId": current_question.get("fieldId") if current_question else None,
                "createdAt": f"2026-01-01T00:00:00.{self._message_sequence:06d}+00:00",
            },
        )
        self.provider.output_for_utterance[turn.utterance] = turn.structured_output
        result = generate_structured_interview_result(
            self.record,
            self.knowledge,
            self.user,
            persist_assistant_messages=False,
            provider=self.provider,
        )
        state_after = result.get("interviewState") or {}
        structured_output = state_after.get("lastStructuredOutput")
        if not isinstance(structured_output, Mapping):
            structured_output = {}
        return {
            "input": turn.utterance,
            "messageId": turn.message_id,
            "routerIntent": "N/A",
            "fastDecision": "N/A",
            "structuredDialogueAct": structured_output.get("dialogueAct"),
            "stateBefore": _state_projection(state_before),
            "stateAfter": _state_projection(state_after),
            "currentTarget": _target_from_current_state(state_before),
            "nextTarget": deepcopy(result.get("nextQuestionTarget")),
            "action": result.get("action"),
            "generatedQuestion": str(result.get("reply") or ""),
            "question": deepcopy(result.get("question")),
            "questionDefinitionSeenByGenerator": deepcopy(
                self.provider.question_calls[-1]["context"].get("questionDefinition")
            )
            if self.provider.question_calls
            else None,
            "stateChanged": _state_projection(state_before) != _state_projection(state_after),
        }

    def characterize_duplicate_identity(self) -> dict[str, Any]:
        """Observe API-level reuse for one provider source item id."""

        session_id = "characterization-voice-session"
        session = VoiceSession(
            id=session_id,
            tenantId=self.user.tenant_id,
            createdByUserId=self.user.user_id,
            updatedByUserId=self.user.user_id,
            ownerUserId=self.user.user_id,
            recordId=self.record["id"],
            provider="openai_realtime",
            currentQuestionId="q-001",
            status="active",
        ).model_dump()
        save_voice_session(session)
        source_item_id = "realtime-item-001"
        payload = VoiceTurnCreate(
            transcript="こんにちは",
            answerToQuestionId="q-001",
            clientTurnId=f"openai-{source_item_id}",
        )
        first = create_voice_turn(session_id, payload)
        second = create_voice_turn(session_id, payload)
        return {
            "voiceSessionId": session_id,
            "sourceItemId": source_item_id,
            "clientTurnId": payload.clientTurnId,
            "firstTurnId": first["id"],
            "secondTurnId": second["id"],
            "sameCanonicalTurn": first["id"] == second["id"],
            "storedTurnCount": len(
                [
                    row
                    for row in store.list("voice_turns", self.user.tenant_id)
                    if row.get("voiceSessionId") == session_id
                ]
            ),
        }


def output_for(
    utterance: str,
    *,
    dialogue_act: str = "ANSWER",
    sufficiency: str = "SUFFICIENT",
    field_id: str | None = None,
    value: str | None = None,
    item_values: tuple[tuple[str, str], ...] = (),
    message_id: str = "message-001",
) -> CharacterizationTurn:
    if item_values and field_id:
        updates = [
            FieldUpdate(
                fieldId=field_id,
                itemId=item_id,
                value=item_value,
                evidenceTranscriptIds=[message_id],
                answerResolution="AUTO_CONFIRM",
            )
            for item_id, item_value in item_values
        ]
    elif field_id:
        updates = [
            FieldUpdate(
                fieldId=field_id,
                value=value or utterance,
                evidenceTranscriptIds=[message_id],
                answerResolution="AUTO_CONFIRM",
            )
        ]
    else:
        updates = []
    return CharacterizationTurn(
        utterance=utterance,
        message_id=message_id,
        structured_output=StructuredInterviewOutput(
            dialogueAct=dialogue_act,
            transcriptAssessment=TranscriptAssessment(
                rawTranscript=utterance,
                normalizedTranscript=utterance,
            ),
            answerAssessment=AnswerAssessment(sufficiency=sufficiency),
            fieldUpdates=updates,
        ),
    )


def clear_characterization_store() -> None:
    store.tables.clear()
