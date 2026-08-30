import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { GuideSelectorDialog } from "./GuideSelectorDialog";
import { getGuideDefinition, type GuideId } from "./guideRegistry";
import {
  GUIDE_REQUEST_STORAGE_KEY,
  isKnowledgeCreationGuideAutoPromptDisabled,
  parseHelpGuideRequest,
  setKnowledgeCreationGuideAutoPromptDisabled,
} from "./guideStorage";
import { InteractiveGuide } from "./InteractiveGuide";

type GuideContextValue = {
  openGuideSelector: () => void;
  startGuide: (guideId: GuideId) => void;
  isKnowledgeCreationGuideAutoPromptDisabled: () => boolean;
  setKnowledgeCreationGuideAutoPromptDisabled: (disabled: boolean) => void;
};

type GuideProviderProps = {
  userId?: string | null;
  userRole?: string | null;
  currentPath: string;
  onNavigate: (path: string) => void;
  children: ReactNode;
};

const GuideContext = createContext<GuideContextValue | null>(null);

export function GuideProvider({ userId, userRole, currentPath, onNavigate, children }: GuideProviderProps) {
  const [isSelectorOpen, setIsSelectorOpen] = useState(false);
  const [activeGuideId, setActiveGuideId] = useState<GuideId | null>(null);

  const openGuideSelector = useCallback(() => {
    setActiveGuideId(null);
    setIsSelectorOpen(true);
  }, []);

  const startGuide = useCallback((guideId: GuideId) => {
    if (!getGuideDefinition(guideId, userRole ?? undefined)) return;
    setIsSelectorOpen(false);
    setActiveGuideId(guideId);
  }, [userRole]);

  useEffect(() => {
    function handleGuideRequest(event: StorageEvent) {
      if (event.key !== GUIDE_REQUEST_STORAGE_KEY) return;
      const request = parseHelpGuideRequest(event.newValue);
      if (!request) return;
      if (request.type === "open-selector") {
        openGuideSelector();
        return;
      }
      if (getGuideDefinition(request.guideId, userRole ?? undefined)) {
        startGuide(request.guideId);
      } else {
        openGuideSelector();
      }
    }

    window.addEventListener("storage", handleGuideRequest);
    return () => window.removeEventListener("storage", handleGuideRequest);
  }, [openGuideSelector, startGuide, userRole]);

  const value = useMemo<GuideContextValue>(() => ({
    openGuideSelector,
    startGuide,
    isKnowledgeCreationGuideAutoPromptDisabled: () => isKnowledgeCreationGuideAutoPromptDisabled(userId),
    setKnowledgeCreationGuideAutoPromptDisabled: (disabled) => setKnowledgeCreationGuideAutoPromptDisabled(userId, disabled),
  }), [openGuideSelector, startGuide, userId]);

  return (
    <GuideContext.Provider value={value}>
      {children}
      <GuideSelectorDialog
        isOpen={isSelectorOpen}
        userId={userId}
        userRole={userRole}
        currentPath={currentPath}
        onClose={() => setIsSelectorOpen(false)}
        onSelect={startGuide}
      />
      <InteractiveGuide
        definition={getGuideDefinition(activeGuideId, userRole ?? undefined)}
        userId={userId}
        currentPath={currentPath}
        onNavigate={onNavigate}
        onClose={() => setActiveGuideId(null)}
      />
    </GuideContext.Provider>
  );
}

export function useGuide() {
  const context = useContext(GuideContext);
  if (!context) throw new Error("useGuide must be used inside GuideProvider");
  return context;
}
