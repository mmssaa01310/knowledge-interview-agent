import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react";
import type { Knowledge } from "@ai-interviewer/shared-types";
import { useI18n } from "../../../i18n";
import {
  orderKnowledges,
  readKnowledgeNavigationPreferences,
  writeKnowledgeNavigationPreferences,
  type KnowledgeNavigationPreferences,
} from "../knowledgeNavigationPreferences";

type KnowledgeWorkspaceNavProps = {
  knowledges: Knowledge[];
  selectedKnowledgeId?: string | null;
  onNavigate: (path: string) => void;
  onOpenCreateKnowledge: () => void;
  userId?: string | null;
  tenantId?: string | null;
  canManage?: boolean;
  isPreparingKnowledgeCreation?: boolean;
  knowledgeCreationError?: string;
};

const PINNED_GROUP_KEY = "__pinned__";
const UNTAGGED_GROUP_KEY = "__untagged__";

type KnowledgeTagGroup = {
  key: string;
  label: string;
  knowledges: Knowledge[];
};

type DragPlacement = "before" | "after";

type DragOverTarget = {
  groupKey: string;
  knowledgeId: string;
  placement: DragPlacement;
};

type PendingPointerDrag = {
  knowledgeId: string;
  groupKey: string;
  pointerId: number;
  startX: number;
  startY: number;
  active: boolean;
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
  userId,
  tenantId,
  canManage = true,
  isPreparingKnowledgeCreation = false,
  knowledgeCreationError = ""
}: KnowledgeWorkspaceNavProps) {
  const { t } = useI18n();
  const [navigationPreferences, setNavigationPreferences] = useState<KnowledgeNavigationPreferences>(
    () => readKnowledgeNavigationPreferences(userId, tenantId),
  );
  const navigationPreferencesRef = useRef(navigationPreferences);
  const [draggedKnowledgeId, setDraggedKnowledgeId] = useState<string | null>(null);
  const [dragOverTarget, setDragOverTarget] = useState<DragOverTarget | null>(null);
  const draggedKnowledgeIdRef = useRef<string | null>(null);
  const draggedGroupKeyRef = useRef<string | null>(null);
  const pendingPointerDragRef = useRef<PendingPointerDrag | null>(null);

  useEffect(() => {
    const nextPreferences = readKnowledgeNavigationPreferences(userId, tenantId);
    navigationPreferencesRef.current = nextPreferences;
    setNavigationPreferences(nextPreferences);
  }, [tenantId, userId]);

  const orderedKnowledges = useMemo(
    () => orderKnowledges(knowledges, navigationPreferences),
    [knowledges, navigationPreferences],
  );
  const pinnedKnowledgeIds = useMemo(
    () => new Set(navigationPreferences.pinned),
    [navigationPreferences.pinned],
  );
  const pinnedKnowledges = useMemo(
    () => orderedKnowledges.filter((knowledge) => pinnedKnowledgeIds.has(knowledge.id)),
    [orderedKnowledges, pinnedKnowledgeIds],
  );
  const tagGroups = useMemo(
    () => buildTagGroups(
      orderedKnowledges.filter((knowledge) => !pinnedKnowledgeIds.has(knowledge.id)),
      t("navigation.untaggedKnowledge"),
    ),
    [orderedKnowledges, pinnedKnowledgeIds, t],
  );
  const navigationGroups = useMemo(() => {
    if (pinnedKnowledges.length === 0) return tagGroups;
    return [
      {
        key: PINNED_GROUP_KEY,
        label: t("navigation.pinnedKnowledgeGroup"),
        knowledges: pinnedKnowledges,
      },
      ...tagGroups,
    ];
  }, [pinnedKnowledges, t, tagGroups]);
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (!selectedKnowledgeId) return;
    const selectedGroupKeys = navigationGroups
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
  }, [navigationGroups, selectedKnowledgeId]);

  function toggleGroup(groupKey: string) {
    setCollapsedGroups((current) => ({ ...current, [groupKey]: !current[groupKey] }));
  }

  function updateNavigationPreferences(
    update: (current: KnowledgeNavigationPreferences) => KnowledgeNavigationPreferences,
  ) {
    const nextPreferences = update(navigationPreferencesRef.current);
    navigationPreferencesRef.current = nextPreferences;
    setNavigationPreferences(nextPreferences);
    writeKnowledgeNavigationPreferences(userId, tenantId, nextPreferences);
  }

  function finishKnowledgeDrag() {
    draggedKnowledgeIdRef.current = null;
    draggedGroupKeyRef.current = null;
    pendingPointerDragRef.current = null;
    setDraggedKnowledgeId(null);
    setDragOverTarget(null);
  }

  function toggleKnowledgePinned(knowledgeId: string) {
    const isPinned = navigationPreferencesRef.current.pinned.includes(knowledgeId);
    updateNavigationPreferences((current) => ({
      ...current,
      pinned: isPinned
        ? current.pinned.filter((id) => id !== knowledgeId)
        : [...current.pinned, knowledgeId],
    }));
  }

  function canDropKnowledge(sourceId: string, sourceGroupKey: string, targetId: string, targetGroupKey: string) {
    if (sourceId === targetId || sourceGroupKey !== targetGroupKey) return false;
    return pinnedKnowledgeIds.has(sourceId) === pinnedKnowledgeIds.has(targetId);
  }

  function reorderKnowledge(
    sourceId: string,
    targetId: string,
    sourceGroupKey: string,
    targetGroupKey: string,
    placement: DragPlacement,
    groupKnowledges: Knowledge[],
  ) {
    if (!canDropKnowledge(sourceId, sourceGroupKey, targetId, targetGroupKey)) return false;
    if (!groupKnowledges.some((knowledge) => knowledge.id === sourceId)
      || !groupKnowledges.some((knowledge) => knowledge.id === targetId)) return false;

    const currentOrder = orderKnowledges(knowledges, navigationPreferencesRef.current);
    const withoutSource = currentOrder
      .map((knowledge) => knowledge.id)
      .filter((knowledgeId) => knowledgeId !== sourceId);
    const targetIndex = withoutSource.indexOf(targetId);
    if (targetIndex < 0) return false;

    withoutSource.splice(targetIndex + (placement === "after" ? 1 : 0), 0, sourceId);
    updateNavigationPreferences((current) => ({ ...current, order: withoutSource }));
    finishKnowledgeDrag();
    return true;
  }

  function moveKnowledgeByKeyboard(knowledgeId: string, groupKey: string, direction: -1 | 1, groupKnowledges: Knowledge[]) {
    const groupIndex = groupKnowledges.findIndex((knowledge) => knowledge.id === knowledgeId);
    const targetKnowledge = groupKnowledges[groupIndex + direction];
    if (!targetKnowledge) return;
    reorderKnowledge(
      knowledgeId,
      targetKnowledge.id,
      groupKey,
      groupKey,
      direction < 0 ? "before" : "after",
      groupKnowledges,
    );
  }

  function getDropPlacement(element: HTMLElement, clientY: number): DragPlacement {
    const bounds = element.getBoundingClientRect();
    return clientY <= bounds.top + bounds.height / 2 ? "before" : "after";
  }

  function getGroupDropTarget(element: HTMLElement, clientY: number) {
    const entries = [...element.querySelectorAll<HTMLElement>("[data-knowledge-drop-target]")];
    if (entries.length === 0) return null;

    const firstEntry = entries[0];
    const lastEntry = entries[entries.length - 1];
    const firstBounds = firstEntry.getBoundingClientRect();
    const lastBounds = lastEntry.getBoundingClientRect();
    if (clientY < firstBounds.top) {
      return {
        groupKey: element.dataset.knowledgeGroup || "",
        knowledgeId: firstEntry.dataset.knowledgeId || "",
        placement: "before" as const,
      };
    }
    if (clientY > lastBounds.bottom) {
      return {
        groupKey: element.dataset.knowledgeGroup || "",
        knowledgeId: lastEntry.dataset.knowledgeId || "",
        placement: "after" as const,
      };
    }

    let closestEntry = firstEntry;
    let closestDistance = Number.POSITIVE_INFINITY;
    for (const entry of entries) {
      const bounds = entry.getBoundingClientRect();
      const distance = Math.abs(clientY - (bounds.top + bounds.height / 2));
      if (distance < closestDistance) {
        closestEntry = entry;
        closestDistance = distance;
      }
    }
    return {
      groupKey: element.dataset.knowledgeGroup || "",
      knowledgeId: closestEntry.dataset.knowledgeId || "",
      placement: getDropPlacement(closestEntry, clientY),
    };
  }

  function getDropTargetAtPoint(clientX: number, clientY: number) {
    const pointElement = document.elementFromPoint(clientX, clientY);
    const itemElement = pointElement?.closest<HTMLElement>("[data-knowledge-drop-target]");
    if (itemElement) {
      const groupKey = itemElement.dataset.knowledgeGroup;
      const knowledgeId = itemElement.dataset.knowledgeId;
      if (!groupKey || !knowledgeId) return null;
      return {
        element: itemElement,
        groupKey,
        knowledgeId,
        placement: getDropPlacement(itemElement, clientY),
      };
    }
    const groupElement = pointElement?.closest<HTMLElement>("[data-knowledge-group-drop-target]");
    if (!groupElement) return null;
    const groupTarget = getGroupDropTarget(groupElement, clientY);
    return groupTarget ? { element: groupElement, ...groupTarget } : null;
  }

  function updateDragOverTarget(
    sourceId: string,
    sourceGroupKey: string,
    targetId: string,
    targetGroupKey: string,
    placement: DragPlacement,
  ) {
    if (!canDropKnowledge(sourceId, sourceGroupKey, targetId, targetGroupKey)) {
      setDragOverTarget(null);
      return false;
    }
    setDragOverTarget((current) => (
      current?.groupKey === targetGroupKey
      && current.knowledgeId === targetId
      && current.placement === placement
        ? current
        : { groupKey: targetGroupKey, knowledgeId: targetId, placement }
    ));
    return true;
  }

  function handleKnowledgePointerDown(event: ReactPointerEvent<HTMLDivElement>, knowledgeId: string, groupKey: string) {
    if (event.target instanceof Element && event.target.closest("[data-knowledge-pin]")) return;
    if (!(event.target instanceof Element) || !event.target.closest(".workspace-item-drag-handle")) return;
    pendingPointerDragRef.current = {
      knowledgeId,
      groupKey,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      active: false,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function handleKnowledgePointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const pending = pendingPointerDragRef.current;
    if (!pending || pending.pointerId !== event.pointerId) return;
    const distance = Math.hypot(event.clientX - pending.startX, event.clientY - pending.startY);
    if (!pending.active && distance < 8) return;
    if (!pending.active) {
      pending.active = true;
      draggedKnowledgeIdRef.current = pending.knowledgeId;
      draggedGroupKeyRef.current = pending.groupKey;
      setDraggedKnowledgeId(pending.knowledgeId);
    }
    event.preventDefault();
    const target = getDropTargetAtPoint(event.clientX, event.clientY);
    if (!target) {
      setDragOverTarget(null);
      return;
    }
    updateDragOverTarget(
      pending.knowledgeId,
      pending.groupKey,
      target.knowledgeId,
      target.groupKey,
      target.placement,
    );
  }

  function handleKnowledgePointerUp(event: ReactPointerEvent<HTMLDivElement>) {
    const pending = pendingPointerDragRef.current;
    if (!pending || pending.pointerId !== event.pointerId) return;
    if (pending.active) {
      event.preventDefault();
      const target = getDropTargetAtPoint(event.clientX, event.clientY);
      const group = target ? navigationGroups.find((candidate) => candidate.key === target.groupKey) : undefined;
      if (target && group) {
        reorderKnowledge(
          pending.knowledgeId,
          target.knowledgeId,
          pending.groupKey,
          target.groupKey,
          target.placement,
          group.knowledges,
        );
      }
    }
    finishKnowledgeDrag();
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture?.(event.pointerId);
    }
  }

  function handleKnowledgePointerCancel() {
    finishKnowledgeDrag();
  }

  function handleKnowledgeHandleKeyDown(
    event: ReactKeyboardEvent<HTMLButtonElement>,
    knowledgeId: string,
    groupKey: string,
    groupKnowledges: Knowledge[],
  ) {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    moveKnowledgeByKeyboard(knowledgeId, groupKey, event.key === "ArrowUp" ? -1 : 1, groupKnowledges);
  }

  function renderKnowledgeItem(knowledge: Knowledge, groupKey: string, groupKnowledges: Knowledge[]) {
    const isPinned = pinnedKnowledgeIds.has(knowledge.id);
    const isDragged = draggedKnowledgeId === knowledge.id;
    const dropPlacement = dragOverTarget?.groupKey === groupKey && dragOverTarget.knowledgeId === knowledge.id
      ? dragOverTarget.placement
      : null;
    return (
      <div
        key={`${groupKey}-${knowledge.id}`}
        className={`workspace-item-entry${isDragged ? " dragging" : ""}${dropPlacement ? ` drop-target-${dropPlacement}` : ""}`}
        data-knowledge-drop-target="true"
        data-knowledge-id={knowledge.id}
        data-knowledge-group={groupKey}
      >
        <div
          className={selectedKnowledgeId === knowledge.id ? "workspace-item-row active" : "workspace-item-row"}
          onPointerDown={(event) => handleKnowledgePointerDown(event, knowledge.id, groupKey)}
          onPointerMove={handleKnowledgePointerMove}
          onPointerUp={handleKnowledgePointerUp}
          onPointerCancel={handleKnowledgePointerCancel}
        >
          <button
            type="button"
            className={selectedKnowledgeId === knowledge.id ? "workspace-item active" : "workspace-item"}
            data-guide="knowledge-item"
            data-knowledge-path={`/knowledge-dbs/${knowledge.knowledgeDbId}/knowledges/${knowledge.id}/interview`}
            onClick={() => onNavigate(`/knowledge-dbs/${knowledge.knowledgeDbId}/knowledges/${knowledge.id}/interview`)}
          >
            <strong>{knowledge.name}</strong>
            <span>{knowledge.purpose ?? t("knowledge.purposeNotSet")}</span>
          </button>
          <button
            type="button"
            className={isPinned ? "workspace-item-pin-button pinned" : "workspace-item-pin-button"}
            data-knowledge-pin="true"
            aria-label={t(isPinned ? "navigation.unpinKnowledge" : "navigation.pinKnowledge", { name: knowledge.name })}
            aria-pressed={isPinned}
            title={t(isPinned ? "navigation.unpinKnowledge" : "navigation.pinKnowledge", { name: knowledge.name })}
            onClick={() => toggleKnowledgePinned(knowledge.id)}
          >
            <PinGlyph filled={isPinned} />
          </button>
          <button
            type="button"
            className="workspace-item-drag-handle"
            aria-label={t("navigation.dragKnowledge", { name: knowledge.name })}
            title={t("navigation.dragKnowledge", { name: knowledge.name })}
            aria-keyshortcuts="ArrowUp ArrowDown"
            onKeyDown={(event) => handleKnowledgeHandleKeyDown(event, knowledge.id, groupKey, groupKnowledges)}
          >
            <DragHandleGlyph />
          </button>
        </div>
      </div>
    );
  }

  function renderKnowledgeGroup(group: KnowledgeTagGroup, index: number) {
    const isPinnedGroup = group.key === PINNED_GROUP_KEY;
    const isCollapsed = Boolean(collapsedGroups[group.key]);
    const groupId = isPinnedGroup ? "workspace-pinned-knowledge-group" : `workspace-tag-group-${index}`;
    const groupLabel = isPinnedGroup ? t("navigation.pinnedKnowledgeGroup") : group.label;
    const groupAriaLabel = isPinnedGroup
      ? groupLabel
      : t("navigation.tagGroup", { tag: group.label });
    const groupToggleLabel = isPinnedGroup
      ? (isCollapsed ? t("navigation.expandPinnedKnowledgeGroup") : t("navigation.collapsePinnedKnowledgeGroup"))
      : (isCollapsed
        ? t("navigation.expandTagGroup", { tag: group.label })
        : t("navigation.collapseTagGroup", { tag: group.label }));
    return (
      <section className={isPinnedGroup ? "workspace-tag-group pinned" : "workspace-tag-group"} key={group.key} aria-label={groupAriaLabel}>
        <button
          type="button"
          className="workspace-tag-group-header"
          aria-expanded={!isCollapsed}
          aria-controls={groupId}
          aria-label={groupToggleLabel}
          onClick={() => toggleGroup(group.key)}
        >
          <span className="workspace-tag-group-name">
            <span className="workspace-tag-group-mark" aria-hidden="true">
              {isPinnedGroup ? <PinGlyph filled /> : group.key === UNTAGGED_GROUP_KEY ? "·" : "#"}
            </span>
            {groupLabel}
          </span>
          <span className="workspace-tag-group-count">{group.knowledges.length}</span>
          <span className={isCollapsed ? "workspace-tag-group-chevron" : "workspace-tag-group-chevron open"} aria-hidden="true" />
        </button>
        {!isCollapsed ? (
          <div
            id={groupId}
            className="workspace-tag-group-items"
            data-knowledge-group-drop-target="true"
            data-knowledge-group={group.key}
          >
            {group.knowledges.map((knowledge) => renderKnowledgeItem(knowledge, group.key, group.knowledges))}
          </div>
        ) : null}
      </section>
    );
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
      ) : navigationGroups.map((group, index) => renderKnowledgeGroup(group, index))}
    </div>
  );
}

function PinGlyph({ filled = false }: { filled?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" fill={filled ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M8 3h8l-1 6 3 3v2h-5v7l-1 1-1-1v-7H6v-2l3-3-1-6Z" />
    </svg>
  );
}

function DragHandleGlyph() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M8 5h.01M8 12h.01M8 19h.01M16 5h.01M16 12h.01M16 19h.01" strokeWidth="2.8" />
    </svg>
  );
}
