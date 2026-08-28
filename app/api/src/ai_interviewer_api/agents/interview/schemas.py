from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ai_interviewer_api.agents.interview_knowledge.schemas import InterviewProfile
from ai_interviewer_api.models.interview_plan import (
    CapturedInterviewItem,
    InterviewPlan,
    InterviewQuestionPlan,
)


class InterviewMessage(BaseModel):
    id: str | None = None
    role: Literal["user", "assistant", "system"]
    content: str
    questionId: str | None = None
    questionType: Literal["configured_field", "follow_up", "structured"] | None = None
    fieldId: str | None = None
    answerToQuestionId: str | None = None
    answerToFieldId: str | None = None
    turnType: Literal["ANSWER", "CONTROL"] | None = None
    targetType: str | None = None
    targetId: str | None = None
    isLegacy: bool = False


class InterviewQuestion(BaseModel):
    questionId: str
    questionType: Literal["configured_field", "follow_up", "structured"]
    fieldId: str | None = None
    text: str
    retrievalPolicy: Literal["never", "auto", "required"] = "auto"
    questionPlan: InterviewQuestionPlan | None = None
    targetType: str | None = None
    targetId: str | None = None
    candidateSource: Literal["user_statement", "assistant_proposal"] | None = None


class InterviewField(BaseModel):
    fieldId: str | None = None
    name: str
    description: str | None = None
    aiQuestionExamples: list[str] = Field(default_factory=list)
    inputType: str | None = None
    required: bool = False
    retrievalPolicy: Literal["never", "auto", "required"] = "auto"
    questionPlan: InterviewQuestionPlan | None = None


class InterviewFieldState(BaseModel):
    fieldId: str
    status: Literal["pending", "asking", "completed"] = "pending"
    answerSummary: str | None = None
    missingInformation: list[str] = Field(default_factory=list)
    answerState: Literal["UNANSWERED", "CANDIDATE_PENDING", "AWAITING_CONFIRMATION", "CONFIRMED"] = "UNANSWERED"
    candidateAnswer: str | None = None
    candidateSource: Literal["user_statement", "assistant_proposal"] | None = None
    candidateProposalMessageId: str | None = None
    confirmedSource: Literal["user_statement", "assistant_proposal"] | None = None
    confirmedProposalMessageId: str | None = None
    confirmationEvidenceTranscriptIds: list[str] = Field(default_factory=list)
    rawAnswer: str | None = None
    rawAnswerHistory: list[str] = Field(default_factory=list)
    recordAnswer: str | None = None
    capturedItems: list[CapturedInterviewItem] = Field(default_factory=list)
    candidateItems: list[CapturedInterviewItem] = Field(default_factory=list)
    confirmedItems: list[CapturedInterviewItem] = Field(default_factory=list)
    missingRequiredItemIds: list[str] = Field(default_factory=list)
    answerDisposition: Literal["ANSWERED", "UNCLEAR", "IRRELEVANT"] | None = None


class InterviewState(BaseModel):
    status: Literal["in_progress", "completed"] = "in_progress"
    interviewProfile: InterviewProfile = "fixed_form"
    currentFieldId: str | None = None
    currentQuestionId: str | None = None
    completedFieldIds: list[str] = Field(default_factory=list)
    pendingFieldIds: list[str] = Field(default_factory=list)
    askedQuestions: list[InterviewQuestion] = Field(default_factory=list)
    followUpCounts: dict[str, int] = Field(default_factory=dict)
    fieldStates: dict[str, InterviewFieldState] = Field(default_factory=dict)
    lastProcessedUserMessageId: str | None = None
    nextQuestionTarget: dict[str, Any] | None = None
    requirementStates: dict[str, dict[str, Any]] = Field(default_factory=dict)
    processState: dict[str, Any] = Field(default_factory=dict)
    applicabilityState: dict[str, dict[str, Any]] = Field(default_factory=dict)
    contradictions: list[dict[str, Any]] = Field(default_factory=list)
    openIssues: list[dict[str, Any]] = Field(default_factory=list)
    processVersion: int = 0
    stateVersion: int = 0


class InterviewFieldEvaluation(BaseModel):
    fieldId: str
    isComplete: bool = False
    answerSummary: str = ""
    recordAnswer: str | None = None
    missingInformation: list[str] = Field(default_factory=list)
    nextAction: Literal["follow_up", "next_field"] = "follow_up"
    decision: Literal[
        "CONFIRMABLE",
        "NEEDS_MORE_INFORMATION",
        "NOT_ANSWER",
        "UNCLEAR",
        "REQUEST_GUIDANCE",
        "CORRECT_PREVIOUS_FIELD",
    ] | None = None
    isRelevant: bool | None = None
    isSufficient: bool | None = None
    targetFieldId: str | None = None
    retrievalNeeded: bool = False
    evaluationReason: str | None = None
    confirmationQuestion: str | None = None
    confirmationOutcome: Literal[
        "CONFIRM",
        "REVISE_WITH_CONTENT",
        "REJECT_WITHOUT_CONTENT",
        "UNCLEAR",
    ] | None = None
    capturedItems: list[CapturedInterviewItem] = Field(default_factory=list)
    answerDisposition: Literal["ANSWERED", "UNCLEAR", "IRRELEVANT"] | None = None
    evaluationStatus: Literal["OK", "EVALUATION_ERROR"] = "OK"


class InterviewTurnInput(BaseModel):
    knowledge_id: str | None = None
    knowledge_name: str | None = None
    knowledge_description: str | None = None
    target_business: str | None = None
    target_equipment: str | None = None
    record_title: str | None = None
    custom_prompt: str | None = None
    interview_plan: InterviewPlan | None = None
    user_message: str
    conversation_history: list[InterviewMessage] = Field(default_factory=list)
    approved_fields: list[InterviewField] = Field(default_factory=list)
    current_field: InterviewField | None = None
    current_question: InterviewQuestion | None = None
    interview_state: InterviewState | None = None
    follow_up_count: int = 0
    max_follow_up_questions_per_field: int = 2


class InterviewTurnOutput(BaseModel):
    reply: str = ""
    field_evaluation: InterviewFieldEvaluation
    follow_up_question: str | None = None
    used_tools: list[str] = Field(default_factory=list)


class InterviewAgentResult(BaseModel):
    status: Literal["in_progress", "completed"]
    action: Literal["ask_configured_field", "ask_follow_up", "ask_structured", "finish"]
    reply: str
    question: InterviewQuestion | None = None
    completedFieldId: str | None = None
    currentFieldId: str | None = None
    answerSummary: str | None = None
    recordAnswer: str | None = None
    missingInformation: list[str] = Field(default_factory=list)
    used_tools: list[str] = Field(default_factory=list)
