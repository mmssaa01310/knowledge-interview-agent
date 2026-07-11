import type { DocumentSummary } from "../../lib/api";

type DocumentsSectionProps = {
  documents: DocumentSummary[];
};

export function DocumentsSection({ documents }: DocumentsSectionProps) {
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Documents</p>
          <h2>ドキュメント取り込み</h2>
          <p className="lede">取り込み状態とユーザーの既読・確認済み状態は別々に管理します。</p>
        </div>
        <button className="primary">ドキュメント追加</button>
      </div>
      <div className="table-list">
        <div className="table-row table-head document-row">
          <span>ファイル名</span>
          <span>形式</span>
          <span>取り込み状態</span>
          <span>既読状態</span>
        </div>
        {documents.length === 0 ? (
          <p className="empty">取り込み対象のドキュメントはまだありません。</p>
        ) : documents.map((doc) => (
          <div key={doc.id} className="table-row document-row">
            <span>
              <strong>{doc.fileName}</strong>
              <small>進捗 {doc.progressPercent}%</small>
            </span>
            <span>{doc.contentType}</span>
            <span><span className="status-pill">{doc.ingestionStatus}</span></span>
            <span><span className="status-pill muted">unread</span></span>
          </div>
        ))}
      </div>
    </section>
  );
}
