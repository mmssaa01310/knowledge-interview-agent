import { useEffect, useState } from "react";
import { ingestionStatuses } from "../features/documents/constants";
import { formatDate, formatNumber } from "../lib/date";
import { useI18n } from "../i18n";
import type { KnowledgeLayoutProps } from "../types/pageProps";

function knowledgeTitle(document: KnowledgeLayoutProps["documents"][number]) {
  return document.title || document.fileName;
}

export function KnowledgeDocumentsContent(props: KnowledgeLayoutProps) {
  const { t, locale } = useI18n();
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null);
  const [deleteConfirmationText, setDeleteConfirmationText] = useState("");
  const [isEditingDocument, setIsEditingDocument] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editKnowledgeType, setEditKnowledgeType] = useState<"known_fact" | "glossary">("known_fact");
  const [editContent, setEditContent] = useState("");
  const deleteTarget = props.documents.find((document) => document.id === deleteTargetId) ?? null;
  const deleteConfirmationPhrase = t("knowledge.documents.deleteConfirmationPhrase");

  useEffect(() => {
    const opened = props.openedDocument;
    setIsEditingDocument(false);
    if (!opened) {
      setEditTitle("");
      setEditContent("");
      return;
    }
    setEditTitle(knowledgeTitle(opened.document));
    setEditKnowledgeType(opened.document.knowledgeType === "glossary" ? "glossary" : "known_fact");
    setEditContent(opened.content);
  }, [props.openedDocument]);

  function openDeleteDialog(documentId: string) {
    setDeleteConfirmationText("");
    setDeleteTargetId(documentId);
  }

  function closeDeleteDialog() {
    setDeleteConfirmationText("");
    setDeleteTargetId(null);
  }

  function getIngestionStatusLabel(status: string) {
    return t(`knowledge.documents.ingestionStatusLabels.${ingestionStatuses.includes(status) ? status : "uploaded"}`);
  }

  return (
    <>
      <div className="inline-form prior-knowledge-form prior-knowledge-entry-form">
        <label className="field-group">
          <span>{t("knowledge.documents.titleField")}</span>
          <input
            type="text"
            value={props.newDocumentTitle}
            onChange={(event) => props.setNewDocumentTitle(event.target.value)}
            placeholder={t("knowledge.documents.titlePlaceholder")}
            maxLength={200}
          />
        </label>
        <label className="field-group">
          <span>{t("knowledge.documents.knowledgeType")}</span>
          <select
            value={props.newDocumentKnowledgeType}
            onChange={(event) => props.setNewDocumentKnowledgeType(event.target.value as "known_fact" | "glossary")}
          >
            <option value="known_fact">{t("knowledge.documents.knownFact")}</option>
            <option value="glossary">{t("knowledge.documents.glossary")}</option>
          </select>
        </label>
        <label className="field-group prior-knowledge-content-field">
          <span>{t("knowledge.documents.content")}</span>
          <textarea
            value={props.newDocumentContent}
            onChange={(event) => props.setNewDocumentContent(event.target.value)}
            placeholder={t("knowledge.documents.contentPlaceholder")}
            rows={7}
            maxLength={100000}
          />
        </label>
        <button
          type="button"
          className="primary compact prior-knowledge-submit"
          onClick={props.onCreatePriorKnowledge}
          disabled={!props.newDocumentTitle.trim() || !props.newDocumentContent.trim() || props.isSavingDocument}
        >
          {props.isSavingDocument ? t("knowledge.documents.saving") : t("knowledge.documents.addButton")}
        </button>
      </div>
      <p className="form-help">{t("knowledge.documents.addPlaceholder")}</p>
      {props.documentNotice ? <p className="notice" role="status">{props.documentNotice}</p> : null}
      <div className="table-list">
        <div className="table-row document-detail-row table-head"><span>{t("knowledge.documents.titleField")}</span><span>{t("knowledge.documents.ingestionStatus")}</span><span>{t("knowledge.documents.progressChunks")}</span><span>{t("knowledge.documents.registration")}</span><span>{t("knowledge.documents.operation")}</span></div>
        {props.documents.length === 0 ? <p className="empty">{t("knowledge.documents.empty")}</p> : null}
        {props.documents.map((doc) => {
          const isOpening = props.openingDocumentId === doc.id;
          const isDeleting = props.deletingDocumentId === doc.id;
          const isBusy = Boolean(props.openingDocumentId || props.deletingDocumentId);
          return (
            <div className="table-row document-row" key={doc.id}>
              <span><strong>{knowledgeTitle(doc)}</strong><small>{doc.knowledgeType === "glossary" ? t("knowledge.documents.glossary") : t("knowledge.documents.knownFact")}</small></span>
              <span><span className="status-pill">{getIngestionStatusLabel(doc.ingestionStatus)}</span></span>
              <span>
                <small>{t("knowledge.documents.progress", { value: formatNumber(doc.progressPercent, locale) })}</small>
                <small>{t("knowledge.documents.chunk", { value: typeof doc.chunkCount === "number" ? formatNumber(doc.chunkCount, locale) : "-" })}</small>
              </span>
              <span>
                <small>{doc.createdByUserId ?? t("common.unknown")}</small>
                <small>{formatDate(doc.createdAt, locale)}</small>
              </span>
              <span className="inline-actions">
                <button type="button" className="ghost compact" onClick={() => props.onOpenDocument(doc.id)} disabled={isBusy}>
                  {isOpening ? t("common.loading") : t("common.open")}
                </button>
                <button type="button" className="danger compact" onClick={() => openDeleteDialog(doc.id)} disabled={isBusy}>
                  {isDeleting ? t("knowledge.documents.deleting") : t("common.delete")}
                </button>
              </span>
            </div>
          );
        })}
      </div>
      {props.documents.some((doc) => doc.errorMessage) ? (
        <div className="ai-assist">
          <strong>{t("knowledge.documents.ingestionError")}</strong>
          {props.documents.filter((doc) => doc.errorMessage).map((doc) => (
            <span key={doc.id}>{knowledgeTitle(doc)}: {doc.errorMessage}</span>
          ))}
        </div>
      ) : null}
      {props.openedDocument ? (
        <div
          className="dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) props.onCloseDocument();
          }}
        >
          <article className="dialog-panel document-content-dialog" role="dialog" aria-modal="true" aria-labelledby="document-content-title">
            <div className="dialog-header">
              <div>
                <h2 id="document-content-title">{knowledgeTitle(props.openedDocument.document)}</h2>
                <p>{props.openedDocument.document.knowledgeType === "glossary" ? t("knowledge.documents.glossary") : t("knowledge.documents.knownFact")}</p>
              </div>
              <button type="button" className="ghost compact" onClick={() => setIsEditingDocument((current) => !current)} disabled={props.isSavingDocument}>
                {isEditingDocument ? t("common.cancel") : t("common.edit")}
              </button>
            </div>
            {isEditingDocument ? (
              <div className="prior-knowledge-entry-form">
                <label className="field-group">
                  <span>{t("knowledge.documents.titleField")}</span>
                  <input type="text" value={editTitle} onChange={(event) => setEditTitle(event.target.value)} maxLength={200} />
                </label>
                <label className="field-group">
                  <span>{t("knowledge.documents.knowledgeType")}</span>
                  <select value={editKnowledgeType} onChange={(event) => setEditKnowledgeType(event.target.value as "known_fact" | "glossary")}>
                    <option value="known_fact">{t("knowledge.documents.knownFact")}</option>
                    <option value="glossary">{t("knowledge.documents.glossary")}</option>
                  </select>
                </label>
                <label className="field-group prior-knowledge-content-field">
                  <span>{t("knowledge.documents.content")}</span>
                  <textarea value={editContent} onChange={(event) => setEditContent(event.target.value)} rows={12} maxLength={100000} />
                </label>
              </div>
            ) : (
              <div className="document-content-view">
                <strong>{t("knowledge.documents.content")}</strong>
                <pre className="document-content-viewer">{props.openedDocument.content}</pre>
              </div>
            )}
            <div className="dialog-actions">
              {isEditingDocument ? (
                <button
                  type="button"
                  className="primary"
                  disabled={!editTitle.trim() || !editContent.trim() || props.isSavingDocument}
                  onClick={() => {
                    props.onUpdatePriorKnowledge(props.openedDocument!.document.id, {
                      title: editTitle.trim(),
                      knowledgeType: editKnowledgeType,
                      content: editContent,
                    }).then((saved) => {
                      if (saved) setIsEditingDocument(false);
                    });
                  }}
                >
                  {props.isSavingDocument ? t("knowledge.documents.saving") : t("common.save")}
                </button>
              ) : null}
              <button type="button" className="ghost" onClick={props.onCloseDocument}>{t("common.close")}</button>
            </div>
          </article>
        </div>
      ) : null}
      {deleteTarget ? (
        <div className="dialog-backdrop" role="presentation">
          <div className="dialog-panel document-delete-dialog" role="dialog" aria-modal="true" aria-labelledby="document-delete-title">
            <div className="dialog-header">
              <div>
                <h2 id="document-delete-title">{t("knowledge.documents.deleteTitle")}</h2>
                <p>{t("knowledge.documents.deletePrompt", { fileName: knowledgeTitle(deleteTarget) })}</p>
              </div>
            </div>
            <label className="delete-confirmation-field">
              <span>{t("knowledge.documents.deleteVerification", { phrase: deleteConfirmationPhrase })}</span>
              <input
                type="text"
                value={deleteConfirmationText}
                onChange={(event) => setDeleteConfirmationText(event.target.value)}
                placeholder={deleteConfirmationPhrase}
                autoComplete="off"
                autoFocus
              />
            </label>
            <div className="dialog-actions">
              <button type="button" className="ghost" onClick={closeDeleteDialog}>{t("common.cancel")}</button>
              <button
                type="button"
                className="danger"
                disabled={deleteConfirmationText !== deleteConfirmationPhrase || Boolean(props.deletingDocumentId)}
                onClick={() => {
                  const documentId = deleteTarget.id;
                  closeDeleteDialog();
                  props.onDeleteDocument(documentId);
                }}
              >
                {t("common.delete")}
              </button>
            </div>
          </div>
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
