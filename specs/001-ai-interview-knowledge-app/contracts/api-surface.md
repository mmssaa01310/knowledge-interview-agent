# API Surface Contract: AI Interview Knowledge Capture Current Baseline

## Authentication

- `GET /api/health`
- `GET /api/me`
  - Requires `x-dev-token` or a Bearer token matching one of the development tokens.

## Knowledge DB

- `GET /api/knowledge-dbs`
- `POST /api/knowledge-dbs`
- `GET /api/knowledge-dbs/{knowledgeDbId}`
- `PATCH /api/knowledge-dbs/{knowledgeDbId}`
- `DELETE /api/knowledge-dbs/{knowledgeDbId}`

## Knowledge

- `GET /api/knowledge-dbs/{knowledgeDbId}/knowledges`
- `POST /api/knowledge-dbs/{knowledgeDbId}/knowledges`
- `GET /api/knowledges/{knowledgeId}`
- `PATCH /api/knowledges/{knowledgeId}`
- `DELETE /api/knowledges/{knowledgeId}`
- `POST /api/knowledges/{knowledgeId}/record-summary-draft`

## Knowledge Fields

- `GET /api/knowledges/{knowledgeId}/fields`
- `POST /api/knowledges/{knowledgeId}/fields`
- `PATCH /api/knowledge-fields/{fieldId}`
- `DELETE /api/knowledge-fields/{fieldId}`
- `POST /api/knowledges/{knowledgeId}/generate-fields`
- `POST /api/knowledges/{knowledgeId}/field-suggestions`

## Records

- `GET /api/knowledges/{knowledgeId}/records`
- `POST /api/knowledges/{knowledgeId}/records`
- `GET /api/records/{recordId}`
- `PATCH /api/records/{recordId}`
- `DELETE /api/records/{recordId}`
- `POST /api/records/{recordId}/messages`
- `POST /api/records/{recordId}/summary-proposals`
- `GET /api/records/{recordId}/stream`

Expected SSE sequence:

```text
event: stream_start
data: {}

event: delta
data: {"text":"..."}

event: delta
data: {"text":"..."}

event: stream_end
data: {}

event: proposal_created
data: {"proposalId":"..."}
```

## Proposals and Approvals

- `GET /api/records/{recordId}/proposals`
- `POST /api/proposals/{proposalId}/approve`
- `POST /api/records/{recordId}/approve-all-proposals`
- `POST /api/records/bulk-approve`

## Documents

- `GET /api/knowledges/{knowledgeId}/documents`
- `POST /api/knowledges/{knowledgeId}/documents`
- `POST /api/documents/{documentId}/read`
- `POST /api/documents/{documentId}/acknowledge`

## Reference Chat

- `POST /api/chats`
- `POST /api/chats/{chatId}/messages`
