import jaJP from "./ja-JP.json";
import enUS from "./en-US.json";
import zhCN from "./zh-CN.json";
import thTH from "./th-TH.json";
import type { UiLocale } from "../../i18n/localeMetadata";

export type HelpSectionContent = {
  id: string;
  title: string;
  summary: string;
  steps: string[];
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

export const helpContentByLocale: Record<UiLocale, HelpContent> = {
  "ja-JP": jaJP,
  "en-US": enUS,
  "zh-CN": zhCN,
  "th-TH": thTH,
};
