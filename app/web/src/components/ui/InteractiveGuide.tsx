import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useI18n } from "../../i18n";

type GuideStep = {
  id: string;
  title: string;
  description: string;
  selectors: readonly string[];
};

type InteractiveGuideProps = {
  isOpen: boolean;
  onClose: () => void;
};

type GuideRect = {
  top: number;
  left: number;
  width: number;
  height: number;
};

function isVisibleTarget(element: HTMLElement) {
  const style = window.getComputedStyle(element);
  if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
  const rect = element.getBoundingClientRect();
  return rect.width > 0
    && rect.height > 0
    && rect.bottom > 0
    && rect.right > 0
    && rect.top < window.innerHeight
    && rect.left < window.innerWidth;
}

export function InteractiveGuide({ isOpen, onClose }: InteractiveGuideProps) {
  const { t } = useI18n();
  const [stepIndex, setStepIndex] = useState(0);
  const [targetRect, setTargetRect] = useState<GuideRect | null>(null);
  const nextButtonRef = useRef<HTMLButtonElement | null>(null);
  const dialogTitleId = "kikiori-interactive-guide-title";
  const steps = useMemo<GuideStep[]>(() => [
    {
      id: "navigation",
      title: t("guide.steps.navigation.title"),
      description: t("guide.steps.navigation.description"),
      selectors: ["#workspace-navigation"],
    },
    {
      id: "interview",
      title: t("guide.steps.interview.title"),
      description: t("guide.steps.interview.description"),
      selectors: ["[data-guide=interview-pane]"],
    },
    {
      id: "knowledge",
      title: t("guide.steps.knowledge.title"),
      description: t("guide.steps.knowledge.description"),
      selectors: ["[data-guide=knowledge-pane]", "[data-guide=knowledge-toggle]"],
    },
    {
      id: "composer",
      title: t("guide.steps.composer.title"),
      description: t("guide.steps.composer.description"),
      selectors: ["[data-guide=message-composer]"],
    },
  ], [t]);
  const currentStep = steps[Math.min(stepIndex, steps.length - 1)];

  useEffect(() => {
    if (isOpen) setStepIndex(0);
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;

    function updateTarget() {
      const target = currentStep?.selectors
        .map((selector) => document.querySelector<HTMLElement>(selector))
        .find((element) => element && isVisibleTarget(element));
      if (!target) {
        setTargetRect(null);
        return;
      }
      const rect = target.getBoundingClientRect();
      setTargetRect({ top: rect.top, left: rect.left, width: rect.width, height: rect.height });
    }

    const frameId = window.requestAnimationFrame(updateTarget);
    const transitionTimeoutId = window.setTimeout(updateTarget, 240);
    window.addEventListener("resize", updateTarget);
    window.addEventListener("scroll", updateTarget, true);
    return () => {
      window.cancelAnimationFrame(frameId);
      window.clearTimeout(transitionTimeoutId);
      window.removeEventListener("resize", updateTarget);
      window.removeEventListener("scroll", updateTarget, true);
    };
  }, [currentStep, isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(() => nextButtonRef.current?.focus());
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [isOpen, stepIndex]);

  useEffect(() => {
    if (!isOpen) return;

    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen || !currentStep) return null;

  const isLastStep = stepIndex === steps.length - 1;
  const spotlightStyle = targetRect ? {
    top: Math.max(4, targetRect.top - 8),
    left: Math.max(4, targetRect.left - 8),
    width: Math.min(targetRect.width + 16, window.innerWidth - Math.max(4, targetRect.left - 8) - 4),
    height: targetRect.height + 16,
  } : undefined;

  return createPortal(
    <div className="interactive-guide" role="dialog" aria-modal="true" aria-labelledby={dialogTitleId}>
      <button type="button" className="interactive-guide-backdrop" onClick={onClose} aria-label={t("guide.close")} />
      {spotlightStyle ? <div className="interactive-guide-spotlight" style={spotlightStyle} aria-hidden="true" /> : null}
      <section className="interactive-guide-card" role="document">
        <div className="interactive-guide-card-header">
          <span className="interactive-guide-progress">{t("guide.progress", { current: stepIndex + 1, total: steps.length })}</span>
          <button type="button" className="interactive-guide-close" onClick={onClose} aria-label={t("guide.close")}>×</button>
        </div>
        <h2 id={dialogTitleId}>{currentStep.title}</h2>
        <p>{currentStep.description}</p>
        <div className="interactive-guide-actions">
          <button
            type="button"
            className="ghost compact"
            onClick={() => setStepIndex((value) => Math.max(0, value - 1))}
            disabled={stepIndex === 0}
          >
            {t("guide.previous")}
          </button>
          <button
            ref={nextButtonRef}
            type="button"
            className="primary compact"
            onClick={() => (isLastStep ? onClose() : setStepIndex((value) => Math.min(steps.length - 1, value + 1)))}
          >
            {isLastStep ? t("guide.finish") : t("guide.next")}
          </button>
        </div>
      </section>
    </div>,
    document.body,
  );
}
