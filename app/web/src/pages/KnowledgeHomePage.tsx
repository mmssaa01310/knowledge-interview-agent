import { formatDate } from "../lib/date";
import type { KnowledgeLayoutProps } from "../types/pageProps";

export function KnowledgeHomePage({
  knowledgeDbs,
  navigate,
  onDeleteKnowledgeDb
}: KnowledgeLayoutProps) {
  const totalKnowledges = knowledgeDbs.reduce((sum, db) => sum + (db.knowledgeCount ?? 0), 0);

  return (
    <section className="panel page-stack">
      <div className="panel-header">
        <div>
          <h2>ナレッジDB一覧</h2>
          <p className="lede">作成済みナレッジDBを一覧し、設定、記録、文書、参照チャットへ遷移します。</p>
        </div>
        <button className="ghost" onClick={() => navigate("/chatbots")}>参照チャットを開く</button>
      </div>

      <div className="workspace-summary">
        <div>
          <strong>{knowledgeDbs.length}</strong>
          <span>ナレッジDB</span>
        </div>
        <div>
          <strong>{totalKnowledges}</strong>
          <span>総ナレッジ数</span>
        </div>
        <div>
          <strong>-</strong>
          <span>総ドキュメント数</span>
        </div>
        <div>
          <strong>{knowledgeDbs.filter((db) => db.status === "active").length}</strong>
          <span>稼働中DB</span>
        </div>
        <div>
          <strong>{knowledgeDbs.filter((db) => db.language === "ja").length}</strong>
          <span>日本語設定</span>
        </div>
      </div>

      <div className="table-list">
        <div className="table-row database-row table-head">
          <span>ナレッジDB</span>
          <span>用途 / 対象</span>
          <span>作成者 / 更新</span>
          <span>件数</span>
          <span>ステータス</span>
          <span>操作</span>
        </div>
        {knowledgeDbs.length === 0 ? (
          <p className="empty">ナレッジDBがありません。左の入力欄かサイドバーから新規作成してください。</p>
        ) : knowledgeDbs.map((db) => (
          <div className="table-row database-row" key={db.id}>
            <span>
              <strong>{db.name}</strong>
              {db.description && <small>{db.description}</small>}
            </span>
            <span>
              <strong>-</strong>
              <small>ナレッジDB</small>
            </span>
            <span>
              <small>{db.createdByUserId}</small>
              <small>{formatDate(db.updatedAt)}</small>
            </span>
            <span>
              <small>ナレッジ {db.knowledgeCount ?? 0}</small>
            </span>
            <span>
              <span className={db.status === "active" ? "status-pill" : "status-pill muted"}>{db.status}</span>
            </span>
            <span className="inline-actions">
              <button className="ghost compact" onClick={() => navigate(`/knowledge-dbs/${db.id}`)}>ナレッジ</button>
              <button className="primary compact" onClick={() => navigate("/chatbots")}>チャット</button>
              <button className="danger compact" onClick={() => onDeleteKnowledgeDb(db.id)}>削除</button>
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
