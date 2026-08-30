import { useI18n } from "../../i18n";
import { useTheme } from "../../theme";

function ThemeGlyph({ theme }: { theme: "light" | "dark" }) {
  return theme === "dark" ? (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M20.5 15.3A8 8 0 0 1 8.7 3.5 8 8 0 1 0 20.5 15.3Z" />
    </svg>
  ) : (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="3.6" />
      <path d="M12 2.5v2M12 19.5v2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M2.5 12h2M19.5 12h2M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" />
    </svg>
  );
}

export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  const { t } = useI18n();
  const nextTheme = theme === "light" ? "dark" : "light";

  return (
    <button
      type="button"
      className="user-menu-item theme-toggle"
      role="menuitemcheckbox"
      aria-checked={theme === "dark"}
      aria-label={t(nextTheme === "dark" ? "navigation.themeSwitchToDark" : "navigation.themeSwitchToLight")}
      onClick={toggleTheme}
    >
      <span className="user-menu-item-icon"><ThemeGlyph theme={theme} /></span>
      <span>{t("navigation.theme")}</span>
      <span className="theme-toggle-value">{t(`navigation.theme${theme === "dark" ? "Dark" : "Light"}`)}</span>
    </button>
  );
}
