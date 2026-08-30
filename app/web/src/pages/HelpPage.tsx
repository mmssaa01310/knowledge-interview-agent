import { LocaleSwitcher } from "../components/ui/LocaleSwitcher";
import { helpContentByLocale } from "../content/help";
import { useI18n } from "../i18n";

export function HelpPage() {
  const { locale, t } = useI18n();
  const content = helpContentByLocale[locale];

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
        <h1>{content.title}</h1>
        <p className="help-intro">{content.intro}</p>
        <div className="help-layout">
          <aside className="help-toc" aria-label={t("help.tableOfContents")}>
            <div className="help-toc-card">
              <strong>{t("help.tableOfContents")}</strong>
              <nav>
                {content.sections.map((section) => (
                  <a href={`#help-${section.id}`} key={section.id}>{section.title}</a>
                ))}
                <a href="#help-faq">{t("help.faqTitle")}</a>
              </nav>
            </div>
          </aside>
          <div className="help-content">
            {content.sections.map((section) => (
              <section className="help-content-section" id={`help-${section.id}`} key={section.id}>
                <h2>{section.title}</h2>
                <p>{section.summary}</p>
                <div className="help-steps-label">{t("help.stepsLabel")}</div>
                <ol className="help-step-list">
                  {section.steps.map((step) => <li key={step}>{step}</li>)}
                </ol>
              </section>
            ))}
            <section className="help-content-section help-faq" id="help-faq">
              <h2>{t("help.faqTitle")}</h2>
              {content.faq.map((item) => (
                <details key={item.question}>
                  <summary>{item.question}</summary>
                  <p>{item.answer}</p>
                </details>
              ))}
            </section>
          </div>
        </div>
        <p className="help-external-note">{t("help.externalNote")}</p>
        <a className="primary help-back-link" href="/knowledge-dbs">{t("help.backToApp")}</a>
      </article>
    </main>
  );
}
