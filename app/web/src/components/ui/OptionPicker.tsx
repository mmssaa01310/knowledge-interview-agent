import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

export type OptionPickerOption = {
  value: string;
  label: string;
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
  placement = "bottom",
  className = "",
}: OptionPickerProps) {
  const pickerId = useId();
  const listboxId = `${pickerId}-options`;
  const [isOpen, setIsOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const optionRefs = useRef<Array<HTMLDivElement | null>>([]);
  const selectedOption = options.find((option) => option.value === value);
  const filteredOptions = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLocaleLowerCase();
    if (!normalizedQuery) return options;
    return options.filter((option) => (
      option.label.toLocaleLowerCase().includes(normalizedQuery)
      || option.description?.toLocaleLowerCase().includes(normalizedQuery)
    ));
  }, [options, searchQuery]);

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
    if (restoreFocus) triggerRef.current?.focus();
  }

  function selectOption(option: OptionPickerOption) {
    if (option.disabled) return;
    onChange(option.value);
    closeMenu();
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
            {filteredOptions.length === 0 ? (
              <p className="option-picker-empty">{emptyLabel}</p>
            ) : filteredOptions.map((option, index) => (
              <div
                ref={(element) => { optionRefs.current[index] = element; }}
                key={option.value}
                className={`option-picker-option${option.value === value ? " selected" : ""}${option.disabled ? " disabled" : ""}`}
                role="option"
                aria-selected={option.value === value}
                aria-disabled={option.disabled || undefined}
                tabIndex={option.disabled ? -1 : 0}
                onClick={() => selectOption(option)}
                onKeyDown={(event) => handleOptionKeyDown(event, index)}
              >
                <span className="option-picker-option-copy">
                  <strong>{option.label}</strong>
                  {option.description ? <small>{option.description}</small> : null}
                </span>
                {option.value === value ? <span className="option-picker-check" aria-hidden="true">✓</span> : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
