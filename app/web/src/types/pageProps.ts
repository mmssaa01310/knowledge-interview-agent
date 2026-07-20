import type { InterviewRecord, Knowledge, KnowledgeDb } from "@ai-interviewer/shared-types";
import type { AiProposal, DocumentSummary, KnowledgeField } from "../lib/api";
import type { Route } from "../routes/routeTypes";
import type {
  Chatbot,
  ChatMessage,
  DocumentReadState,
  InterviewAnswerTarget,
  InterviewState,
  InterviewStreamMetadata
} from "./app";

export type PromptProfile = {
  id: string;
  name: string;
  prompt: string;
};

export type KnowledgeLayoutProps = {
  route: Route;
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
  settingsLanguage: Knowledge["language"];
  setSettingsLanguage: (value: Knowledge["language"]) => void;
  settingsDefaultModelId: string;
  setSettingsDefaultModelId: (value: string) => void;
  settingsNotice: string;
  newDbName: string;
  setNewDbName: (value: string) => void;
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
  proposals: AiProposal[];
  overviewSummaryDraft: string;
  setOverviewSummaryDraft: (value: string) => void;
  isGeneratingOverviewSummary: boolean;
  chatInput: string;
  setChatInput: (value: string) => void;
  interviewMessages: ChatMessage[];
  interviewState: InterviewState | null;
  interviewStreamMetadata: InterviewStreamMetadata | null;
  streamingInterviewReply: string;
  isInterviewStreaming: boolean;
  structuredDraft: Record<string, string>;
  setStructuredDraft: (value: Record<string, string>) => void;
  interviewAnswerOverrides: Record<string, string>;
  setInterviewAnswerOverrides: (value: Record<string, string>) => void;
  deletedExtraQuestionIds: string[];
  setDeletedExtraQuestionIds: (value: string[]) => void;
  summaryDraft: string;
  setSummaryDraft: (value: string) => void;
  isGeneratingSummary: boolean;
  recordNotice: string;
  setRecordNotice: (value: string) => void;
  navigate: (path: string) => void;
  onCreateKnowledgeDb: () => void;
  isCreatingKnowledgeDb: boolean;
  createKnowledgeDbError: string;
  onDeleteKnowledgeDb: (knowledgeDbId: string) => void;
  onCreateKnowledge: (payload: {
    name: string;
    description?: string;
    purpose?: string;
  }) => void;
  onDeleteKnowledge: (knowledgeId: string) => void;
  onGenerateOverviewSummary: () => void;
  onSaveOverviewSummary: () => void;
  onRevertOverviewSummary: () => void;
  onCreateDemoData: () => void;
  onSaveSettings: (activeTab: "basic" | "fields" | "assist") => void;
  onClearSettingsNotice: () => void;
  onCreatePromptProfile?: (payload: { name: string; prompt: string }) => Promise<PromptProfile>;
  onCreateDocument: () => void;
  onCreateRecord: () => void;
  onDeleteRecord: (recordId: string) => void;
  onBulkApproveRecords: () => void;
  onSaveInterviewDraft: () => void;
  onSaveInterviewAnswer: (fieldId: string, answerSummary: string) => Promise<void>;
  onDeleteInterviewAnswers: () => void;
  onDeleteInterviewChat: () => void;
  onStartInterview: () => void;
  onSendInterviewMessage: (target?: InterviewAnswerTarget | null) => void;
  onAppendInterviewMessage: (message: ChatMessage) => void;
  onRefreshInterviewSnapshot: () => void;
  onGenerateRecordSummary: () => void;
  onSaveRecordSummary: () => void;
  onRevertRecordSummary: () => void;
  onApproveOne: (proposalId: string) => void;
  onRejectProposal: (proposalId: string) => void;
  onRemoveProposal: (proposalId: string) => void;
  onApproveAllForRecord: () => void;
};

export type ChatbotLayoutProps = {
  route: Route;
  chatbots: Chatbot[];
  selectedChatbot: Chatbot;
  knowledgeDbs: KnowledgeDb[];
  knowledges: Knowledge[];
  documents: DocumentSummary[];
  chatbotInput: string;
  setChatbotInput: (value: string) => void;
  chatbotMessages: ChatMessage[];
  navigate: (path: string) => void;
  onCreateChatbot: () => void;
  onSendChatbotMessage: () => void;
  onUpdateReferences: (value: Partial<Chatbot>) => void;
};
