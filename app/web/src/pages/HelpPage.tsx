import { useState } from "react";
import { LocaleSwitcher } from "../components/ui/LocaleSwitcher";
import {
  helpContentByLocale,
  helpGroupOrder,
  type HelpSectionGroup,
  type HelpSectionIcon,
} from "../content/help";
import type { GuideId } from "../features/guides/guideRegistry";
import { requestGuideFromHelp } from "../features/guides/guideStorage";
import { useI18n } from "../i18n";

const helpSectionIconSources: Record<HelpSectionIcon, string> = {
  kikiori: "/images/kikiori-icon.svg",
  kiko: "/images/kiko-waiting.svg",
};

export function HelpPage() {
  const { locale, t } = useI18n();
  const content = helpContentByLocale[locale];
  const [guideMessage, setGuideMessage] = useState<string | null>(null);

  function requestInteractiveGuide(guideId?: GuideId) {
    const requested = requestGuideFromHelp(guideId);
    setGuideMessage(t(requested ? "help.guideRequestSent" : "help.guideRequestUnavailable"));
  }

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
        <div className="help-hero">
          <div className="help-hero-copy">
            <p className="eyebrow">KIKIORI</p>
            <h1>{content.title}</h1>
            <p className="help-intro">{content.intro}</p>
          </div>
          <div className="help-hero-actions">
            <button className="primary help-guide-button" type="button" onClick={() => requestInteractiveGuide()}>
              <span aria-hidden="true">◎</span>
              {t("help.startGuide")}
            </button>
            <p>{t("help.guideHint")}</p>
          </div>
        </div>
        {guideMessage ? <p className="help-guide-status" role="status">{guideMessage}</p> : null}

        <section className="help-quick-start" aria-labelledby="help-quick-start-title">
          <div className="help-quick-start-heading">
            <div>
              <p className="help-section-label">{t("help.quickStartLabel")}</p>
              <h2 id="help-quick-start-title">{content.quickStart.title}</h2>
            </div>
            <p>{content.quickStart.summary}</p>
          </div>
          <ol className="help-quick-start-list">
            {content.quickStart.steps.map((step, index) => (
              <li className="help-quick-start-step" key={step.id}>
                <span className="help-quick-start-number" aria-hidden="true">{index + 1}</span>
                <div className="help-quick-start-copy">
                  <strong>{step.title}</strong>
                  <p>{step.description}</p>
                  {step.guideId ? (
                    <button className="ghost compact help-guide-link" type="button" onClick={() => requestInteractiveGuide(step.guideId)}>
                      <span aria-hidden="true">◎</span>
                      {t("help.viewOnScreen")}
                    </button>
                  ) : null}
                </div>
              </li>
            ))}
          </ol>
        </section>

        <div className="help-layout">
          <aside className="help-toc" aria-label={t("help.tableOfContents")}>
            <div className="help-toc-card">
              <strong>{t("help.tableOfContents")}</strong>
              <nav>
                {helpGroupOrder.map((groupId) => {
                  const sections = content.sections.filter((section) => section.group === groupId);
                  return (
                    <div className="help-toc-group" key={groupId}>
                      <span>{content.groups[groupId].title}</span>
                      {sections.map((section) => (
                        <a href={`#help-${section.id}`} key={section.id}>{section.title}</a>
                      ))}
                    </div>
                  );
                })}
                <div className="help-toc-group">
                  <span>{t("help.referenceLabel")}</span>
                  <a href="#help-faq">{t("help.faqTitle")}</a>
                </div>
              </nav>
            </div>
          </aside>

          <div className="help-content">
            {helpGroupOrder.map((groupId: HelpSectionGroup) => {
              const sections = content.sections.filter((section) => section.group === groupId);
              if (sections.length === 0) return null;
              return (
                <section className="help-content-group" id={`help-group-${groupId}`} key={groupId}>
                  <div className="help-content-group-heading">
                    <h2>{content.groups[groupId].title}</h2>
                    <p>{content.groups[groupId].description}</p>
                  </div>
                  <div className="help-content-group-sections">
                    {sections.map((section) => (
                      <section className="help-content-section" id={`help-${section.id}`} key={section.id}>
                        <div className={section.icon ? "help-section-heading with-icon" : "help-section-heading"}>
                          {section.icon ? <img className="help-section-icon" src={helpSectionIconSources[section.icon]} alt="" aria-hidden="true" /> : null}
                          <h3>{section.title}</h3>
                        </div>
                        <p>{section.summary}</p>
                        {section.steps && section.steps.length > 0 ? (
                          <>
                            <div className="help-steps-label">{t("help.stepsLabel")}</div>
                            <ol className="help-step-list">
                              {section.steps.map((step) => <li key={step}>{step}</li>)}
                            </ol>
                          </>
                        ) : null}
                        {section.tip ? (
                          <aside className="help-tip">
                            <strong>{t("help.tipLabel")}</strong>
                            <p>{section.tip}</p>
                          </aside>
                        ) : null}
                        {section.guideId ? (
                          <div className="help-section-actions">
                            <button className="ghost compact help-guide-link" type="button" onClick={() => requestInteractiveGuide(section.guideId)}>
                              <span aria-hidden="true">◎</span>
                              {t("help.viewOnScreen")}
                            </button>
                          </div>
                        ) : null}
                      </section>
                    ))}
                  </div>
                </section>
              );
            })}
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
