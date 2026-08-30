import { useEffect, useMemo, useState } from "react";
import { useI18n } from "../i18n";
import {
  ApiError,
  fetchAdminDashboard,
  fetchLearningAnalyses,
  generateLearningAnalysis,
  reviewLearningAnalysis,
  updateLearningAnalysis,
  type UserProfile,
} from "../lib/api";
import { formatDate, formatNumber } from "../lib/date";
import type {
  AdminDashboard,
  DashboardFilters,
  LearningAnalysisDraft,
  LearningAnalysisUpdatePayload,
  RecordReviewPriority,
} from "../types/dashboard";
import type { Knowledge } from "@ai-interviewer/shared-types";
import { OptionPicker } from "../components/ui/OptionPicker";

type AdminDashboardPageProps = {
  user: UserProfile | null;
  knowledges: Knowledge[];
  onNavigate: (path: string) => void;
};

type DashboardTab = "analysis" | "learning_support";

type LearningAnalysisForm = Required<LearningAnalysisUpdatePayload>;

const emptyLearningAnalysisForm: LearningAnalysisForm = {
  summary: "",
  trendSummary: "",
  learnerGuidance: "",
  instructorGuidance: "",
};

function learningAnalysisFormFromDraft(draft: LearningAnalysisDraft): LearningAnalysisForm {
  return {
    summary: draft.summary,
    trendSummary: draft.trendSummary,
    learnerGuidance: draft.learnerGuidance,
    instructorGuidance: draft.instructorGuidance,
  };
}

