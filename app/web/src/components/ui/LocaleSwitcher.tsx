import { useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { useI18n } from "../../i18n";

type LocaleSwitcherProps = {
  compact?: boolean;
  className?: string;
};

export function LocaleSwitcher({ compact = false, className = "" }: LocaleSwitcherProps) {
  const { locale, locales, setLocale, t } = useI18n();
  const [isOpen, setIsOpen] = useState(false);
  const switcherId = useId();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const optionRefs = useRef<Array<HTMLDivElement | null>>([]);
  const currentLocale = locales.find((option) => option.code === locale) ?? locales[0];
  const currentIndex = Math.max(0, locales.findIndex((option) => option.code === locale));
  const listboxId = `${switcherId}-options`;

  useEffect(() => {
    if (!isOpen) return;

    function handlePointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }

    function handleFocusIn(event: FocusEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }

    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key !== "Escape") return;
      setIsOpen(false);
      triggerRef.current?.focus();
    }

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("focusin", handleFocusIn);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("focusin", handleFocusIn);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen]);

  function focusOption(index: number) {
    const nextIndex = Math.min(Math.max(index, 0), locales.length - 1);
    optionRefs.current[nextIndex]?.focus();
  }

  function openMenu(index = currentIndex) {
    setIsOpen(true);
    window.requestAnimationFrame(() => focusOption(index));
  }

  function closeMenu() {
    setIsOpen(false);
    triggerRef.current?.focus();
  }

  function selectLocale(nextLocale: typeof locale) {
    setLocale(nextLocale);
    closeMenu();
  }

  function handleTriggerKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      openMenu(currentIndex);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      openMenu(currentIndex);
    } else if (event.key === "Home" && isOpen) {
      event.preventDefault();
      focusOption(0);
    } else if (event.key === "End" && isOpen) {
      event.preventDefault();
      focusOption(locales.length - 1);
    }
  }

  function handleOptionKeyDown(event: KeyboardEvent<HTMLDivElement>, index: number, optionCode: typeof locale) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      focusOption((index + 1) % locales.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      focusOption((index - 1 + locales.length) % locales.length);
    } else if (event.key === "Home") {
      event.preventDefault();
      focusOption(0);
    } else if (event.key === "End") {
      event.preventDefault();
      focusOption(locales.length - 1);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectLocale(optionCode);
    }
  }

  return (
    <div className={`locale-switcher${compact ? " compact" : ""}${className ? ` ${className}` : ""}`} ref={rootRef} data-open={isOpen ? "true" : "false"}>
      {!compact ? <span className="locale-switcher-label">{t("common.language")}</span> : null}
      <div className="locale-switcher-control">
        <button
          ref={triggerRef}
          type="button"
          className="locale-switcher-trigger"
          aria-label={t("common.selectLanguage")}
          aria-controls={listboxId}
          aria-expanded={isOpen}
          aria-haspopup="listbox"
          onClick={() => (isOpen ? closeMenu() : openMenu())}
          onKeyDown={handleTriggerKeyDown}
        >
          <span className="locale-switcher-current">
            {compact ? <span className="locale-switcher-compact-label">{t("common.language")}</span> : null}
            <span className="locale-switcher-current-name">{currentLocale?.name ?? locale}</span>
          </span>
          <span className={isOpen ? "locale-switcher-chevron open" : "locale-switcher-chevron"} aria-hidden="true" />
        </button>
        {isOpen ? (
          <div id={listboxId} className="locale-switcher-options" role="listbox" aria-label={t("common.selectLanguage")}>
            {locales.map((option, index) => (
              <div
                ref={(element) => { optionRefs.current[index] = element; }}
                key={option.code}
                className={option.code === locale ? "locale-switcher-option selected" : "locale-switcher-option"}
                role="option"
                aria-selected={option.code === locale}
                tabIndex={0}
                onClick={() => selectLocale(option.code)}
                onKeyDown={(event) => handleOptionKeyDown(event, index, option.code)}
              >
                <span className="locale-switcher-option-name">{option.name}</span>
                {option.code === locale ? <span className="locale-switcher-option-check" aria-hidden="true">✓</span> : null}
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
