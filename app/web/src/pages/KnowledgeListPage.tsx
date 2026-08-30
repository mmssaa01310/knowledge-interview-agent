import { formatDate, formatNumber } from "../lib/date";
import type { Knowledge } from "@ai-interviewer/shared-types";
import { useI18n } from "../i18n";

type KnowledgeListPageProps = {
  knowledges: Knowledge[];
  onNavigate: (path: string) => void;
  onOpenCreateKnowledge: () => void;
  onOpenDashboard?: () => void;
  canManage?: boolean;
  isPreparingKnowledgeCreation?: boolean;
  knowledgeCreationError?: string;
};

export function KnowledgeListPage({
  knowledges,
  onNavigate,
  onOpenCreateKnowledge,
  onOpenDashboard,
  canManage = true,
  isPreparingKnowledgeCreation = false,
  knowledgeCreationError = ""
}: KnowledgeListPageProps) {
  const { t, locale } = useI18n();
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>{t("knowledge.listTitle")}</h2>
        </div>
        <div className="panel-header-actions">
          {onOpenDashboard ? (
            <button type="button" className="ghost" onClick={onOpenDashboard}>
              {t("dashboard.open")}
            </button>
          ) : null}
          {canManage ? (
            <button
              type="button"
              className="primary"
              onClick={onOpenCreateKnowledge}
              disabled={isPreparingKnowledgeCreation}
            >
              {isPreparingKnowledgeCreation ? t("knowledge.preparingCreate") : t("knowledge.createButton")}
            </button>
          ) : null}
        </div>
      </div>
      {knowledgeCreationError && <p className="notice error">{knowledgeCreationError}</p>}
      <div className="table-list">
        <div className="table-row table-head knowledge-list-row">
          <span>{t("knowledge.table.name")}</span>
          <span>{t("knowledge.table.tags")}</span>
          <span>{t("knowledge.table.purpose")}</span>
          <span>{t("knowledge.table.recordCount")}</span>
          <span>{t("knowledge.table.updatedAt")}</span>
        </div>
        {knowledges.length === 0 ? (
          <p className="empty">{canManage ? t("knowledge.emptyManage") : t("knowledge.emptyView")}</p>
        ) : knowledges.map((knowledge) => (
          <button
            type="button"
            key={knowledge.id}
            className="table-row selectable knowledge-list-row"
            onClick={() => onNavigate(`/knowledge-dbs/${knowledge.knowledgeDbId}/knowledges/${knowledge.id}/interview`)}
          >
            <span>
              <strong>{knowledge.name}</strong>
              {knowledge.description && <small>{knowledge.description}</small>}
            </span>
            <span className="knowledge-table-tags">
              {knowledge.tags?.length ? (
                <span className="knowledge-tag-list" aria-label={t("knowledge.tagsLabel")}>
                  {knowledge.tags.map((tag) => <span className="knowledge-tag" key={tag}>#{tag}</span>)}
                </span>
              ) : <span className="knowledge-table-empty">{t("knowledge.tagsNotSet")}</span>}
            </span>
            <span>{knowledge.purpose ?? knowledge.category ?? t("knowledge.purposeNotSet")}</span>
            <span>{formatNumber(knowledge.recordCount ?? 0, locale)}</span>
            <span>{formatDate(knowledge.updatedAt, locale)}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
