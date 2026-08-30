import { I18nProvider } from "../i18n";
import { ThemeProvider } from "../theme";

type AppProvidersProps = {
  children: React.ReactNode;
};

export function AppProviders({ children }: AppProvidersProps) {
  return <ThemeProvider><I18nProvider>{children}</I18nProvider></ThemeProvider>;
}
