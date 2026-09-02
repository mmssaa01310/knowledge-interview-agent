from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_interviewer_api.services.interview_answer_resolution import AnswerResolution


InterviewProfile = Literal["fixed_form", "business_process", "system_requirement"]
ApplicabilityStatus = Literal["unknown", "present", "not_applicable"]
CandidateSource = Literal["user_statement", "assistant_proposal", "document_reference"]
UtteranceCompleteness = Literal["COMPLETE", "INCOMPLETE", "UNCERTAIN"]
TranscriptCorrectionStatus = Literal["NONE", "CORRECTED", "UNCERTAIN"]
AnswerSufficiency = Literal[
    "SUFFICIENT",
    "PARTIAL",
    "AMBIGUOUS",
    "EXAMPLE_MISSING",
    "REASON_MISSING",
    "CRITERIA_MISSING",
    "UNANSWERABLE",
    "REFUSAL",
    "INCOMPLETE",
]
ProbeType = Literal["NONE", "REFRAME", "EXAMPLE", "REASON", "CRITERIA", "CLARIFY", "RETRY"]
StructuredDialogueAct = Literal[
    "ANSWER",
    "CLARIFICATION_REQUEST",
    "QUESTION_TO_ASSISTANT",
    "CONVERSATION_REQUEST",
    "BACKCHANNEL",
    "HESITATION",
    "CORRECTION",
    "REJECTION",
    "CONFIRMATION",
    "IRRELEVANT",
    "OTHER",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


ProcessConfirmationStatus = Literal["candidate", "confirmed"]


class FieldUpdate(StrictModel):
    fieldId: str
    value: str
    evidenceTranscriptIds: list[str] = Field(default_factory=list)
    itemId: str | None = None
    candidateSource: CandidateSource = "user_statement"
    answerResolution: AnswerResolution | None = None


class RequirementUpdate(StrictModel):
    requirementId: str
    value: str
    evidenceTranscriptIds: list[str] = Field(default_factory=list)
    candidateSource: CandidateSource = "user_statement"
    answerResolution: AnswerResolution | None = None


class RequirementEdit(StrictModel):
    """A management edit to an existing requirement value."""

    requirementId: str
    value: str


class RequirementPatch(StrictModel):
    """Structured updates for the requirement side of a full-screen edit."""

    updateRequirements: list[RequirementEdit] = Field(default_factory=list)


class ProcessParticipant(StrictModel):
    participantId: str
    name: str
    role: str | None = None
    kind: Literal["person", "organization", "system", "unknown"] = "unknown"
    evidenceTranscriptIds: list[str] = Field(default_factory=list)
    lifecycle: Literal["active", "superseded"] = "active"
    confirmationStatus: ProcessConfirmationStatus = "candidate"
    candidateSource: CandidateSource = "user_statement"


class ProcessNode(StrictModel):
    nodeId: str
    label: str
    nodeType: Literal[
        "start",
        "activity",
        "decision",
        "end",
        "system",
        "data",
        "subprocess",
        "unknown",
    ] = "activity"
    participantIds: list[str] = Field(default_factory=list)
    evidenceTranscriptIds: list[str] = Field(default_factory=list)
    lifecycle: Literal["active", "superseded"] = "active"
    confirmationStatus: ProcessConfirmationStatus = "candidate"
    candidateSource: CandidateSource = "user_statement"


class ProcessEdge(StrictModel):
    edgeId: str
    sourceNodeId: str
    targetNodeId: str
    label: str | None = None
    condition: str | None = None
    evidenceTranscriptIds: list[str] = Field(default_factory=list)
    lifecycle: Literal["active", "superseded"] = "active"
    confirmationStatus: ProcessConfirmationStatus = "candidate"
    candidateSource: CandidateSource = "user_statement"


class ProcessInteraction(StrictModel):
    interactionId: str
    sequence: int = 0
    sourceParticipantId: str
    targetParticipantId: str
    action: str
    data: str | None = None
    interactionType: Literal[
        "message",
        "return",
        "async",
        "notification",
        "handoff",
        "exception",
    ] = "message"
    fragmentType: Literal["none", "alt", "opt", "loop"] = "none"
    fragmentId: str | None = None
    fragmentLabel: str | None = None
    evidenceTranscriptIds: list[str] = Field(default_factory=list)
    lifecycle: Literal["active", "superseded"] = "active"
    confirmationStatus: ProcessConfirmationStatus = "candidate"
    candidateSource: CandidateSource = "user_statement"


class ProcessPatch(StrictModel):
    baseProcessVersion: int = 0
    addParticipants: list[ProcessParticipant] = Field(default_factory=list)
    updateParticipants: list[ProcessParticipant] = Field(default_factory=list)
    addNodes: list[ProcessNode] = Field(default_factory=list)
    updateNodes: list[ProcessNode] = Field(default_factory=list)
    addEdges: list[ProcessEdge] = Field(default_factory=list)
    updateEdges: list[ProcessEdge] = Field(default_factory=list)
    removeEdges: list[str] = Field(default_factory=list)
    addInteractions: list[ProcessInteraction] = Field(default_factory=list)
    updateInteractions: list[ProcessInteraction] = Field(default_factory=list)
    removeInteractions: list[str] = Field(default_factory=list)


class Contradiction(StrictModel):
    contradictionId: str
    topic: str
    description: str
    severity: Literal["low", "medium", "high"] = "medium"
    evidenceTranscriptIds: list[str] = Field(default_factory=list)


class ApplicabilityUpdate(StrictModel):
    topic: Literal[
        "process",
        "branch",
        "exception",
        "external_system",
        "error_handling",
        "handoff",
        "input_output",
    ]
    status: ApplicabilityStatus
    evidenceTranscriptIds: list[str] = Field(default_factory=list)
    reason: str | None = None


class OpenIssue(StrictModel):
    issueId: str
    topic: str
    description: str
    evidenceTranscriptIds: list[str] = Field(default_factory=list)


class TranscriptAssessment(StrictModel):
    """Assessment of the STT text before it can become a formal answer."""

    rawTranscript: str = ""
    normalizedTranscript: str = ""
    correctionStatus: TranscriptCorrectionStatus = "NONE"
    correctionCandidates: list[str] = Field(default_factory=list, max_length=3)
    correctionReason: str | None = None


class AnswerAssessment(StrictModel):
    """Semantic answer sufficiency used by the backend coordinator."""

    sufficiency: AnswerSufficiency = "SUFFICIENT"
    probeType: ProbeType = "NONE"


class StructuredInterviewOutput(StrictModel):
    """Meaning extraction contract. It contains no generated question or layout."""

    dialogueAct: StructuredDialogueAct = "ANSWER"
    utteranceCompleteness: UtteranceCompleteness = "COMPLETE"
    transcriptAssessment: TranscriptAssessment = Field(default_factory=TranscriptAssessment)
    answerAssessment: AnswerAssessment = Field(default_factory=AnswerAssessment)
    fieldUpdates: list[FieldUpdate] = Field(default_factory=list)
    requirementUpdates: list[RequirementUpdate] = Field(default_factory=list)
    processPatch: ProcessPatch = Field(default_factory=ProcessPatch)
    contradictions: list[Contradiction] = Field(default_factory=list)
    resolvedContradictionIds: list[str] = Field(default_factory=list)
    applicability: list[ApplicabilityUpdate] = Field(default_factory=list)
    openIssues: list[OpenIssue] = Field(default_factory=list)


class ProcessModelEditOutput(StrictModel):
    """Structured contract for a management user's full-screen edit command."""

    reply: str
    requirementPatch: RequirementPatch = Field(default_factory=RequirementPatch)
    processPatch: ProcessPatch = Field(default_factory=ProcessPatch)


class QuestionGenerationOutput(StrictModel):
    questionText: str
    documentCandidateValue: str | None = Field(default=None, max_length=1000)
    documentCandidateSourceIds: list[str] = Field(default_factory=list, max_length=6)
