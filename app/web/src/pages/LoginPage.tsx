import { useI18n } from "../i18n";
import { LocaleSwitcher } from "../components/ui/LocaleSwitcher";
import { ThemeLogo } from "../components/ui/ThemeLogo";
import { useState } from "react";
import { login } from "../lib/api";

type LoginPageProps = {
  onLogin: () => void;
};

export function LoginPage({ onLogin }: LoginPageProps) {
  const { t } = useI18n();
  const [loginId, setLoginId] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(false);
    try {
      await login(loginId, password);
      onLogin();
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  }
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
        <form onSubmit={(event) => void submit(event)}>
          <label>{t("common.account.loginId")}<input autoComplete="username" required maxLength={128} value={loginId} onChange={(event) => setLoginId(event.target.value)} /></label>
          <label>{t("common.account.password")}<input type="password" autoComplete="current-password" required maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} /></label>
          {error ? <p role="alert">{t("common.account.loginFailed")}</p> : null}
          <button className="primary" type="submit" disabled={busy}>{busy ? t("common.loading") : t("common.account.login")}</button>
        </form>
      </section>
    </main>
  );
}
