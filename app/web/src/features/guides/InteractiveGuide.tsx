import { useEffect, useRef } from "react";
import { driver, type Driver, type DriveStep, type PopoverDOM } from "driver.js";
import { useI18n } from "../../i18n";
import { resolveGuideRoute, type GuideDefinition, type GuideStepDefinition } from "./guideRegistry";
import { setGuideProgress } from "./guideStorage";

type GuideCloseReason = "completed" | "dismissed" | "stopped";

type InteractiveGuideProps = {
  definition: GuideDefinition | null;
  userId?: string | null;
  currentPath: string;
  onNavigate: (path: string) => void;
  onClose: () => void;
};

function isVisibleElement(element: Element | null | undefined): element is HTMLElement {
  if (!(element instanceof HTMLElement)) return false;
  const style = window.getComputedStyle(element);
  if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
  const rect = element.getBoundingClientRect();
  return rect.width > 0
    && rect.height > 0
    && rect.right > 0
    && rect.bottom > 0
    && rect.left < window.innerWidth
    && rect.top < window.innerHeight;
}

function isRenderedElement(element: Element | null | undefined): element is HTMLElement {
  if (!(element instanceof HTMLElement)) return false;
  const style = window.getComputedStyle(element);
  if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
  const rect = element.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

function findRenderedTarget(step: GuideStepDefinition) {
  return [step.target, ...(step.fallbackTargets ?? [])]
    .map((selector) => document.querySelector<HTMLElement>(selector))
    .find((element) => isRenderedElement(element));
}

function findVisibleTarget(step: GuideStepDefinition) {
  const target = findRenderedTarget(step);
  return isVisibleElement(target) ? target : null;
}

function findPrimaryTarget(step: GuideStepDefinition) {
  return isVisibleElement(document.querySelector<HTMLElement>(step.target));
}

function waitForTarget(step: GuideStepDefinition, timeoutMs: number) {
  return new Promise<HTMLElement | null>((resolve) => {
    let settled = false;
    const observer = new MutationObserver(check);
    const intervalId = window.setInterval(check, 80);
    const timeoutId = window.setTimeout(() => finish(null), timeoutMs);

    function finish(target: HTMLElement | null) {
      if (settled) return;
      settled = true;
      observer.disconnect();
      window.clearInterval(intervalId);
      window.clearTimeout(timeoutId);
      window.removeEventListener("resize", check);
      resolve(target);
    }

    function check() {
      let target = findVisibleTarget(step);
      if (!target) {
        const renderedTarget = findRenderedTarget(step);
        if (renderedTarget) {
          renderedTarget.scrollIntoView({ block: "center", inline: "nearest", behavior: "auto" });
          target = findVisibleTarget(step);
        }
      }
      if (target) finish(target);
    }

    if (document.body) observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class", "style", "aria-expanded"] });
    window.addEventListener("resize", check);
    check();
  });
}

function waitForPath(pathname: string, timeoutMs: number) {
  return new Promise<boolean>((resolve) => {
    let settled = false;
    const intervalId = window.setInterval(check, 80);
    const timeoutId = window.setTimeout(() => finish(false), timeoutMs);

    function finish(matched: boolean) {
      if (settled) return;
      settled = true;
      window.clearInterval(intervalId);
      window.clearTimeout(timeoutId);
      window.removeEventListener("popstate", check);
      resolve(matched);
    }

    function check() {
      if (window.location.pathname === pathname) finish(true);
    }

    window.addEventListener("popstate", check);
    check();
  });
}

function appendKikoBadge(popover: PopoverDOM) {
  if (popover.wrapper.querySelector(".kikiori-driver-popover-kiko")) return;
  const badge = document.createElement("span");
  badge.className = "kikiori-driver-popover-kiko";
  badge.setAttribute("aria-hidden", "true");
  const image = document.createElement("img");
  image.src = "/images/kiko-waiting.svg";
  image.alt = "";
  badge.append(image);
  popover.wrapper.insertBefore(badge, popover.title);
}

export function InteractiveGuide({ definition, userId, currentPath, onNavigate, onClose }: InteractiveGuideProps) {
  const { t } = useI18n();
  const translateRef = useRef(t);
  const onNavigateRef = useRef(onNavigate);
  const onCloseRef = useRef(onClose);
  translateRef.current = t;
  onNavigateRef.current = onNavigate;
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!definition) return;

    const guideId = definition.id;
    const translate = (key: string, values?: Record<string, string | number>) => translateRef.current(key, values);
    let activeDriver: Driver | null = null;
    let isCancelled = false;
    let isClosing = false;
    let isMoving = false;
    let openedNavigationDrawer = false;
    let openedKnowledgeDrawer = false;
    let placeholder: HTMLElement | null = null;
    let guideContextPath = window.location.pathname;
    let steps: GuideStepDefinition[] = [...definition.steps];
    let dynamicStepsExpanded = false;

    setGuideProgress(userId, guideId, "in_progress");

    async function prepareStep(step: GuideStepDefinition) {
      if (step.route) {
        // Keep the route context in sync with user actions performed while the
        // guide is paused (for example, creating a knowledge or opening a
        // record). This also lets one definition continue across route changes.
        if (window.location.pathname.includes("/knowledges/")) {
          guideContextPath = window.location.pathname;
        }
        if (!guideContextPath.includes("/knowledges/")) {
          const knowledgePath = document.querySelector<HTMLElement>("[data-guide=\"knowledge-item\"]")?.dataset.knowledgePath;
          if (knowledgePath) guideContextPath = knowledgePath;
        }
        if (step.route === "knowledge-record-detail" && !/\/records\/[^/]+$/.test(guideContextPath)) {
          const recordPath = document.querySelector<HTMLElement>("[data-guide=\"record-item\"]")?.dataset.recordPath;
          if (recordPath) guideContextPath = recordPath;
        }
        const targetPath = resolveGuideRoute(step.route, guideContextPath);
        if (targetPath && window.location.pathname !== targetPath) {
          onNavigateRef.current(targetPath);
          const routeReady = await waitForPath(targetPath, 3000);
          if (!routeReady) return null;
        }
      }

      if (
        step.openNavigationDrawer
        && !findPrimaryTarget(step)
        && window.matchMedia("(max-width: 1199px)").matches
      ) {
        const trigger = document.querySelector<HTMLButtonElement>('[data-guide="navigation-trigger"]');
        const navigation = document.querySelector<HTMLElement>('[data-guide="navigation"]');
        if (trigger && navigation && !navigation.classList.contains("responsive-open")) {
          trigger.click();
          trigger.setAttribute("data-guide-opened", "true");
          openedNavigationDrawer = true;
        }
      }

      if (
        step.openKnowledgeDrawer
        && !findPrimaryTarget(step)
        && window.matchMedia("(max-width: 900px)").matches
      ) {
        const toggle = document.querySelector<HTMLButtonElement>('[data-guide="knowledge-toggle"]');
        if (toggle && toggle.getAttribute("aria-expanded") !== "true") {
          toggle.click();
          toggle.setAttribute("data-guide-opened", "true");
          openedKnowledgeDrawer = true;
        }
      }

      if (step.activateSelector) {
        const activationControl = document.querySelector<HTMLElement>(step.activateSelector);
        if (activationControl && activationControl.getAttribute("aria-selected") !== "true") {
          activationControl.click();
        }
      }

      const targetStep = openedKnowledgeDrawer ? { ...step, fallbackTargets: [] } : step;
      return waitForTarget(targetStep, step.timeoutMs ?? (step.required ? 5000 : 1200));
    }

    function closeOpenedKnowledgeDrawer() {
      const toggle = document.querySelector<HTMLButtonElement>('[data-guide="knowledge-toggle"]');
      const pane = document.querySelector<HTMLElement>('[data-guide="knowledge-pane"]');
      const wasOpenedByGuide = openedKnowledgeDrawer || toggle?.getAttribute("data-guide-opened") === "true";
      if (!wasOpenedByGuide) return;
      if (toggle && (toggle.getAttribute("aria-expanded") === "true" || pane?.classList.contains("knowledge-panel-open"))) {
        toggle.click();
      }
      toggle?.removeAttribute("data-guide-opened");
      openedKnowledgeDrawer = false;
    }

    function closeOpenedNavigationDrawer() {
      const trigger = document.querySelector<HTMLButtonElement>('[data-guide="navigation-trigger"]');
      const navigation = document.querySelector<HTMLElement>('[data-guide="navigation"]');
      const wasOpenedByGuide = openedNavigationDrawer || trigger?.getAttribute("data-guide-opened") === "true";
      if (!wasOpenedByGuide) return;
      if (trigger && navigation?.classList.contains("responsive-open")) {
        document.querySelector<HTMLButtonElement>('[data-guide="navigation-backdrop"]')?.click();
      }
      trigger?.removeAttribute("data-guide-opened");
      openedNavigationDrawer = false;
    }

    function restoreApplicationAccessibility() {
      const knowledgeToggle = document.querySelector<HTMLButtonElement>('[data-guide="knowledge-toggle"]');
      const knowledgePane = document.querySelector<HTMLElement>('[data-guide="knowledge-pane"]');
      if (knowledgeToggle && knowledgePane) {
        knowledgeToggle.setAttribute("aria-controls", "interview-context-panel");
        knowledgeToggle.setAttribute("aria-expanded", knowledgePane.classList.contains("knowledge-panel-open") ? "true" : "false");
      }

      const navigationTrigger = document.querySelector<HTMLButtonElement>('[data-guide="navigation-trigger"]');
      const navigation = document.querySelector<HTMLElement>('[data-guide="navigation"]');
      if (navigationTrigger && navigation) {
        navigationTrigger.setAttribute("aria-controls", "workspace-navigation");
        navigationTrigger.setAttribute("aria-expanded", navigation.classList.contains("responsive-open") ? "true" : "false");
      }
    }

    function getGuidePlaceholder() {
      if (placeholder) return placeholder;
      placeholder = document.createElement("span");
      placeholder.id = "kikiori-guide-placeholder";
      placeholder.setAttribute("aria-hidden", "true");
      placeholder.style.position = "fixed";
      placeholder.style.top = "50%";
      placeholder.style.left = "50%";
      placeholder.style.width = "1px";
      placeholder.style.height = "1px";
      placeholder.style.opacity = "0";
      placeholder.style.pointerEvents = "none";
      document.body.append(placeholder);
      return placeholder;
    }

    function createDriverSteps(): DriveStep[] {
      return steps.map((step) => ({
        // Route transitions and responsive drawers can make a target appear after
        // the driver is created. The engine resolves the real target in
        // prepareStep; the placeholder keeps Driver.js from dropping future steps.
        element: () => findVisibleTarget(step) ?? getGuidePlaceholder(),
        popover: {
          title: translate(step.titleKey),
          description: translate(step.descriptionKey),
          side: step.placement ?? "bottom",
          align: "start",
        },
      }));
    }

    function expandDynamicSteps(instance?: Driver) {
      if (dynamicStepsExpanded) return 0;

      const expandedSteps: GuideStepDefinition[] = [];
      let expandedCount = 0;
      for (const step of steps) {
        if (!step.repeatTarget) {
          expandedSteps.push(step);
          continue;
        }

        const targets = [...document.querySelectorAll<HTMLElement>(step.repeatTarget)]
          .filter((element) => isRenderedElement(element))
          .slice(0, step.maxRepeats ?? Number.POSITIVE_INFINITY);
        if (targets.length === 0) {
          expandedSteps.push(step);
          continue;
        }

        expandedCount = targets.length;
        for (const [index, target] of targets.entries()) {
          const targetIndex = target.dataset.guideIndex ?? String(index);
          expandedSteps.push({
            ...step,
            id: `${step.id}-${targetIndex}`,
            target: `${step.target}[data-guide-index="${targetIndex}"]`,
            fallbackTargets: [],
            repeatTarget: undefined,
          });
        }
      }

      if (expandedCount === 0) return 0;
      dynamicStepsExpanded = true;
      steps = expandedSteps;
      // `setSteps` resets Driver.js state while a tour is active. Update only
      // the configuration so the current popover and keyboard focus survive
      // the dynamic expansion.
      if (instance) instance.setConfig({ ...instance.getConfig(), steps: createDriverSteps() });
      return expandedCount;
    }

    function isActionComplete(step: GuideStepDefinition) {
      return Boolean(step.completionTarget && isRenderedElement(document.querySelector(step.completionTarget)));
    }

    function promptForAction(step: GuideStepDefinition, instance: Driver) {
      const popover = instance.getState("popover") as PopoverDOM | undefined;
      if (!popover) return;
      popover.description.textContent = `${translate(step.descriptionKey)} ${translate("guide.completeActionToContinue")}`;
      popover.nextButton.textContent = translate("guide.continueAfterAction");
    }

    function notifyClose(reason: GuideCloseReason) {
      if (isClosing) return;
      isClosing = true;
      setGuideProgress(userId, guideId, reason === "completed" ? "completed" : "dismissed");
      if (activeDriver?.isActive()) {
        activeDriver.destroy();
      }
      // Driver.js captures clicks while its overlay is active. Close drawers
      // after tearing the overlay down so the application's own controls
      // receive the click and the layout returns to its initial state.
      closeOpenedNavigationDrawer();
      closeOpenedKnowledgeDrawer();
      restoreApplicationAccessibility();
      window.requestAnimationFrame(restoreApplicationAccessibility);
      onCloseRef.current();
    }

    function stopForMissingTarget(step: GuideStepDefinition) {
      console.warn(`[guide:${guideId}] target not found: ${step.target}`);
      notifyClose("stopped");
    }

    async function moveToAvailableStep(index: number, instance: Driver, direction: 1 | -1 = 1) {
      if (isCancelled || isClosing || isMoving) return;
      isMoving = true;
      let nextIndex = index;
      let target: HTMLElement | null = null;

      while (nextIndex >= 0 && nextIndex < steps.length) {
        const step = steps[nextIndex];
        target = await prepareStep(step);
        if (isCancelled || isClosing) {
          isMoving = false;
          return;
        }
        if (target && step.repeatTarget) {
          const expandedCount = expandDynamicSteps(instance);
          if (expandedCount > 0) {
            nextIndex = direction === 1 ? nextIndex : nextIndex + expandedCount - 1;
            target = await prepareStep(steps[nextIndex]);
          }
        }
        if (target) break;
        if (step.required) {
          stopForMissingTarget(step);
          isMoving = false;
          return;
        }
        nextIndex += 1;
        if (direction === -1) nextIndex -= 2;
      }

      if (direction === 1 && nextIndex >= steps.length) {
        notifyClose("completed");
      } else if (target) {
        instance.moveTo(nextIndex);
      }
      isMoving = false;
    }

    function moveForward(instance: Driver) {
      const index = instance.getActiveIndex() ?? -1;
      const step = steps[index];
      if (!step) return;
      if (step.kind === "action") {
        if (!isActionComplete(step)) {
          promptForAction(step, instance);
          return;
        }
        if (step.completeAfterAction || index >= steps.length - 1) {
          notifyClose("completed");
          return;
        }
      }
      void moveToAvailableStep(index + 1, instance, 1);
    }

    async function start() {
      expandDynamicSteps();
      let firstIndex = 0;
      while (firstIndex < steps.length) {
        const target = await prepareStep(steps[firstIndex]);
        if (isCancelled) return;
        if (target) break;
        if (steps[firstIndex].required) {
          stopForMissingTarget(steps[firstIndex]);
          return;
        }
        firstIndex += 1;
      }
      if (firstIndex >= steps.length) {
        notifyClose("completed");
        return;
      }

      activeDriver = driver({
        steps: createDriverSteps(),
        animate: true,
        duration: 180,
        overlayColor: "#0f232e",
        overlayOpacity: 0.5,
        stagePadding: 7,
        stageRadius: 12,
        disableActiveInteraction: false,
        allowClose: true,
        allowScroll: true,
        allowKeyboardControl: true,
        showProgress: true,
        popoverClass: "kikiori-driver-popover",
        nextBtnText: translate("guide.next"),
        prevBtnText: translate("guide.previous"),
        doneBtnText: translate("guide.finish"),
        onPopoverRender: (popover, options) => {
          appendKikoBadge(popover);
          if (typeof options.index === "number") {
            popover.progress.textContent = translate("guide.progress", { current: options.index + 1, total: steps.length });
            if (steps[options.index]?.kind === "action") {
              popover.nextButton.textContent = translate("guide.continueAfterAction");
            }
          }
          popover.closeButton.setAttribute("aria-label", translate("guide.close"));
        },
        onNextClick: (_element, _step, options) => {
          moveForward(options.driver);
        },
        onPrevClick: (_element, _step, options) => {
          void moveToAvailableStep((options.driver.getActiveIndex() ?? 1) - 1, options.driver, -1);
        },
        onCloseClick: () => notifyClose("dismissed"),
        onDoneClick: (_element, _step, options) => moveForward(options.driver),
        onDestroyed: () => {
          restoreApplicationAccessibility();
          if (!isClosing) notifyClose("dismissed");
        },
      });

      if (!isCancelled) activeDriver.drive(firstIndex);
    }

    void start();
    return () => {
      isCancelled = true;
      if (activeDriver?.isActive()) activeDriver.destroy();
      closeOpenedNavigationDrawer();
      closeOpenedKnowledgeDrawer();
      placeholder?.remove();
    };
  }, [definition, userId]);

  // currentPath is intentionally kept as a prop so the host re-renders when a guide changes route.
  // Route/DOM readiness is observed by prepareStep rather than by a device-specific branch.
  void currentPath;
  return null;
}
