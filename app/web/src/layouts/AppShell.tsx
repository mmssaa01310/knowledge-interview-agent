import type { Knowledge } from "@ai-interviewer/shared-types";
import { useState } from "react";
import { WorkspaceNav } from "./WorkspaceNav";
import type { UserProfile } from "../lib/api";
import type { AppSection } from "../types/app";

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
  const [isWorkspaceNavCollapsed, setIsWorkspaceNavCollapsed] = useState(false);

  return (
    <div className={isWorkspaceNavCollapsed ? "app-shell sidebar-collapsed" : "app-shell"}>
      <WorkspaceNav
        activeSection={activeSection}
        user={user}
        knowledges={knowledges}
        selectedKnowledgeId={selectedKnowledgeId}
        onNavigate={onNavigate}
        onOpenCreateKnowledge={onOpenCreateKnowledge}
        isPreparingKnowledgeCreation={isPreparingKnowledgeCreation}
        knowledgeCreationError={knowledgeCreationError}
        isCollapsed={isWorkspaceNavCollapsed}
        onToggleCollapsed={() => setIsWorkspaceNavCollapsed((value) => !value)}
      />
      <main className="main-content" data-active-path={activePath}>{children}</main>
    </div>
  );
}
