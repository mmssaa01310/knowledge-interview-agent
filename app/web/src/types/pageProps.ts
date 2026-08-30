import type { InterviewRecord, Knowledge, KnowledgeDb } from "@ai-interviewer/shared-types";
import type { AiProposal, DocumentSummary, KnowledgeField, UserProfile } from "../lib/api";
import type { Route } from "../routes/routeTypes";
import type {
  ChatMessage,
  DocumentReadState,
  InterviewAnswerTarget,
  InterviewState,
  InterviewStreamMetadata,
  ProcessModelState
} from "./app";
import type { GuidanceDraft, GuidanceUpdatePayload } from "./dashboard";

export type PromptProfile = {
  id: string;
  name: string;
  prompt: string;
};

export type KnowledgeLayoutProps = {
  route: Route;
  user: UserProfile | null;
  knowledgeDbs: KnowledgeDb[];
  knowledges: Knowledge[];
  selectedKnowledgeDb: KnowledgeDb | null;
  selectedKnowledge: Knowledge | null;
  records: InterviewRecord[];
  documents: DocumentSummary[];
  sortedFields: KnowledgeField[];
  draftFields: KnowledgeField[];
  setDraftFields: (fields: KnowledgeField[]) => void;
  settingsName: string;
  setSettingsName: (value: string) => void;
  settingsDescription: string;
  setSettingsDescription: (value: string) => void;
  settingsSystemPrompt: string;
  setSettingsSystemPrompt: (value: string) => void;
  promptProfiles?: PromptProfile[];
  settingsCategory: string;
  setSettingsCategory: (value: string) => void;
  settingsTargetBusiness: string;
  setSettingsTargetBusiness: (value: string) => void;
  settingsTargetEquipment: string;
  setSettingsTargetEquipment: (value: string) => void;
  settingsTags: string[];
  setSettingsTags: (value: string[]) => void;
  settingsLanguage: Knowledge["language"];
  setSettingsLanguage: (value: Knowledge["language"]) => void;
  settingsDefaultModelId: string;
  setSettingsDefaultModelId: (value: string) => void;
  settingsInterviewPlan: Knowledge["interviewPlan"];
  setSettingsInterviewPlan: (value: Knowledge["interviewPlan"]) => void;
  settingsNotice: string;
  settingsSaveState: "idle" | "saving" | "success" | "error";
  newRecordTitle: string;
  setNewRecordTitle: (value: string) => void;
  newDocumentName: string;
  setNewDocumentName: (value: string) => void;
  selectedRecordIds: string[];
  setSelectedRecordIds: (value: string[]) => void;
  documentReadStates: Record<string, DocumentReadState>;
  onUpdateDocumentReadState: (
    documentId: string,
    nextState: DocumentReadState["readStatus"]
  ) => void;
  selectedRecord: InterviewRecord | null;
  publishedGuidance: GuidanceDraft[];
  proposals: AiProposal[];
  chatInput: string;
  setChatInput: (value: string) => void;
  interviewMessages: ChatMessage[];
  interviewState: InterviewState | null;
  interviewStreamMetadata: InterviewStreamMetadata | null;
  streamingInterviewReply: string;
  isInterviewStreaming: boolean;
  interviewError: boolean;
  structuredDraft: Record<string, string>;
  setStructuredDraft: (value: Record<string, string>) => void;
  interviewAnswerOverrides: Record<string, string>;
  setInterviewAnswerOverrides: (value: Record<string, string>) => void;
  deletedExtraQuestionIds: string[];
  setDeletedExtraQuestionIds: (value: string[]) => void;
  recordNotice: string;
  setRecordNotice: (value: string) => void;
  navigate: (path: string) => void;
  onOpenCreateKnowledge: () => void;
  isPreparingKnowledgeCreation: boolean;
  knowledgeCreationError: string;
  onCreateKnowledge: (payload: {
    name: string;
    description?: string;
    purpose?: string;
  }, knowledgeDbId?: string) => void;
  onDeleteKnowledge: (knowledgeId: string) => void;
  onSaveSettings: (activeTab: "fields" | "execution") => void;
  onClearSettingsNotice: () => void;
  onCreatePromptProfile?: (payload: { name: string; prompt: string }) => Promise<PromptProfile>;
  onCreateDocument: () => void;
  onCreateRecord: () => void;
  onDeleteRecord: (recordId: string) => void;
  onChangeRecordStatus: (
    status: InterviewRecord["status"],
    reviewNote?: string,
  ) => Promise<void>;
  onChangeRecordStatusForRecord: (
    recordId: string,
    status: InterviewRecord["status"],
    reviewNote?: string,
  ) => Promise<void>;
  onBulkApproveRecords: () => void;
  onSaveInterviewDraft: () => void;
  onSaveInterviewAnswer: (fieldId: string, recordAnswer: string) => Promise<void>;
  onDeleteInterviewAnswers: () => void;
  onDeleteInterviewChat: () => void;
  onStartInterview: () => void;
  onSendInterviewMessage: (target?: InterviewAnswerTarget | null, content?: string) => void;
  onAppendInterviewMessage: (message: ChatMessage) => void;
  onRefreshInterviewSnapshot: () => void;
  onSaveProcessModel: (
    processState: ProcessModelState,
    baseProcessVersion: number,
    baseStateVersion: number,
  ) => Promise<InterviewState>;
  onEditProcessModel: (
    instruction: string,
    baseProcessVersion: number,
    baseStateVersion: number,
  ) => Promise<{ interviewState: InterviewState; reply: string }>;
  onApproveOne: (proposalId: string) => void;
  onRejectProposal: (proposalId: string) => void;
  onRemoveProposal: (proposalId: string) => void;
  onApproveAllForRecord: () => void;
};
