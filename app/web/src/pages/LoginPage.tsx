import { useI18n } from "../i18n";
import { LocaleSwitcher } from "../components/ui/LocaleSwitcher";
import { ThemeLogo } from "../components/ui/ThemeLogo";

type LoginPageProps = {
  onLogin: () => void;
};

export function LoginPage({ onLogin }: LoginPageProps) {
  const { t } = useI18n();
  return (
    <main className="login-page">
      <section className="login-story" aria-label={t("common.appName")}>
        <ThemeLogo compact className="login-story-brand" alt={t("common.appName")} />
        <div className="login-story-copy">
          <p className="login-story-kicker">{t("common.tagline")}</p>
          <h1>{t("common.appName")}</h1>
          <p className="login-story-description">{t("common.productDescription")}</p>
        </div>
        <div className="login-story-art" aria-hidden="true">
          <span className="login-story-card login-story-card-a" />
          <span className="login-story-card login-story-card-b" />
          <span className="login-story-card login-story-card-c" />
        </div>
      </section>
      <section className="login-panel" aria-labelledby="login-title">
        <LocaleSwitcher />
        <div className="login-panel-heading">
          <p className="eyebrow">{t("common.appName")}</p>
          <h2 id="login-title">{t("common.loginTitle")}</h2>
        </div>
        <p className="login-development-note">{t("common.developmentAuthNotice")}</p>
        <button className="primary" onClick={onLogin}>{t("common.continue")}</button>
      </section>
    </main>
  );
}
