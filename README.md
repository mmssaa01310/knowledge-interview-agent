# AI Interviewer

製造現場の暗黙知を AI インタビューで構造化し、承認済みナレッジとして蓄積・再利用する MVP の雛形です。

## Structure

```text
app/
├── web/      # React + Vite frontend
├── api/      # FastAPI backend
└── worker/   # SQS/document ingestion worker
packages/
└── shared-types/
infra/
└── cdk/
```

## Quick Start

- Web: `cd app/web && pnpm install && pnpm dev`
- API: `cd app/api && uv sync && uv run uvicorn ai_interviewer_api.main:app --reload --port 8000`
- Voice: `cd app/voice && uv sync && VOICE_API_BASE_URL=http://127.0.0.1:8000 uv run uvicorn ai_interviewer_voice.main:app --reload --port 8010`
- Worker: `cd app/worker && uv sync && uv run python -m ai_interviewer_worker.main`

通常のローカル開発はDocker Composeを推奨します。ブラウザからは `http://localhost:5173` だけを開きます。
Vite dev server が `/api` を `app/api`、`/voice` を `app/voice` へproxyするため、FrontendコードでAPI/Voiceのポートを直接指定しません。

## Docker Compose (Development)

- 初回ビルド込み起動: `docker compose -f infra/docker-compose.yml up --build`
- バックグラウンド起動: `docker compose -f infra/docker-compose.yml up -d --build`
- 停止: `docker compose -f infra/docker-compose.yml down`
- ポート競合時: `WEB_PORT=5174 API_PORT=8001 docker compose -f infra/docker-compose.yml up`

開発用 compose は以下をまとめて起動します。

- Frontend: Vite 開発サーバー `http://localhost:5173`
- API: FastAPI + `uvicorn --reload` `http://localhost:8000`
- Voice: FastAPI + `uvicorn --reload` `http://localhost:8010`

ソースコードは bind mount されるため、`app/web`、`app/api`、`app/voice` の変更はコンテナ内でホットリロードされます。依存関係は dev 用イメージ build 時に入るため、通常の再起動で毎回 `pnpm install` / `pip install` は走りません。
`5173`、`8000`、`8010` が既に使われている場合は、`WEB_PORT` / `API_PORT` / `VOICE_PORT` で公開ポートを切り替えられます。ブラウザは公開ポートを切り替えたWebのURLだけを開きます。
