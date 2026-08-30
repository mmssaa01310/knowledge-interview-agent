import { useEffect, useRef } from "react";
import { useI18n } from "../../i18n";
import { getGuideDefinitions, type GuideId } from "./guideRegistry";
import { getGuideProgress } from "./guideStorage";

type GuideSelectorDialogProps = {
  isOpen: boolean;
  userId?: string | null;
  userRole?: string | null;
  onClose: () => void;
  onSelect: (guideId: GuideId) => void;
};

export function GuideSelectorDialog({ isOpen, userId, userRole, onClose, onSelect }: GuideSelectorDialogProps) {
  const { t } = useI18n();
  const firstOptionRef = useRef<HTMLButtonElement | null>(null);

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
        <div className="guide-selector-list">
          {getGuideDefinitions(userRole ?? undefined).map((definition, index) => {
            const progress = getGuideProgress(userId, definition.id);
            return (
              <button
                type="button"
                className="guide-selector-option"
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
        <p className="guide-selector-note">{t("guide.selector.manualNotice")}</p>
      </div>
    </div>
  );
}
