import { ingestionStatuses, readStatuses } from "../features/documents/constants";
import { formatDate, formatNumber } from "../lib/date";
import { useI18n } from "../i18n";
import type { KnowledgeLayoutProps } from "../types/pageProps";

export function KnowledgeDocumentsContent(props: KnowledgeLayoutProps) {
  const { t, locale } = useI18n();

  function getReadStatusLabel(status: string) {
    return t(`knowledge.documents.readStatusLabels.${status}`);
  }

  function getIngestionStatusLabel(status: string) {
    return t(`knowledge.documents.ingestionStatusLabels.${ingestionStatuses.includes(status) ? status : "uploaded"}`);
  }

  return (
    <>
      <div className="inline-form wide">
        <input value={props.newDocumentName} onChange={(event) => props.setNewDocumentName(event.target.value)} placeholder={t("knowledge.documents.addPlaceholder")} />
        <button className="primary" onClick={props.onCreateDocument}>{t("knowledge.documents.addButton")}</button>
        <button
          type="button"
          className="icon-info-button"
          aria-label={t("knowledge.documents.supportedAria")}
          data-tooltip={t("knowledge.documents.supportedTooltip")}
        >
          <span aria-hidden="true">i</span>
        </button>
      </div>
      <div className="table-list">
        <div className="table-row document-detail-row table-head"><span>{t("knowledge.documents.file")}</span><span>{t("knowledge.documents.ingestionStatus")}</span><span>{t("knowledge.documents.progressChunks")}</span><span>{t("knowledge.documents.registration")}</span><span>{t("knowledge.documents.readStatus")}</span><span>{t("knowledge.documents.operation")}</span></div>
        {props.documents.length === 0 ? <p className="empty">{t("knowledge.documents.empty")}</p> : null}
        {props.documents.map((doc, index) => {
          const readState = props.documentReadStates[doc.id];

          return (
            <div className="table-row document-row" key={doc.id}>
              <span><strong>{doc.fileName}</strong><small>{doc.contentType}</small></span>
              <span><span className="status-pill">{getIngestionStatusLabel(doc.ingestionStatus)}</span></span>
              <span>
                <small>{t("knowledge.documents.progress", { value: formatNumber(doc.progressPercent, locale) })}</small>
                <small>{t("knowledge.documents.chunk", { value: typeof doc.chunkCount === "number" ? formatNumber(doc.chunkCount, locale) : "-" })}</small>
              </span>
              <span>
                <small>{doc.createdByUserId ?? t("common.unknown")}</small>
                <small>{formatDate(doc.createdAt, locale)}</small>
              </span>
              <span>
                <span className="status-pill muted">{getReadStatusLabel(readState?.readStatus ?? readStatuses[index % readStatuses.length])}</span>
                <small>{t("knowledge.documents.progress", { value: formatNumber(readState?.readProgress ?? 0, locale) })} / {t("knowledge.documents.lastReference", { date: formatDate(readState?.lastOpenedAt ?? doc.createdAt, locale) })}</small>
              </span>
              <span className="inline-actions">
                <button className="ghost compact" onClick={() => props.onUpdateDocumentReadState(doc.id, "opened")}>{t("common.open")}</button>
                <button className="ghost compact" onClick={() => props.onUpdateDocumentReadState(doc.id, "read")}>{t("common.read")}</button>
                <button className="primary compact" onClick={() => props.onUpdateDocumentReadState(doc.id, "acknowledged")}>{t("common.acknowledged")}</button>
              </span>
            </div>
          );
        })}
      </div>
      {props.documents.some((doc) => doc.errorMessage) ? (
        <div className="ai-assist">
          <strong>{t("knowledge.documents.ingestionError")}</strong>
          {props.documents.filter((doc) => doc.errorMessage).map((doc) => (
            <span key={doc.id}>{doc.fileName}: {doc.errorMessage}</span>
          ))}
        </div>
      ) : null}
    </>
  );
}

export function KnowledgeDocumentsPage(props: KnowledgeLayoutProps) {
  const { t } = useI18n();
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>{t("knowledge.documents.title")}</h2>
        </div>
      </div>
      <KnowledgeDocumentsContent {...props} />
    </section>
  );
}
