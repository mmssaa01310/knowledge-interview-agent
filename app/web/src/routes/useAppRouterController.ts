import { parseRoute, routeSection } from "./routeUtils";
import { useEffect, useState } from "react";
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

  const {
    user,
    knowledgeLayoutProps
  } = useKnowledgeWorkspaceController({ route, navigate });

  return {
    route,
    currentSection,
    user,
    navigate,
    knowledgeLayoutProps
  };
}
