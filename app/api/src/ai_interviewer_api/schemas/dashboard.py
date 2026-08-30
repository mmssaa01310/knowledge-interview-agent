from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ReviewPriorityLevel = Literal["low", "medium", "high"]
LearningAssessmentStatus = Literal[
    "confirmed",
    "partially_confirmed",
    "not_evidenced",
    "needs_follow_up",
    "not_applicable",
]
GuidanceStatus = Literal["draft", "published", "unpublished"]
LearningAnalysisStatus = Literal["draft", "reviewed"]


class StrictDashboardModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DashboardFilters(StrictDashboardModel):
    dateFrom: date | None = None
    dateTo: date | None = None
    knowledgeId: str | None = None
    profile: str | None = None
    recordStatus: str | None = None
    timezone: str


class DashboardTotals(StrictDashboardModel):
    knowledgeCount: int
    recordCount: int
    inProgressCount: int
    submittedCount: int
    returnedCount: int
    approvedCount: int
    pendingReviewCount: int
    highPriorityCount: int
    mediumPriorityCount: int


class TrendPoint(StrictDashboardModel):
    date: date
    createdCount: int
    submittedCount: int
    approvedCount: int


class KnowledgeDashboardSummary(StrictDashboardModel):
    knowledgeId: str
    knowledgeDbId: str
    knowledgeName: str
    profile: str | None = None
    recordCount: int
    inProgressCount: int
    submittedCount: int
    returnedCount: int
    approvedCount: int
    highPriorityCount: int
    mediumPriorityCount: int


class ActivitySummary(StrictDashboardModel):
    userId: str
    displayName: str
    recordCount: int
    answerCount: int
    submittedCount: int
    confirmedCount: int
    partiallyConfirmedCount: int
    notEvidencedCount: int
    needsFollowUpCount: int
    notApplicableCount: int
    lastActivityAt: str | None = None


class ReviewReason(StrictDashboardModel):
    code: str
    targetType: str
    targetId: str | None = None
    targetLabel: str | None = None
    evidenceIds: list[str] = Field(default_factory=list)


class RecordReviewPriority(StrictDashboardModel):
    recordId: str
    knowledgeId: str
    knowledgeDbId: str
    knowledgeName: str
    title: str
    ownerUserId: str | None = None
    ownerDisplayName: str | None = None
    recordStatus: str
    level: ReviewPriorityLevel
    reasons: list[ReviewReason]
    updatedAt: str


class LearningStatusSummary(StrictDashboardModel):
    confirmed: int
    partiallyConfirmed: int
    notEvidenced: int
    needsFollowUp: int
    notApplicable: int


class GuidanceDraftSummary(StrictDashboardModel):
    id: str
    recordId: str
    knowledgeId: str
    recordTitle: str
    status: GuidanceStatus
    modelId: str
    updatedAt: str


class LearningObjectiveTrend(StrictDashboardModel):
    objectiveId: str
    label: str
    recordCount: int
    confirmedCount: int
    partiallyConfirmedCount: int
    notEvidencedCount: int
    needsFollowUpCount: int
    notApplicableCount: int


class LearningAnalysisScope(StrictDashboardModel):
    dateFrom: date | None = None
    dateTo: date | None = None
    knowledgeId: str
    profile: str | None = None
    recordStatus: str | None = None
    timezone: str
    recordCount: int
    recordIds: list[str] = Field(default_factory=list)


class LearningAnalysisTheme(StrictDashboardModel):
    themeId: str
    title: str
    summary: str
    objectiveIds: list[str] = Field(default_factory=list)
    evidenceRecordIds: list[str] = Field(default_factory=list)
    learnerGuidance: str
    instructorGuidance: str
    followUpQuestion: str


class LearningPersonalAdviceFocus(StrictDashboardModel):
    title: str
    summary: str
    objectiveIds: list[str] = Field(default_factory=list)
    evidenceRecordIds: list[str] = Field(default_factory=list)
    nextStep: str
    followUpQuestion: str


class LearningPersonalAdvice(StrictDashboardModel):
    respondentId: str
    displayName: str
    recordIds: list[str] = Field(default_factory=list)
    summary: str
    focusAreas: list[LearningPersonalAdviceFocus] = Field(default_factory=list)
    nextSteps: list[str] = Field(default_factory=list)
    followUpQuestions: list[str] = Field(default_factory=list)


class LearningAnalysisDraftResponse(StrictDashboardModel):
    id: str
    knowledgeId: str
    knowledgeName: str
    status: LearningAnalysisStatus
    modelId: str
    scope: LearningAnalysisScope
    objectiveTrends: list[LearningObjectiveTrend]
    summary: str
    trendSummary: str
    learnerGuidance: str
    instructorGuidance: str
    themes: list[LearningAnalysisTheme]
    personalAdvice: list[LearningPersonalAdvice]
    generatedAt: str
    reviewedAt: str | None = None
    reviewedByUserId: str | None = None
    updatedAt: str


