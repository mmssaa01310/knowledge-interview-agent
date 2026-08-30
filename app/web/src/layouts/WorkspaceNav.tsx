import { useState } from "react";
import type { Knowledge } from "@ai-interviewer/shared-types";
import { getDevelopmentToken, setDevelopmentToken, type UserProfile } from "../lib/api";
import { KnowledgeWorkspaceNav } from "../features/knowledge/components/KnowledgeWorkspaceNav";
import { LocaleSwitcher } from "../components/ui/LocaleSwitcher";
import { OptionPicker } from "../components/ui/OptionPicker";
import { useI18n } from "../i18n";
import type { AppSection } from "../types/app";

type WorkspaceNavProps = {
  activeSection: AppSection;
  user: UserProfile | null;
  knowledges: Knowledge[];
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
  user,
  knowledges,
  selectedKnowledgeId,
  onNavigate,
  onOpenCreateKnowledge,
  isPreparingKnowledgeCreation,
  knowledgeCreationError,
  isCollapsed,
  onToggleCollapsed
}: WorkspaceNavProps) {
  const { t } = useI18n();
  const [developmentToken, setDevelopmentTokenState] = useState(getDevelopmentToken);
  const canManageSystem = user?.role === "admin";
  const canManageKnowledge = user?.role === "admin" || user?.role === "knowledge_manager";

  function handleDevelopmentUserChange(token: string) {
    setDevelopmentToken(token);
    setDevelopmentTokenState(token);
    window.location.reload();
  }

  if (isCollapsed) {
    return (
      <aside className="app-sidebar collapsed" aria-label={t("navigation.mainNavigation")}>
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
      </aside>
    );
  }

  return (
    <aside className="app-sidebar" aria-label={t("navigation.mainNavigation")}>
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
        <LocaleSwitcher />
        {canManageSystem ? (
          <button
            type="button"
            className={activeSection === "settings" ? "sidebar-system-link active" : "sidebar-system-link"}
            onClick={() => onNavigate("/settings")}
          >
            {t("navigation.systemSettings")}
          </button>
        ) : null}
        {import.meta.env.DEV ? (
          <div className="dev-user-switcher">
            <span>{t("navigation.developmentUser")}</span>
            <OptionPicker
              value={developmentToken}
              options={[
                { value: "dev-admin", label: t("navigation.roles.admin") },
                { value: "dev-manager", label: t("navigation.roles.knowledge_manager") },
                { value: "dev-interviewer", label: t("navigation.roles.interviewer") },
                { value: "dev-viewer", label: t("navigation.roles.viewer") },
              ]}
              onChange={handleDevelopmentUserChange}
              ariaLabel={t("navigation.developmentUser")}
              className="dev-user-picker"
              placement="top"
            />
          </div>
        ) : null}
        <div className="sidebar-user">
          <p>{t("navigation.loggedIn")}</p>
          <strong>{user ? `${user.displayName} / ${t(`navigation.roles.${user.role}`)}` : t("navigation.disconnected")}</strong>
        </div>
      </div>
    </aside>
  );
}
