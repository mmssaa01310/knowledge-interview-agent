# AIインタビュー

現場の人との対話から得た知恵を、チームで確認・活用する知識へつなぐWebアプリです。

## Structure

```text
app/
├── web/      # React + Vite frontend
├── api/      # FastAPI backend
└── worker/   # document ingestion sample worker
packages/
└── shared-types/
infra/
├── docker-compose.yml
└── postgres/ # local PostgreSQL setup
```

## Quick Start

通常のローカル開発はDocker Composeを使います。

```bash
cp .env.example .env
# OPENAI_SETTINGS_ENCRYPTION_KEYにBase64形式の32-byte鍵を設定
docker compose --env-file .env -f infra/docker-compose.yml up --build
```

Webは`http://localhost:5173`、APIは`http://localhost:8001`で利用できます。ComposeはWeb、FastAPI、PostgreSQLを起動します。初回ログイン後、アプリ全体設定画面からOpenAI Project APIキーを登録してください。キーはAPIが暗号化してPostgreSQLに保存し、テキスト生成、質問設計、GPT Live音声インタビューに使用します。

APIキーの暗号化鍵はOpenAI APIキーとは別の値です。暗号化鍵はAPIサーバーの環境設定だけに置き、OpenAI APIキーを`.env`や`VITE_*`へ記載しないでください。

直接起動する場合:

- Web: `cd app/web && pnpm install && pnpm dev`
- API: `cd app/api && uv sync && uv run uvicorn ai_interviewer_api.main:app --reload --port 8001`
- Worker sample: `cd app/worker && uv sync && uv run python -m ai_interviewer_worker.main`（Composeでは起動しません）

Viteは`/api`をFastAPIへproxyします。データベース接続先は`DATABASE_URL`で変更できます。

事前知識の検索先はPostgreSQLが既定です。Elastic Cloudを利用する場合は`.env`へ`DOCUMENT_KNOWLEDGE_BACKEND=elasticsearch`と接続情報を設定できます。AI処理は管理画面に登録したOpenAI Project APIキーを使い、キーはBackendで暗号化して保存します。

## Documentation

ドキュメントサイトはMkDocsで生成します。`docs/`全体が公開対象です。

```bash
uv run --group dev mkdocs serve
uv run --group dev mkdocs build --strict
```

仕様、現行実装、開発手順の入口は[docs/index.md](docs/index.md)です。