class AdminDashboardResponse(StrictDashboardModel):
    generatedAt: str
    filters: DashboardFilters
    totals: DashboardTotals
    timeSeries: list[TrendPoint]
    knowledgeSummaries: list[KnowledgeDashboardSummary]
    activityByUser: list[ActivitySummary]
    reviewPriorities: list[RecordReviewPriority]
    reviewPriorityTotal: int
    learningStatus: LearningStatusSummary
    guidanceDrafts: list[GuidanceDraftSummary]


class GuidanceAssessment(StrictDashboardModel):
    objectiveId: str
    label: str
    status: LearningAssessmentStatus
    suggestedStatus: LearningAssessmentStatus | None = None
    evidenceIds: list[str] = Field(default_factory=list)
    learnerGuidance: str
    instructorGuidance: str
    followUpQuestion: str


class GuidanceDraftResponse(StrictDashboardModel):
    id: str
    recordId: str
    knowledgeId: str
    status: GuidanceStatus
    modelId: str
    inputVersion: int
    summary: str
    learnerGuidance: str
    instructorGuidance: str | None = None
    assessments: list[GuidanceAssessment]
    generatedAt: str
    publishedAt: str | None = None
    publishedByUserId: str | None = None
    updatedAt: str


class GuidanceUpdateRequest(StrictDashboardModel):
    summary: str | None = Field(default=None, min_length=1, max_length=10000)
    learnerGuidance: str | None = Field(default=None, min_length=1, max_length=10000)
    instructorGuidance: str | None = Field(default=None, max_length=10000)


class GuidanceAssessmentOutput(StrictDashboardModel):
    objectiveId: str
    status: Literal[
        "confirmed",
        "partially_confirmed",
        "not_evidenced",
        "needs_follow_up",
    ]
    evidenceIds: list[str] = Field(default_factory=list)
    learnerGuidance: str
    instructorGuidance: str
    followUpQuestion: str


class GuidanceGenerationOutput(StrictDashboardModel):
    summary: str
    learnerGuidance: str
    instructorGuidance: str
    assessments: list[GuidanceAssessmentOutput] = Field(default_factory=list)


class LearningAnalysisRequest(StrictDashboardModel):
    dateFrom: date | None = None
    dateTo: date | None = None
    knowledgeId: str = Field(min_length=1, max_length=200)
    profile: str | None = None
    recordStatus: str | None = None


class LearningAnalysisUpdateRequest(StrictDashboardModel):
    summary: str | None = Field(default=None, min_length=1, max_length=10000)
    trendSummary: str | None = Field(default=None, min_length=1, max_length=10000)
    learnerGuidance: str | None = Field(default=None, min_length=1, max_length=10000)
    instructorGuidance: str | None = Field(default=None, min_length=1, max_length=10000)


class LearningAnalysisThemeOutput(StrictDashboardModel):
    themeId: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)
    objectiveIds: list[str] = Field(default_factory=list, max_length=20)
    evidenceRecordIds: list[str] = Field(default_factory=list, max_length=100)
    learnerGuidance: str = Field(min_length=1, max_length=4000)
    instructorGuidance: str = Field(min_length=1, max_length=4000)
    followUpQuestion: str = Field(min_length=1, max_length=2000)


class LearningPersonalAdviceFocusOutput(StrictDashboardModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)
    objectiveIds: list[str] = Field(default_factory=list, max_length=20)
    evidenceRecordIds: list[str] = Field(default_factory=list, max_length=100)
    nextStep: str = Field(min_length=1, max_length=2000)
    followUpQuestion: str = Field(min_length=1, max_length=2000)


class LearningPersonalAdviceItemOutput(StrictDashboardModel):
    respondentKey: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=10000)
    focusAreas: list[LearningPersonalAdviceFocusOutput] = Field(default_factory=list, max_length=8)
    nextSteps: list[str] = Field(default_factory=list, max_length=8)
    followUpQuestions: list[str] = Field(default_factory=list, max_length=8)


class LearningPersonalAdviceGenerationOutput(StrictDashboardModel):
    advice: list[LearningPersonalAdviceItemOutput] = Field(default_factory=list, max_length=100)


class LearningAnalysisGenerationOutput(StrictDashboardModel):
    summary: str = Field(min_length=1, max_length=10000)
    trendSummary: str = Field(min_length=1, max_length=10000)
    learnerGuidance: str = Field(min_length=1, max_length=10000)
    instructorGuidance: str = Field(min_length=1, max_length=10000)
    themes: list[LearningAnalysisThemeOutput] = Field(default_factory=list, max_length=12)
