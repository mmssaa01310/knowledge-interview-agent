import { getCurrentUiLocale } from "../i18n/locale";
import { getLocaleMetadata, type UiLocale } from "../i18n/localeMetadata";

export function formatDate(value?: string, locale: UiLocale = getCurrentUiLocale(), timeZone?: string) {
  if (!value) return "-";
  const metadata = getLocaleMetadata(locale);
  return new Intl.DateTimeFormat(metadata.dateLocale, {
    dateStyle: "medium",
    timeStyle: "short",
    ...(timeZone ? { timeZone } : {}),
  }).format(new Date(value));
}

export function formatNumber(value: number, locale: UiLocale = getCurrentUiLocale()) {
  return new Intl.NumberFormat(getLocaleMetadata(locale).numberLocale).format(value);
}

export function formatPercent(value: number, locale: UiLocale = getCurrentUiLocale()) {
  return new Intl.NumberFormat(getLocaleMetadata(locale).numberLocale, {
    style: "percent",
    maximumFractionDigits: 0,
  }).format(value);
}
