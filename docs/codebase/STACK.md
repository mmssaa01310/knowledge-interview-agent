# 技術スタック

## 1. 実行環境

| 領域 | 実装 | 根拠 |
| --- | --- | --- |
| Web | React 18、TypeScript、Vite 5 | `app/web/package.json` |
| API | Python 3.12以上、FastAPI、Uvicorn | `app/api/pyproject.toml` |
| AI API | OpenAI Python SDK、Responses API、GPT Live | `app/api/pyproject.toml`、`services/live_session.py` |
| 保存 | PostgreSQL 16、psycopg 3、JSONB | `infra/docker-compose.yml`、`app/api/pyproject.toml` |
| 依存管理 | pnpm workspace、uv | `package.json`、`pnpm-workspace.yaml`、各`pyproject.toml` |

## 2. 主な依存

| 依存 | 役割 |
| --- | --- |
| `react` / `react-dom` | Web UI |
| `i18next` / `react-i18next` | UI多言語 |
| `fastapi` / `uvicorn` | APIサーバー |
| `openai` | Responses APIとGPT Liveのサーバー連携 |
| `cryptography` | アプリ設定のAES-GCM暗号化 |
| `psycopg[binary]` | PostgreSQL接続 |

## 3. 主なコマンド

```bash
pnpm --dir app/web lint
pnpm --dir app/web check:i18n
pnpm --dir app/web build
cd app/api && uv run pytest
cd app/worker && uv run pytest
uv run --group dev mkdocs build --strict
```

## 4. 設定

- OpenAI Project APIキーは管理画面から登録し、Backendで暗号化保存する。
- `OPENAI_SETTINGS_ENCRYPTION_KEY`はDB外で管理する暗号化キーである。
- 保存先は`DATABASE_URL`で設定し、既定はComposeのPostgreSQLである。
- ComposeはWeb、API、PostgreSQLを起動する。
