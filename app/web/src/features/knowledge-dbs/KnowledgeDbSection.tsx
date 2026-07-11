import type { KnowledgeDb } from "@ai-interviewer/shared-types";

type KnowledgeDbSectionProps = {
  knowledgeDbs: KnowledgeDb[];
  selectedKnowledgeDbId: string | null;
  onSelect: (knowledgeDbId: string) => void;
  onCreateDemo: () => void;
  isSeeding: boolean;
};

export function KnowledgeDbSection({
  knowledgeDbs,
  selectedKnowledgeDbId,
  onSelect,
  onCreateDemo,
  isSeeding
}: KnowledgeDbSectionProps) {
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>ナレッジDB一覧</h2>
          <p className="lede">業務領域ごとにヒアリング項目、記録、文書、参照チャットを管理します。</p>
        </div>
        <button className="primary" onClick={onCreateDemo} disabled={isSeeding}>
          {isSeeding ? "作成中" : "デモDB作成"}
        </button>
      </div>
      <div className="table-list">
        <div className="table-row table-head">
          <span>DB名</span>
          <span>用途</span>
          <span>対象設備</span>
          <span>状態</span>
          <span>件数</span>
        </div>
        {knowledgeDbs.length === 0 ? (
          <p className="empty">データがありません。デモDB作成を実行してください。</p>
        ) : knowledgeDbs.map((db) => (
          <button
            key={db.id}
            className={db.id === selectedKnowledgeDbId ? "table-row selectable selected" : "table-row selectable"}
            onClick={() => onSelect(db.id)}
          >
            <span>
              <strong>{db.name}</strong>
              <small>{db.description ?? "説明未設定"}</small>
            </span>
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
