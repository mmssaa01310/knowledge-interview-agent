import type { GuideId } from "./guideRegistry";

export type GuideStatus = "not_started" | "in_progress" | "completed" | "dismissed";

export type GuideProgress = {
  guideId: GuideId;
  version: number;
  status: GuideStatus;
  completedAt?: string;
};

type StoredGuidePreferences = {
  guides: Partial<Record<GuideId, GuideProgress>>;
  knowledgeCreationAutoPromptDisabled?: boolean;
};

const STORAGE_KEY_PREFIX = "ai-interviewer.guide-preferences.v1";
const GUIDE_VERSION = 1;

function storageKey(userId: string | null | undefined) {
  return `${STORAGE_KEY_PREFIX}:${userId || "anonymous"}`;
}

function readPreferences(userId: string | null | undefined): StoredGuidePreferences {
  try {
    const raw = window.localStorage.getItem(storageKey(userId));
    if (!raw) return { guides: {} };
    const parsed = JSON.parse(raw) as Partial<StoredGuidePreferences>;
    return {
      guides: parsed.guides ?? {},
      knowledgeCreationAutoPromptDisabled: parsed.knowledgeCreationAutoPromptDisabled === true,
    };
  } catch {
    return { guides: {} };
  }
}

function writePreferences(userId: string | null | undefined, preferences: StoredGuidePreferences) {
  try {
    window.localStorage.setItem(storageKey(userId), JSON.stringify(preferences));
  } catch {
    // Storage不可でもガイド自体は利用できるようにする。
  }
}

export function getGuideProgress(userId: string | null | undefined, guideId: GuideId): GuideProgress {
  const progress = readPreferences(userId).guides[guideId];
  return progress?.version === GUIDE_VERSION
    ? progress
    : { guideId, version: GUIDE_VERSION, status: "not_started" };
}

export function setGuideProgress(
  userId: string | null | undefined,
  guideId: GuideId,
  status: GuideStatus,
) {
  const preferences = readPreferences(userId);
  preferences.guides[guideId] = {
    guideId,
    version: GUIDE_VERSION,
    status,
    ...(status === "completed" ? { completedAt: new Date().toISOString() } : {}),
  };
  writePreferences(userId, preferences);
}

export function isKnowledgeCreationGuideAutoPromptDisabled(userId: string | null | undefined) {
  return readPreferences(userId).knowledgeCreationAutoPromptDisabled === true;
}

export function setKnowledgeCreationGuideAutoPromptDisabled(
  userId: string | null | undefined,
  disabled: boolean,
) {
  const preferences = readPreferences(userId);
  preferences.knowledgeCreationAutoPromptDisabled = disabled;
  writePreferences(userId, preferences);
}
