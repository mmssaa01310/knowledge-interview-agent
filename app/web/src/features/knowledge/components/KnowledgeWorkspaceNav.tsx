import type { Knowledge } from "@ai-interviewer/shared-types";

type KnowledgeWorkspaceNavProps = {
  knowledges: Knowledge[];
  selectedKnowledgeId?: string | null;
  onNavigate: (path: string) => void;
  onOpenCreateKnowledge: () => void;
  canManage?: boolean;
  isPreparingKnowledgeCreation?: boolean;
  knowledgeCreationError?: string;
};

function sortKnowledges(knowledges: Knowledge[]) {
  return [...knowledges].sort((left, right) => {
    const createdAtOrder = left.createdAt.localeCompare(right.createdAt);
    return createdAtOrder !== 0 ? createdAtOrder : left.id.localeCompare(right.id);
  });
}

export function KnowledgeWorkspaceNav({
  knowledges,
  selectedKnowledgeId,
  onNavigate,
  onOpenCreateKnowledge,
  canManage = true,
  isPreparingKnowledgeCreation = false,
  knowledgeCreationError = ""
}: KnowledgeWorkspaceNavProps) {
  const orderedKnowledges = sortKnowledges(knowledges);

  return (
    <div className="sidebar-section">
      <div className="workspace-nav-header">
        <strong>ナレッジ</strong>
        {canManage ? (
          <button
            type="button"
            className="workspace-create"
            onClick={onOpenCreateKnowledge}
            disabled={isPreparingKnowledgeCreation}
            aria-label="ナレッジを作成"
          >
            {isPreparingKnowledgeCreation ? "準備中" : "+ ナレッジを作成"}
          </button>
        ) : null}
      </div>
      {knowledgeCreationError && <p className="workspace-error">{knowledgeCreationError}</p>}
      {orderedKnowledges.length === 0 ? (
        <p className="workspace-empty">{canManage ? "ナレッジがありません。" : "利用できるナレッジがありません。"}</p>
      ) : orderedKnowledges.map((knowledge) => (
        <button
          type="button"
          key={knowledge.id}
          className={selectedKnowledgeId === knowledge.id ? "workspace-item active" : "workspace-item"}
          onClick={() => onNavigate(`/knowledge-dbs/${knowledge.knowledgeDbId}/knowledges/${knowledge.id}/interview`)}
        >
          <strong>{knowledge.name}</strong>
          <span>{knowledge.purpose ?? "用途未設定"}</span>
        </button>
      ))}
    </div>
  );
}
