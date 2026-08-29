import { I18nProvider } from "../i18n";

type AppProvidersProps = {
  children: React.ReactNode;
};

export function AppProviders({ children }: AppProvidersProps) {
  return <I18nProvider>{children}</I18nProvider>;
}
