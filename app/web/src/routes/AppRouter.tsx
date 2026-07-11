import { AppShell } from "../layouts/AppShell";
import { ChatbotLayout } from "../layouts/ChatbotLayout";
import { KnowledgeLayout } from "../layouts/KnowledgeLayout";
import { LoginPage } from "../pages/LoginPage";
import { SettingsPage } from "../pages/SettingsPage";
import { CreateKnowledgeDbDialog } from "./CreateKnowledgeDbDialog";
import { useAppRouterController } from "./useAppRouterController";

export function AppRouter() {
  const {
    route,
    currentSection,
    user,
    knowledgeDbs,
    selectedKnowledgeDb,
    chatbots,
    selectedChatbot,
    navigate,
    openCreateKnowledgeDbDialog,
    isCreatingKnowledgeDb,
    knowledgeLayoutProps,
    chatbotLayoutProps,
    createKnowledgeDbDialogProps
  } = useAppRouterController();

  if (route.name === "login") {
    return <LoginPage onLogin={() => navigate("/knowledge")} />;
  }

  return (
    <>
      <AppShell
        user={user}
        activeSection={currentSection}
        activePath={window.location.pathname}
        knowledgeDbs={knowledgeDbs}
        selectedKnowledgeDbId={selectedKnowledgeDb?.id}
        chatbots={chatbots}
        selectedChatbotId={"chatbotId" in route ? selectedChatbot?.id : null}
        onNavigate={navigate}
        onCreateKnowledgeDb={openCreateKnowledgeDbDialog}
        isCreatingKnowledgeDb={isCreatingKnowledgeDb}
        createKnowledgeDbError=""
        onCreateChatbot={chatbotLayoutProps.onCreateChatbot}
      >
      {currentSection === "settings" ? (
        <SettingsPage />
      ) : currentSection === "knowledge" ? (
        <KnowledgeLayout {...knowledgeLayoutProps} />
      ) : (
        <ChatbotLayout {...chatbotLayoutProps} />
      )}
      </AppShell>
      <CreateKnowledgeDbDialog {...createKnowledgeDbDialogProps} />
    </>
  );
}
