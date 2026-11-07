# 技術スタック

## Frontend

- React、TypeScript、Vite
- `pnpm` workspace
- UI API通信は同一オリジンの`/api`を使用する。
- 音声はBrowser WebRTCからOpenAI GPT Liveへ直接接続し、APIキーはBackendから公開しない。

## Backend API

- Python 3.12、FastAPI、Pydantic v2
- `uv`で依存を管理する。
- AIのテキスト処理・質問設計・構造化出力はOpenAI Python SDKのResponses APIを使用する。
- 音声インタビューはOpenAI GPT Live (`gpt-live-1`)を使う。
- OpenAI Project APIキーはアプリ全体設定に暗号化保存し、全テナントで共有する。
- APIキー暗号化にはAES-GCMを使用し、鍵を`OPENAI_SETTINGS_ENCRYPTION_KEY`から取得する。

## モデル

- `gpt-6-luna`: 既定のStructured Interview、質問設計モデル
- `gpt-6.1-terra`: 精度を優先するStructured Interview、質問設計モデル
- `gpt-live-1`: 固定の音声会話モデル

Structured OutputはResponses APIのJSON Schemaで要求し、BackendでPydantic検証してから状態へ反映する。

## データとローカル構成

- PostgreSQL: 業務データと暗号化済みOpenAI設定
- Elasticsearch: 任意の文書検索バックエンド
- Docker Compose: Web、FastAPI、PostgreSQLのローカル起動
- `app/worker`: 将来の非同期処理に向けた最小サンプル。現行Composeでは起動しない。

テキストAI処理はOpenAI Responses API、音声対話はGPT Liveを使い、OpenAI Project APIキーはBackendで暗号化して管理する。

## 開発・ドキュメント

```bash
# Web
pnpm --dir app/web dev
pnpm --dir app/web build

# API
cd app/api
uv sync
uv run uvicorn ai_interviewer_api.main:app --reload --port 8001

# Documentation
uv run --group dev mkdocs serve
```
