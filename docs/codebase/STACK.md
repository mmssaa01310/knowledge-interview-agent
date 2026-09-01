# 技術スタック

## 1. 実行環境

| 領域 | 実装 | 根拠 |
| --- | --- | --- |
| Web | React 18、TypeScript、Vite 5 | `app/web/package.json` |
| API | Python 3.12以上、FastAPI、Uvicorn | `app/api/pyproject.toml` |
| Voice | Python 3.12以上、FastAPI、aiortc / PyAV | `app/voice/pyproject.toml` |
| Worker | Python 3.12以上の最小Worker | `app/worker/pyproject.toml` |
| 保存 | PostgreSQL 16、psycopg 3、JSONB | `infra/docker-compose.yml`、`app/api/pyproject.toml` |
| 依存管理 | pnpm 11 workspace、uv | `package.json`、`pnpm-workspace.yaml`、各`pyproject.toml` |

## 2. 主な本番依存

| 依存 | 役割 | 根拠 |
| --- | --- | --- |
| `react` / `react-dom` | Web UI | `app/web/package.json` |
| `i18next` / `react-i18next` | UI多言語 | `app/web/package.json` |
| `driver.js` | 画面上の操作ガイド | `app/web/package.json` |
| `@xyflow/react` | 処理フロー表示 | `app/web/package.json` |
| `fastapi` / `uvicorn` | API・VoiceのHTTPサーバー | `app/api/pyproject.toml`、`app/voice/pyproject.toml` |
| `psycopg[binary]` | APIからPostgreSQLへの接続 | `app/api/pyproject.toml` |
| `boto3` | APIのBedrock呼び出し | `app/api/pyproject.toml` |
| `aiortc` / `av` / Transcribe SDK | VoiceのWebRTC・音声入出力 | `app/voice/pyproject.toml` |

Workerには外部SDK依存が定義されていない。

## 3. 開発ツール

| ツール | 用途 | 根拠 |
| --- | --- | --- |
| TypeScript compiler | Webの型検査とVite Build | `app/web/package.json` |
| pytest | API・Voice・Workerのテスト | 各`pyproject.toml`、`*/tests/` |
| Node test runner | Webの単体テスト | `app/web/tests/answerVisibility.test.mjs` |
| MkDocs Material | ドキュメントサイト | `pyproject.toml`、`mkdocs.yml` |
| Docker Compose | ローカル複数サービス起動 | `infra/docker-compose.yml` |

## 4. 主なコマンド

```bash
# Web
pnpm --dir app/web lint
pnpm --dir app/web check:i18n
pnpm --dir app/web build
node --test app/web/tests/*.test.mjs

# Python services
cd app/api && uv run pytest
cd app/voice && uv run pytest
cd app/worker && uv run pytest

# Documentation
uv run --group dev mkdocs build --strict
```

## 5. 設定

* WebのAPI/Voice proxyは`app/web/vite.config.ts`で設定する。
* APIの保存先は`DATABASE_URL`で、既定はローカルPostgreSQLである。
* ComposeではWeb、API、Voice、PostgreSQLを起動する。WorkerはComposeサービスに含まれない。
* AWS認証情報は環境変数とホストの`~/.aws`読み取り専用マウントを使用する。

## 根拠

* `app/web/package.json`
* `app/api/pyproject.toml`
* `app/voice/pyproject.toml`
* `app/worker/pyproject.toml`
* `infra/docker-compose.yml`
* `mkdocs.yml`
