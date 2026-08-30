import { useEffect, useRef } from "react";
import { useI18n } from "../../i18n";
import { getGuideDefinitions, getRecommendedGuideDefinitions, type GuideId } from "./guideRegistry";
import { getGuideProgress } from "./guideStorage";

type GuideSelectorDialogProps = {
  isOpen: boolean;
  userId?: string | null;
  userRole?: string | null;
  currentPath: string;
  onClose: () => void;
  onSelect: (guideId: GuideId) => void;
};

export function GuideSelectorDialog({ isOpen, userId, userRole, currentPath, onClose, onSelect }: GuideSelectorDialogProps) {
  const { t } = useI18n();
  const firstOptionRef = useRef<HTMLButtonElement | null>(null);
  const recordStatus = document.querySelector<HTMLElement>('[data-guide="interview-pane"]')?.dataset.guideRecordStatus;
  const availableGuides = getGuideDefinitions(userRole ?? undefined);
  const recommendedGuides = getRecommendedGuideDefinitions(userRole ?? undefined, { currentPath, recordStatus });
  const otherGuides = availableGuides.filter((definition) => !recommendedGuides.some((recommended) => recommended.id === definition.id));

  useEffect(() => {
    if (!isOpen) return;
    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKeyDown);
    window.requestAnimationFrame(() => firstOptionRef.current?.focus());
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div className="dialog-backdrop guide-selector-backdrop" role="presentation">
      <div className="dialog-panel guide-selector-dialog" role="dialog" aria-modal="true" aria-labelledby="guide-selector-title">
        <div className="dialog-header">
          <div>
            <p className="eyebrow">KIKIORI</p>
            <h2 id="guide-selector-title">{t("guide.selector.title")}</h2>
            <p>{t("guide.selector.description")}</p>
          </div>
          <button type="button" className="ghost compact" onClick={onClose} aria-label={t("guide.close")}>
            ×
          </button>
        </div>
        {recommendedGuides.length > 0 ? (
          <section className="guide-selector-section" aria-labelledby="guide-selector-recommended-title">
            <h3 id="guide-selector-recommended-title">{t("guide.selector.recommended")}</h3>
            <p>{t("guide.selector.recommendedDescription")}</p>
            <div className="guide-selector-list">
              {recommendedGuides.map((definition, index) => {
                const progress = getGuideProgress(userId, definition.id);
                return (
                  <button
                    type="button"
                    className="guide-selector-option recommended"
                    key={definition.id}
                    data-guide-option={definition.id}
                    ref={index === 0 ? firstOptionRef : undefined}
                    onClick={() => onSelect(definition.id)}
                  >
                    <span className="guide-selector-option-copy">
                      <strong>{t(definition.titleKey)}</strong>
                      <span>{t(definition.descriptionKey)}</span>
                    </span>
                    <span className={`guide-selector-status ${progress.status}`}>
                      {t(`guide.status.${progress.status}`)}
                    </span>
                  </button>
                );
              })}
            </div>
          </section>
        ) : null}
        <section className="guide-selector-section" aria-labelledby="guide-selector-all-title">
          <h3 id="guide-selector-all-title">{t("guide.selector.allGuides")}</h3>
          <div className="guide-selector-list">
          {otherGuides.map((definition, index) => {
            const progress = getGuideProgress(userId, definition.id);
            return (
              <button
                type="button"
                className="guide-selector-option"
                key={definition.id}
                data-guide-option={definition.id}
                ref={recommendedGuides.length === 0 && index === 0 ? firstOptionRef : undefined}
                onClick={() => onSelect(definition.id)}
              >
                <span className="guide-selector-option-copy">
                  <strong>{t(definition.titleKey)}</strong>
                  <span>{t(definition.descriptionKey)}</span>
                </span>
                <span className={`guide-selector-status ${progress.status}`}>
                  {t(`guide.status.${progress.status}`)}
                </span>
              </button>
            );
          })}
          </div>
        </section>
        <p className="guide-selector-note">{t("guide.selector.manualNotice")}</p>
      </div>
    </div>
  );
}
