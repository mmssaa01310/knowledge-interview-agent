import type { Knowledge, KnowledgeTag } from "@ai-interviewer/shared-types";
import type { OptionPickerOption } from "../../components/ui/OptionPicker";

function normalizeTag(value: string) {
  return value.trim().replace(/^#+/, "");
}

export function buildKnowledgeTagOptions(
  availableTags: readonly KnowledgeTag[],
  knowledges: readonly Knowledge[],
  locale: string,
  notSetLabel: string,
): OptionPickerOption[] {
  const options = new Map<string, OptionPickerOption>();
  availableTags.forEach((tag) => {
    const value = normalizeTag(tag.name);
    if (!value) return;
    options.set(value.toLocaleLowerCase(), { id: tag.id, value, label: `#${value}` });
  });
  knowledges.forEach((knowledge) => {
    (knowledge.tags ?? []).forEach((rawTag) => {
      const value = normalizeTag(rawTag);
      if (!value || options.has(value.toLocaleLowerCase())) return;
      options.set(value.toLocaleLowerCase(), { value, label: `#${value}` });
    });
  });
  return [
    { value: "", label: notSetLabel },
    ...[...options.values()].sort((left, right) => left.label.localeCompare(right.label, locale)),
  ];
}
