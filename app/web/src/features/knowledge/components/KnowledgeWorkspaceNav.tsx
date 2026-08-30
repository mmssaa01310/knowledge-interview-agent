import { useEffect, useMemo, useState } from "react";
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

const UNTAGGED_GROUP_KEY = "__untagged__";

type KnowledgeTagGroup = {
  key: string;
  label: string;
  knowledges: Knowledge[];
};

function buildTagGroups(knowledges: Knowledge[], untaggedLabel: string): KnowledgeTagGroup[] {
  const groups = new Map<string, KnowledgeTagGroup>();
  for (const knowledge of knowledges) {
    const uniqueTags = new Map<string, string>();
    for (const rawTag of knowledge.tags ?? []) {
      const label = rawTag.trim();
      if (!label) continue;
      const key = `tag:${label.toLocaleLowerCase()}`;
      if (!uniqueTags.has(key)) uniqueTags.set(key, label);
    }
    const tagEntries = uniqueTags.size > 0
      ? [...uniqueTags.entries()]
      : [[UNTAGGED_GROUP_KEY, untaggedLabel] as const];
    for (const [key, label] of tagEntries) {
      const group = groups.get(key) ?? { key, label, knowledges: [] };
      group.knowledges.push(knowledge);
      groups.set(key, group);
    }
  }
  return [...groups.values()].sort((left, right) => {
    if (left.key === UNTAGGED_GROUP_KEY) return 1;
    if (right.key === UNTAGGED_GROUP_KEY) return -1;
    return left.label.localeCompare(right.label);
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
  const orderedKnowledges = useMemo(() => sortKnowledges(knowledges), [knowledges]);
  const tagGroups = useMemo(
    () => buildTagGroups(orderedKnowledges, t("navigation.untaggedKnowledge")),
    [orderedKnowledges, t],
  );
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (!selectedKnowledgeId) return;
    const selectedGroupKeys = tagGroups
      .filter((group) => group.knowledges.some((knowledge) => knowledge.id === selectedKnowledgeId))
      .map((group) => group.key);
    if (selectedGroupKeys.length === 0) return;
    setCollapsedGroups((current) => {
      let changed = false;
      const next = { ...current };
      selectedGroupKeys.forEach((key) => {
        if (next[key]) {
          next[key] = false;
          changed = true;
        }
      });
      return changed ? next : current;
    });
  }, [selectedKnowledgeId, tagGroups]);

  function toggleGroup(groupKey: string) {
    setCollapsedGroups((current) => ({ ...current, [groupKey]: !current[groupKey] }));
  }

  return (
    <div className="sidebar-section" data-guide="knowledge-navigation">
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
            data-guide="knowledge-create"
          >
            {isPreparingKnowledgeCreation ? "…" : "+"}
          </button>
        ) : null}
      </div>
      {knowledgeCreationError && <p className="workspace-error">{knowledgeCreationError}</p>}
      {orderedKnowledges.length === 0 ? (
        <p className="workspace-empty">{canManage ? t("knowledge.emptyManage") : t("knowledge.emptyView")}</p>
      ) : tagGroups.map((group, index) => {
        const isCollapsed = Boolean(collapsedGroups[group.key]);
        const groupId = `workspace-tag-group-${index}`;
        return (
          <section className="workspace-tag-group" key={group.key} aria-label={t("navigation.tagGroup", { tag: group.label })}>
            <button
              type="button"
              className="workspace-tag-group-header"
              aria-expanded={!isCollapsed}
              aria-controls={groupId}
              aria-label={isCollapsed
                ? t("navigation.expandTagGroup", { tag: group.label })
                : t("navigation.collapseTagGroup", { tag: group.label })}
              onClick={() => toggleGroup(group.key)}
            >
              <span className="workspace-tag-group-name"><span className="workspace-tag-group-mark" aria-hidden="true">{group.key === UNTAGGED_GROUP_KEY ? "·" : "#"}</span>{group.label}</span>
              <span className="workspace-tag-group-count">{group.knowledges.length}</span>
              <span className={isCollapsed ? "workspace-tag-group-chevron" : "workspace-tag-group-chevron open"} aria-hidden="true" />
            </button>
            {!isCollapsed ? (
              <div id={groupId} className="workspace-tag-group-items">
                {group.knowledges.map((knowledge) => (
                  <button
                    type="button"
                    key={`${group.key}-${knowledge.id}`}
                    className={selectedKnowledgeId === knowledge.id ? "workspace-item active" : "workspace-item"}
                    data-guide="knowledge-item"
                    data-knowledge-path={`/knowledge-dbs/${knowledge.knowledgeDbId}/knowledges/${knowledge.id}/interview`}
                    onClick={() => onNavigate(`/knowledge-dbs/${knowledge.knowledgeDbId}/knowledges/${knowledge.id}/interview`)}
                  >
                    <strong>{knowledge.name}</strong>
                    <span>{knowledge.purpose ?? t("knowledge.purposeNotSet")}</span>
                  </button>
                ))}
              </div>
            ) : null}
          </section>
        );
      })}
    </div>
  );
}
