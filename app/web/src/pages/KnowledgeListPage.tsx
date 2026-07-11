import type { KnowledgeDb } from "@ai-interviewer/shared-types";

type KnowledgeListPageProps = {
  knowledgeDbs: KnowledgeDb[];
  onNavigate: (path: string) => void;
  createKnowledgeDbError?: string;
};

export function KnowledgeListPage({
  knowledgeDbs,
  onNavigate,
  createKnowledgeDbError = ""
}: KnowledgeListPageProps) {
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>ナレッジDB一覧</h2>
          <p className="lede">左タブの「+ 新規ナレッジDB作成」から登録し、DBを選択してナレッジ一覧へ進みます。</p>
        </div>
      </div>
      {createKnowledgeDbError && <p className="notice error">{createKnowledgeDbError}</p>}
      <div className="table-list">
        <div className="table-row table-head">
          <span>DB名</span>
          <span>用途</span>
          <span>対象設備</span>
          <span>状態</span>
          <span>件数</span>
        </div>
        {knowledgeDbs.length === 0 ? (
          <p className="empty">ナレッジDBがありません。左タブの「+ 新規ナレッジDB作成」から登録してください。</p>
        ) : knowledgeDbs.map((db) => (
          <button
            type="button"
            key={db.id}
            className="table-row selectable"
            onClick={() => onNavigate(`/knowledge-dbs/${db.id}`)}
          >
            <span><strong>{db.name}</strong>{db.description && <small>{db.description}</small>}</span>
            <span>-</span>
            <span>-</span>
            <span><span className="status-pill">{db.status}</span></span>
            <span>ナレッジ {db.knowledgeCount ?? 0}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
