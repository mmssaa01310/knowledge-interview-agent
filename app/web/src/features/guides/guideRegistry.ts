export type GuideId =
  | "getting-started"
  | "create-knowledge"
  | "configure-interview"
  | "configure-questions"
  | "run-interview"
  | "review-knowledge"
  | "revise-returned-record"
  | "browse-records"
  | "admin-analysis"
  | "admin-priorities"
  | "admin-learning";

export type GuideRoute =
  | "dashboard"
  | "knowledge-list"
  | "knowledge-interview-launch"
  | "knowledge-interview"
  | "knowledge-records"
  | "knowledge-record-detail"
  | "knowledge-settings";
export type GuidePlacement = "top" | "right" | "bottom" | "left";
export type GuideStepKind = "explain" | "action" | "checkpoint";
export type GuideSurface =
  | "dashboard"
  | "knowledge-list"
  | "knowledge-settings"
  | "interview-launch"
  | "records"
  | "record-detail"
  | "other";

export type GuideRecommendationContext = {
  currentPath: string;
  recordStatus?: string | null;
};

export type GuideStepDefinition = {
  id: string;
  target: string;
  repeatTarget?: string;
  maxRepeats?: number;
  fallbackTargets?: readonly string[];
  titleKey: string;
  descriptionKey: string;
  placement?: GuidePlacement;
  route?: GuideRoute;
  required?: boolean;
  openNavigationDrawer?: boolean;
  openKnowledgeDrawer?: boolean;
  activateSelector?: string;
  timeoutMs?: number;
  /** 操作待ちStepでは、利用者が操作後にこの要素が現れたことを確認して進める。 */
  kind?: GuideStepKind;
  completionTarget?: string;
  completeAfterAction?: boolean;
};

export type GuideDefinition = {
  id: GuideId;
  titleKey: string;
  descriptionKey: string;
  steps: readonly GuideStepDefinition[];
  roles?: readonly string[];
  recommendedSurfaces?: readonly GuideSurface[];
  recommendedRecordStatuses?: readonly string[];
};

const managementRoles = ["admin", "knowledge_manager"] as const;

