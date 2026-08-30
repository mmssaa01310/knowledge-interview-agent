# コードベース構成

## 1. 主な構成

| パス | 役割 | 根拠 |
| --- | --- | --- |
| `app/web/` | React/Viteの画面、i18n、操作ガイド、Voice UI | `app/web/src/` |
| `app/api/` | REST/SSE API、認可、AI処理、Repository | `app/api/src/ai_interviewer_api/` |
| `app/voice/` | WebRTCと音声Runtime。API内部HTTPを呼び出す | `app/voice/src/ai_interviewer_voice/` |
| `app/worker/` | 文書取り込み状態を返す最小Worker | `app/worker/src/ai_interviewer_worker/` |
| `packages/shared-types/` | Webで共有するTypeScript型 | `packages/shared-types/src/` |
| `infra/` | Docker Compose、PostgreSQL初期スキーマ、CDK雛形 | `infra/docker-compose.yml`、`infra/postgres/`、`infra/cdk/` |
| `docs/` | 仕様、現行実装、設計、手順、コードベース案内 | `docs/` |

## 2. 起動点

* Web: `app/web/src/main.tsx` → `app/web/src/app/App.tsx`
* API: `app/api/src/ai_interviewer_api/main.py`
* Voice: `app/voice/src/ai_interviewer_voice/main.py`
* Worker: `app/worker/src/ai_interviewer_worker/main.py`
* APIルート集約: `app/api/src/ai_interviewer_api/routers/routes.py`

Composeは`infra/docker-compose.yml`でWeb、API、Voice、PostgreSQLを定義する。

## 3. 主な責務境界

| 境界 | 置くもの | 置かないもの |
| --- | --- | --- |
| `app/web/src/pages` | ページ組み立てと表示 | APIの業務判定、認可保証 |
| `app/web/src/features` | 機能単位のUI、クライアント処理 | Backendの状態遷移複製 |
| `app/api/src/ai_interviewer_api/routers` | HTTP request/responseと依存注入 | SQL、AI Providerの詳細 |
| `app/api/src/ai_interviewer_api/services` | 業務処理、状態遷移、認可後の操作 | HTTPルート定義 |
| `app/api/src/ai_interviewer_api/repositories` | PostgreSQL Storeとスコープ済み取得 | UI表示の判断 |
| `app/voice` | WebRTC、音声入出力、Runtime変換 | AI評価、RAG、InterviewState更新 |

## 4. 命名と構成

* React ComponentはPascalCaseの`.tsx`、機能モジュールはcamelCaseの`.ts`が中心である。
* Pythonはsnake_caseのモジュールと関数、PascalCaseの型を使う。
* Webは`features/`を機能単位、APIは`routers` / `services` / `repositories`を責務単位に分ける。
* Webは相対importを使用し、TypeScriptの`paths` aliasは設定されていない。

## 根拠

* `app/web/src/main.tsx`
* `app/api/src/ai_interviewer_api/main.py`
* `app/api/src/ai_interviewer_api/routers/routes.py`
* `app/voice/src/ai_interviewer_voice/main.py`
* `app/worker/src/ai_interviewer_worker/main.py`
* `infra/docker-compose.yml`
