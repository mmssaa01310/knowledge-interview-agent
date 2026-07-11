import type { KnowledgeDb } from "@ai-interviewer/shared-types";

type KnowledgeWorkspaceNavProps = {
  knowledgeDbs: KnowledgeDb[];
  selectedKnowledgeDbId?: string | null;
  onNavigate: (path: string) => void;
  onCreateKnowledgeDb: () => void;
  isCreatingKnowledgeDb?: boolean;
  createKnowledgeDbError?: string;
  onToggleCollapsed: () => void;
};

export function KnowledgeWorkspaceNav({
  knowledgeDbs,
  selectedKnowledgeDbId,
  onNavigate,
  onCreateKnowledgeDb,
  isCreatingKnowledgeDb = false,
  createKnowledgeDbError = "",
  onToggleCollapsed
}: KnowledgeWorkspaceNavProps) {
  return (
    <aside className="workspace-nav">
      <button
        type="button"
        className="workspace-collapse-button"
        onClick={onToggleCollapsed}
        aria-label="左側ナビを閉じる"
        title="左側ナビを閉じる"
      >
        <span className="nav-toggle-icon close" aria-hidden="true" />
      </button>
      <div className="workspace-nav-header">
        <strong>ナレッジDB</strong>
        <button
          type="button"
          className="workspace-create"
          onClick={onCreateKnowledgeDb}
          disabled={isCreatingKnowledgeDb}
          aria-label="新規ナレッジDB作成"
        >
          {isCreatingKnowledgeDb ? "作成中" : "+ 新規ナレッジDB作成"}
        </button>
      </div>
      {createKnowledgeDbError && <p className="workspace-error">{createKnowledgeDbError}</p>}
      {knowledgeDbs.length === 0 ? (
        <p className="workspace-empty">ナレッジDBがありません。</p>
      ) : knowledgeDbs.map((db) => (
        <button
          type="button"
          key={db.id}
          className={selectedKnowledgeDbId === db.id ? "workspace-item active" : "workspace-item"}
          onClick={() => onNavigate(`/knowledge-dbs/${db.id}`)}
        >
          <strong>{db.name}</strong>
          <span>ナレッジ {db.knowledgeCount ?? 0}</span>
        </button>
      ))}
    </aside>
  );
}