export function AdminDashboardPage({ user, knowledges, onNavigate }: AdminDashboardPageProps) {
  const { t, locale } = useI18n();
  const canViewDashboard = user?.role === "admin" || user?.role === "knowledge_manager";
  const [filters, setFilters] = useState<DashboardFilters>({});
  const [appliedFilters, setAppliedFilters] = useState<DashboardFilters>({});
  const [dashboard, setDashboard] = useState<AdminDashboard | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [activeTab, setActiveTab] = useState<DashboardTab>("analysis");
  const [learningAnalyses, setLearningAnalyses] = useState<LearningAnalysisDraft[]>([]);
  const [selectedLearningAnalysis, setSelectedLearningAnalysis] = useState<LearningAnalysisDraft | null>(null);
  const [learningAnalysisForm, setLearningAnalysisForm] = useState<LearningAnalysisForm>(emptyLearningAnalysisForm);
  const [learningAnalysisError, setLearningAnalysisError] = useState("");
  const [learningAnalysisBusyKey, setLearningAnalysisBusyKey] = useState<string | null>(null);
  const appliedFilterKey = JSON.stringify(appliedFilters);

  async function loadDashboard() {
    setIsLoading(true);
    setError("");
    try {
      const [nextDashboard, nextLearningAnalyses] = await Promise.all([
        fetchAdminDashboard(appliedFilters),
        fetchLearningAnalyses(appliedFilters.knowledgeId),
      ]);
      setDashboard(nextDashboard);
      setLearningAnalyses(nextLearningAnalyses);
    } catch (loadError) {
      console.error("Failed to load admin dashboard", loadError);
      setError(
        loadError instanceof ApiError && loadError.status === 403
          ? t("dashboard.errors.permission")
          : t("dashboard.errors.load"),
      );
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    if (!canViewDashboard) return;
    setSelectedLearningAnalysis(null);
    setLearningAnalysisForm(emptyLearningAnalysisForm);
    void loadDashboard();
  }, [user?.role, appliedFilterKey]);

  const maxTrendValue = useMemo(() => {
    if (!dashboard?.timeSeries.length) return 1;
    return Math.max(
      1,
      ...dashboard.timeSeries.flatMap((point) => [
        point.createdCount,
        point.submittedCount,
        point.approvedCount,
      ]),
    );
  }, [dashboard?.timeSeries]);

  if (!user) {
    return <section className="panel dashboard-page"><p className="empty">{t("common.loading")}</p></section>;
  }

  if (!canViewDashboard) {
    return (
      <section className="panel dashboard-page">
        <p className="notice error">{t("dashboard.errors.permission")}</p>
      </section>
    );
  }

  function updateFilter(key: keyof DashboardFilters, value: string) {
    setFilters((current) => ({
      ...current,
      [key]: value || undefined,
    }));
  }

  function submitFilters(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAppliedFilters({ ...filters });
  }

  function resetFilters() {
    setFilters({});
    setAppliedFilters({});
  }

  function handleLearningAnalysisKnowledgeChange(knowledgeId: string) {
    const nextKnowledgeId = knowledgeId || undefined;
    setFilters((current) => ({ ...current, knowledgeId: nextKnowledgeId }));
    setAppliedFilters((current) => ({ ...current, knowledgeId: nextKnowledgeId }));
    setLearningAnalysisError("");
  }

  function openReviewRecord(item: RecordReviewPriority) {
    onNavigate(
      `/knowledge-dbs/${item.knowledgeDbId}/knowledges/${item.knowledgeId}/records/${item.recordId}`,
    );
  }

  function reasonLabel(code: string, targetLabel?: string | null) {
    const translated = t(`dashboard.reasons.${code}`, { label: targetLabel ?? "" });
    return translated === `dashboard.reasons.${code}` ? targetLabel || code : translated;
  }

  function handleSelectLearningAnalysis(analysis: LearningAnalysisDraft) {
    setSelectedLearningAnalysis(analysis);
    setLearningAnalysisForm(learningAnalysisFormFromDraft(analysis));
    setLearningAnalysisError("");
  }

  async function handleGenerateLearningAnalysis() {
    if (!appliedFilters.knowledgeId || analysisRecordCount < 2 || learningAnalysisBusyKey) return;
    const key = "generate-learning-analysis";
    setLearningAnalysisBusyKey(key);
    setLearningAnalysisError("");
    try {
      const draft = await generateLearningAnalysis(appliedFilters);
      setSelectedLearningAnalysis(draft);
      setLearningAnalysisForm(learningAnalysisFormFromDraft(draft));
      await loadDashboard();
    } catch (analysisError) {
      console.error("Failed to generate learning analysis", analysisError);
      setLearningAnalysisError(
        analysisError instanceof ApiError && analysisError.detail === "learning_analysis_requires_multiple_records"
          ? t("dashboard.analysis.requiresMultipleRecords")
          : t("dashboard.errors.analysisGenerate"),
      );
    } finally {
      setLearningAnalysisBusyKey(null);
    }
  }

  async function handleSaveLearningAnalysis() {
    if (!selectedLearningAnalysis || learningAnalysisBusyKey) return;
    const key = `save-learning-analysis-${selectedLearningAnalysis.id}`;
    setLearningAnalysisBusyKey(key);
    setLearningAnalysisError("");
    try {
      const updated = await updateLearningAnalysis(selectedLearningAnalysis.id, learningAnalysisForm);
      setSelectedLearningAnalysis(updated);
      setLearningAnalysisForm(learningAnalysisFormFromDraft(updated));
      await loadDashboard();
    } catch (saveError) {
      console.error("Failed to save learning analysis", saveError);
      setLearningAnalysisError(t("dashboard.errors.analysisSave"));
    } finally {
      setLearningAnalysisBusyKey(null);
    }
  }

  async function handleReviewLearningAnalysis() {
    if (!selectedLearningAnalysis || learningAnalysisBusyKey || selectedLearningAnalysis.status === "reviewed") return;
    const key = `review-learning-analysis-${selectedLearningAnalysis.id}`;
    setLearningAnalysisBusyKey(key);
    setLearningAnalysisError("");
    try {
      const reviewed = await reviewLearningAnalysis(selectedLearningAnalysis.id);
      setSelectedLearningAnalysis(reviewed);
      setLearningAnalysisForm(learningAnalysisFormFromDraft(reviewed));
      await loadDashboard();
    } catch (reviewError) {
      console.error("Failed to review learning analysis", reviewError);
      setLearningAnalysisError(t("dashboard.errors.analysisReview"));
    } finally {
      setLearningAnalysisBusyKey(null);
    }
  }

  const totals = dashboard?.totals;
  const dashboardTimezone = dashboard?.filters.timezone;
  const analysisRecordCount = appliedFilters.knowledgeId ? totals?.recordCount ?? 0 : 0;

  return (
    <section className="dashboard-page page-stack">
      <header className="dashboard-header">
        <div>
          <button type="button" className="ghost compact" onClick={() => onNavigate("/knowledge-dbs")}>
            ← {t("dashboard.backToKnowledge")}
          </button>
          <p className="eyebrow">KIKIORI</p>
          <h1>{t("dashboard.title")}</h1>
          <p className="dashboard-description">{t("dashboard.description")}</p>
        </div>
        <p className="dashboard-safety-note">{t("dashboard.activityNote")}</p>
      </header>

      <form className="dashboard-filters" onSubmit={submitFilters}>
        <label>
          {t("dashboard.filters.from")}
          <input type="date" value={filters.dateFrom ?? ""} onChange={(event) => updateFilter("dateFrom", event.target.value)} />
        </label>
        <label>
          {t("dashboard.filters.to")}
          <input type="date" value={filters.dateTo ?? ""} onChange={(event) => updateFilter("dateTo", event.target.value)} />
        </label>
        <label>
          <span>{t("dashboard.filters.knowledge")}</span>
          <OptionPicker
            value={filters.knowledgeId ?? ""}
            options={[
              { value: "", label: t("dashboard.filters.all") },
              ...knowledges.map((knowledge) => ({ value: knowledge.id, label: knowledge.name })),
            ]}
            onChange={(value) => updateFilter("knowledgeId", value)}
            ariaLabel={t("dashboard.filters.knowledge")}
            searchable={knowledges.length > 6}
            searchPlaceholder={t("dashboard.filters.knowledge")}
            emptyLabel={t("dashboard.filters.all")}
          />
        </label>
        <label>
          <span>{t("dashboard.filters.profile")}</span>
          <OptionPicker
            value={filters.profile ?? ""}
            options={[
              { value: "", label: t("dashboard.filters.all") },
              { value: "fixed_form", label: t("interview.profile.fixed_form") },
              { value: "business_process", label: t("interview.profile.business_process") },
              { value: "system_requirement", label: t("interview.profile.system_requirement") },
            ]}
            onChange={(value) => updateFilter("profile", value)}
            ariaLabel={t("dashboard.filters.profile")}
            emptyLabel={t("dashboard.filters.all")}
          />
        </label>
        <label>
          <span>{t("dashboard.filters.status")}</span>
          <OptionPicker
            value={filters.recordStatus ?? ""}
            options={[
              { value: "", label: t("dashboard.filters.all") },
              { value: "in_progress", label: t("interview.status.in_progress") },
              { value: "submitted", label: t("interview.status.submitted") },
              { value: "returned", label: t("interview.status.returned") },
              { value: "approved", label: t("interview.status.approved") },
            ]}
            onChange={(value) => updateFilter("recordStatus", value)}
            ariaLabel={t("dashboard.filters.status")}
            emptyLabel={t("dashboard.filters.all")}
          />
        </label>
        <div className="dashboard-filter-actions">
          <button type="submit" className="primary" disabled={isLoading}>{t("dashboard.filters.apply")}</button>
          <button type="button" className="ghost" onClick={resetFilters} disabled={isLoading}>{t("dashboard.filters.reset")}</button>
        </div>
      </form>

      <div className="dashboard-tabs" role="tablist" aria-label={t("dashboard.tabs.label")}>
        <button
          id="dashboard-tab-analysis"
          type="button"
          role="tab"
          aria-selected={activeTab === "analysis"}
          aria-controls="dashboard-tabpanel"
          className="dashboard-tab"
          onClick={() => setActiveTab("analysis")}
        >
          {t("dashboard.tabs.analysis")}
        </button>
        <button
          id="dashboard-tab-learning-support"
          type="button"
          role="tab"
          aria-selected={activeTab === "learning_support"}
          aria-controls="dashboard-tabpanel"
          className="dashboard-tab"
          onClick={() => setActiveTab("learning_support")}
        >
          {t("dashboard.tabs.learningSupport")}
        </button>
      </div>

      {isLoading && !dashboard ? <p className="notice" role="status">{t("dashboard.loading")}</p> : null}
      {error ? <p className="notice error" role="alert">{error}</p> : null}

      {dashboard ? (
        <div
          className={`dashboard-tabpanel dashboard-tabpanel-${activeTab}`}
          role="tabpanel"
          id="dashboard-tabpanel"
          aria-labelledby={activeTab === "analysis" ? "dashboard-tab-analysis" : "dashboard-tab-learning-support"}
          tabIndex={0}
        >
          <div className="dashboard-kpi-grid">
            <div className="dashboard-kpi"><span>{t("dashboard.totals.knowledge")}</span><strong>{formatNumber(totals?.knowledgeCount ?? 0, locale)}</strong></div>
            <div className="dashboard-kpi"><span>{t("dashboard.totals.records")}</span><strong>{formatNumber(totals?.recordCount ?? 0, locale)}</strong></div>
            <div className="dashboard-kpi"><span>{t("dashboard.totals.pending")}</span><strong>{formatNumber(totals?.pendingReviewCount ?? 0, locale)}</strong></div>
            <div className="dashboard-kpi priority-kpi"><span>{t("dashboard.totals.priority")}</span><strong>{formatNumber((totals?.highPriorityCount ?? 0) + (totals?.mediumPriorityCount ?? 0), locale)}</strong><small>{t("dashboard.totals.priorityBreakdown", { high: totals?.highPriorityCount ?? 0, medium: totals?.mediumPriorityCount ?? 0 })}</small></div>
          </div>

          <div className="dashboard-content-grid">
            <section className="dashboard-card dashboard-trend-card">
              <div className="dashboard-section-heading"><div><p className="eyebrow">{t("dashboard.trend.eyebrow")}</p><h2>{t("dashboard.trend.title")}</h2></div></div>
              {dashboard.timeSeries.length === 0 ? <p className="empty">{t("dashboard.trend.empty")}</p> : (
                <div className="dashboard-trend-list">
                  {dashboard.timeSeries.map((point) => (
                    <div className="dashboard-trend-row" key={point.date}>
                      <time dateTime={point.date}>{point.date}</time>
                      <div className="dashboard-trend-bars">
                        <span className="dashboard-bar created" style={{ "--bar-size": `${(point.createdCount / maxTrendValue) * 100}%` } as React.CSSProperties} title={t("dashboard.trend.created", { count: point.createdCount })} />
                        <span className="dashboard-bar submitted" style={{ "--bar-size": `${(point.submittedCount / maxTrendValue) * 100}%` } as React.CSSProperties} title={t("dashboard.trend.submitted", { count: point.submittedCount })} />
                        <span className="dashboard-bar approved" style={{ "--bar-size": `${(point.approvedCount / maxTrendValue) * 100}%` } as React.CSSProperties} title={t("dashboard.trend.approved", { count: point.approvedCount })} />
                      </div>
                      <span className="dashboard-trend-counts">{formatNumber(point.createdCount, locale)} / {formatNumber(point.submittedCount, locale)} / {formatNumber(point.approvedCount, locale)}</span>
                    </div>
                  ))}
                </div>
              )}
              <p className="dashboard-legend"><span className="legend-dot created" />{t("dashboard.trend.createdLabel")} <span className="legend-dot submitted" />{t("dashboard.trend.submittedLabel")} <span className="legend-dot approved" />{t("dashboard.trend.approvedLabel")}</p>
            </section>

            <section className="dashboard-card">
              <div className="dashboard-section-heading"><div><p className="eyebrow">{t("dashboard.learning.eyebrow")}</p><h2>{t("dashboard.learning.title")}</h2></div></div>
              <div className="dashboard-status-list">
                {(["confirmed", "partiallyConfirmed", "notEvidenced", "needsFollowUp", "notApplicable"] as const).map((status) => (
                  <div key={status}><span>{t(`dashboard.learning.status.${status}`)}</span><strong>{formatNumber(dashboard.learningStatus[status], locale)}</strong></div>
                ))}
              </div>
              <p className="form-help">{t("dashboard.learning.description")}</p>
            </section>
          </div>

          <section className="dashboard-card dashboard-analysis-card">
            <div className="dashboard-section-heading">
              <div>
                <p className="eyebrow">{t("dashboard.analysis.eyebrow")}</p>
                <h2>{t("dashboard.analysis.title")}</h2>
                <p>{t("dashboard.analysis.description")}</p>
              </div>
              <span className="dashboard-analysis-safety">{t("dashboard.analysis.safety")}</span>
            </div>
            <div className="dashboard-analysis-toolbar">
              <div className="dashboard-analysis-scope">
                <label className="dashboard-analysis-knowledge-select">
                  <span>{t("dashboard.analysis.knowledgeLabel")}</span>
                  <OptionPicker
                    value={appliedFilters.knowledgeId ?? ""}
                    options={[
                      { value: "", label: t("dashboard.analysis.selectKnowledge") },
                      ...knowledges.map((knowledge) => ({ value: knowledge.id, label: knowledge.name })),
                    ]}
                    onChange={handleLearningAnalysisKnowledgeChange}
                    ariaLabel={t("dashboard.analysis.knowledgeLabel")}
                    searchable={knowledges.length > 6}
                    searchPlaceholder={t("dashboard.analysis.knowledgeLabel")}
                    emptyLabel={t("dashboard.analysis.selectKnowledge")}
                  />
                </label>
                <small>{appliedFilters.knowledgeId
                  ? t("dashboard.analysis.scopeRecords", { count: formatNumber(analysisRecordCount, locale) })
                  : t("dashboard.analysis.scopeSelectFirst")}</small>
              </div>
              <button
                type="button"
                className="primary"
                onClick={() => void handleGenerateLearningAnalysis()}
                disabled={!appliedFilters.knowledgeId || analysisRecordCount < 2 || Boolean(learningAnalysisBusyKey)}
              >
                {learningAnalysisBusyKey === "generate-learning-analysis" ? t("dashboard.analysis.generating") : t("dashboard.analysis.generate")}
              </button>
            </div>
            {!appliedFilters.knowledgeId ? <p className="form-help">{t("dashboard.analysis.selectKnowledgeHelp")}</p> : null}
            {appliedFilters.knowledgeId && analysisRecordCount < 2 ? <p className="form-help">{t("dashboard.analysis.requiresMultipleRecords")}</p> : null}
            {learningAnalysisError ? <p className="notice error" role="alert">{learningAnalysisError}</p> : null}
            {learningAnalyses.length > 0 ? (
              <div className="dashboard-analysis-list" role="list" aria-label={t("dashboard.analysis.savedLabel")}>
                {learningAnalyses.map((analysis) => (
                  <button
                    type="button"
                    className={`dashboard-analysis-row${selectedLearningAnalysis?.id === analysis.id ? " selected" : ""}`}
                    key={analysis.id}
                    onClick={() => handleSelectLearningAnalysis(analysis)}
                    disabled={Boolean(learningAnalysisBusyKey)}
                  >
                    <span>
                      <strong>{analysis.knowledgeName}</strong>
                      <small>{t("dashboard.analysis.savedMeta", { count: formatNumber(analysis.scope.recordCount, locale), date: formatDate(analysis.updatedAt, locale, dashboardTimezone) })}</small>
                    </span>
                    <span className={`dashboard-analysis-status ${analysis.status}`}>{t(`dashboard.analysis.status.${analysis.status}`)}</span>
                  </button>
                ))}
              </div>
            ) : null}
            {selectedLearningAnalysis ? (
              <div className="dashboard-analysis-editor">
                <div className="dashboard-analysis-editor-heading">
                  <div>
                    <h3>{t("dashboard.analysis.editorTitle")}</h3>
                    <p>{t("dashboard.analysis.editorMeta", { model: selectedLearningAnalysis.modelId, count: formatNumber(selectedLearningAnalysis.scope.recordCount, locale) })}</p>
                  </div>
                  <span className={`dashboard-analysis-status ${selectedLearningAnalysis.status}`}>{t(`dashboard.analysis.status.${selectedLearningAnalysis.status}`)}</span>
                </div>
                <div className="dashboard-analysis-form">
                  <label>
                    {t("dashboard.analysis.summary")}
                    <textarea value={learningAnalysisForm.summary} onChange={(event) => setLearningAnalysisForm((current) => ({ ...current, summary: event.target.value }))} disabled={selectedLearningAnalysis.status === "reviewed"} />
                  </label>
                  <label>
                    {t("dashboard.analysis.trendSummary")}
                    <textarea value={learningAnalysisForm.trendSummary} onChange={(event) => setLearningAnalysisForm((current) => ({ ...current, trendSummary: event.target.value }))} disabled={selectedLearningAnalysis.status === "reviewed"} />
                  </label>
                  <label>
                    {t("dashboard.analysis.learner")}
                    <textarea value={learningAnalysisForm.learnerGuidance} onChange={(event) => setLearningAnalysisForm((current) => ({ ...current, learnerGuidance: event.target.value }))} disabled={selectedLearningAnalysis.status === "reviewed"} />
                  </label>
                  <label>
                    {t("dashboard.analysis.instructor")}
                    <textarea value={learningAnalysisForm.instructorGuidance} onChange={(event) => setLearningAnalysisForm((current) => ({ ...current, instructorGuidance: event.target.value }))} disabled={selectedLearningAnalysis.status === "reviewed"} />
                  </label>
                </div>
                <div className="dashboard-analysis-trends">
                  <div className="dashboard-analysis-subheading"><h4>{t("dashboard.analysis.trendsTitle")}</h4><small>{t("dashboard.analysis.trendsDescription")}</small></div>
                  <div className="dashboard-analysis-trend-list">
                    {selectedLearningAnalysis.objectiveTrends.map((trend) => (
                      <div className="dashboard-analysis-trend-row" key={trend.objectiveId}>
                        <span><strong>{trend.label}</strong><small>{t("dashboard.analysis.trendRecords", { count: formatNumber(trend.recordCount, locale) })}</small></span>
                        <span className="dashboard-analysis-trend-counts" title={t("dashboard.analysis.trendCountTitle")}>{formatNumber(trend.confirmedCount, locale)} / {formatNumber(trend.partiallyConfirmedCount, locale)} / {formatNumber(trend.notEvidencedCount, locale)} / {formatNumber(trend.needsFollowUpCount, locale)} / {formatNumber(trend.notApplicableCount, locale)}</span>
                      </div>
                    ))}
                  </div>
                </div>
                {selectedLearningAnalysis.themes.length > 0 ? (
                  <div className="dashboard-analysis-themes">
                    <h4>{t("dashboard.analysis.themesTitle")}</h4>
                    {selectedLearningAnalysis.themes.map((theme) => (
                      <article className="dashboard-analysis-theme" key={theme.themeId}>
                        <h5>{theme.title}</h5>
                        <p>{theme.summary}</p>
                        <small>{t("dashboard.analysis.themeEvidence", { count: formatNumber(theme.evidenceRecordIds.length, locale) })}</small>
                        {theme.followUpQuestion ? <p className="dashboard-analysis-follow-up">{theme.followUpQuestion}</p> : null}
                      </article>
                    ))}
                  </div>
                ) : null}
                <div className="dashboard-analysis-personal">
                  <div className="dashboard-analysis-subheading">
                    <div>
                      <h4>{t("dashboard.analysis.personalTitle")}</h4>
                      <small>{t("dashboard.analysis.personalDescription")}</small>
                    </div>
                  </div>
                  {selectedLearningAnalysis.personalAdvice.length > 0 ? (
                    <div className="dashboard-analysis-personal-list" role="list">
                      {selectedLearningAnalysis.personalAdvice.map((advice) => (
                        <article className="dashboard-analysis-personal-card" key={advice.respondentId}>
                          <div className="dashboard-analysis-personal-heading">
                            <h5>{advice.displayName}</h5>
                            <small>{t("dashboard.analysis.personalRecords", { count: formatNumber(advice.recordIds.length, locale) })}</small>
                          </div>
                          <p>{advice.summary}</p>
                          {advice.focusAreas.length > 0 ? (
                            <div className="dashboard-analysis-personal-focus">
                              <strong>{t("dashboard.analysis.personalFocusTitle")}</strong>
                              {advice.focusAreas.map((focus) => (
                                <div className="dashboard-analysis-personal-focus-item" key={`${advice.respondentId}-${focus.title}`}>
                                  <h6>{focus.title}</h6>
                                  <p>{focus.summary}</p>
                                  <small>{t("dashboard.analysis.personalEvidence", { count: formatNumber(focus.evidenceRecordIds.length, locale) })} · {focus.nextStep}</small>
                                  {focus.followUpQuestion ? <p className="dashboard-analysis-follow-up">{focus.followUpQuestion}</p> : null}
                                </div>
                              ))}
                            </div>
                          ) : null}
                          {advice.nextSteps.length > 0 ? (
                            <div className="dashboard-analysis-personal-list-section">
                              <strong>{t("dashboard.analysis.personalNextSteps")}</strong>
                              <ul>
                                {advice.nextSteps.map((step) => <li key={step}>{step}</li>)}
                              </ul>
                            </div>
                          ) : null}
                          {advice.followUpQuestions.length > 0 ? (
                            <div className="dashboard-analysis-personal-list-section">
                              <strong>{t("dashboard.analysis.personalQuestions")}</strong>
                              <ul>
                                {advice.followUpQuestions.map((question) => <li key={question}>{question}</li>)}
                              </ul>
                            </div>
                          ) : null}
                        </article>
                      ))}
                    </div>
                  ) : (
                    <p className="empty">{t("dashboard.analysis.personalEmpty")}</p>
                  )}
                </div>
                <div className="dashboard-review-actions">
                  {selectedLearningAnalysis.status !== "reviewed" ? <button type="button" className="ghost" onClick={() => void handleSaveLearningAnalysis()} disabled={Boolean(learningAnalysisBusyKey)}>{learningAnalysisBusyKey === `save-learning-analysis-${selectedLearningAnalysis.id}` ? t("common.saving") : t("common.save")}</button> : null}
                  {selectedLearningAnalysis.status !== "reviewed" ? <button type="button" className="primary" onClick={() => void handleReviewLearningAnalysis()} disabled={Boolean(learningAnalysisBusyKey)}>{learningAnalysisBusyKey === `review-learning-analysis-${selectedLearningAnalysis.id}` ? t("dashboard.analysis.reviewing") : t("dashboard.analysis.review")}</button> : null}
                </div>
              </div>
            ) : null}
          </section>

          <section className="dashboard-card">
            <div className="dashboard-section-heading"><div><p className="eyebrow">{t("dashboard.knowledge.eyebrow")}</p><h2>{t("dashboard.knowledge.title")}</h2></div></div>
            {dashboard.knowledgeSummaries.length === 0 ? <p className="empty">{t("dashboard.empty")}</p> : (
              <div className="dashboard-table dashboard-knowledge-table">
                <div className="dashboard-table-row dashboard-table-head"><span>{t("dashboard.knowledge.name")}</span><span>{t("dashboard.knowledge.records")}</span><span>{t("dashboard.knowledge.statuses")}</span><span>{t("dashboard.knowledge.priority")}</span></div>
                {dashboard.knowledgeSummaries.map((knowledge) => (
                  <div className="dashboard-table-row" key={knowledge.knowledgeId}>
                    <span><strong>{knowledge.knowledgeName}</strong><small>{knowledge.profile ? t(`interview.profile.${knowledge.profile}`) : t("interview.profile.notSet")}</small></span>
                    <span>{formatNumber(knowledge.recordCount, locale)}</span>
                    <span>{formatNumber(knowledge.inProgressCount, locale)} / {formatNumber(knowledge.submittedCount, locale)} / {formatNumber(knowledge.approvedCount, locale)}</span>
                    <span>{formatNumber(knowledge.highPriorityCount + knowledge.mediumPriorityCount, locale)}</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          <div className="dashboard-content-grid">
            <section className="dashboard-card">
              <div className="dashboard-section-heading"><div><p className="eyebrow">{t("dashboard.activity.eyebrow")}</p><h2>{t("dashboard.activity.title")}</h2></div></div>
              {dashboard.activityByUser.length === 0 ? <p className="empty">{t("dashboard.activity.empty")}</p> : (
                <div className="dashboard-activity-list">
                  {dashboard.activityByUser.map((activity) => (
                    <div className="dashboard-activity-row" key={activity.userId}>
                      <div className="dashboard-activity-person"><strong>{activity.displayName}</strong><small>{t("dashboard.activity.records", { count: formatNumber(activity.recordCount, locale) })} · {activity.lastActivityAt ? formatDate(activity.lastActivityAt, locale, dashboardTimezone) : "-"}</small></div>
                      <div className="dashboard-activity-metrics">
                        <span><strong>{formatNumber(activity.answerCount, locale)}</strong>{t("dashboard.activity.answersLabel")}</span>
                        <span><strong>{formatNumber(activity.submittedCount, locale)}</strong>{t("dashboard.activity.submittedLabel")}</span>
                        <span><strong>{formatNumber(activity.confirmedCount, locale)}</strong>{t("dashboard.activity.confirmedLabel")}</span>
                        <span><strong>{formatNumber(activity.partiallyConfirmedCount, locale)}</strong>{t("dashboard.activity.partiallyConfirmedLabel")}</span>
                        <span><strong>{formatNumber(activity.notEvidencedCount, locale)}</strong>{t("dashboard.activity.notEvidencedLabel")}</span>
                        <span><strong>{formatNumber(activity.needsFollowUpCount, locale)}</strong>{t("dashboard.activity.needsFollowUpLabel")}</span>
                        <span><strong>{formatNumber(activity.notApplicableCount, locale)}</strong>{t("dashboard.activity.notApplicableLabel")}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
          </section>
          </div>

          <section className="dashboard-card dashboard-review-card">
            <div className="dashboard-section-heading"><div><p className="eyebrow">{t("dashboard.review.eyebrow")}</p><h2>{t("dashboard.review.title")}</h2><p>{t("dashboard.review.description", { count: dashboard.reviewPriorityTotal })}</p></div></div>
            {dashboard.reviewPriorities.length === 0 ? <p className="empty">{t("dashboard.review.empty")}</p> : (
              <div className="dashboard-review-list" role="list">
                {dashboard.reviewPriorities.map((item) => (
                  <details className="dashboard-review-item" key={item.recordId}>
                    <summary className="dashboard-review-summary">
                      <span className={`dashboard-priority ${item.level}`}>{t(`dashboard.priority.${item.level}`)}</span>
                      <span className="dashboard-review-title">{item.title}</span>
                      <span className="dashboard-review-meta">{item.knowledgeName} · {item.ownerDisplayName || t("common.notSet")}</span>
                      <span className="dashboard-review-reason-count">{t("dashboard.review.reasonCount", { count: formatNumber(item.reasons.length, locale) })}</span>
                    </summary>
                    <div className="dashboard-review-details">
                      <ul className="dashboard-reason-list">
                        {item.reasons.map((reason, index) => <li key={`${reason.code}-${reason.targetId ?? index}`}>{reasonLabel(reason.code, reason.targetLabel)}</li>)}
                      </ul>
                      <div className="dashboard-review-actions">
                        <button type="button" className="ghost compact" onClick={() => openReviewRecord(item)}>{t("dashboard.review.open")}</button>
                      </div>
                    </div>
                  </details>
                ))}
              </div>
            )}
          </section>

        </div>
      ) : null}
    </section>
  );
}
