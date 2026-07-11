export const routePaths = {
  login: "/login",
  settings: "/settings",
  knowledgeDbs: "/knowledge-dbs",
  knowledgeDb: (knowledgeDbId: string) => `/knowledge-dbs/${knowledgeDbId}`,
  knowledgeNew: (knowledgeDbId: string) => `/knowledge-dbs/${knowledgeDbId}/knowledges/new`,
  knowledgeOverview: (knowledgeDbId: string, knowledgeId: string) => `/knowledge-dbs/${knowledgeDbId}/knowledges/${knowledgeId}`,
  knowledgeRecords: (knowledgeDbId: string, knowledgeId: string) => `/knowledge-dbs/${knowledgeDbId}/knowledges/${knowledgeId}/records`,
  interviewRecord: (knowledgeDbId: string, knowledgeId: string, recordId: string) => `/knowledge-dbs/${knowledgeDbId}/knowledges/${knowledgeId}/records/${recordId}`,
  knowledgeSettings: (knowledgeDbId: string, knowledgeId: string) => `/knowledge-dbs/${knowledgeDbId}/knowledges/${knowledgeId}/settings`,
  knowledgeDocuments: (knowledgeDbId: string, knowledgeId: string) => `/knowledge-dbs/${knowledgeDbId}/knowledges/${knowledgeId}/documents`,
  chatbots: "/chatbots",
  chatbotOverview: (chatbotId: string) => `/chatbots/${chatbotId}`,
  chatbotChat: (chatbotId: string) => `/chatbots/${chatbotId}/chat`,
  chatbotReferences: (chatbotId: string) => `/chatbots/${chatbotId}/references`
};
