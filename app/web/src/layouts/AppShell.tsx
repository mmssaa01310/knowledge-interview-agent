import type { Knowledge } from "@ai-interviewer/shared-types";
import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react";
import { WorkspaceNav } from "./WorkspaceNav";
import type { UserProfile } from "../lib/api";
import type { AppSection } from "../types/app";
import { useI18n } from "../i18n";
import { GuideProvider, useGuide } from "../features/guides/GuideProvider";
import { ThemeLogo } from "../components/ui/ThemeLogo";

const SIDEBAR_WIDTH_STORAGE_KEY = "ai-interviewer.sidebar-width";
const SIDEBAR_DEFAULT_WIDTH = 252;
const SIDEBAR_MIN_WIDTH = 220;
const SIDEBAR_MAX_WIDTH = 420;

function clampSidebarWidth(width: number) {
  return Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, Math.round(width)));
}

function getStoredSidebarWidth() {
  try {
    const storedWidth = window.localStorage.getItem(SIDEBAR_WIDTH_STORAGE_KEY);
    if (storedWidth === null) return SIDEBAR_DEFAULT_WIDTH;
    const parsedWidth = Number(storedWidth);
    return Number.isFinite(parsedWidth) ? clampSidebarWidth(parsedWidth) : SIDEBAR_DEFAULT_WIDTH;
  } catch {
    return SIDEBAR_DEFAULT_WIDTH;
  }
}

type AppShellProps = {
  activeSection: AppSection;
  activePath: string;
  user: UserProfile | null;
  knowledges: Knowledge[];
  selectedKnowledgeId?: string | null;
  children: React.ReactNode;
  onNavigate: (path: string) => void;
  onOpenCreateKnowledge: () => void;
  isPreparingKnowledgeCreation?: boolean;
  knowledgeCreationError?: string;
  onLogout: () => void;
};

export function AppShell(props: AppShellProps) {
  return (
    <GuideProvider userId={props.user?.userId} userRole={props.user?.role} currentPath={props.activePath} onNavigate={props.onNavigate}>
      <AppShellContent {...props} />
    </GuideProvider>
  );
}

