import { LocaleSwitcher } from "../components/ui/LocaleSwitcher";
import { helpContentByLocale, type HelpSectionIcon } from "../content/help";
import { useI18n } from "../i18n";

const helpSectionIconSources: Record<HelpSectionIcon, string> = {
  kikiori: "/images/kikiori-icon.svg",
  kiko: "/images/kiko-waiting.svg",
};

export function HelpPage() {
  const { locale, t } = useI18n();
  const content = helpContentByLocale[locale];

  function closeHelpPage() {
    window.close();
    if (!window.closed) {
      window.location.replace("/knowledge-dbs");
    }
  }

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
                <div className={section.icon ? "help-section-heading with-icon" : "help-section-heading"}>
                  {section.icon ? <img className="help-section-icon" src={helpSectionIconSources[section.icon]} alt="" aria-hidden="true" /> : null}
                  <h2>{section.title}</h2>
                </div>
                <p>{section.summary}</p>
                {section.steps.length > 0 ? (
                  <>
                    <div className="help-steps-label">{t("help.stepsLabel")}</div>
                    <ol className="help-step-list">
                      {section.steps.map((step) => <li key={step}>{step}</li>)}
                    </ol>
                  </>
                ) : null}
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
        <button className="primary help-back-link" type="button" onClick={closeHelpPage}>{t("help.close")}</button>
      </article>
    </main>
  );
}
