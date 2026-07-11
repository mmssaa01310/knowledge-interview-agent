# Quickstart: AI Interview Knowledge Capture Current Baseline

## Goal

ローカル開発で Web、API、Worker の現在実装を起動し、ナレッジ管理、記録、モックSSE、提案承認、文書状態、参照チャットを確認できること。

## Prerequisites

- Node.js 22+
- pnpm 11.x
- Python 3.10+
- `uv`
- Docker Desktop または互換コンテナランタイム

## Run Services

### Option 1: Local processes

```bash
# Terminal 1
cd app/api
uv run uvicorn ai_interviewer_api.main:app --reload --port 8000

# Terminal 2
cd app/web
pnpm dev --host 0.0.0.0

# Terminal 3
cd app/worker
uv run python -m ai_interviewer_worker.main
```

### Option 2: Docker Compose

```bash
docker compose -f infra/docker-compose.yml up --build
```

## Development Authentication

Use one of the following headers:

```text
x-dev-token: dev-admin
x-dev-token: dev-manager
x-dev-token: dev-interviewer
x-dev-token: dev-viewer
```

The web frontend uses `x-dev-token: dev-manager` by default for API calls.

## Validation Flow

1. Open `http://localhost:5173`.
2. Create a knowledge DB.
3. Create a knowledge under that DB.
4. Add knowledge fields or run field generation.
5. Create a record and send a message.
6. Confirm SSE events from `/api/records/{recordId}/stream`.
7. Approve a proposal individually or with record bulk approval.
8. Register a document and confirm it transitions to `queued`.
9. Update document read status and acknowledge status.
10. Create a local chatbot, set references, and send a reference chat message.
