import { useEffect, useRef, useState } from "react";
import type { UserProfile } from "../../lib/api";
import { getDevelopmentToken, setDevelopmentToken } from "../../lib/api";
import { useI18n } from "../../i18n";
import { LocaleSwitcher } from "./LocaleSwitcher";
import { OptionPicker } from "./OptionPicker";
import { ThemeToggle } from "./ThemeToggle";
import type { AppSection } from "../../types/app";

type UserMenuProps = {
  user: UserProfile | null;
  activeSection: AppSection;
  isCollapsed?: boolean;
  onNavigate: (path: string) => void;
  onStartGuide: () => void;
  onLogout: () => void;
};

function UserGlyph() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="8" r="3.2" />
      <path d="M5.5 19c.7-3.1 2.8-4.7 6.5-4.7s5.8 1.6 6.5 4.7" />
    </svg>
  );
}

function SettingsGlyph() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m12 3 1 2.1 2.3.6 2-.9 1.9 1.9-.9 2 .6 2.3 2.1 1v2.7l-2.1 1-.6 2.3.9 2-1.9 1.9-2-.9-2.3.6-1 2.1H9.3l-1-2.1-2.3-.6-2 .9-1.9-1.9.9-2-.6-2.3-2.1-1v-2.7l2.1-1 .6-2.3-.9-2L4 4.8l2 .9 2.3-.6 1-2.1H12Z" />
      <circle cx="10.7" cy="12.3" r="2.5" />
    </svg>
  );
}

function ChevronGlyph({ direction = "right" }: { direction?: "down" | "right" }) {
  return <span className={`user-menu-chevron ${direction}`} aria-hidden="true" />;
}

function ExternalLinkGlyph() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M14 5h5v5" />
      <path d="m19 5-8 8" />
      <path d="M18 13v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
    </svg>
  );
}

function GuideGlyph() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4.5 5.5h15v11h-15z" />
      <path d="m8 19 2-2.5h4l2 2.5" />
      <path d="M8.5 9h7M8.5 12h4" />
    </svg>
  );
}

function LogoutGlyph() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M10 5H6.5A1.5 1.5 0 0 0 5 6.5v11A1.5 1.5 0 0 0 6.5 19H10" />
      <path d="M13 8.5 16.5 12 13 15.5M16 12H9" />
    </svg>
  );
}

