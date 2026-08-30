import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

export type ColorTheme = "light" | "dark";

const COLOR_THEME_STORAGE_KEY = "kikiori.color-theme";

type ThemeContextValue = {
  theme: ColorTheme;
  setTheme: (theme: ColorTheme) => void;
  toggleTheme: () => void;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

function readStoredTheme(): ColorTheme {
  if (typeof window === "undefined") return "light";
  try {
    return window.localStorage.getItem(COLOR_THEME_STORAGE_KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

export function applyColorTheme(theme: ColorTheme) {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
}

export function initializeColorTheme() {
  const theme = readStoredTheme();
  applyColorTheme(theme);
  return theme;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ColorTheme>(initializeColorTheme);

  const setTheme = useCallback((nextTheme: ColorTheme) => {
    setThemeState(nextTheme);
    applyColorTheme(nextTheme);
    try {
      window.localStorage.setItem(COLOR_THEME_STORAGE_KEY, nextTheme);
    } catch {
      // Storageが使えない環境でも、この画面を切り替える。
    }
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(theme === "light" ? "dark" : "light");
  }, [setTheme, theme]);

  const value = useMemo<ThemeContextValue>(() => ({ theme, setTheme, toggleTheme }), [setTheme, theme, toggleTheme]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("useTheme must be used inside ThemeProvider");
  return context;
}
