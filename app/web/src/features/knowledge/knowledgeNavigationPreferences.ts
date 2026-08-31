import type { Knowledge } from "@ai-interviewer/shared-types";

export type KnowledgeNavigationPreferences = {
  order: string[];
  pinned: string[];
};

const STORAGE_KEY_PREFIX = "ai-interviewer.knowledge-navigation.v1";

export const EMPTY_KNOWLEDGE_NAVIGATION_PREFERENCES: KnowledgeNavigationPreferences = {
  order: [],
  pinned: [],
};

function uniqueStringArray(value: unknown) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.filter((item): item is string => typeof item === "string" && item.length > 0))];
}
function storageKey(userId: string | null | undefined, tenantId: string | null | undefined) {
  if (!userId) return null;
  const scope = `${tenantId || "unknown-tenant"}:${userId}`;
  return `${STORAGE_KEY_PREFIX}:${encodeURIComponent(scope)}`;
}

export function readKnowledgeNavigationPreferences(
  userId: string | null | undefined,
  tenantId: string | null | undefined,
): KnowledgeNavigationPreferences {
  const key = storageKey(userId, tenantId);
  if (!key || typeof window === "undefined") return { ...EMPTY_KNOWLEDGE_NAVIGATION_PREFERENCES };

  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return { ...EMPTY_KNOWLEDGE_NAVIGATION_PREFERENCES };
    const parsed = JSON.parse(raw) as { order?: unknown; pinned?: unknown };
    return {
      order: uniqueStringArray(parsed.order),
      pinned: uniqueStringArray(parsed.pinned),
    };
  } catch {
    return { ...EMPTY_KNOWLEDGE_NAVIGATION_PREFERENCES };
  }
}

export function writeKnowledgeNavigationPreferences(
  userId: string | null | undefined,
  tenantId: string | null | undefined,
  preferences: KnowledgeNavigationPreferences,
) {
  const key = storageKey(userId, tenantId);
  if (!key || typeof window === "undefined") return;

  try {
    window.localStorage.setItem(key, JSON.stringify({
      order: uniqueStringArray(preferences.order),
      pinned: uniqueStringArray(preferences.pinned),
    }));
  } catch {
    // Storage不可でも、現在の画面上の並び替え・ピン留めは継続する。
  }
}

function sortByCreatedAt(knowledges: Knowledge[]) {
  return [...knowledges].sort((left, right) => {
    const createdAtOrder = left.createdAt.localeCompare(right.createdAt);
    return createdAtOrder !== 0 ? createdAtOrder : left.id.localeCompare(right.id);
  });
}

export function orderKnowledges(
  knowledges: Knowledge[],
  preferences: KnowledgeNavigationPreferences,
) {
  const fallbackOrder = sortByCreatedAt(knowledges);
  const fallbackIndex = new Map(fallbackOrder.map((knowledge, index) => [knowledge.id, index]));
  const customIndex = new Map(
    uniqueStringArray(preferences.order).map((knowledgeId, index) => [knowledgeId, index]),
  );
  const pinnedIds = new Set(uniqueStringArray(preferences.pinned));

  return fallbackOrder.sort((left, right) => {
    const pinnedOrder = Number(pinnedIds.has(right.id)) - Number(pinnedIds.has(left.id));
    if (pinnedOrder !== 0) return pinnedOrder;

    const leftIndex = customIndex.get(left.id);
    const rightIndex = customIndex.get(right.id);
    if (leftIndex !== undefined || rightIndex !== undefined) {
      const normalizedLeftIndex = leftIndex ?? Number.MAX_SAFE_INTEGER;
      const normalizedRightIndex = rightIndex ?? Number.MAX_SAFE_INTEGER;
      if (normalizedLeftIndex !== normalizedRightIndex) return normalizedLeftIndex - normalizedRightIndex;
    }

    return (fallbackIndex.get(left.id) ?? 0) - (fallbackIndex.get(right.id) ?? 0);
  });
}
