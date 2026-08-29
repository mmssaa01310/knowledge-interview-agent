import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import i18next from "i18next";
import { I18nextProvider, initReactI18next, useTranslation } from "react-i18next";
import {
  DEFAULT_UI_LOCALE,
  getLocaleMetadata,
  isSupportedLocale,
  SUPPORTED_LOCALES,
  type UiLocale,
} from "./localeMetadata";
import {
  resolveClientLocale,
  setCurrentUiLocale,
  writeLocalePreference,
} from "./locale";
import type { LocaleSource } from "./locale";
import { messages } from "./messages";

export type TranslationValues = Record<string, string | number>;
export type Translate = (key: string, values?: TranslationValues) => string;

type I18nContextValue = {
  locale: UiLocale;
  uiLocale: UiLocale;
  setLocale: (locale: UiLocale) => void;
  setProfileLocale: (locale: string | null | undefined) => void;
  t: Translate;
  locales: typeof SUPPORTED_LOCALES;
};

const I18nContext = createContext<I18nContextValue | null>(null);

const initialClientLocale = resolveClientLocale();
setCurrentUiLocale(initialClientLocale.locale);

const i18n = i18next.createInstance();
i18n.on("languageChanged", (language) => {
  if (isSupportedLocale(language)) {
    setCurrentUiLocale(language);
  }
});
i18n.use(initReactI18next).init({
  resources: Object.fromEntries(
    SUPPORTED_LOCALES.map(({ code }) => [code, { translation: messages[code] }]),
  ),
  lng: initialClientLocale.locale,
  fallbackLng: DEFAULT_UI_LOCALE,
  supportedLngs: SUPPORTED_LOCALES.map(({ code }) => code),
  load: "currentOnly",
  defaultNS: "translation",
  ns: ["translation"],
  interpolation: {
    // 翻訳リソースは `{value}` 形式で管理しているため、同じ形式で補間する。
    // ここを変更する場合は、全Localeの翻訳リソースを同時に更新すること。
    prefix: "{",
    suffix: "}",
    escapeValue: false,
  },
  returnEmptyString: false,
  initAsync: false,
});

function I18nStateProvider({ children }: { children: ReactNode }) {
  const initial = initialClientLocale;
  const [locale, setLocaleState] = useState<UiLocale>(initial.locale);
  const [source, setSource] = useState<LocaleSource>(initial.source);
  const { t: i18nextTranslate } = useTranslation();
  const t = useCallback<Translate>(
    (key, values) => i18nextTranslate(key, values),
    [i18nextTranslate],
  );

  useEffect(() => {
    setCurrentUiLocale(locale);
    const metadata = getLocaleMetadata(locale);
    if (i18n.language !== locale) {
      void i18n.changeLanguage(locale);
    }
    document.documentElement.lang = metadata.code;
    document.documentElement.dir = metadata.dir;
    document.documentElement.dataset.uiLocale = metadata.code;
    document.title = `${t("common.appName")} | ${t("common.tagline")}`;
  }, [locale, t]);

  function setLocale(nextLocale: UiLocale) {
    if (!isSupportedLocale(nextLocale)) return;
    writeLocalePreference(nextLocale);
    setSource("explicit");
    setLocaleState(nextLocale);
  }

  function setProfileLocale(profileLocale: string | null | undefined) {
    if (source === "explicit") return;
    if (isSupportedLocale(profileLocale)) {
      setSource("profile");
      setLocaleState(profileLocale);
      return;
    }
    if (source !== "profile") return;
    const fallback = resolveClientLocale();
    setSource(fallback.source);
    setLocaleState(fallback.locale);
  }

  const value = useMemo<I18nContextValue>(() => ({
    locale,
    uiLocale: locale,
    setLocale,
    setProfileLocale,
    t,
    locales: SUPPORTED_LOCALES,
  }), [locale, source, t]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function I18nProvider({ children }: { children: ReactNode }) {
  return (
    <I18nextProvider i18n={i18n}>
      <I18nStateProvider>{children}</I18nStateProvider>
    </I18nextProvider>
  );
}

export function useI18n() {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error("useI18n must be used inside I18nProvider");
  }
  return context;
}
