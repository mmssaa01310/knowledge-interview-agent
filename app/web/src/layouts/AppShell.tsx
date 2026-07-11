import type { KnowledgeDb } from "@ai-interviewer/shared-types";
import { useState } from "react";
import { GlobalNav } from "./GlobalNav";
import { WorkspaceNav } from "./WorkspaceNav";
import type { UserProfile } from "../lib/api";
import type { AppSection, Chatbot } from "../types/app";

type AppShellProps = {
  activeSection: AppSection;
  activePath: string;
  user: UserProfile | null;
  knowledgeDbs: KnowledgeDb[];
  selectedKnowledgeDbId?: string | null;
  chatbots: Chatbot[];
  selectedChatbotId?: string | null;
  children: React.ReactNode;
  onNavigate: (path: string) => void;
  onCreateKnowledgeDb: () => void;
  isCreatingKnowledgeDb?: boolean;
  createKnowledgeDbError?: string;
  onCreateChatbot: () => void;
};

export function AppShell({
  activeSection,
  activePath,
  user,
  knowledgeDbs,
  selectedKnowledgeDbId,
  chatbots,
  selectedChatbotId,
  children,
  onNavigate,
  onCreateKnowledgeDb,
  isCreatingKnowledgeDb,
  createKnowledgeDbError,
  onCreateChatbot
}: AppShellProps) {
  const [isWorkspaceNavCollapsed, setIsWorkspaceNavCollapsed] = useState(false);

  return (
    <div className={isWorkspaceNavCollapsed ? "app-shell workspace-collapsed" : "app-shell"}>
      <GlobalNav activeSection={activeSection} user={user} onNavigate={onNavigate} />
      <WorkspaceNav
        activeSection={activeSection}
        knowledgeDbs={knowledgeDbs}
        selectedKnowledgeDbId={selectedKnowledgeDbId}
        chatbots={chatbots}
        selectedChatbotId={selectedChatbotId}
        onNavigate={onNavigate}
        onCreateKnowledgeDb={onCreateKnowledgeDb}
        isCreatingKnowledgeDb={isCreatingKnowledgeDb}
        createKnowledgeDbError={createKnowledgeDbError}
        onCreateChatbot={onCreateChatbot}
        isCollapsed={isWorkspaceNavCollapsed}
        onToggleCollapsed={() => setIsWorkspaceNavCollapsed((value) => !value)}
      />
      <main className="main-content" data-active-path={activePath}>{children}</main>
    </div>
  );
}