const guideDefinitions: readonly GuideDefinition[] = [
  {
    id: "getting-started",
    titleKey: "guide.catalog.gettingStarted.title",
    descriptionKey: "guide.catalog.gettingStarted.description",
    recommendedSurfaces: ["knowledge-list", "interview-launch"],
    steps: [
      {
        id: "navigation",
        target: '[data-guide="navigation"]',
        fallbackTargets: ['[data-guide="navigation-trigger"]'],
        titleKey: "guide.steps.navigation.title",
        descriptionKey: "guide.steps.navigation.description",
        placement: "right",
        required: true,
        openNavigationDrawer: true,
      },
      {
        id: "knowledge-list",
        target: '[data-guide="knowledge-list"]',
        fallbackTargets: ['[data-guide="knowledge-navigation"]'],
        titleKey: "guide.steps.knowledgeList.title",
        descriptionKey: "guide.steps.knowledgeList.description",
        placement: "bottom",
      },
      {
        id: "knowledge-item",
        target: '[data-guide="knowledge-item"]',
        titleKey: "guide.steps.knowledgeItem.title",
        descriptionKey: "guide.steps.knowledgeItem.description",
        placement: "right",
        timeoutMs: 450,
      },
      {
        id: "user-menu",
        target: '[data-guide="user-menu"]',
        titleKey: "guide.steps.userMenu.title",
        descriptionKey: "guide.steps.userMenu.description",
        placement: "top",
        timeoutMs: 450,
      },
    ],
  },
  {
    id: "create-knowledge",
    titleKey: "guide.catalog.createKnowledge.title",
    descriptionKey: "guide.catalog.createKnowledge.description",
    roles: managementRoles,
    recommendedSurfaces: ["knowledge-list"],
    steps: [
      {
        id: "knowledge-list",
        target: '[data-guide="knowledge-list"]',
        fallbackTargets: ['[data-guide="knowledge-navigation"]'],
        titleKey: "guide.steps.knowledgeList.title",
        descriptionKey: "guide.steps.knowledgeList.description",
        placement: "bottom",
        route: "knowledge-list",
        required: true,
      },
      {
        id: "create-button",
        target: 'main [data-guide="knowledge-create"]',
        fallbackTargets: ['[data-guide="knowledge-create"]'],
        titleKey: "guide.steps.createKnowledge.title",
        descriptionKey: "guide.steps.createKnowledge.description",
        placement: "right",
        route: "knowledge-list",
        required: true,
        kind: "action",
        completionTarget: '[data-guide="knowledge-create-form"]',
      },
      {
        id: "create-form",
        target: '[data-guide="knowledge-create-form"]',
        titleKey: "guide.steps.createKnowledgeForm.title",
        descriptionKey: "guide.steps.createKnowledgeForm.description",
        placement: "bottom",
        required: true,
        kind: "action",
        completionTarget: '[data-guide="knowledge-settings"]',
        completeAfterAction: true,
      },
    ],
  },
  {
    id: "configure-interview",
    titleKey: "guide.catalog.configureInterview.title",
    descriptionKey: "guide.catalog.configureInterview.description",
    roles: managementRoles,
    recommendedSurfaces: ["knowledge-settings"],
    steps: [
      {
        id: "settings",
        target: '[data-guide="knowledge-settings"]',
        titleKey: "guide.steps.knowledgeSettings.title",
        descriptionKey: "guide.steps.knowledgeSettings.description",
        placement: "bottom",
        route: "knowledge-settings",
        required: true,
      },
      {
        id: "interview-settings",
        target: '[data-guide="interview-settings"]',
        titleKey: "guide.steps.interviewSettings.title",
        descriptionKey: "guide.steps.interviewSettings.description",
        placement: "right",
        route: "knowledge-settings",
        activateSelector: "#settings-tab-execution",
        required: true,
      },
      {
        id: "save-settings",
        target: '[data-guide="knowledge-confirm"]',
        titleKey: "guide.steps.knowledgeConfirm.title",
        descriptionKey: "guide.steps.knowledgeConfirm.description",
        placement: "top",
        route: "knowledge-settings",
        activateSelector: "#settings-tab-execution",
        required: true,
        kind: "checkpoint",
      },
    ],
  },
  {
    id: "configure-questions",
    titleKey: "guide.catalog.configureQuestions.title",
    descriptionKey: "guide.catalog.configureQuestions.description",
    roles: managementRoles,
    recommendedSurfaces: ["knowledge-settings"],
    steps: [
      {
        id: "settings",
        target: '[data-guide="knowledge-settings"]',
        titleKey: "guide.steps.knowledgeSettings.title",
        descriptionKey: "guide.steps.knowledgeSettings.description",
        placement: "bottom",
        route: "knowledge-settings",
        required: true,
      },
      {
        id: "question-settings",
        target: '[data-guide="question-settings"]',
        titleKey: "guide.steps.questionSettings.title",
        descriptionKey: "guide.steps.questionSettings.description",
        placement: "right",
        route: "knowledge-settings",
        activateSelector: "#settings-tab-fields",
        required: true,
      },
      {
        id: "question-add",
        target: '[data-guide="question-add"]',
        titleKey: "guide.steps.questionAdd.title",
        descriptionKey: "guide.steps.questionAdd.description",
        placement: "left",
        route: "knowledge-settings",
        activateSelector: "#settings-tab-fields",
      },
      {
        id: "question-edit",
        target: '[data-guide="knowledge-edit"]',
        titleKey: "guide.steps.knowledgeEdit.title",
        descriptionKey: "guide.steps.knowledgeEdit.description",
        placement: "right",
        route: "knowledge-settings",
        activateSelector: "#settings-tab-fields",
      },
      {
        id: "save-settings",
        target: '[data-guide="knowledge-confirm"]',
        titleKey: "guide.steps.knowledgeConfirm.title",
        descriptionKey: "guide.steps.knowledgeConfirm.description",
        placement: "top",
        route: "knowledge-settings",
        activateSelector: "#settings-tab-fields",
        required: true,
        kind: "checkpoint",
      },
    ],
  },
  {
    id: "run-interview",
    titleKey: "guide.catalog.runInterview.title",
    descriptionKey: "guide.catalog.runInterview.description",
    roles: ["admin", "knowledge_manager", "interviewer"],
    recommendedSurfaces: ["interview-launch", "record-detail"],
    recommendedRecordStatuses: ["draft", "in_progress"],
    steps: [
      {
        id: "interview-start",
        target: '[data-guide="interview-start"]',
        titleKey: "guide.steps.interviewStart.title",
        descriptionKey: "guide.steps.interviewStart.description",
        placement: "bottom",
        route: "knowledge-interview-launch",
        required: true,
      },
      {
        id: "interview-entry",
        target: '[data-guide="interview-entry"]',
        fallbackTargets: ['[data-guide="interview-resume"]'],
        titleKey: "guide.steps.interviewEntry.title",
        descriptionKey: "guide.steps.interviewEntry.description",
        placement: "bottom",
        route: "knowledge-interview-launch",
        required: true,
        kind: "action",
        completionTarget: '[data-guide="interview-pane"]',
      },
      {
        id: "interview-pane",
        target: '[data-guide="interview-pane"]',
        titleKey: "guide.steps.interview.title",
        descriptionKey: "guide.steps.interview.description",
        placement: "top",
        route: "knowledge-interview",
        required: true,
      },
      {
        id: "composer",
        target: '[data-guide="message-composer"]',
        titleKey: "guide.steps.composer.title",
        descriptionKey: "guide.steps.composer.description",
        placement: "top",
        route: "knowledge-interview",
      },
      {
        id: "knowledge-panel",
        target: '[data-guide="knowledge-pane"]',
        fallbackTargets: ['[data-guide="knowledge-toggle"]'],
        titleKey: "guide.steps.knowledge.title",
        descriptionKey: "guide.steps.knowledge.description",
        placement: "left",
        route: "knowledge-interview",
        openKnowledgeDrawer: true,
      },
    ],
  },
  {
    id: "review-knowledge",
    titleKey: "guide.catalog.reviewKnowledge.title",
    descriptionKey: "guide.catalog.reviewKnowledge.description",
    roles: managementRoles,
    recommendedSurfaces: ["records", "record-detail"],
    recommendedRecordStatuses: ["submitted"],
    steps: [
      {
        id: "records",
        target: '[data-guide="knowledge-records"]',
        titleKey: "guide.steps.records.title",
        descriptionKey: "guide.steps.records.description",
        placement: "top",
        route: "knowledge-records",
        required: true,
      },
      {
        id: "record-open",
        target: '[data-guide="record-open"]',
        titleKey: "guide.steps.recordItem.title",
        descriptionKey: "guide.steps.recordItem.description",
        placement: "top",
        route: "knowledge-records",
        kind: "action",
        completionTarget: '[data-guide="interview-pane"]',
      },
      {
        id: "record-detail",
        target: '[data-guide-record-review="true"]',
        fallbackTargets: ['[data-guide="knowledge-pane"]', '[data-guide="interview-pane"]'],
        titleKey: "guide.steps.recordDetail.title",
        descriptionKey: "guide.steps.recordDetail.description",
        placement: "left",
        route: "knowledge-record-detail",
        openKnowledgeDrawer: true,
      },
      {
        id: "knowledge-review",
        target: '[data-guide="knowledge-review"]',
        repeatTarget: '[data-guide="knowledge-review"]',
        maxRepeats: 3,
        titleKey: "guide.steps.knowledgeReview.title",
        descriptionKey: "guide.steps.knowledgeReview.description",
        placement: "left",
        route: "knowledge-record-detail",
        openKnowledgeDrawer: true,
      },
      {
        id: "knowledge-confirm",
        target: '[data-guide="knowledge-confirm"]',
        titleKey: "guide.steps.knowledgeConfirm.title",
        descriptionKey: "guide.steps.knowledgeConfirm.description",
        placement: "top",
        route: "knowledge-record-detail",
      },
    ],
  },
  {
    id: "revise-returned-record",
    titleKey: "guide.catalog.reviseReturnedRecord.title",
    descriptionKey: "guide.catalog.reviseReturnedRecord.description",
    roles: ["interviewer"],
    recommendedSurfaces: ["record-detail"],
    recommendedRecordStatuses: ["returned"],
    steps: [
      {
        id: "returned-note",
        target: '[data-guide="returned-note"]',
        titleKey: "guide.steps.returnedNote.title",
        descriptionKey: "guide.steps.returnedNote.description",
        placement: "bottom",
        route: "knowledge-record-detail",
        required: true,
      },
      {
        id: "composer",
        target: '[data-guide="message-composer"]',
        titleKey: "guide.steps.composer.title",
        descriptionKey: "guide.steps.composer.description",
        placement: "top",
        route: "knowledge-record-detail",
        required: true,
      },
      {
        id: "knowledge-panel",
        target: '[data-guide="knowledge-pane"]',
        fallbackTargets: ['[data-guide="knowledge-toggle"]'],
        titleKey: "guide.steps.knowledge.title",
        descriptionKey: "guide.steps.knowledge.description",
        placement: "left",
        route: "knowledge-record-detail",
        openKnowledgeDrawer: true,
      },
    ],
  },
  {
    id: "browse-records",
    titleKey: "guide.catalog.browseRecords.title",
    descriptionKey: "guide.catalog.browseRecords.description",
    roles: ["viewer"],
    recommendedSurfaces: ["records", "record-detail"],
    steps: [
      {
        id: "records",
        target: '[data-guide="knowledge-records"]',
        titleKey: "guide.steps.records.title",
        descriptionKey: "guide.steps.records.description",
        placement: "top",
        route: "knowledge-records",
        required: true,
      },
      {
        id: "record-open",
        target: '[data-guide="record-open"]',
        titleKey: "guide.steps.recordItem.title",
        descriptionKey: "guide.steps.recordItem.description",
        placement: "top",
        route: "knowledge-records",
        kind: "action",
        completionTarget: '[data-guide="interview-pane"]',
      },
      {
        id: "record-detail",
        target: '[data-guide="interview-pane"]',
        titleKey: "guide.steps.recordDetail.title",
        descriptionKey: "guide.steps.recordDetail.description",
        placement: "top",
        route: "knowledge-record-detail",
      },
    ],
  },
  {
    id: "admin-analysis",
    titleKey: "guide.catalog.adminAnalysis.title",
    descriptionKey: "guide.catalog.adminAnalysis.description",
    roles: managementRoles,
    recommendedSurfaces: ["dashboard"],
    steps: [
      {
        id: "dashboard",
        target: '[data-guide="admin-analysis"]',
        titleKey: "guide.steps.dashboard.title",
        descriptionKey: "guide.steps.dashboard.description",
        placement: "bottom",
        route: "dashboard",
        required: true,
      },
      {
        id: "dashboard-filters",
        target: '[data-guide="dashboard-filters"]',
        titleKey: "guide.steps.dashboardFilters.title",
        descriptionKey: "guide.steps.dashboardFilters.description",
        placement: "bottom",
        route: "dashboard",
        required: true,
      },
      {
        id: "dashboard-summary",
        target: '[data-guide="dashboard-summary"]',
        titleKey: "guide.steps.dashboardSummary.title",
        descriptionKey: "guide.steps.dashboardSummary.description",
        placement: "bottom",
        route: "dashboard",
        activateSelector: "#dashboard-tab-analysis",
      },
      {
        id: "dashboard-trend",
        target: '[data-guide="dashboard-trend"]',
        titleKey: "guide.steps.dashboardTrend.title",
        descriptionKey: "guide.steps.dashboardTrend.description",
        placement: "right",
        route: "dashboard",
        activateSelector: "#dashboard-tab-analysis",
      },
    ],
  },
  {
    id: "admin-priorities",
    titleKey: "guide.catalog.adminPriorities.title",
    descriptionKey: "guide.catalog.adminPriorities.description",
    roles: managementRoles,
    recommendedSurfaces: ["dashboard"],
    steps: [
      {
        id: "dashboard",
        target: '[data-guide="admin-analysis"]',
        titleKey: "guide.steps.dashboard.title",
        descriptionKey: "guide.steps.dashboard.description",
        placement: "bottom",
        route: "dashboard",
        required: true,
      },
      {
        id: "dashboard-filters",
        target: '[data-guide="dashboard-filters"]',
        titleKey: "guide.steps.dashboardFilters.title",
        descriptionKey: "guide.steps.dashboardFilters.description",
        placement: "bottom",
        route: "dashboard",
      },
      {
        id: "dashboard-review",
        target: '[data-guide="dashboard-review"]',
        titleKey: "guide.steps.dashboardReview.title",
        descriptionKey: "guide.steps.dashboardReview.description",
        placement: "top",
        route: "dashboard",
        activateSelector: "#dashboard-tab-analysis",
      },
    ],
  },
  {
    id: "admin-learning",
    titleKey: "guide.catalog.adminLearning.title",
    descriptionKey: "guide.catalog.adminLearning.description",
    roles: managementRoles,
    recommendedSurfaces: ["dashboard"],
    steps: [
      {
        id: "dashboard",
        target: '[data-guide="admin-analysis"]',
        titleKey: "guide.steps.dashboard.title",
        descriptionKey: "guide.steps.dashboard.description",
        placement: "bottom",
        route: "dashboard",
        required: true,
      },
      {
        id: "dashboard-tabs",
        target: '[data-guide="dashboard-tabs"]',
        titleKey: "guide.steps.dashboardTabs.title",
        descriptionKey: "guide.steps.dashboardTabs.description",
        placement: "bottom",
        route: "dashboard",
        required: true,
      },
      {
        id: "dashboard-learning",
        target: '[data-guide="dashboard-learning"]',
        titleKey: "guide.steps.dashboardLearning.title",
        descriptionKey: "guide.steps.dashboardLearning.description",
        placement: "left",
        route: "dashboard",
        activateSelector: "#dashboard-tab-analysis",
      },
      {
        id: "dashboard-analysis",
        target: '[data-guide="dashboard-analysis"]',
        titleKey: "guide.steps.dashboardAnalysis.title",
        descriptionKey: "guide.steps.dashboardAnalysis.description",
        placement: "top",
        route: "dashboard",
        activateSelector: "#dashboard-tab-learning-support",
      },
    ],
  },
];

