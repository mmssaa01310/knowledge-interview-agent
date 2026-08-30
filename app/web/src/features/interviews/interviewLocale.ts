import type { InterviewLocale, InterviewRecord, Knowledge } from "@ai-interviewer/shared-types";
import type { UiLocale } from "../../i18n/localeMetadata";

export const INTERVIEW_LOCALE_OPTIONS = [
  { value: "ja-JP", labelKey: "interview.languages.jaJP" },
  { value: "en-US", labelKey: "interview.languages.enUS" },
  { value: "zh-CN", labelKey: "interview.languages.zhCN" },
  { value: "pt-BR", labelKey: "interview.languages.ptBR" },
] as const satisfies ReadonlyArray<{ value: InterviewLocale; labelKey: string }>;

export function isInterviewLocale(value: unknown): value is InterviewLocale {
  return INTERVIEW_LOCALE_OPTIONS.some((option) => option.value === value);
}

export function getInterviewLocaleLabelKey(locale: InterviewLocale) {
  return INTERVIEW_LOCALE_OPTIONS.find((option) => option.value === locale)?.labelKey
    ?? "interview.languages.jaJP";
}

export function resolveDefaultInterviewLocale(
  knowledge: Knowledge | null | undefined,
  uiLocale: UiLocale = "ja-JP",
): InterviewLocale {
  const configuredLocale = knowledge?.interviewPlan?.interviewLocale;
  if (isInterviewLocale(configuredLocale)) return configuredLocale;
  if (knowledge?.language === "en") return "en-US";
  if (knowledge?.language === "multi" && isInterviewLocale(uiLocale)) return uiLocale;
  return "ja-JP";
}

export function resolveRecordInterviewLocale(
  record: InterviewRecord | null | undefined,
  knowledge: Knowledge | null | undefined,
  uiLocale: UiLocale = "ja-JP",
): InterviewLocale {
  return isInterviewLocale(record?.interviewLocale)
    ? record.interviewLocale
    : resolveDefaultInterviewLocale(knowledge, uiLocale);
}
