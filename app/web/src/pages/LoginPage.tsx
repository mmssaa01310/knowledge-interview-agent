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
          <svg className="login-voice-illustration" viewBox="0 0 460 190" focusable="false">
            <circle className="login-voice-orbit" cx="230" cy="95" r="76" />
            <circle className="login-voice-orbit login-voice-orbit-inner" cx="230" cy="95" r="57" />
            <g className="login-voice-wave">
              {[10, 17, 25, 35, 47, 57, 44, 31, 21, 13, 18, 29, 42, 56, 48, 37, 27, 18, 12].map((height, index) => (
                <rect key={index} x={107 + index * 13} y={95 - height / 2} width="5" height={height} rx="2.5" />
              ))}
            </g>
            <g className="login-voice-microphone">
              <rect x="217" y="46" width="26" height="59" rx="13" />
              <path d="M202 88v6a28 28 0 0 0 56 0v-6M230 122v20m-16 0h32" />
            </g>
            <circle className="login-voice-signal" cx="117" cy="58" r="4" />
            <circle className="login-voice-signal login-voice-signal-secondary" cx="344" cy="132" r="3" />
          </svg>
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
