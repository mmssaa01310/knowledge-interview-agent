# AI Interviewer Constitution

## Core Principles

### I. User-Scoped Security First
Every persisted entity MUST carry `tenantId`, `createdByUserId`, and `updatedByUserId`, and every read/write path MUST enforce authenticated identity plus role-based authorization. Elasticsearch queries, SSE streams, approval actions, and document access MUST all filter by tenant and user access scope at the API layer.

### II. Human Approval Gates AI Knowledge
AI-generated outputs are drafts, never truth. Any AI-proposed field value, summary, or knowledge record MUST remain `draft` or `needs_review` until an authorized human explicitly approves it. Unapproved knowledge MUST NOT be used as RAG evidence, exported externally, or presented as finalized structured knowledge.

### III. Contracts Before Implementation
Every feature starts from explicit artifacts in `specs/<feature>/`: user stories in `spec.md`, architecture and constraints in `plan.md`, data definitions in `data-model.md`, and API/behavior contracts in `contracts/`. Backend schemas, frontend forms, and worker payloads MUST derive from these artifacts to keep web, API, and worker behavior aligned.

### IV. Async and Streaming by Design
User-facing requests MUST stay responsive. Long-running AI and document-processing work MUST use streaming or asynchronous execution where supported, and no blocking heavy processing should be added to synchronous REST handlers.

### V. Auditability and Operational Clarity
Security-sensitive and business-critical actions MUST leave traceable records. Authentication events, CRUD operations, AI proposal generation and edits, approvals, rejections, ingestion state changes, and export attempts MUST generate structured audit logs. Logs and traces MUST never expose secrets or raw tokens, and failures MUST surface actionable status for operators and end users.

## Platform Constraints

- UI language is Japanese by default; data model and prompts must remain extensible for multilingual content.
- Frontend stack is React + Vite + TypeScript. The application is run locally with Docker Compose.
- Backend stack is FastAPI + Pydantic with PostgreSQL persistence and SSE support. Elasticsearch is available for document search where configured.
- PostgreSQL is the primary persistence layer. Designs should reuse the repository and service patterns already in place.
- AI text and live audio requests use the OpenAI API with the application-wide OpenAI API key configured by an application administrator.

## Development Workflow and Quality Gates

- Work proceeds in Spec Kit order: constitution, specification, plan, tasks, then implementation.
- Each feature spec MUST define independently testable user stories with acceptance scenarios before coding starts.
- Plan artifacts MUST capture architecture, data model, API contracts, quickstart verification, and constitution checks before implementation tasks are accepted.
- Tests are mandatory for authorization, approval workflows, ingestion state transitions, and streaming/API contracts. Other coverage should focus on the highest-risk paths first.
- UI and API changes that affect cross-app contracts MUST update shared types or documented schemas in the same feature.
- No feature is complete until quickstart steps and core acceptance criteria can be exercised in a local or mocked environment.

## Governance

This constitution overrides ad hoc implementation preferences. Any exception requires a documented rationale in the relevant `plan.md`, including the simpler alternative that was rejected and the operational risk introduced. Reviews must verify compliance with the approval gate, access control, and audit logging requirements before merge.

**Version**: 1.0.0 | **Ratified**: 2026-07-07 | **Last Amended**: 2026-11-07
