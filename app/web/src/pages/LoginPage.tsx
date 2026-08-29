import { useI18n } from "../i18n";
import { LocaleSwitcher } from "../components/ui/LocaleSwitcher";

type LoginPageProps = {
  onLogin: () => void;
};

export function LoginPage({ onLogin }: LoginPageProps) {
  const { t } = useI18n();
  return (
    <main className="login-page">
      <section className="login-panel">
        <LocaleSwitcher />
        <img className="login-brand-image" src="/images/kikiori-logo.svg" alt="KIKIORI" />
        <p className="login-brand-caption">{t("common.tagline")}</p>
        <h1>{t("common.appName")}</h1>
        <p>{t("common.productDescription")}</p>
        <p>{t("common.developmentAuthNotice")}</p>
        <button className="primary" onClick={onLogin}>{t("common.continue")}</button>
      </section>
    </main>
  );
}
