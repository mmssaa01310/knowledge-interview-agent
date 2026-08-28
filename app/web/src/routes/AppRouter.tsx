import { AppShell } from "../layouts/AppShell";
import { KnowledgeLayout } from "../layouts/KnowledgeLayout";
import { RecordsLayout } from "../layouts/RecordsLayout";
import { LoginPage } from "../pages/LoginPage";
import { SettingsPage } from "../pages/SettingsPage";
import { useAppRouterController } from "./useAppRouterController";

export function AppRouter() {
  const {
    route,
    currentSection,
    user,
    navigate,
    knowledgeLayoutProps,
  } = useAppRouterController();

  if (route.name === "login") {
    return <LoginPage onLogin={() => navigate(user?.role === "interviewer" || user?.role === "viewer" ? "/records" : "/knowledge-dbs")} />;
  }

  return (
    <>
      <AppShell
        user={user}
        activeSection={currentSection}
        activePath={window.location.pathname}
        knowledges={knowledgeLayoutProps.knowledges}
        recordCount={knowledgeLayoutProps.records.length}
        selectedKnowledgeId={knowledgeLayoutProps.selectedKnowledge?.id}
        onNavigate={navigate}
        onOpenCreateKnowledge={knowledgeLayoutProps.onOpenCreateKnowledge}
        isPreparingKnowledgeCreation={knowledgeLayoutProps.isPreparingKnowledgeCreation}
        knowledgeCreationError={knowledgeLayoutProps.knowledgeCreationError}
      >
      {currentSection === "settings" ? <SettingsPage /> : currentSection === "records" ? <RecordsLayout {...knowledgeLayoutProps} /> : <KnowledgeLayout {...knowledgeLayoutProps} />}
      </AppShell>
    </>
  );
}
