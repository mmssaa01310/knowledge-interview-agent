import type { Knowledge } from "@ai-interviewer/shared-types";
import type { UserProfile } from "../lib/api";
import { KnowledgeWorkspaceNav } from "../features/knowledge/components/KnowledgeWorkspaceNav";
import { UserMenu } from "../components/ui/UserMenu";
import { useI18n } from "../i18n";
import type { AppSection } from "../types/app";

type WorkspaceNavProps = {
  id?: string;
  activeSection: AppSection;
  user: UserProfile | null;
  knowledges: Knowledge[];
  selectedKnowledgeId?: string | null;
  onNavigate: (path: string) => void;
  onOpenCreateKnowledge: () => void;
  isPreparingKnowledgeCreation?: boolean;
  knowledgeCreationError?: string;
  isCollapsed: boolean;
  isResponsiveOpen?: boolean;
  onToggleCollapsed: () => void;
  onStartGuide: () => void;
  onLogout: () => void;
};

export function WorkspaceNav({
  id,
  activeSection,
  user,
  knowledges,
  selectedKnowledgeId,
  onNavigate,
  onOpenCreateKnowledge,
  isPreparingKnowledgeCreation,
  knowledgeCreationError,
  isCollapsed,
  isResponsiveOpen = false,
  onToggleCollapsed,
  onStartGuide,
  onLogout,
}: WorkspaceNavProps) {
  const { t } = useI18n();
  const canManageKnowledge = user?.role === "admin" || user?.role === "knowledge_manager";

  if (isCollapsed && !isResponsiveOpen) {
    return (
      <aside id={id} className="app-sidebar collapsed" aria-label={t("navigation.mainNavigation")} data-guide="navigation">
        <div className="sidebar-collapsed-brand">
          <img src="/images/kikiori-icon.svg" alt="KIKIORI" />
        </div>
        <button
          type="button"
          className="workspace-collapse-button"
          onClick={onToggleCollapsed}
          aria-label={t("navigation.navOpen")}
          title={t("navigation.navOpen")}
          aria-expanded="false"
        >
          <span className="nav-toggle-icon open" aria-hidden="true" />
        </button>
        <UserMenu
          user={user}
          activeSection={activeSection}
          isCollapsed
          onNavigate={onNavigate}
          onStartGuide={onStartGuide}
          onLogout={onLogout}
        />
      </aside>
    );
  }

  return (
    <aside id={id} className={isResponsiveOpen ? "app-sidebar responsive-open" : "app-sidebar"} aria-label={t("navigation.mainNavigation")} data-guide="navigation">
      <div className="sidebar-header">
        <div className="brand">
          <img className="brand-logo-image" src="/images/kikiori-logo.svg" alt="KIKIORI" />
          <span className="brand-tagline">{t("common.tagline")}</span>
        </div>
        <button
          type="button"
          className="workspace-collapse-button"
          onClick={onToggleCollapsed}
          aria-label={t("navigation.navClose")}
          title={t("navigation.navClose")}
          aria-expanded="true"
        >
          <span className="nav-toggle-icon close" aria-hidden="true" />
        </button>
      </div>

      <nav className="sidebar-content" aria-label={t("navigation.workspaceNavigation")}>
        {activeSection === "settings" ? (
          <div className="sidebar-section sidebar-settings-section">
            <span className="sidebar-section-kicker">{t("navigation.workspace")}</span>
            <strong className="sidebar-section-title"><span className="nav-section-icon" aria-hidden="true">⚙</span>{t("navigation.settings")}</strong>
          </div>
        ) : (
          <KnowledgeWorkspaceNav
            knowledges={knowledges}
            selectedKnowledgeId={selectedKnowledgeId}
            onNavigate={onNavigate}
            onOpenCreateKnowledge={onOpenCreateKnowledge}
            canManage={canManageKnowledge}
            isPreparingKnowledgeCreation={isPreparingKnowledgeCreation}
            knowledgeCreationError={knowledgeCreationError}
          />
        )}
      </nav>

      <div className="sidebar-footer">
        <UserMenu
          user={user}
          activeSection={activeSection}
          onNavigate={onNavigate}
          onStartGuide={onStartGuide}
          onLogout={onLogout}
        />
      </div>
    </aside>
  );
}
