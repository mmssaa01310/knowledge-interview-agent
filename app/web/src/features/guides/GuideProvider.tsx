import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { GuideSelectorDialog } from "./GuideSelectorDialog";
import { getGuideDefinition, type GuideId } from "./guideRegistry";
import { isKnowledgeCreationGuideAutoPromptDisabled, setKnowledgeCreationGuideAutoPromptDisabled } from "./guideStorage";
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
