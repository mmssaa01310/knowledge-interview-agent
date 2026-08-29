export const SUPPORTED_LOCALES = [
  {
    code: "ja-JP",
    name: "日本語",
    dir: "ltr",
    fallback: "ja-JP",
    dateLocale: "ja-JP",
    numberLocale: "ja-JP",
  },
  {
    code: "en-US",
    name: "English",
    dir: "ltr",
    fallback: "ja-JP",
    dateLocale: "en-US",
    numberLocale: "en-US",
  },
  {
    code: "zh-CN",
    name: "简体中文",
    dir: "ltr",
    fallback: "ja-JP",
    dateLocale: "zh-CN",
    numberLocale: "zh-CN",
  },
  {
    code: "th-TH",
    name: "ไทย",
    dir: "ltr",
    fallback: "ja-JP",
    dateLocale: "th-TH",
    numberLocale: "th-TH",
  },
] as const;

export type UiLocale = (typeof SUPPORTED_LOCALES)[number]["code"];
export const DEFAULT_UI_LOCALE: UiLocale = "ja-JP";

export type LocaleMetadata = (typeof SUPPORTED_LOCALES)[number];

export type LocalePreferences = {
  uiLocale: UiLocale;
  interviewLocale?: string | null;
  timezone?: string | null;
};

export function isSupportedLocale(value: unknown): value is UiLocale {
  return typeof value === "string"
    && SUPPORTED_LOCALES.some((locale) => locale.code === value);
}

export function getLocaleMetadata(locale: UiLocale) {
  return SUPPORTED_LOCALES.find((item) => item.code === locale) ?? SUPPORTED_LOCALES[0];
}
