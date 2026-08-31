import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

export type OptionPickerOption = {
  value: string;
  label: string;
  id?: string;
  description?: string;
  disabled?: boolean;
};

type OptionPickerProps = {
  value: string;
  options: readonly OptionPickerOption[];
  onChange: (value: string) => void;
  ariaLabel: string;
  placeholder?: string;
  searchPlaceholder?: string;
  emptyLabel?: string;
  disabled?: boolean;
  searchable?: boolean;
  creatable?: boolean;
  onCreateOption?: (value: string) => void;
  createOptionLabel?: (value: string) => string;
  selectedValueLabel?: (value: string) => string;
  showOptionActions?: (option: OptionPickerOption) => boolean;
  onEditOption?: (option: OptionPickerOption) => void;
  onUpdateOption?: (option: OptionPickerOption, value: string) => Promise<void>;
  onDeleteOption?: (option: OptionPickerOption) => void;
  editOptionLabel?: (option: OptionPickerOption) => string;
  deleteOptionLabel?: (option: OptionPickerOption) => string;
  editOptionInputLabel?: (option: OptionPickerOption) => string;
  saveOptionLabel?: string;
  cancelOptionEditLabel?: string;
  optionUpdateErrorLabel?: string;
  placement?: "bottom" | "top";
  className?: string;
};

