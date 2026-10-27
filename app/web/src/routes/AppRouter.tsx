import { lazy, Suspense, useEffect } from "react";
import { AppShell } from "../layouts/AppShell";
import { KnowledgeLayout } from "../layouts/KnowledgeLayout";
import { LoginPage } from "../pages/LoginPage";
import { HelpPage } from "../pages/HelpPage";
import { useI18n } from "../i18n";
import { logout } from "../lib/api";
import { useAppRouterController } from "./useAppRouterController";

const SettingsPage = lazy(() => import("../pages/SettingsPage").then(({ SettingsPage: page }) => ({ default: page })));
const AdminDashboardPage = lazy(() => import("../pages/AdminDashboardPage").then(({ AdminDashboardPage: page }) => ({ default: page })));

export function AppRouter() {
  const { setProfileLocale, t } = useI18n();
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
    return <LoginPage onLogin={() => window.location.replace("/knowledge-dbs")} />;
  }

  if (route.name === "help") {
    return <HelpPage />;
  }

  async function handleLogout() {
    try {
      await logout();
      window.location.replace("/login");
    } catch {
      window.alert(t("common.account.operationFailed"));
    }
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
        onLogout={handleLogout}
      >
      <Suspense fallback={<p className="empty" role="status">{t("common.loading")}</p>}>
        {currentSection === "settings"
          ? <SettingsPage user={user} onNavigate={navigate} />
            : route.name === "dashboard"
            ? <AdminDashboardPage user={user} knowledges={knowledgeLayoutProps.knowledges} onNavigate={navigate} />
            : <KnowledgeLayout {...knowledgeLayoutProps} />}
      </Suspense>
      </AppShell>
    </>
  );
}
