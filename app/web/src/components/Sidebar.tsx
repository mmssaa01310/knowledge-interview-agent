import type { UserProfile } from "../lib/api";

type AppSection = "knowledge" | "chatbots";

type KnowledgeDbNavItem = {
  id: string;
  name: string;
  knowledgeCount?: number;
};

type ChatbotNavItem = {
  id: string;
  name: string;
};

type SidebarProps = {
  user: UserProfile | null;
  activeSection: AppSection;
  activePath: string;
  knowledgeDbs: KnowledgeDbNavItem[];
  selectedKnowledgeDbId?: string | null;
  chatbots: ChatbotNavItem[];
  selectedChatbotId?: string | null;
  onNavigate: (path: string) => void;
  onCreateKnowledgeDb: () => void;
  onCreateChatbot: () => void;
  onLogout?: () => void;
};

function isExactPath(activePath: string, targetPath: string) {
  return activePath === targetPath;
}

function isBranchPath(activePath: string, targetPath: string) {
  return activePath === targetPath || activePath.startsWith(`${targetPath}/`);
}

export function Sidebar({
  user,
  activeSection,
  activePath,
  knowledgeDbs,
  selectedKnowledgeDbId,
  chatbots,
  selectedChatbotId,
  onNavigate,
  onCreateKnowledgeDb,
  onCreateChatbot,
  onLogout
}: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">AI</div>
        <div>
          <strong>AI Interviewer</strong>
          <p>Knowledge Ops</p>
        </div>
      </div>

      <nav className="sidebar-nav" aria-label="Main navigation">
        <button
          type="button"
          className={activeSection === "knowledge" ? "sidebar-nav-item active" : "sidebar-nav-item"}
          onClick={() => onNavigate("/knowledge-dbs")}
        >
          ナレッジ作成
        </button>
        <button
          type="button"
          className={activeSection === "chatbots" ? "sidebar-nav-item active" : "sidebar-nav-item"}
          onClick={() => onNavigate("/chatbots")}
        >
          チャットボット作成
        </button>
      </nav>

      {activeSection === "knowledge" ? (
        <KnowledgeSidebarSection
          activePath={activePath}
          knowledgeDbs={knowledgeDbs}
          selectedKnowledgeDbId={selectedKnowledgeDbId}
          onNavigate={onNavigate}
          onCreateKnowledgeDb={onCreateKnowledgeDb}
        />
      ) : (
        <ChatbotSidebarSection
          activePath={activePath}
          chatbots={chatbots}
          selectedChatbotId={selectedChatbotId}
          onNavigate={onNavigate}
          onCreateChatbot={onCreateChatbot}
        />
      )}

      <div className="sidebar-footer">
        <p>ログイン中</p>
        <strong>{user ? `${user.displayName} / ${user.role}` : "未接続"}</strong>
        {onLogout ? (
          <button type="button" className="sidebar-logout" onClick={onLogout}>
            ログアウト
          </button>
        ) : null}
      </div>
    </aside>
  );
}

type KnowledgeSidebarSectionProps = {
  activePath: string;
  knowledgeDbs: KnowledgeDbNavItem[];
  selectedKnowledgeDbId?: string | null;
  onNavigate: (path: string) => void;
  onCreateKnowledgeDb: () => void;
};

function KnowledgeSidebarSection({
  activePath,
  knowledgeDbs,
  selectedKnowledgeDbId,
  onNavigate,
  onCreateKnowledgeDb
}: KnowledgeSidebarSectionProps) {
  return (
    <section className="sidebar-section">
      <div className="sidebar-section-header">
        <span>ナレッジDB</span>
        <button
          type="button"
          className="sidebar-small-button"
          onClick={onCreateKnowledgeDb}
          aria-label="ナレッジを作成"
        >
          +
        </button>
      </div>

      <button
        type="button"
        className={isExactPath(activePath, "/knowledge-dbs") ? "sidebar-sub-item active" : "sidebar-sub-item"}
        onClick={() => onNavigate("/knowledge-dbs")}
      >
        ナレッジDB一覧
      </button>

      <div className="sidebar-list">
        {knowledgeDbs.length === 0 ? (
          <p className="sidebar-empty">ナレッジDBがありません。</p>
        ) : knowledgeDbs.map((db) => {
          const isSelectedDb = selectedKnowledgeDbId === db.id || isBranchPath(activePath, `/knowledge-dbs/${db.id}`);

          return (
            <div key={db.id} className={isSelectedDb ? "sidebar-db selected" : "sidebar-db"}>
              <button
                type="button"
                className="sidebar-db-name"
                onClick={() => onNavigate(`/knowledge-dbs/${db.id}`)}
              >
                {db.name}
              </button>
              <div className="sidebar-db-meta">
                <span>ナレッジ {db.knowledgeCount ?? 0}</span>
              </div>

              {isSelectedDb ? (
                <div className="sidebar-child-nav">
                  <button
                    type="button"
                    className={isBranchPath(activePath, `/knowledge-dbs/${db.id}`) ? "sidebar-child-item active" : "sidebar-child-item"}
                    onClick={() => onNavigate(`/knowledge-dbs/${db.id}`)}
                  >
                    ナレッジ一覧
                  </button>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}

type ChatbotSidebarSectionProps = {
  activePath: string;
  chatbots: ChatbotNavItem[];
  selectedChatbotId?: string | null;
  onNavigate: (path: string) => void;
  onCreateChatbot: () => void;
};

function ChatbotSidebarSection({
  activePath,
  chatbots,
  selectedChatbotId,
  onNavigate,
  onCreateChatbot
}: ChatbotSidebarSectionProps) {
  return (
    <section className="sidebar-section">
      <div className="sidebar-section-header">
        <span>チャットボット</span>
        <button
          type="button"
          className="sidebar-small-button"
          onClick={onCreateChatbot}
          aria-label="新規チャットボットを作成"
        >
          +
        </button>
      </div>

      <button
        type="button"
        className={isExactPath(activePath, "/chatbots") ? "sidebar-sub-item active" : "sidebar-sub-item"}
        onClick={() => onNavigate("/chatbots")}
      >
        チャットボット一覧
      </button>

      <div className="sidebar-list">
        {chatbots.length === 0 ? (
          <p className="sidebar-empty">チャットボットがありません。</p>
        ) : chatbots.map((chatbot) => {
          const isSelectedChatbot = selectedChatbotId === chatbot.id || isBranchPath(activePath, `/chatbots/${chatbot.id}`);

          return (
            <div key={chatbot.id} className={isSelectedChatbot ? "sidebar-db selected" : "sidebar-db"}>
              <button
                type="button"
                className="sidebar-db-name"
                onClick={() => onNavigate(`/chatbots/${chatbot.id}/chat`)}
              >
                {chatbot.name}
              </button>

              {isSelectedChatbot ? (
                <div className="sidebar-child-nav">
                  <button
                    type="button"
                    className={isExactPath(activePath, `/chatbots/${chatbot.id}/chat`) ? "sidebar-child-item active" : "sidebar-child-item"}
                    onClick={() => onNavigate(`/chatbots/${chatbot.id}/chat`)}
                  >
                    チャット
                  </button>
                  <button
                    type="button"
                    className={isExactPath(activePath, `/chatbots/${chatbot.id}/references`) ? "sidebar-child-item active" : "sidebar-child-item"}
                    onClick={() => onNavigate(`/chatbots/${chatbot.id}/references`)}
                  >
                    参照設定
                  </button>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}
