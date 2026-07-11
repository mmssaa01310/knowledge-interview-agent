import { getRouteKnowledgeDbId, getRouteKnowledgeId, parseRoute, routeSection } from "./routeUtils";
import { useEffect, useState } from "react";
import { useChatbotController } from "./useChatbotController";
import { useKnowledgeWorkspaceController } from "./useKnowledgeWorkspaceController";

export function useAppRouterController() {
  const [route, setRoute] = useState(() => parseRoute(window.location.pathname));
  const currentSection = routeSection(route);

  function navigate(path: string) {
    window.history.pushState(null, "", path);
    setRoute(parseRoute(path));
  }

  useEffect(() => {
    const handlePopState = () => setRoute(parseRoute(window.location.pathname));
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  const routeKnowledgeDbId = getRouteKnowledgeDbId(route);
  const routeKnowledgeId = getRouteKnowledgeId(route);

  const {
    user,
    knowledgeDbs,
    knowledges,
    documents,
    selectedKnowledgeDb,
    knowledgeLayoutProps,
    openCreateKnowledgeDbDialog,
    isCreatingKnowledgeDb,
    createKnowledgeDbDialogProps
  } = useKnowledgeWorkspaceController({ route, navigate });

  const {
    chatbots,
    selectedChatbot,
    handleCreateChatbot,
    chatbotLayoutProps
  } = useChatbotController({
    route,
    navigate,
    knowledgeDbs,
    knowledges,
    documents
  });

  return {
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
  };
}