export function UserMenu({
  user,
  activeSection,
  isCollapsed = false,
  onNavigate,
  onStartGuide,
  onLogout,
}: UserMenuProps) {
  const { t } = useI18n();
  const [isOpen, setIsOpen] = useState(false);
  const [isDeveloperSettingsOpen, setIsDeveloperSettingsOpen] = useState(false);
  const [developmentToken, setDevelopmentTokenState] = useState(getDevelopmentToken);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const displayName = user?.displayName?.trim() || t("navigation.disconnected");
  const roleLabel = user ? t(`navigation.roles.${user.role}`) : t("navigation.disconnected");
  const avatarText = [...displayName][0] ?? "?";
  const triggerLabel = `${displayName} / ${roleLabel}`;
  const canManageSystem = user?.role === "admin";

  useEffect(() => {
    if (!isOpen) return;

    function handlePointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setIsOpen(false);
        setIsDeveloperSettingsOpen(false);
      }
    }

    function handleFocusIn(event: FocusEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setIsOpen(false);
        setIsDeveloperSettingsOpen(false);
      }
    }

    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key !== "Escape") return;
      setIsOpen(false);
      setIsDeveloperSettingsOpen(false);
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

  useEffect(() => {
    if (!isOpen) return;
    window.requestAnimationFrame(() => {
      menuRef.current?.querySelector<HTMLElement>("[data-user-menu-first-focus]")?.focus();
    });
  }, [isOpen]);

  function closeMenu(restoreFocus = false) {
    setIsOpen(false);
    setIsDeveloperSettingsOpen(false);
    if (restoreFocus) triggerRef.current?.focus();
  }

  function handleDevelopmentUserChange(token: string) {
    setDevelopmentToken(token);
    setDevelopmentTokenState(token);
    closeMenu();
    window.location.reload();
  }

  function handleNavigate(path: string) {
    closeMenu();
    onNavigate(path);
  }

  function handleStartGuide() {
    closeMenu();
    onStartGuide();
  }

  function handleLogout() {
    closeMenu();
    onLogout();
  }

  return (
    <div className={isCollapsed ? "sidebar-user-menu collapsed" : "sidebar-user-menu"} ref={rootRef} data-open={isOpen ? "true" : "false"} data-guide="user-menu">
      <button
        ref={triggerRef}
        type="button"
        className="sidebar-user-menu-trigger"
        aria-label={triggerLabel}
        aria-controls="kikiori-user-menu"
        aria-expanded={isOpen}
        aria-haspopup="menu"
        title={isCollapsed ? triggerLabel : undefined}
        onClick={() => setIsOpen((value) => !value)}
      >
        <span className="sidebar-user-avatar" aria-hidden="true">
          {user ? avatarText : <UserGlyph />}
        </span>
        <span className="sidebar-user-menu-copy">
          <strong>{displayName}</strong>
          <small>{roleLabel}</small>
        </span>
        <span className="sidebar-user-menu-settings" aria-hidden="true"><SettingsGlyph /></span>
      </button>

      {isOpen ? (
        <div id="kikiori-user-menu" ref={menuRef} className="user-menu-surface" role="menu" aria-label={t("navigation.userMenu")}>
          <div className="user-menu-profile">
            <span className="sidebar-user-avatar large" aria-hidden="true">{user ? avatarText : <UserGlyph />}</span>
            <span className="user-menu-profile-copy">
              <strong>{displayName}</strong>
              <small>{roleLabel}</small>
            </span>
          </div>

          <div className="user-menu-items">
            <LocaleSwitcher compact />
            <ThemeToggle />
            <a
              className="user-menu-item"
              href="/help"
              target="_blank"
              rel="noopener noreferrer"
              role="menuitem"
              data-user-menu-first-focus
              onClick={() => closeMenu()}
            >
              <span className="user-menu-item-icon" aria-hidden="true">?</span>
              <span>{t("navigation.help")}</span>
              <ExternalLinkGlyph />
            </a>
            <button type="button" className="user-menu-item" role="menuitem" onClick={handleStartGuide}>
              <span className="user-menu-item-icon"><GuideGlyph /></span>
              <span>{t("navigation.startGuide")}</span>
            </button>
            {canManageSystem ? (
              <button
                type="button"
                className={activeSection === "settings" ? "user-menu-item active" : "user-menu-item"}
                role="menuitem"
                onClick={() => handleNavigate("/settings")}
              >
                <span className="user-menu-item-icon"><SettingsGlyph /></span>
                <span>{t("navigation.systemSettings")}</span>
              </button>
            ) : null}
          </div>

          {import.meta.env.DEV ? (
            <div className="user-menu-developer">
              <button
                type="button"
                className="user-menu-item user-menu-developer-toggle"
                role="menuitem"
                aria-expanded={isDeveloperSettingsOpen}
                onClick={() => setIsDeveloperSettingsOpen((value) => !value)}
              >
                <span className="user-menu-item-icon"><SettingsGlyph /></span>
                <span>{t("navigation.developerSettings")}</span>
                <ChevronGlyph direction={isDeveloperSettingsOpen ? "down" : "right"} />
              </button>
              {isDeveloperSettingsOpen ? (
                <div className="user-menu-developer-panel">
                  <span className="user-menu-developer-label">{t("navigation.developmentUser")}</span>
                  <OptionPicker
                    value={developmentToken}
                    options={[
                      { value: "dev-admin", label: t("navigation.roles.admin") },
                      { value: "dev-manager", label: t("navigation.roles.knowledge_manager") },
                      { value: "dev-interviewer", label: t("navigation.roles.interviewer") },
                      { value: "dev-viewer", label: t("navigation.roles.viewer") },
                    ]}
                    onChange={handleDevelopmentUserChange}
                    ariaLabel={t("navigation.developmentUser")}
                    className="dev-user-picker"
                    placement="bottom"
                  />
                </div>
              ) : null}
            </div>
          ) : null}

          <div className="user-menu-divider" role="separator" />
          <button type="button" className="user-menu-item destructive" role="menuitem" onClick={handleLogout}>
            <span className="user-menu-item-icon"><LogoutGlyph /></span>
            <span>{t("navigation.logout")}</span>
          </button>
        </div>
      ) : null}
    </div>
  );
}
