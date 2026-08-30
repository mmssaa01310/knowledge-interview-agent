import jaJP from "./ja-JP.json";
import enUS from "./en-US.json";
import zhCN from "./zh-CN.json";
import thTH from "./th-TH.json";
import type { UiLocale } from "../../i18n/localeMetadata";

export type HelpSectionIcon = "kikiori" | "kiko";

export type HelpSectionContent = {
  id: string;
  title: string;
  summary: string;
  steps: string[];
  icon?: HelpSectionIcon;
};

export type HelpFaqContent = {
  question: string;
  answer: string;
};

export type HelpContent = {
  title: string;
  intro: string;
  sections: HelpSectionContent[];
  faq: HelpFaqContent[];
};

type RawHelpSectionContent = Omit<HelpSectionContent, "icon"> & { icon?: unknown };
type RawHelpContent = Omit<HelpContent, "sections"> & { sections: RawHelpSectionContent[] };

function normalizeHelpContent(content: RawHelpContent): HelpContent {
  return {
    ...content,
    sections: content.sections.map((section) => ({
      ...section,
      icon: section.icon === "kikiori" || section.icon === "kiko" ? section.icon : undefined,
    })),
  };
}

export const helpContentByLocale: Record<UiLocale, HelpContent> = {
  "ja-JP": normalizeHelpContent(jaJP),
  "en-US": normalizeHelpContent(enUS),
  "zh-CN": normalizeHelpContent(zhCN),
  "th-TH": normalizeHelpContent(thTH),
};
