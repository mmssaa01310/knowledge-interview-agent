import { parseRoute, routeSection } from "./routeUtils";
import { useCallback, useEffect, useRef, useState } from "react";
import { useKnowledgeWorkspaceController } from "./useKnowledgeWorkspaceController";

type NavigationGuard = (nextPath: string) => boolean;

export function useAppRouterController() {
  const [route, setRoute] = useState(() => parseRoute(window.location.pathname));
  const currentPathRef = useRef(window.location.pathname);
  const navigationGuardRef = useRef<NavigationGuard | null>(null);
  const hasUnsavedChangesRef = useRef(false);
  const currentSection = routeSection(route);

  const registerNavigationGuard = useCallback((guard: NavigationGuard | null, hasUnsavedChanges = false) => {
    navigationGuardRef.current = guard;
    hasUnsavedChangesRef.current = hasUnsavedChanges;
  }, []);

  const navigate = useCallback((path: string) => {
    if (navigationGuardRef.current && !navigationGuardRef.current(path)) return;
    window.history.pushState(null, "", path);
    currentPathRef.current = path;
    setRoute(parseRoute(path));
  }, []);

  useEffect(() => {
    const handlePopState = () => {
      const nextPath = window.location.pathname;
      if (navigationGuardRef.current && !navigationGuardRef.current(nextPath)) {
        window.history.pushState(null, "", currentPathRef.current);
        return;
      }
      currentPathRef.current = nextPath;
      setRoute(parseRoute(nextPath));
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  useEffect(() => {
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!hasUnsavedChangesRef.current) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, []);

  const {
    user,
    knowledgeLayoutProps
  } = useKnowledgeWorkspaceController({ route, navigate, registerNavigationGuard });

  return {
    route,
    currentSection,
    user,
    navigate,
    knowledgeLayoutProps
  };
}
