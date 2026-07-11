import type { KnowledgeDb } from "@ai-interviewer/shared-types";
import { ChatbotWorkspaceNav } from "../features/chatbots/components/ChatbotWorkspaceNav";
import { KnowledgeWorkspaceNav } from "../features/knowledge/components/KnowledgeWorkspaceNav";
import type { Chatbot, AppSection } from "../types/app";

type WorkspaceNavProps = {
  activeSection: AppSection;
  knowledgeDbs: KnowledgeDb[];
  selectedKnowledgeDbId?: string | null;
  chatbots: Chatbot[];
  selectedChatbotId?: string | null;
  onNavigate: (path: string) => void;
  onCreateKnowledgeDb: () => void;
  isCreatingKnowledgeDb?: boolean;
  createKnowledgeDbError?: string;
  onCreateChatbot: () => void;
  isCollapsed: boolean;
  onToggleCollapsed: () => void;
};

export function WorkspaceNav({
  activeSection,
  knowledgeDbs,
  selectedKnowledgeDbId,
  chatbots,
  selectedChatbotId,
  onNavigate,
  onCreateKnowledgeDb,
  isCreatingKnowledgeDb,
  createKnowledgeDbError,
  onCreateChatbot,
  isCollapsed,
  onToggleCollapsed
}: WorkspaceNavProps) {
  if (isCollapsed) {
    return (
      <aside className="workspace-nav collapsed">
        <button
          type="button"
          className="workspace-collapse-button"
          onClick={onToggleCollapsed}
          aria-label="左側ナビを開く"
          title="左側ナビを開く"
        >
          <span className="nav-toggle-icon open" aria-hidden="true" />
        </button>
      </aside>
    );
  }

  if (activeSection === "settings") {
    return (
      <aside className="workspace-nav">
        <button
          type="button"
          className="workspace-collapse-button"
          onClick={onToggleCollapsed}
          aria-label="左側ナビを閉じる"
          title="左側ナビを閉じる"
        >
          <span className="nav-toggle-icon close" aria-hidden="true" />
        </button>
        <strong>設定</strong>
        <p className="workspace-empty">ユーザー設定とモデル設定を管理します。</p>
      </aside>
    );
  }

  if (activeSection === "chatbots") {
    return (
      <ChatbotWorkspaceNav
        chatbots={chatbots}
        selectedChatbotId={selectedChatbotId}
        onNavigate={onNavigate}
        onCreateChatbot={onCreateChatbot}
        onToggleCollapsed={onToggleCollapsed}
      />
    );
  }

  return (
    <KnowledgeWorkspaceNav
      knowledgeDbs={knowledgeDbs}
      selectedKnowledgeDbId={selectedKnowledgeDbId}
      onNavigate={onNavigate}
      onCreateKnowledgeDb={onCreateKnowledgeDb}
      isCreatingKnowledgeDb={isCreatingKnowledgeDb}
      createKnowledgeDbError={createKnowledgeDbError}
      onToggleCollapsed={onToggleCollapsed}
    />
  );
}
