# KIKIORI

「聞く＋織る」。会話から知識を引き出し、知識として織り上げるAIインタビュー / ナレッジ構造化アプリです。

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
- API: `cd app/api && uv sync && uv run uvicorn ai_interviewer_api.main:app --reload --port 8001`
- Voice: `cd app/voice && uv sync && VOICE_API_BASE_URL=http://127.0.0.1:8001 uv run uvicorn ai_interviewer_voice.main:app --reload --port 8010`
- Worker: `cd app/worker && uv sync && uv run python -m ai_interviewer_worker.main`

通常のローカル開発はDocker Composeを推奨します。ブラウザからは `http://localhost:5173` だけを開きます。
Vite dev server が `/api` を `app/api`、`/voice` を `app/voice` へproxyするため、FrontendコードでAPI/Voiceのポートを直接指定しません。

## Docker Compose (Development)

- 初回ビルド込み起動: `docker compose -f infra/docker-compose.yml up --build`
- バックグラウンド起動: `docker compose -f infra/docker-compose.yml up -d --build`
- 停止: `docker compose -f infra/docker-compose.yml down`

開発用 compose は以下をまとめて起動します。

- Frontend: Vite 開発サーバー `http://localhost:5173`
- API: FastAPI + `uvicorn --reload` `http://localhost:8001`
- Voice: FastAPI + `uvicorn --reload` `http://localhost:8010`

ソースコードは bind mount されるため、`app/web`、`app/api`、`app/voice` の変更はコンテナ内でホットリロードされます。依存関係は dev 用イメージ build 時に入るため、通常の再起動で毎回 `pnpm install` / `pip install` は走りません。
既定ポートは`5173`（Web）、`8001`（API）、`8010`（Voice）で固定しています。ソースコードの変更は各開発サーバーのホットリロードで反映されます。別のポートが必要な場合だけ、`WEB_PORT` / `API_PORT`を明示的に変更してください。
