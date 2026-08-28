import { useState } from "react";
import type { Knowledge } from "@ai-interviewer/shared-types";
import { getDevelopmentToken, setDevelopmentToken, type UserProfile } from "../lib/api";
import { KnowledgeWorkspaceNav } from "../features/knowledge/components/KnowledgeWorkspaceNav";
import { RecordsWorkspaceNav } from "../features/records/components/RecordsWorkspaceNav";
import type { AppSection } from "../types/app";

type WorkspaceNavProps = {
  activeSection: AppSection;
  activePath: string;
  user: UserProfile | null;
  knowledges: Knowledge[];
  recordCount: number;
  selectedKnowledgeId?: string | null;
  onNavigate: (path: string) => void;
  onOpenCreateKnowledge: () => void;
  isPreparingKnowledgeCreation?: boolean;
  knowledgeCreationError?: string;
  isCollapsed: boolean;
  onToggleCollapsed: () => void;
};

export function WorkspaceNav({
  activeSection,
  activePath,
  user,
  knowledges,
  recordCount,
  selectedKnowledgeId,
  onNavigate,
  onOpenCreateKnowledge,
  isPreparingKnowledgeCreation,
  knowledgeCreationError,
  isCollapsed,
  onToggleCollapsed
}: WorkspaceNavProps) {
  const [developmentToken, setDevelopmentTokenState] = useState(getDevelopmentToken);
  const canManageSystem = user?.role === "admin";

  function handleDevelopmentUserChange(token: string) {
    setDevelopmentToken(token);
    setDevelopmentTokenState(token);
    window.location.reload();
  }

  if (isCollapsed) {
    return (
      <aside className="app-sidebar collapsed" aria-label="メインナビゲーション">
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

  return (
    <aside className="app-sidebar" aria-label="メインナビゲーション">
      <div className="sidebar-header">
        <div className="brand">
          <div className="brand-mark">AI</div>
          <div>
            <strong>AI Interviewer</strong>
          </div>
        </div>
        <button
          type="button"
          className="workspace-collapse-button"
          onClick={onToggleCollapsed}
          aria-label="左側ナビを閉じる"
          title="左側ナビを閉じる"
        >
          <span className="nav-toggle-icon close" aria-hidden="true" />
        </button>
      </div>

      <div className="sidebar-content">
        {activeSection === "settings" ? (
          <div className="sidebar-section">
            <strong>設定</strong>
          </div>
        ) : activeSection === "records" ? (
          <RecordsWorkspaceNav
            activePath={activePath}
            recordCount={recordCount}
            onNavigate={onNavigate}
          />
        ) : (
          <KnowledgeWorkspaceNav
            knowledges={knowledges}
            selectedKnowledgeId={selectedKnowledgeId}
            onNavigate={onNavigate}
            onOpenCreateKnowledge={onOpenCreateKnowledge}
            isPreparingKnowledgeCreation={isPreparingKnowledgeCreation}
            knowledgeCreationError={knowledgeCreationError}
          />
        )}
      </div>

      <div className="sidebar-footer">
        {canManageSystem ? (
          <button
            type="button"
            className={activeSection === "settings" ? "sidebar-system-link active" : "sidebar-system-link"}
            onClick={() => onNavigate("/settings")}
          >
            システム設定
          </button>
        ) : null}
        {import.meta.env.DEV ? (
          <label className="dev-user-switcher">
            <span>開発用ユーザー</span>
            <select
              value={developmentToken}
              onChange={(event) => handleDevelopmentUserChange(event.target.value)}
            >
              <option value="dev-admin">システム管理者</option>
              <option value="dev-manager">ナレッジ管理者</option>
              <option value="dev-interviewer">インタビュー対象者</option>
              <option value="dev-viewer">閲覧者</option>
            </select>
          </label>
        ) : null}
        <div className="sidebar-user">
          <p>ログイン中</p>
          <strong>{user ? `${user.displayName} / ${roleLabels[user.role]}` : "未接続"}</strong>
        </div>
      </div>
    </aside>
  );
}

const roleLabels: Record<UserProfile["role"], string> = {
  admin: "システム管理者",
  knowledge_manager: "ナレッジ管理者",
  interviewer: "インタビュー対象者",
  viewer: "閲覧者",
};
