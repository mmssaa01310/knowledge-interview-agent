import { useEffect } from "react";
import { AppShell } from "../layouts/AppShell";
import { KnowledgeLayout } from "../layouts/KnowledgeLayout";
import { LoginPage } from "../pages/LoginPage";
import { SettingsPage } from "../pages/SettingsPage";
import { useI18n } from "../i18n";
import { useAppRouterController } from "./useAppRouterController";

export function AppRouter() {
  const { setProfileLocale } = useI18n();
  const {
    route,
    currentSection,
    user,
    navigate,
    knowledgeLayoutProps,
  } = useAppRouterController();

  useEffect(() => {
    setProfileLocale(user?.uiLocale);
  }, [setProfileLocale, user?.uiLocale]);

  if (route.name === "login") {
    return <LoginPage onLogin={() => navigate("/knowledge-dbs")} />;
  }

  return (
    <>
      <AppShell
        user={user}
        activeSection={currentSection}
        activePath={window.location.pathname}
        knowledges={knowledgeLayoutProps.knowledges}
        selectedKnowledgeId={knowledgeLayoutProps.selectedKnowledge?.id}
        onNavigate={navigate}
        onOpenCreateKnowledge={knowledgeLayoutProps.onOpenCreateKnowledge}
        isPreparingKnowledgeCreation={knowledgeLayoutProps.isPreparingKnowledgeCreation}
        knowledgeCreationError={knowledgeLayoutProps.knowledgeCreationError}
      >
      {currentSection === "settings" ? <SettingsPage /> : <KnowledgeLayout {...knowledgeLayoutProps} />}
      </AppShell>
    </>
  );
}
