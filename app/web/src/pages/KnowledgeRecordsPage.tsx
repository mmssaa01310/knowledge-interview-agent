import type { InterviewRecord } from "@ai-interviewer/shared-types";
import { formatDate, formatNumber } from "../lib/date";
import { useI18n } from "../i18n";
import type { KnowledgeLayoutProps } from "../types/pageProps";

export function KnowledgeRecordsPage(props: KnowledgeLayoutProps) {
  const { t, locale } = useI18n();
  const { selectedKnowledgeDb, selectedKnowledge } = props;
  if (!selectedKnowledgeDb || !selectedKnowledge) return null;

  const basePath = `/knowledge-dbs/${selectedKnowledgeDb.id}/knowledges/${selectedKnowledge.id}`;
  const isAdmin = props.user?.role === "admin";
  const isManagementUser = props.user?.role === "admin" || props.user?.role === "knowledge_manager";
  const recordCountLabel = t("common.itemCount", { count: formatNumber(props.records.length, locale) });

  function openRecord(recordId: string) {
    props.navigate(`${basePath}/records/${recordId}`);
  }

  function returnRecord(recordId: string) {
    const reviewNote = window.prompt(t("knowledge.records.reviewPrompt"), "");
    if (reviewNote?.trim()) {
      void props.onChangeRecordStatusForRecord(recordId, "returned", reviewNote.trim());
    }
  }

  function approveRecord(recordId: string) {
    if (window.confirm(t("knowledge.records.approvePrompt"))) {
      void props.onChangeRecordStatusForRecord(recordId, "approved");
    }
  }

  return (
    <section className="panel page-stack">
      <div className="panel-header">
        <div className="panel-header-title">
          <h2>{t("knowledge.records.title")}</h2>
          <span className="counter" aria-label={recordCountLabel}>{recordCountLabel}</span>
        </div>
      </div>

      {props.recordNotice ? <p className="notice" role="status">{props.recordNotice}</p> : null}

      <div className="table-list">
        <div className="table-row table-head records-workspace-row knowledge-records-row">
          <span>{t("knowledge.records.record")}</span>
          <span>{t("knowledge.records.assignee")}</span>
          <span>{t("knowledge.records.status")}</span>
          <span>{t("knowledge.records.updatedAt")}</span>
          <span>{t("knowledge.records.operation")}</span>
        </div>
        {props.records.length === 0 ? (
          <p className="empty">{t("knowledge.records.empty")}</p>
        ) : props.records.map((record) => (
          <div className="table-row records-workspace-row knowledge-records-row" key={record.id}>
            <span>
              <strong>{record.title}</strong>
              <small>{record.targetEquipment || record.targetProcess || "-"}</small>
            </span>
            <span>{record.ownerUserId || t("common.notSet")}</span>
            <span>
              <span className={record.status === "approved" ? "status-pill" : "status-pill muted"}>
                {t(`interview.status.${record.status}`)}
              </span>
            </span>
            <span>{formatDate(record.updatedAt, locale)}</span>
            <span className="inline-actions">
              <button className="ghost compact" type="button" onClick={() => openRecord(record.id)}>
                {props.user?.role === "viewer" ? t("knowledge.records.viewerAction") : t("knowledge.records.editAction")}
              </button>
              {isAdmin ? (
                <button className="danger compact" type="button" onClick={() => void props.onDeleteRecord(record.id)}>
                  {t("knowledge.records.delete")}
                </button>
              ) : null}
              {isManagementUser && record.status === "submitted" ? (
                <>
                  <button className="ghost compact" type="button" onClick={() => returnRecord(record.id)}>
                    {t("knowledge.records.return")}
                  </button>
                  <button className="primary compact" type="button" onClick={() => approveRecord(record.id)}>
                    {t("knowledge.records.approve")}
                  </button>
                </>
              ) : null}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
