import { isGuideId, type GuideId } from "./guideRegistry";

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
const GUIDE_VERSION = 2;
export const GUIDE_REQUEST_STORAGE_KEY = "ai-interviewer.guide-request.v1";

export type HelpGuideRequest =
  | { type: "open-selector"; requestedAt: number }
  | { type: "start-guide"; guideId: GuideId; requestedAt: number };

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

export function requestGuideFromHelp(guideId?: GuideId) {
  const request: HelpGuideRequest = guideId
    ? { type: "start-guide", guideId, requestedAt: Date.now() }
    : { type: "open-selector", requestedAt: Date.now() };
  try {
    window.localStorage.setItem(GUIDE_REQUEST_STORAGE_KEY, JSON.stringify(request));
    return true;
  } catch {
    return false;
  }
}

export function parseHelpGuideRequest(value: string | null): HelpGuideRequest | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as { type?: unknown; guideId?: unknown; requestedAt?: unknown };
    if (typeof parsed.requestedAt !== "number") return null;
    if (parsed.type === "open-selector") {
      return { type: "open-selector", requestedAt: parsed.requestedAt };
    }
    if (parsed.type === "start-guide" && typeof parsed.guideId === "string" && isGuideId(parsed.guideId)) {
      return { type: "start-guide", guideId: parsed.guideId, requestedAt: parsed.requestedAt };
    }
    return null;
  } catch {
    return null;
  }
}