export function OptionPicker({
  value,
  options,
  onChange,
  ariaLabel,
  placeholder,
  searchPlaceholder,
  emptyLabel = "No options",
  disabled = false,
  searchable = false,
  creatable = false,
  onCreateOption,
  createOptionLabel,
  selectedValueLabel,
  showOptionActions,
  onEditOption,
  onUpdateOption,
  onDeleteOption,
  editOptionLabel,
  deleteOptionLabel,
  editOptionInputLabel,
  saveOptionLabel = "Save",
  cancelOptionEditLabel = "Cancel",
  optionUpdateErrorLabel = "Could not update option",
  placement = "bottom",
  className = "",
}: OptionPickerProps) {
  const pickerId = useId();
  const listboxId = `${pickerId}-options`;
  const [isOpen, setIsOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [editingOptionId, setEditingOptionId] = useState<string | null>(null);
  const [editingOptionValue, setEditingOptionValue] = useState("");
  const [isUpdatingOption, setIsUpdatingOption] = useState(false);
  const [optionUpdateError, setOptionUpdateError] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const optionRefs = useRef<Array<HTMLDivElement | null>>([]);
  const selectedOption = options.find((option) => option.value === value)
    ?? (creatable && value
      ? { value, label: selectedValueLabel?.(value) ?? value }
      : undefined);
  const filteredOptions = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLocaleLowerCase();
    if (!normalizedQuery) return options;
    return options.filter((option) => (
      option.label.toLocaleLowerCase().includes(normalizedQuery)
      || option.description?.toLocaleLowerCase().includes(normalizedQuery)
    ));
  }, [options, searchQuery]);
  const normalizedCreateValue = searchQuery.trim();
  const normalizedCreateValueForComparison = normalizedCreateValue.toLocaleLowerCase();
  const canCreateOption = creatable
    && normalizedCreateValue.length > 0
    && !options.some((option) => (
      option.value.trim().toLocaleLowerCase() === normalizedCreateValueForComparison
      || option.label.trim().toLocaleLowerCase() === normalizedCreateValueForComparison
    ));

  useEffect(() => {
    if (!isOpen) return;

    function handlePointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setIsOpen(false);
        setSearchQuery("");
      }
    }

    function handleFocusIn(event: FocusEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setIsOpen(false);
        setSearchQuery("");
      }
    }

    function handleDocumentKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key !== "Escape") return;
      setIsOpen(false);
      setSearchQuery("");
      triggerRef.current?.focus();
    }

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("focusin", handleFocusIn);
    document.addEventListener("keydown", handleDocumentKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("focusin", handleFocusIn);
      document.removeEventListener("keydown", handleDocumentKeyDown);
    };
  }, [isOpen]);

  function enabledOptionIndexes() {
    return filteredOptions.reduce<number[]>((indexes, option, index) => {
      if (!option.disabled) indexes.push(index);
      return indexes;
    }, []);
  }

  function focusOption(index: number) {
    const indexes = enabledOptionIndexes();
    if (indexes.length === 0) return;
    const currentPosition = indexes.indexOf(index);
    const nextPosition = currentPosition >= 0 ? currentPosition : 0;
    optionRefs.current[indexes[nextPosition]]?.focus();
  }

  function focusRelativeOption(index: number, offset: number) {
    const indexes = enabledOptionIndexes();
    if (indexes.length === 0) return;
    const currentPosition = Math.max(0, indexes.indexOf(index));
    const nextPosition = (currentPosition + offset + indexes.length) % indexes.length;
    optionRefs.current[indexes[nextPosition]]?.focus();
  }

  function openMenu() {
    if (disabled) return;
    setIsOpen(true);
    window.requestAnimationFrame(() => {
      if (searchable) {
        searchRef.current?.focus();
        return;
      }
      const selectedIndex = filteredOptions.findIndex((option) => option.value === value && !option.disabled);
      focusOption(selectedIndex >= 0 ? selectedIndex : 0);
    });
  }

  function closeMenu(restoreFocus = true) {
    setIsOpen(false);
    setSearchQuery("");
    setEditingOptionId(null);
    setEditingOptionValue("");
    setOptionUpdateError(false);
    if (restoreFocus) triggerRef.current?.focus();
  }

  function startInlineEdit(option: OptionPickerOption) {
    if (!option.id || !onUpdateOption) return;
    setEditingOptionId(option.id);
    setEditingOptionValue(option.value);
    setOptionUpdateError(false);
  }

  function cancelInlineEdit() {
    if (isUpdatingOption) return;
    setEditingOptionId(null);
    setEditingOptionValue("");
    setOptionUpdateError(false);
  }

  async function saveInlineEdit(option: OptionPickerOption) {
    if (!option.id || !onUpdateOption || !editingOptionValue.trim() || isUpdatingOption) return;
    setIsUpdatingOption(true);
    setOptionUpdateError(false);
    try {
      await onUpdateOption(option, editingOptionValue.trim());
      setEditingOptionId(null);
      setEditingOptionValue("");
    } catch (error) {
      console.error("Failed to update option", error);
      setOptionUpdateError(true);
    } finally {
      setIsUpdatingOption(false);
    }
  }

  function selectOption(option: OptionPickerOption) {
    if (option.disabled) return;
    onChange(option.value);
    closeMenu();
  }

  function createOption() {
    if (!canCreateOption) return;
    onCreateOption?.(normalizedCreateValue);
    selectOption({
      value: normalizedCreateValue,
      label: createOptionLabel?.(normalizedCreateValue) ?? normalizedCreateValue,
    });
  }

  function handleTriggerKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openMenu();
    }
  }

  function handleSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      focusOption(0);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const normalizedQuery = searchQuery.trim().toLocaleLowerCase();
      const exactOption = filteredOptions.find((option) => (
        option.value.trim().toLocaleLowerCase() === normalizedQuery
        || option.label.trim().toLocaleLowerCase() === normalizedQuery
      ));
      if (exactOption) {
        selectOption(exactOption);
      } else {
        createOption();
      }
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeMenu();
    }
  }

  function handleOptionKeyDown(event: KeyboardEvent<HTMLDivElement>, index: number) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      focusRelativeOption(index, 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      focusRelativeOption(index, -1);
    } else if (event.key === "Home") {
      event.preventDefault();
      focusOption(0);
    } else if (event.key === "End") {
      event.preventDefault();
      const indexes = enabledOptionIndexes();
      if (indexes.length > 0) focusOption(indexes[indexes.length - 1]);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      const option = filteredOptions[index];
      if (option) selectOption(option);
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeMenu();
    }
  }

  return (
    <div className={`option-picker ${className}`.trim()} ref={rootRef} data-open={isOpen ? "true" : "false"}>
      <button
        ref={triggerRef}
        type="button"
        className="option-picker-trigger"
        aria-label={ariaLabel}
        aria-controls={listboxId}
        aria-expanded={isOpen}
        aria-haspopup="listbox"
        disabled={disabled}
        onClick={() => (isOpen ? closeMenu() : openMenu())}
        onKeyDown={handleTriggerKeyDown}
      >
        <span className={selectedOption ? "option-picker-trigger-label" : "option-picker-trigger-label placeholder"}>
          {selectedOption?.label ?? placeholder ?? emptyLabel}
        </span>
        <span className={isOpen ? "option-picker-chevron open" : "option-picker-chevron"} aria-hidden="true" />
      </button>
      {isOpen ? (
        <div className={`option-picker-menu ${placement === "top" ? "top" : ""}`.trim()}>
          {searchable ? (
            <div className="option-picker-search-wrap">
              <input
                ref={searchRef}
                className="option-picker-search"
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                onKeyDown={handleSearchKeyDown}
                placeholder={searchPlaceholder}
                aria-label={searchPlaceholder ?? ariaLabel}
              />
            </div>
          ) : null}
          <div id={listboxId} className="option-picker-options" role="listbox" aria-label={ariaLabel}>
            {filteredOptions.length === 0 && !canCreateOption ? (
              <p className="option-picker-empty">{emptyLabel}</p>
            ) : filteredOptions.map((option, index) => {
              const isEditing = editingOptionId === option.id && Boolean(onUpdateOption);
              return (
                <div
                  ref={(element) => { optionRefs.current[index] = element; }}
                  key={option.value}
                  className={`option-picker-option${option.value === value ? " selected" : ""}${option.disabled ? " disabled" : ""}${isEditing ? " editing" : ""}`}
                  role="option"
                  aria-selected={option.value === value}
                  aria-disabled={option.disabled || undefined}
                  tabIndex={isEditing || option.disabled ? -1 : 0}
                  onClick={(event) => {
                    if (isEditing) {
                      event.stopPropagation();
                      return;
                    }
                    selectOption(option);
                  }}
                  onKeyDown={(event) => {
                    if (isEditing) {
                      event.stopPropagation();
                      return;
                    }
                    handleOptionKeyDown(event, index);
                  }}
                >
                  {isEditing ? (
                    <>
                      <input
                        className="option-picker-inline-edit-input"
                        value={editingOptionValue}
                        autoFocus
                        aria-label={editOptionInputLabel?.(option) ?? editOptionLabel?.(option) ?? "Edit option"}
                        onChange={(event) => setEditingOptionValue(event.target.value)}
                        onClick={(event) => event.stopPropagation()}
                        onKeyDown={(event) => {
                          event.stopPropagation();
                          if (event.key === "Enter") {
                            event.preventDefault();
                            void saveInlineEdit(option);
                          } else if (event.key === "Escape") {
                            event.preventDefault();
                            cancelInlineEdit();
                          }
                        }}
                      />
                      <span className="option-picker-inline-edit-actions">
                        <button
                          type="button"
                          className="option-picker-action option-picker-action-save"
                          aria-label={saveOptionLabel}
                          title={saveOptionLabel}
                          disabled={!editingOptionValue.trim() || isUpdatingOption}
                          onClick={(event) => {
                            event.stopPropagation();
                            void saveInlineEdit(option);
                          }}
                          onKeyDown={(event) => event.stopPropagation()}
                        >
                          <svg className="option-picker-action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                            <path d="m5 12 4 4L19 6" />
                          </svg>
                        </button>
                        <button
                          type="button"
                          className="option-picker-action option-picker-action-cancel"
                          aria-label={cancelOptionEditLabel}
                          title={cancelOptionEditLabel}
                          disabled={isUpdatingOption}
                          onClick={(event) => {
                            event.stopPropagation();
                            cancelInlineEdit();
                          }}
                          onKeyDown={(event) => event.stopPropagation()}
                        >
                          <svg className="option-picker-action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
                            <path d="m6 6 12 12" />
                            <path d="m18 6-12 12" />
                          </svg>
                        </button>
                      </span>
                      {optionUpdateError ? <small className="option-picker-inline-edit-error">{optionUpdateErrorLabel}</small> : null}
                    </>
                  ) : (
                    <>
                      <span className="option-picker-option-copy">
                        <strong>{option.label}</strong>
                        {option.description ? <small>{option.description}</small> : null}
                      </span>
                      {showOptionActions?.(option) ? (
                        <span className="option-picker-actions">
                          {onEditOption || onUpdateOption ? (
                            <button
                              type="button"
                              className="option-picker-action edit"
                              aria-label={editOptionLabel?.(option) ?? "Edit option"}
                              title={editOptionLabel?.(option) ?? "Edit option"}
                              onClick={(event) => {
                                event.stopPropagation();
                                if (onUpdateOption && option.id) {
                                  startInlineEdit(option);
                                  return;
                                }
                                onEditOption?.(option);
                                closeMenu(false);
                              }}
                              onKeyDown={(event) => event.stopPropagation()}
                            >
                              <svg className="option-picker-action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                                <path d="M12 20h9" />
                                <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
                              </svg>
                            </button>
                          ) : null}
                          {onDeleteOption ? (
                            <button
                              type="button"
                              className="option-picker-action danger"
                              aria-label={deleteOptionLabel?.(option) ?? "Delete option"}
                              title={deleteOptionLabel?.(option) ?? "Delete option"}
                              onClick={(event) => {
                                event.stopPropagation();
                                onDeleteOption(option);
                                closeMenu(false);
                              }}
                              onKeyDown={(event) => event.stopPropagation()}
                            >
                              <svg className="option-picker-action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
                                <path d="m6 6 12 12" />
                                <path d="m18 6-12 12" />
                              </svg>
                            </button>
                          ) : null}
                        </span>
                      ) : null}
                      {option.value === value ? <span className="option-picker-check" aria-hidden="true">✓</span> : null}
                    </>
                  )}
                </div>
              );
            })}
            {canCreateOption ? (
              <div
                className="option-picker-option option-picker-create"
                role="option"
                aria-selected="false"
                tabIndex={0}
                onClick={createOption}
                onKeyDown={(event) => {
                  if (event.key !== "Enter" && event.key !== " ") return;
                  event.preventDefault();
                  createOption();
                }}
              >
                <span className="option-picker-option-copy">
                  <strong>{createOptionLabel?.(normalizedCreateValue) ?? normalizedCreateValue}</strong>
                </span>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
