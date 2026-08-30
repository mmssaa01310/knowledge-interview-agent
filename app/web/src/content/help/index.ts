import jaJP from "./ja-JP.json";
import enUS from "./en-US.json";
import zhCN from "./zh-CN.json";
import thTH from "./th-TH.json";
import type { UiLocale } from "../../i18n/localeMetadata";
import { isGuideId, type GuideId } from "../../features/guides/guideRegistry";

export type HelpSectionIcon = "kikiori" | "kiko";
export const helpGroupOrder = ["start", "core", "admin", "reference"] as const;
export type HelpSectionGroup = typeof helpGroupOrder[number];

export type HelpGroupContent = {
  title: string;
  description: string;
};

export type HelpQuickStartStep = {
  id: string;
  title: string;
  description: string;
  guideId?: GuideId;
};

export type HelpSectionContent = {
  id: string;
  group: HelpSectionGroup;
  title: string;
  summary: string;
  steps?: string[];
  icon?: HelpSectionIcon;
  tip?: string;
  guideId?: GuideId;
};

export type HelpFaqContent = {
  question: string;
  answer: string;
};

export type HelpContent = {
  title: string;
  intro: string;
  quickStart: {
    title: string;
    summary: string;
    steps: HelpQuickStartStep[];
  };
  groups: Record<HelpSectionGroup, HelpGroupContent>;
  sections: HelpSectionContent[];
  faq: HelpFaqContent[];
};

type RawHelpSectionContent = Omit<HelpSectionContent, "icon" | "group" | "guideId"> & {
  icon?: unknown;
  group: unknown;
  guideId?: unknown;
};
type RawHelpQuickStartStep = Omit<HelpQuickStartStep, "guideId"> & { guideId?: unknown };
type RawHelpContent = Omit<HelpContent, "sections" | "quickStart"> & {
  quickStart: Omit<HelpContent["quickStart"], "steps"> & { steps: RawHelpQuickStartStep[] };
  sections: RawHelpSectionContent[];
};

function normalizeGroup(value: unknown): HelpSectionGroup {
  return typeof value === "string" && helpGroupOrder.includes(value as HelpSectionGroup)
    ? value as HelpSectionGroup
    : "reference";
}

function normalizeGuideId(value: unknown): GuideId | undefined {
  return typeof value === "string" && isGuideId(value) ? value : undefined;
}

function normalizeHelpContent(content: RawHelpContent): HelpContent {
  return {
    ...content,
    quickStart: {
      ...content.quickStart,
      steps: content.quickStart.steps.map((step) => ({
        ...step,
        guideId: normalizeGuideId(step.guideId),
      })),
    },
    sections: content.sections.map((section) => ({
      ...section,
      icon: section.icon === "kikiori" || section.icon === "kiko" ? section.icon : undefined,
      group: normalizeGroup(section.group),
      guideId: normalizeGuideId(section.guideId),
    })),
  };
}

export const helpContentByLocale: Record<UiLocale, HelpContent> = {
  "ja-JP": normalizeHelpContent(jaJP),
  "en-US": normalizeHelpContent(enUS),
  "zh-CN": normalizeHelpContent(zhCN),
  "th-TH": normalizeHelpContent(thTH),
};