export function getGuideDefinitions(userRole?: string) {
  return guideDefinitions.filter((definition) => !definition.roles || (userRole && definition.roles.includes(userRole)));
}

export function getGuideDefinition(id: GuideId | null | undefined, userRole?: string) {
  return getGuideDefinitions(userRole).find((definition) => definition.id === id) ?? null;
}

export function isGuideId(value: string): value is GuideId {
  return guideDefinitions.some((definition) => definition.id === value);
}

export function getGuideSurface(pathname: string): GuideSurface {
  if (pathname === "/dashboard") return "dashboard";
  if (/\/settings\/?$/.test(pathname)) return "knowledge-settings";
  if (/\/records\/[^/]+\/?$/.test(pathname)) return "record-detail";
  if (/\/records\/?$/.test(pathname)) return "records";
  if (/\/interview\/?$/.test(pathname) || /\/knowledges\/[^/]+\/?$/.test(pathname)) return "interview-launch";
  if (pathname === "/knowledge-dbs" || /^\/knowledge-dbs\/[^/]+\/?$/.test(pathname)) return "knowledge-list";
  return "other";
}

export function getRecommendedGuideDefinitions(userRole: string | undefined, context: GuideRecommendationContext) {
  const surface = getGuideSurface(context.currentPath);
  return getGuideDefinitions(userRole).filter((definition) => (
    definition.recommendedSurfaces?.includes(surface)
    && (!definition.recommendedRecordStatuses || Boolean(context.recordStatus && definition.recommendedRecordStatuses.includes(context.recordStatus)))
  ));
}

export function resolveGuideRoute(route: GuideRoute, pathname: string): string | null {
  if (route === "dashboard") return "/dashboard";
  const match = pathname.match(/^\/knowledge-dbs\/([^/]+)\/knowledges\/([^/]+)/);
  const knowledgePath = match ? `/knowledge-dbs/${match[1]}/knowledges/${match[2]}` : null;
  if (route === "knowledge-list") return "/knowledge-dbs";
  if (!knowledgePath) return null;
  if (route === "knowledge-interview-launch") return `${knowledgePath}/interview`;
  if (route === "knowledge-interview") {
    return /\/records\/[^/]+$/.test(pathname) ? pathname : `${knowledgePath}/interview`;
  }
  if (route === "knowledge-records") return `${knowledgePath}/records`;
  if (route === "knowledge-record-detail") {
    return /\/records\/[^/]+$/.test(pathname) ? pathname : null;
  }
  return `${knowledgePath}/settings`;
}
