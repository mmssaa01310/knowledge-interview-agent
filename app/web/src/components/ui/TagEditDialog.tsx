import { useId, useState } from "react";
import type { FormEvent } from "react";
import type { KnowledgeTag } from "@ai-interviewer/shared-types";

type TagEditDialogProps = {
  tag: KnowledgeTag;
  title: string;
  inputLabel: string;
  saveLabel: string;
  cancelLabel: string;
  errorLabel: string;
  onClose: () => void;
  onSave: (value: string) => Promise<void>;
};

export function TagEditDialog({
  tag,
  title,
  inputLabel,
  saveLabel,
  cancelLabel,
  errorLabel,
  onClose,
  onSave,
}: TagEditDialogProps) {
  const titleId = useId();
  const [value, setValue] = useState(tag.name);
  const [isSaving, setIsSaving] = useState(false);
  const [hasError, setHasError] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedValue = value.trim();
    if (!normalizedValue || isSaving) return;
    setIsSaving(true);
    setHasError(false);
    try {
      await onSave(normalizedValue);
      onClose();
    } catch (error) {
      console.error("Failed to update knowledge tag", error);
      setHasError(true);
      setIsSaving(false);
    }
  }

  return (
    <div
      className="dialog-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !isSaving) onClose();
      }}
    >
      <form className="dialog-panel tag-edit-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} onSubmit={handleSubmit}>
        <div className="dialog-header">
          <div>
            <h2 id={titleId}>{title}</h2>
          </div>
        </div>
        <label>
          <span>{inputLabel}</span>
          <input
            type="text"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            autoFocus
            autoComplete="off"
          />
        </label>
        {hasError ? <p className="notice error">{errorLabel}</p> : null}
        <div className="dialog-actions">
          <button type="button" className="ghost" onClick={onClose} disabled={isSaving}>{cancelLabel}</button>
          <button type="submit" className="primary" disabled={!value.trim() || isSaving}>
            {isSaving ? "…" : saveLabel}
          </button>
        </div>
      </form>
    </div>
  );
}
