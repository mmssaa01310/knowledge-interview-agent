import type { Knowledge } from "@ai-interviewer/shared-types";
import { useEffect, useState } from "react";
import { WorkspaceNav } from "./WorkspaceNav";
import type { UserProfile } from "../lib/api";
import type { AppSection } from "../types/app";
import { useI18n } from "../i18n";

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
};

export function AppShell({
  activeSection,
  activePath,
  user,
  knowledges,
  selectedKnowledgeId,
  children,
  onNavigate,
  onOpenCreateKnowledge,
  isPreparingKnowledgeCreation,
  knowledgeCreationError
}: AppShellProps) {
  const { t } = useI18n();
  const [isWorkspaceNavCollapsed, setIsWorkspaceNavCollapsed] = useState(false);
  const [isWorkspaceNavOpen, setIsWorkspaceNavOpen] = useState(false);

  useEffect(() => {
    document.body.classList.toggle("nav-drawer-open", isWorkspaceNavOpen);
    return () => document.body.classList.remove("nav-drawer-open");
  }, [isWorkspaceNavOpen]);

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

  return (
    <div className={isWorkspaceNavCollapsed ? "app-shell sidebar-collapsed" : "app-shell"}>
      <header className="app-mobile-header">
        <img className="app-mobile-brand" src="/images/kikiori-logo.svg" alt={t("common.appName")} />
        <button
          type="button"
          className="app-mobile-nav-trigger"
          onClick={() => setIsWorkspaceNavOpen(true)}
          aria-label={t("navigation.navOpen")}
          aria-controls="workspace-navigation"
          aria-expanded={isWorkspaceNavOpen}
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
      />
      <main className="main-content" data-active-path={activePath}>{children}</main>
    </div>
  );
}
