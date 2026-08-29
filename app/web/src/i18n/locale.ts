import {
  DEFAULT_UI_LOCALE,
  isSupportedLocale,
  SUPPORTED_LOCALES,
  type UiLocale,
} from "./localeMetadata";

export const UI_LOCALE_STORAGE_KEY = "kikiori.ui-locale";
export const UI_LOCALE_EXPLICIT_STORAGE_KEY = "kikiori.ui-locale.explicit";
export const UI_LOCALE_COOKIE_NAME = "kikiori.ui-locale";

export type LocaleSource = "explicit" | "profile" | "cookie" | "storage" | "browser" | "default";

export type ResolvedLocale = {
  locale: UiLocale;
  source: LocaleSource;
};

let currentUiLocale: UiLocale = DEFAULT_UI_LOCALE;

function readLocalStorage(key: string) {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function readCookie() {
  if (typeof document === "undefined") return null;
  const cookie = document.cookie
    .split(";")
    .map((item) => item.trim())
    .find((item) => item.startsWith(`${UI_LOCALE_COOKIE_NAME}=`));
  if (!cookie) return null;
  try {
    return decodeURIComponent(cookie.slice(UI_LOCALE_COOKIE_NAME.length + 1));
  } catch {
    return null;
  }
}

function findBrowserLocale() {
  if (typeof navigator === "undefined") return null;
  const languages = navigator.languages?.length ? navigator.languages : [navigator.language];
  for (const language of languages) {
    if (isSupportedLocale(language)) return language;
    const languagePrefix = language.toLowerCase().split("-")[0];
    const match = SUPPORTED_LOCALES.find((locale) => locale.code.toLowerCase().split("-")[0] === languagePrefix);
    if (match) return match.code;
  }
  return null;
}

export function resolveClientLocale(): ResolvedLocale {
  const explicit = readLocalStorage(UI_LOCALE_EXPLICIT_STORAGE_KEY);
  if (isSupportedLocale(explicit)) return { locale: explicit, source: "explicit" };

  const cookie = readCookie();
  if (isSupportedLocale(cookie)) return { locale: cookie, source: "cookie" };

  const stored = readLocalStorage(UI_LOCALE_STORAGE_KEY);
  if (isSupportedLocale(stored)) return { locale: stored, source: "storage" };

  const browser = findBrowserLocale();
  if (browser) return { locale: browser, source: "browser" };

  return { locale: DEFAULT_UI_LOCALE, source: "default" };
}

export function getCurrentUiLocale() {
  return currentUiLocale;
}

export function setCurrentUiLocale(locale: UiLocale) {
  currentUiLocale = locale;
}

export function writeLocalePreference(locale: UiLocale) {
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(UI_LOCALE_STORAGE_KEY, locale);
      window.localStorage.setItem(UI_LOCALE_EXPLICIT_STORAGE_KEY, locale);
    } catch {
      // Storageが使えない環境でも、現在の画面の言語切替は継続する。
    }
  }
  if (typeof document !== "undefined") {
    document.cookie = `${UI_LOCALE_COOKIE_NAME}=${encodeURIComponent(locale)}; path=/; max-age=31536000; samesite=lax`;
  }
}
