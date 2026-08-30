export type ReviewPriorityLevel = "low" | "medium" | "high";

export type LearningAssessmentStatus =
  | "confirmed"
  | "partially_confirmed"
  | "not_evidenced"
  | "needs_follow_up"
  | "not_applicable";

export type GuidanceStatus = "draft" | "published" | "unpublished";

export type DashboardFilters = {
  dateFrom?: string;
  dateTo?: string;
  knowledgeId?: string;
  profile?: string;
  recordStatus?: string;
};

export type DashboardTotals = {
  knowledgeCount: number;
  recordCount: number;
  inProgressCount: number;
  submittedCount: number;
  returnedCount: number;
  approvedCount: number;
  pendingReviewCount: number;
  highPriorityCount: number;
  mediumPriorityCount: number;
};

export type DashboardTrendPoint = {
  date: string;
  createdCount: number;
  submittedCount: number;
  approvedCount: number;
};

export type KnowledgeDashboardSummary = {
  knowledgeId: string;
  knowledgeDbId: string;
  knowledgeName: string;
  profile?: string | null;
  recordCount: number;
  inProgressCount: number;
  submittedCount: number;
  returnedCount: number;
  approvedCount: number;
  highPriorityCount: number;
  mediumPriorityCount: number;
};

export type ActivitySummary = {
  userId: string;
  displayName: string;
  recordCount: number;
  answerCount: number;
  submittedCount: number;
  confirmedCount: number;
  partiallyConfirmedCount: number;
  notEvidencedCount: number;
  needsFollowUpCount: number;
  notApplicableCount: number;
  lastActivityAt?: string | null;
};

export type ReviewReason = {
  code: string;
  targetType: string;
  targetId?: string | null;
  targetLabel?: string | null;
  evidenceIds: string[];
};

export type RecordReviewPriority = {
  recordId: string;
  knowledgeId: string;
  knowledgeDbId: string;
  knowledgeName: string;
  title: string;
  ownerUserId?: string | null;
  ownerDisplayName?: string | null;
  recordStatus: string;
  level: ReviewPriorityLevel;
  reasons: ReviewReason[];
  updatedAt: string;
};

export type LearningStatusSummary = {
  confirmed: number;
  partiallyConfirmed: number;
  notEvidenced: number;
  needsFollowUp: number;
  notApplicable: number;
};

export type GuidanceDraftSummary = {
  id: string;
  recordId: string;
  knowledgeId: string;
  recordTitle: string;
  status: GuidanceStatus;
  modelId: string;
  updatedAt: string;
};

export type AdminDashboard = {
  generatedAt: string;
  filters: DashboardFilters & { timezone: string };
  totals: DashboardTotals;
  timeSeries: DashboardTrendPoint[];
  knowledgeSummaries: KnowledgeDashboardSummary[];
  activityByUser: ActivitySummary[];
  reviewPriorities: RecordReviewPriority[];
  reviewPriorityTotal: number;
  learningStatus: LearningStatusSummary;
  guidanceDrafts: GuidanceDraftSummary[];
};

export type GuidanceAssessment = {
  objectiveId: string;
  label: string;
  status: LearningAssessmentStatus;
  suggestedStatus?: LearningAssessmentStatus | null;
  evidenceIds: string[];
  learnerGuidance: string;
  instructorGuidance: string;
  followUpQuestion: string;
};

export type GuidanceDraft = {
  id: string;
  recordId: string;
  knowledgeId: string;
  status: GuidanceStatus;
  modelId: string;
  inputVersion: number;
  summary: string;
  learnerGuidance: string;
  instructorGuidance?: string | null;
  assessments: GuidanceAssessment[];
  generatedAt: string;
  publishedAt?: string | null;
  publishedByUserId?: string | null;
  updatedAt: string;
};

export type GuidanceUpdatePayload = {
  summary?: string;
  learnerGuidance?: string;
  instructorGuidance?: string;
};

export type LearningAnalysisStatus = "draft" | "reviewed";

export type LearningObjectiveTrend = {
  objectiveId: string;
  label: string;
  recordCount: number;
  confirmedCount: number;
  partiallyConfirmedCount: number;
  notEvidencedCount: number;
  needsFollowUpCount: number;
  notApplicableCount: number;
};

export type LearningAnalysisScope = {
  dateFrom?: string | null;
  dateTo?: string | null;
  knowledgeId: string;
  profile?: string | null;
  recordStatus?: string | null;
  timezone: string;
  recordCount: number;
  recordIds: string[];
};

export type LearningAnalysisTheme = {
  themeId: string;
  title: string;
  summary: string;
  objectiveIds: string[];
  evidenceRecordIds: string[];
  learnerGuidance: string;
  instructorGuidance: string;
  followUpQuestion: string;
};

export type LearningPersonalAdviceFocus = {
  title: string;
  summary: string;
  objectiveIds: string[];
  evidenceRecordIds: string[];
  nextStep: string;
  followUpQuestion: string;
};

export type LearningPersonalAdvice = {
  respondentId: string;
  displayName: string;
  recordIds: string[];
  summary: string;
  focusAreas: LearningPersonalAdviceFocus[];
  nextSteps: string[];
  followUpQuestions: string[];
};

export type LearningAnalysisDraft = {
  id: string;
  knowledgeId: string;
  knowledgeName: string;
  status: LearningAnalysisStatus;
  modelId: string;
  scope: LearningAnalysisScope;
  objectiveTrends: LearningObjectiveTrend[];
  summary: string;
  trendSummary: string;
  learnerGuidance: string;
  instructorGuidance: string;
  themes: LearningAnalysisTheme[];
  personalAdvice: LearningPersonalAdvice[];
  generatedAt: string;
  reviewedAt?: string | null;
  reviewedByUserId?: string | null;
  updatedAt: string;
};

export type LearningAnalysisUpdatePayload = {
  summary?: string;
  trendSummary?: string;
  learnerGuidance?: string;
  instructorGuidance?: string;
};
