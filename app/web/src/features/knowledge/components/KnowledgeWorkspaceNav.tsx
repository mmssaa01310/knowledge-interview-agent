import type { Knowledge } from "@ai-interviewer/shared-types";
import { useI18n } from "../../../i18n";

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
  const { t } = useI18n();
  const orderedKnowledges = sortKnowledges(knowledges);

  return (
    <div className="sidebar-section">
      <div className="workspace-nav-header">
        <div className="sidebar-section-heading">
          <span className="sidebar-section-kicker">{t("navigation.workspace")}</span>
          <strong className="sidebar-section-title"><span className="nav-section-icon" aria-hidden="true">✦</span>{t("navigation.knowledge")}</strong>
        </div>
        {canManage ? (
          <button
            type="button"
            className="workspace-create icon-action"
            onClick={onOpenCreateKnowledge}
            disabled={isPreparingKnowledgeCreation}
            aria-label={t("navigation.createKnowledge")}
            title={t("navigation.createKnowledge")}
          >
            {isPreparingKnowledgeCreation ? "…" : "+"}
          </button>
        ) : null}
      </div>
      {knowledgeCreationError && <p className="workspace-error">{knowledgeCreationError}</p>}
      {orderedKnowledges.length === 0 ? (
        <p className="workspace-empty">{canManage ? t("knowledge.emptyManage") : t("knowledge.emptyView")}</p>
      ) : orderedKnowledges.map((knowledge) => (
        <button
          type="button"
          key={knowledge.id}
          className={selectedKnowledgeId === knowledge.id ? "workspace-item active" : "workspace-item"}
          onClick={() => onNavigate(`/knowledge-dbs/${knowledge.knowledgeDbId}/knowledges/${knowledge.id}/interview`)}
        >
          <strong>{knowledge.name}</strong>
          <span>{knowledge.purpose ?? t("knowledge.purposeNotSet")}</span>
        </button>
      ))}
    </div>
  );
}
