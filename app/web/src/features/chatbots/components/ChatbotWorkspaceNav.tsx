import type { Chatbot } from "../../../types/app";

type ChatbotWorkspaceNavProps = {
  chatbots: Chatbot[];
  selectedChatbotId?: string | null;
  onNavigate: (path: string) => void;
  onCreateChatbot: () => void;
  onToggleCollapsed: () => void;
};

export function ChatbotWorkspaceNav({
  chatbots,
  selectedChatbotId,
  onNavigate,
  onCreateChatbot,
  onToggleCollapsed
}: ChatbotWorkspaceNavProps) {
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
      <div className="workspace-nav-header">
        <strong>チャットボット</strong>
        <button type="button" className="workspace-create" onClick={onCreateChatbot}>
          + 新規チャットボット
        </button>
      </div>
      {chatbots.length === 0 ? (
        <p className="workspace-empty">チャットボットがありません。</p>
      ) : chatbots.map((chatbot) => (
        <button
          type="button"
          key={chatbot.id}
          className={selectedChatbotId === chatbot.id ? "workspace-item active" : "workspace-item"}
          onClick={() => onNavigate(`/chatbots/${chatbot.id}`)}
        >
          <strong>{chatbot.name}</strong>
          <span>参照DB {chatbot.referenceKnowledgeDbIds.length} / 文書 {chatbot.referenceDocumentIds.length}</span>
        </button>
      ))}
    </aside>
  );
}
