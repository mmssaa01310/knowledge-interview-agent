import type { DocumentSummary } from "../../lib/api";
import { useI18n } from "../../i18n";
import { formatNumber } from "../../lib/date";

type DocumentsSectionProps = {
  documents: DocumentSummary[];
};

export function DocumentsSection({ documents }: DocumentsSectionProps) {
  const { t, locale } = useI18n();
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">{t("knowledge.documents.title")}</p>
          <h2>{t("knowledge.documents.title")}</h2>
          <p className="lede">{t("knowledge.documents.description")}</p>
        </div>
        <button className="primary">{t("knowledge.documents.addButton")}</button>
      </div>
      <div className="table-list">
        <div className="table-row table-head document-row">
          <span>{t("knowledge.documents.file")}</span>
          <span>{t("knowledge.documents.type")}</span>
          <span>{t("knowledge.documents.ingestionStatus")}</span>
          <span>{t("knowledge.documents.readStatus")}</span>
        </div>
        {documents.length === 0 ? (
          <p className="empty">{t("knowledge.documents.empty")}</p>
        ) : documents.map((doc) => (
          <div key={doc.id} className="table-row document-row">
            <span>
              <strong>{doc.fileName}</strong>
              <small>{t("knowledge.documents.progress", { value: formatNumber(doc.progressPercent, locale) })}</small>
            </span>
            <span>{doc.contentType}</span>
            <span><span className="status-pill">{t(`knowledge.documents.ingestionStatusLabels.${doc.ingestionStatus}`)}</span></span>
            <span><span className="status-pill muted">{t("knowledge.documents.readStatusLabels.unread")}</span></span>
          </div>
        ))}
      </div>
    </section>
  );
}
