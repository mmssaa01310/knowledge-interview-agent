import type { AppSection } from "../types/app";
import type { UserProfile } from "../lib/api";

type GlobalNavProps = {
  activeSection: AppSection;
  user: UserProfile | null;
  onNavigate: (path: string) => void;
};

export function GlobalNav({ activeSection, user, onNavigate }: GlobalNavProps) {
  return (
    <nav className="global-nav" aria-label="アプリ全体の分類">
      <div className="brand compact-brand">
        <div className="brand-mark">AI</div>
        <div>
          <strong>AI Interviewer</strong>
          <p>Knowledge Ops</p>
        </div>
      </div>
      <button
        type="button"
        className={activeSection === "knowledge" ? "global-nav-item active" : "global-nav-item"}
        onClick={() => onNavigate("/knowledge-dbs")}
      >
        ナレッジ作成
      </button>
      <button
        type="button"
        className={activeSection === "chatbots" ? "global-nav-item active" : "global-nav-item"}
        onClick={() => onNavigate("/chatbots")}
      >
        チャットボット作成
      </button>
      <div className="global-nav-footer">
        <button
          type="button"
          className={activeSection === "settings" ? "global-nav-item active" : "global-nav-item"}
          onClick={() => onNavigate("/settings")}
        >
          設定
        </button>
        <div className="global-nav-user">
          <p>ログイン中</p>
          <strong>{user ? `${user.displayName} / ${user.role}` : "未接続"}</strong>
        </div>
      </div>
    </nav>
  );
}
