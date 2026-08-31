import { useEffect, useRef, useState } from "react";
import type { DragEvent } from "react";
import { ingestionStatuses } from "../features/documents/constants";
import { formatDate, formatNumber } from "../lib/date";
import { useI18n } from "../i18n";
import type { KnowledgeLayoutProps } from "../types/pageProps";

export function KnowledgeDocumentsContent(props: KnowledgeLayoutProps) {
  const { t, locale } = useI18n();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [isDraggingFile, setIsDraggingFile] = useState(false);
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null);
  const [deleteConfirmationText, setDeleteConfirmationText] = useState("");
  const deleteTarget = props.documents.find((document) => document.id === deleteTargetId) ?? null;
  const deleteConfirmationPhrase = t("knowledge.documents.deleteConfirmationPhrase");

  function openDeleteDialog(documentId: string) {
    setDeleteConfirmationText("");
    setDeleteTargetId(documentId);
  }

  function closeDeleteDialog() {
    setDeleteConfirmationText("");
    setDeleteTargetId(null);
  }

  useEffect(() => {
    if (!props.newDocumentFile && fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }, [props.newDocumentFile]);

  function getIngestionStatusLabel(status: string) {
    return t(`knowledge.documents.ingestionStatusLabels.${ingestionStatuses.includes(status) ? status : "uploaded"}`);
  }

  function handleFileDragOver(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    setIsDraggingFile(true);
  }

  function handleFileDragLeave(event: DragEvent<HTMLLabelElement>) {
    if (event.relatedTarget && event.currentTarget.contains(event.relatedTarget as Node)) return;
    setIsDraggingFile(false);
  }

  function handleFileDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setIsDraggingFile(false);
    props.setNewDocumentFile(event.dataTransfer.files?.[0] ?? null);
  }

  return (
    <>
      <div className="inline-form document-upload-form">
        <label
          className={`document-file-picker${isDraggingFile ? " dragging" : ""}`}
          onDragOver={handleFileDragOver}
          onDragLeave={handleFileDragLeave}
          onDrop={handleFileDrop}
        >
          <span className="document-file-picker-label">{t("knowledge.documents.fileInputLabel")}</span>
          <span className="document-file-picker-control">
            <span className="document-file-picker-trigger" aria-hidden="true">
              {t("knowledge.documents.chooseFile")}
            </span>
            <span className={props.newDocumentFile ? "document-file-picker-name" : "document-file-picker-name placeholder"}>
              {props.newDocumentFile?.name ?? t("knowledge.documents.noFileSelected")}
            </span>
            {!props.newDocumentFile ? <span className="document-file-picker-drop-hint">{t("knowledge.documents.dropHint")}</span> : null}
          </span>
          <input
            className="sr-only document-file-input"
            ref={fileInputRef}
            type="file"
            accept=".csv,.docx,.md,.pdf,.pptx,.txt,.xlsx"
            aria-label={t("knowledge.documents.fileInputLabel")}
            onChange={(event) => props.setNewDocumentFile(event.target.files?.[0] ?? null)}
          />
        </label>
        <button type="button" className="primary compact document-upload-submit" onClick={props.onUploadDocument} disabled={!props.newDocumentFile || props.isUploadingDocument}>
          {props.isUploadingDocument ? t("knowledge.documents.uploading") : t("knowledge.documents.addButton")}
        </button>
      </div>
      <p className="form-help">{t("knowledge.documents.addPlaceholder")}</p>
      {props.documentNotice ? <p className="notice" role="status">{props.documentNotice}</p> : null}
      <div className="table-list">
        <div className="table-row document-detail-row table-head"><span>{t("knowledge.documents.file")}</span><span>{t("knowledge.documents.ingestionStatus")}</span><span>{t("knowledge.documents.progressChunks")}</span><span>{t("knowledge.documents.registration")}</span><span>{t("knowledge.documents.operation")}</span></div>
        {props.documents.length === 0 ? <p className="empty">{t("knowledge.documents.empty")}</p> : null}
        {props.documents.map((doc) => {
          const isOpening = props.openingDocumentId === doc.id;
          const isDeleting = props.deletingDocumentId === doc.id;
          const isBusy = Boolean(props.openingDocumentId || props.deletingDocumentId);
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
            <span key={doc.id}>{doc.fileName}: {doc.errorMessage}</span>
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
                <h2 id="document-content-title">{props.openedDocument.document.fileName}</h2>
                <p>{props.openedDocument.document.contentType}</p>
              </div>
            </div>
            <pre className="document-content-viewer">{props.openedDocument.content}</pre>
            <div className="dialog-actions">
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
                <p>{t("knowledge.documents.deletePrompt", { fileName: deleteTarget.fileName })}</p>
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
