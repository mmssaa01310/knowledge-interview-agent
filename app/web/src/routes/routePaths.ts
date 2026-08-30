export const routePaths = {
  login: "/login",
  help: "/help",
  dashboard: "/dashboard",
  settings: "/settings",
  knowledgeDbs: "/knowledge-dbs",
  knowledgeDb: (knowledgeDbId: string) => `/knowledge-dbs/${knowledgeDbId}`,
  knowledgeNew: (knowledgeDbId: string) => `/knowledge-dbs/${knowledgeDbId}/knowledges/new`,
  knowledgeInterview: (knowledgeDbId: string, knowledgeId: string) => `/knowledge-dbs/${knowledgeDbId}/knowledges/${knowledgeId}/interview`,
  knowledgeRecords: (knowledgeDbId: string, knowledgeId: string) => `/knowledge-dbs/${knowledgeDbId}/knowledges/${knowledgeId}/records`,
  knowledgeRecord: (knowledgeDbId: string, knowledgeId: string, recordId: string) => `/knowledge-dbs/${knowledgeDbId}/knowledges/${knowledgeId}/records/${recordId}`,
  knowledgeSettings: (knowledgeDbId: string, knowledgeId: string) => `/knowledge-dbs/${knowledgeDbId}/knowledges/${knowledgeId}/settings`,
  knowledgeDocuments: (knowledgeDbId: string, knowledgeId: string) => `/knowledge-dbs/${knowledgeDbId}/knowledges/${knowledgeId}/documents`
};
