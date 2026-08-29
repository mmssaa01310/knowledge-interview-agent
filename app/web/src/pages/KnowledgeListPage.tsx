import { formatDate } from "../lib/date";
import type { Knowledge } from "@ai-interviewer/shared-types";

type KnowledgeListPageProps = {
  knowledges: Knowledge[];
  onNavigate: (path: string) => void;
  onOpenCreateKnowledge: () => void;
  canManage?: boolean;
  isPreparingKnowledgeCreation?: boolean;
  knowledgeCreationError?: string;
};

export function KnowledgeListPage({
  knowledges,
  onNavigate,
  onOpenCreateKnowledge,
  canManage = true,
  isPreparingKnowledgeCreation = false,
  knowledgeCreationError = ""
}: KnowledgeListPageProps) {
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>ナレッジ一覧</h2>
        </div>
        {canManage ? (
          <button
            type="button"
            className="primary"
            onClick={onOpenCreateKnowledge}
            disabled={isPreparingKnowledgeCreation}
          >
            {isPreparingKnowledgeCreation ? "準備中" : "+ ナレッジを作成"}
          </button>
        ) : null}
      </div>
      {knowledgeCreationError && <p className="notice error">{knowledgeCreationError}</p>}
      <div className="table-list">
        <div className="table-row table-head">
          <span>ナレッジ名</span>
          <span>用途</span>
          <span>記録数</span>
          <span>更新日時</span>
        </div>
        {knowledges.length === 0 ? (
          <p className="empty">{canManage ? "ナレッジがありません。「+ ナレッジを作成」から登録してください。" : "利用できるナレッジがありません。"}</p>
        ) : knowledges.map((knowledge) => (
          <button
            type="button"
            key={knowledge.id}
            className="table-row selectable"
            onClick={() => onNavigate(`/knowledge-dbs/${knowledge.knowledgeDbId}/knowledges/${knowledge.id}/interview`)}
          >
            <span><strong>{knowledge.name}</strong>{knowledge.description && <small>{knowledge.description}</small>}</span>
            <span>{knowledge.purpose ?? knowledge.category ?? "用途未設定"}</span>
            <span>{knowledge.recordCount ?? 0}</span>
            <span>{formatDate(knowledge.updatedAt)}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
