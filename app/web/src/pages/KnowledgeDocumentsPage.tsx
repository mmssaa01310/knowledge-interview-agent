import { ingestionStatuses, readStatuses } from "../features/documents/constants";
import { formatDate } from "../lib/date";
import type { KnowledgeLayoutProps } from "../types/pageProps";

function getReadStatusLabel(status: string) {
  switch (status) {
    case "opened":
      return "閲覧開始";
    case "reading":
      return "閲覧中";
    case "read":
      return "既読";
    case "acknowledged":
      return "確認済み";
    default:
      return "未読";
  }
}

export function KnowledgeDocumentsContent(props: KnowledgeLayoutProps) {
  return (
    <>
      <div className="inline-form wide">
        <input value={props.newDocumentName} onChange={(event) => props.setNewDocumentName(event.target.value)} placeholder="ドキュメント名またはファイル名 (pdf/docx/xlsx/pptx/txt)" />
        <button className="primary" onClick={props.onCreateDocument}>文書を追加</button>
        <button
          type="button"
          className="icon-info-button"
          aria-label="対応ファイル形式"
          data-tooltip="対応ファイル: PDF / Word / Excel / PowerPoint / Text"
        >
          <span aria-hidden="true">i</span>
        </button>
      </div>
      <div className="table-list">
        <div className="table-row document-detail-row table-head"><span>文書</span><span>取り込み状態</span><span>進捗 / チャンク</span><span>登録情報</span><span>ユーザー既読状態</span><span>操作</span></div>
        {props.documents.map((doc, index) => {
          const readState = props.documentReadStates[doc.id];

          return (
            <div className="table-row document-row" key={doc.id}>
              <span><strong>{doc.fileName}</strong><small>{doc.contentType}</small></span>
              <span><span className="status-pill">{ingestionStatuses.includes(doc.ingestionStatus) ? doc.ingestionStatus : "uploaded"}</span></span>
              <span>
                <small>{doc.progressPercent}% 完了</small>
                <small>チャンク {doc.chunkCount ?? "-"}</small>
              </span>
              <span>
                <small>{doc.createdByUserId ?? "user-manager"}</small>
                <small>{formatDate(doc.createdAt)}</small>
              </span>
              <span>
                <span className="status-pill muted">{readState ? getReadStatusLabel(readState.readStatus) : readStatuses[index % readStatuses.length]}</span>
                <small>進捗 {readState?.readProgress ?? 0}% / 最終参照 {formatDate(readState?.lastOpenedAt ?? doc.createdAt)}</small>
              </span>
              <span className="inline-actions">
                <button className="ghost compact" onClick={() => props.onUpdateDocumentReadState(doc.id, "opened")}>開く</button>
                <button className="ghost compact" onClick={() => props.onUpdateDocumentReadState(doc.id, "read")}>既読</button>
                <button className="primary compact" onClick={() => props.onUpdateDocumentReadState(doc.id, "acknowledged")}>確認済み</button>
              </span>
            </div>
          );
        })}
      </div>
      {props.documents.some((doc) => doc.errorMessage) ? (
        <div className="ai-assist">
          <strong>取り込みエラー</strong>
          {props.documents.filter((doc) => doc.errorMessage).map((doc) => (
            <span key={doc.id}>{doc.fileName}: {doc.errorMessage}</span>
          ))}
        </div>
      ) : null}
    </>
  );
}

export function KnowledgeDocumentsPage(props: KnowledgeLayoutProps) {
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>事前知識</h2>
        </div>
      </div>
      <KnowledgeDocumentsContent {...props} />
    </section>
  );
}
