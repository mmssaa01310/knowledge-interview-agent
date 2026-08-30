import { LocaleSwitcher } from "../components/ui/LocaleSwitcher";
import { useI18n } from "../i18n";

export function HelpPage() {
  const { t } = useI18n();

  return (
    <main className="help-page">
      <header className="help-header">
        <a className="help-brand" href="/knowledge-dbs">
          <img src="/images/kikiori-logo.svg" alt={t("common.appName")} />
        </a>
        <LocaleSwitcher />
      </header>
      <article className="help-panel">
        <p className="eyebrow">KIKIORI</p>
        <h1>{t("help.title")}</h1>
        <p className="help-intro">{t("help.intro")}</p>
        <div className="help-sections">
          <section>
            <h2>{t("help.sections.navigation.title")}</h2>
            <p>{t("help.sections.navigation.description")}</p>
          </section>
          <section>
            <h2>{t("help.sections.interview.title")}</h2>
            <p>{t("help.sections.interview.description")}</p>
          </section>
          <section>
            <h2>{t("help.sections.knowledge.title")}</h2>
            <p>{t("help.sections.knowledge.description")}</p>
          </section>
          <section>
            <h2>{t("help.sections.review.title")}</h2>
            <p>{t("help.sections.review.description")}</p>
          </section>
        </div>
        <a className="primary help-back-link" href="/knowledge-dbs">{t("help.backToApp")}</a>
      </article>
    </main>
  );
}
