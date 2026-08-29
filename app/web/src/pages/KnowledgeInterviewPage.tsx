import { formatDate, formatNumber } from "../lib/date";
import {
  isInterviewConfigurationComplete,
} from "../features/interviews/interviewConfiguration";
import type { KnowledgeLayoutProps } from "../types/pageProps";
import { useI18n } from "../i18n";

const resumableStatuses = new Set(["draft", "in_progress", "returned"]);

export function KnowledgeInterviewPage(props: KnowledgeLayoutProps) {
  const { t, locale } = useI18n();
  const { selectedKnowledgeDb, selectedKnowledge } = props;
  if (!selectedKnowledgeDb || !selectedKnowledge) return null;

  const isConfigured = isInterviewConfigurationComplete(selectedKnowledge);
  const canCreateRecord = props.user?.role === "admin" || props.user?.role === "knowledge_manager";
  const basePath = `/knowledge-dbs/${selectedKnowledgeDb.id}/knowledges/${selectedKnowledge.id}`;
  const resumableRecords = props.records.filter((record) => resumableStatuses.has(record.status));

  function openRecord(recordId: string) {
    props.navigate(`${basePath}/records/${recordId}`);
  }

  return (
    <section className="panel page-stack interview-launch-page">
      <div className="panel-header interview-launch-header">
        <div>
          <h2>{t("knowledge.launch.title")}</h2>
        </div>
        {!isConfigured ? <span className="status-pill muted">{t("knowledge.launch.needsSetup")}</span> : null}
      </div>

      {!isConfigured && canCreateRecord ? (
        <div className="interview-launch-notice">
          <div>
            <strong>{t("knowledge.launch.setupTitle")}</strong>
            <p>{t("knowledge.launch.setupDescription")}</p>
          </div>
          <button className="primary" type="button" onClick={() => props.navigate(`${basePath}/settings`)}>
            {t("knowledge.launch.setupButton")}
          </button>
        </div>
      ) : null}

      {!isConfigured && !canCreateRecord ? (
        <p className="empty">{t("knowledge.launch.cannotStart")}</p>
      ) : null}

      {isConfigured && canCreateRecord ? (
        <section className="interview-launch-card">
          <div className="new-interview-form">
            <label className="sr-only" htmlFor="new-record-title">{t("knowledge.launch.recordTitle")}</label>
            <input
              id="new-record-title"
              value={props.newRecordTitle}
              onChange={(event) => props.setNewRecordTitle(event.target.value)}
              placeholder={t("knowledge.launch.recordTitlePlaceholder")}
            />
            <button type="button" className="primary" onClick={props.onCreateRecord}>
              {t("knowledge.launch.newInterview")}
            </button>
          </div>
          {props.recordNotice ? <p className="notice" role="status">{props.recordNotice}</p> : null}
        </section>
      ) : null}

      {!canCreateRecord && resumableRecords.length === 0 ? (
        <p className="empty">{t("knowledge.launch.accessibleHint")}</p>
      ) : null}

      {resumableRecords.length > 0 ? (
        <section className="interview-resume-section">
          <div className="section-title-row compact-row">
            <div>
              <h3>{t("knowledge.launch.inProgressTitle")}</h3>
            </div>
            <span className="counter">{formatNumber(resumableRecords.length, locale)}</span>
          </div>
          <div className="interview-resume-list">
            {resumableRecords.map((record) => (
              <button
                className="interview-resume-card"
                key={record.id}
                type="button"
                onClick={() => openRecord(record.id)}
                aria-label={t("knowledge.launch.resumeAria", { title: record.title })}
              >
                <div className="interview-resume-card-main">
                  <strong>{record.title}</strong>
                  <span>{record.targetEquipment || record.targetProcess || selectedKnowledge.name}</span>
                  <small>{t("knowledge.launch.lastUpdated", { date: formatDate(record.updatedAt, locale) })}</small>
                </div>
                <div className="interview-resume-card-actions">
                  <span className={record.status === "in_progress" ? "status-pill active" : "status-pill muted"}>
                    {t(`interview.status.${record.status}`)}
                  </span>
                </div>
              </button>
            ))}
          </div>
        </section>
      ) : null}

    </section>
  );
}
