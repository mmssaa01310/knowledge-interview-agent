# コードベース構成

## 1. 主な構成

| パス | 役割 |
| --- | --- |
| `app/web/` | React/ViteのUI、GPT Liveクライアント |
| `app/api/` | REST/SSE API、認可、OpenAI呼び出し、Repository |
| `app/worker/` | 文書取り込み状態の最小Workerサンプル |
| `packages/shared-types/` | 共有型 |
| `infra/` | ComposeとPostgreSQL初期スキーマ |
| `docs/` | 仕様と設計ドキュメント |

## 2. 起動点

- Web: `app/web/src/main.tsx` → `app/web/src/app/App.tsx`
- API: `app/api/src/ai_interviewer_api/main.py`
- Worker: `app/worker/src/ai_interviewer_worker/main.py`
- APIルート集約: `app/api/src/ai_interviewer_api/routers/routes.py`

ComposeはWeb、API、PostgreSQLを定義する。GPT Live音声クライアントはWebからOpenAIへWebRTC接続する。

## 3. 主な責務境界

| 境界 | 置くもの | 置かないもの |
| --- | --- | --- |
| `app/web/src/pages` | ページ組み立てと表示 | Backendの業務判定・認可保証 |
| `app/web/src/features` | 機能UI、hooks、APIクライアント | Backendの状態遷移複製 |
| `app/api/.../routers` | HTTP入出力と依存注入 | SQL、OpenAI SDKの詳細 |
| `app/api/.../services` | 業務処理、状態遷移、認可後の操作 | HTTPルート定義 |
| `app/api/.../agents` | OpenAI SDK呼び出し、Structured Output検証 | Repositoryへの直接書き込み |
| `app/api/.../repositories` | PostgreSQL Store | UI表示の判断 |

## 根拠

- `app/web/src/main.tsx`
- `app/api/src/ai_interviewer_api/main.py`
- `app/api/src/ai_interviewer_api/routers/routes.py`
- `infra/docker-compose.yml`
