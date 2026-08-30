import type { Knowledge } from "@ai-interviewer/shared-types";
import { useEffect, useState } from "react";
import { WorkspaceNav } from "./WorkspaceNav";
import type { UserProfile } from "../lib/api";
import type { AppSection } from "../types/app";
import { useI18n } from "../i18n";
import { GuideProvider, useGuide } from "../features/guides/GuideProvider";

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
  const isInterviewRecordView = /\/records\/[^/]+\/?$/.test(activePath);

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

  function handleStartGuide() {
    setIsWorkspaceNavOpen(false);
    openGuideSelector();
  }

  function handleLogout() {
    setIsWorkspaceNavOpen(false);
    onLogout();
  }

  return (
    <div className={`app-shell${isWorkspaceNavCollapsed ? " sidebar-collapsed" : ""}${isInterviewRecordView ? " interview-record-shell" : ""}`}>
      <header className="app-mobile-header">
        <img className="app-mobile-brand" src="/images/kikiori-logo.svg" alt={t("common.appName")} />
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
        onStartGuide={handleStartGuide}
        onLogout={handleLogout}
      />
      <main className={`main-content${isInterviewRecordView ? " interview-record-main-content" : ""}`} data-active-path={activePath}>{children}</main>
    </div>
  );
}