function AppShellContent({
  activeSection,
  activePath,
  user,
  knowledges,
  selectedKnowledgeId,
  children,
  onNavigate,
  onOpenCreateKnowledge,
  isPreparingKnowledgeCreation,
  knowledgeCreationError,
  onLogout,
}: AppShellProps) {
  const { t } = useI18n();
  const { openGuideSelector } = useGuide();
  const [isWorkspaceNavCollapsed, setIsWorkspaceNavCollapsed] = useState(false);
  const [isWorkspaceNavOpen, setIsWorkspaceNavOpen] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(getStoredSidebarWidth);
  const [isSidebarResizing, setIsSidebarResizing] = useState(false);
  const appShellRef = useRef<HTMLDivElement | null>(null);
  const sidebarResizeRef = useRef<{ startX: number; startWidth: number; currentWidth: number } | null>(null);
  const resizeFrameRef = useRef<number | null>(null);
  const isInterviewRecordView = /\/records\/[^/]+\/?$/.test(activePath);

  useEffect(() => {
    try {
      window.localStorage.setItem(SIDEBAR_WIDTH_STORAGE_KEY, String(sidebarWidth));
    } catch {
      // localStorageが利用できない環境でもサイドバーの操作は継続する。
    }
  }, [sidebarWidth]);

  useEffect(() => {
    if (!isSidebarResizing) return;

    function handlePointerMove(event: globalThis.PointerEvent) {
      const resizeState = sidebarResizeRef.current;
      if (!resizeState) return;
      resizeState.currentWidth = clampSidebarWidth(resizeState.startWidth + event.clientX - resizeState.startX);
      if (resizeFrameRef.current !== null) return;
      resizeFrameRef.current = window.requestAnimationFrame(() => {
        resizeFrameRef.current = null;
        const pendingResize = sidebarResizeRef.current;
        if (!pendingResize) return;
        appShellRef.current?.style.setProperty("--workspace-sidebar-width", `${pendingResize.currentWidth}px`);
      });
    }

    function stopResizing() {
      const resizeState = sidebarResizeRef.current;
      if (resizeFrameRef.current !== null) {
        window.cancelAnimationFrame(resizeFrameRef.current);
        resizeFrameRef.current = null;
      }
      if (resizeState) {
        appShellRef.current?.style.setProperty("--workspace-sidebar-width", `${resizeState.currentWidth}px`);
        setSidebarWidth(resizeState.currentWidth);
      }
      sidebarResizeRef.current = null;
      setIsSidebarResizing(false);
    }

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", stopResizing);
    window.addEventListener("pointercancel", stopResizing);
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", stopResizing);
      window.removeEventListener("pointercancel", stopResizing);
      if (resizeFrameRef.current !== null) {
        window.cancelAnimationFrame(resizeFrameRef.current);
        resizeFrameRef.current = null;
      }
    };
  }, [isSidebarResizing]);

  useEffect(() => {
    document.body.classList.toggle("nav-drawer-open", isWorkspaceNavOpen);
    return () => document.body.classList.remove("nav-drawer-open");
  }, [isWorkspaceNavOpen]);

  useEffect(() => {
    document.body.classList.toggle("interview-record-active", isInterviewRecordView);
    return () => document.body.classList.remove("interview-record-active");
  }, [isInterviewRecordView]);

  function handleNavigate(path: string) {
    setIsWorkspaceNavOpen(false);
    onNavigate(path);
  }

  function handleOpenCreateKnowledge() {
    setIsWorkspaceNavOpen(false);
    onOpenCreateKnowledge();
  }

  function handleWorkspaceNavToggle() {
    if (isWorkspaceNavOpen) {
      setIsWorkspaceNavOpen(false);
      return;
    }
    setIsWorkspaceNavCollapsed((value) => !value);
  }

  function handleSidebarResizeStart(event: ReactPointerEvent<HTMLDivElement>) {
    if (isWorkspaceNavCollapsed || window.matchMedia("(max-width: 1199px)").matches) return;
    event.preventDefault();
    sidebarResizeRef.current = {
      startX: event.clientX,
      startWidth: sidebarWidth,
      currentWidth: sidebarWidth,
    };
    setIsSidebarResizing(true);
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function handleSidebarResizeKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (window.matchMedia("(max-width: 1199px)").matches) return;

    const resizeStep = event.shiftKey ? 40 : 10;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      setSidebarWidth((width) => clampSidebarWidth(width - resizeStep));
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      setSidebarWidth((width) => clampSidebarWidth(width + resizeStep));
    } else if (event.key === "Home") {
      event.preventDefault();
      setSidebarWidth(SIDEBAR_MIN_WIDTH);
    } else if (event.key === "End") {
      event.preventDefault();
      setSidebarWidth(SIDEBAR_MAX_WIDTH);
    }
  }

  function handleStartGuide() {
    setIsWorkspaceNavOpen(false);
    openGuideSelector();
  }

  function handleLogout() {
    setIsWorkspaceNavOpen(false);
    onLogout();
  }

  return (
    <div
      ref={appShellRef}
      className={`app-shell${isWorkspaceNavCollapsed ? " sidebar-collapsed" : ""}${isInterviewRecordView ? " interview-record-shell" : ""}${isSidebarResizing ? " sidebar-resizing" : ""}`}
      style={{ "--workspace-sidebar-width": `${sidebarWidth}px` } as React.CSSProperties}
    >
      <header className="app-mobile-header">
        <ThemeLogo className="app-mobile-brand" alt={t("common.appName")} />
        <button
          type="button"
          className="app-mobile-nav-trigger"
          onClick={() => setIsWorkspaceNavOpen(true)}
          aria-label={t("navigation.navOpen")}
          aria-controls="workspace-navigation"
          aria-expanded={isWorkspaceNavOpen}
          data-guide="navigation-trigger"
        >
          <span className="mobile-nav-trigger-icon" aria-hidden="true" />
        </button>
      </header>
      {isWorkspaceNavOpen ? (
        <button
          type="button"
          className="app-nav-backdrop"
          onClick={() => setIsWorkspaceNavOpen(false)}
          aria-label={t("navigation.navClose")}
          data-guide="navigation-backdrop"
        />
      ) : null}
      <WorkspaceNav
        id="workspace-navigation"
        activeSection={activeSection}
        user={user}
        knowledges={knowledges}
        selectedKnowledgeId={selectedKnowledgeId}
        onNavigate={handleNavigate}
        onOpenCreateKnowledge={handleOpenCreateKnowledge}
        isPreparingKnowledgeCreation={isPreparingKnowledgeCreation}
        knowledgeCreationError={knowledgeCreationError}
        isCollapsed={isWorkspaceNavCollapsed}
        isResponsiveOpen={isWorkspaceNavOpen}
        onToggleCollapsed={handleWorkspaceNavToggle}
        sidebarWidth={sidebarWidth}
        onSidebarResizeStart={handleSidebarResizeStart}
        onSidebarResizeKeyDown={handleSidebarResizeKeyDown}
        onStartGuide={handleStartGuide}
        onLogout={handleLogout}
      />
      <main className={`main-content${isInterviewRecordView ? " interview-record-main-content" : ""}`} data-active-path={activePath}>{children}</main>
    </div>
  );
}
