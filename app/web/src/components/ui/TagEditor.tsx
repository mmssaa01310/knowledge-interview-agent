import { useState } from "react";
import type { KeyboardEvent } from "react";

type TagEditorProps = {
  tags: readonly string[];
  suggestions?: readonly string[];
  onChange: (tags: string[]) => void;
  ariaLabel: string;
  placeholder: string;
  addLabel: string;
  removeLabel: (tag: string) => string;
  suggestionsLabel?: string;
  selectSuggestionLabel?: (tag: string) => string;
  maxTags?: number;
  maxTagLength?: number;
};

function normalizeTagKey(tag: string) {
  return tag.trim().toLocaleLowerCase();
}

function hasTag(tags: readonly string[], candidate: string) {
  const normalizedCandidate = normalizeTagKey(candidate);
  return tags.some((tag) => {
    const normalizedTag = normalizeTagKey(tag);
    return normalizedTag === normalizedCandidate;
  });
}

export function TagEditor({
  tags,
  suggestions = [],
  onChange,
  ariaLabel,
  placeholder,
  addLabel,
  removeLabel,
  suggestionsLabel,
  selectSuggestionLabel,
  maxTags = 20,
  maxTagLength = 40,
}: TagEditorProps) {
  const [draft, setDraft] = useState("");

  function addTag(value = draft) {
    const nextTag = value.trim();
    if (!nextTag || nextTag.length > maxTagLength || tags.length >= maxTags || hasTag(tags, nextTag)) {
      return;
    }
    onChange([...tags, nextTag]);
    setDraft("");
  }

  function removeTag(tagToRemove: string) {
    onChange(tags.filter((tag) => tag !== tagToRemove));
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.nativeEvent.isComposing) return;
    if (event.key === "Enter" || event.key === "," || event.key === "、") {
      event.preventDefault();
      addTag();
      return;
    }
    if (event.key === "Backspace" && !draft && tags.length > 0) {
      onChange(tags.slice(0, -1));
    }
  }

  const canAdd = draft.trim().length > 0
    && draft.trim().length <= maxTagLength
    && tags.length < maxTags
    && !hasTag(tags, draft.trim());
  const availableSuggestions = Array.from(
    new Map(
      suggestions
        .map((tag) => tag.trim())
        .filter(Boolean)
        .map((tag) => [normalizeTagKey(tag), tag] as const),
    ).values(),
  )
    .filter((tag) => tag.length <= maxTagLength)
    .filter((tag) => !hasTag(tags, tag))
    .filter((tag) => !draft.trim() || normalizeTagKey(tag).includes(normalizeTagKey(draft)))
    .sort((left, right) => left.localeCompare(right));

  return (
    <div className="tag-editor">
      {tags.length > 0 ? (
        <div className="tag-editor-list tag-editor-selected-list" aria-label={ariaLabel}>
          {tags.map((tag) => (
            <span className="knowledge-tag" key={tag}>
              <span>#{tag}</span>
              <button type="button" className="tag-editor-remove" onClick={() => removeTag(tag)} aria-label={removeLabel(tag)}>×</button>
            </span>
          ))}
        </div>
      ) : null}
      <div className="tag-editor-input-row">
        <input
          value={draft}
          maxLength={maxTagLength}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          aria-label={ariaLabel}
        />
        <button type="button" className="ghost compact" onClick={() => addTag()} disabled={!canAdd}>{addLabel}</button>
      </div>
      {tags.length < maxTags && suggestionsLabel && selectSuggestionLabel && availableSuggestions.length > 0 ? (
        <div className="tag-editor-suggestions" aria-label={suggestionsLabel}>
          <span className="tag-editor-suggestions-label">{suggestionsLabel}</span>
          <div className="tag-editor-suggestion-list" role="list">
            {availableSuggestions.map((tag) => (
              <button
                type="button"
                className="tag-editor-suggestion"
                key={tag}
                onClick={() => addTag(tag)}
                aria-label={selectSuggestionLabel(tag)}
              >
                #{tag}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
