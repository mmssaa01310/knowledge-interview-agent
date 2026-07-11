# Implementation Plan: AI Interview Knowledge Capture Current Baseline

**Branch**: `001-ai-interview-knowledge-app` | **Date**: 2026-07-11 | **Spec**: [`spec.md`](./spec.md)

**Input**: Current implementation from `/app/web`, `/app/api`, `/app/worker`

## Summary

現在のコードベースは、ナレッジDB管理、ナレッジ管理、ヒアリング項目管理、記録管理、モックSSE、提案承認、文書メタデータ管理、既読状態管理、ローカルチャットボット設定、参照チャットを含む単一テナント寄りの開発ベースラインである。認証は開発トークン、永続化はインメモリ、文書取り込みとAI応答は一部モックまたは簡易実装となっている。

## Technical Context

**Language/Version**: TypeScript 5.x, Python 3.10

**Primary Dependencies**: React, Vite, FastAPI, Pydantic v2, boto3, httpx, uvicorn

**Storage**: In-memory repository store in API process

**Testing**: pytest contract tests for API basics, TypeScript compile and Vite build for web

**Target Platform**: Local development first, Docker-based dev startup under `infra/docker-compose.yml`

**Constraints**: Japanese-first UI, no DB persistence across API restarts, dev-token auth only, chatbot settings are local-only, document registration stores metadata only

## Project Structure

```text
app/
├── web/
│   ├── src/
│   │   ├── app/
│   │   ├── components/
│   │   ├── features/
│   │   ├── layouts/
│   │   ├── lib/
│   │   ├── pages/
│   │   ├── providers/
│   │   ├── routes/
│   │   └── types/
│   ├── Dockerfile
│   └── Dockerfile.dev
├── api/
│   ├── src/ai_interviewer_api/
│   │   ├── auth/
│   │   ├── core/
│   │   ├── models/
│   │   ├── repositories/
│   │   ├── routers/
│   │   ├── schemas/
│   │   └── services/
│   ├── tests/
│   ├── Dockerfile
│   └── Dockerfile.dev
└── worker/
    ├── src/ai_interviewer_worker/
    ├── tests/
    └── Dockerfile
```

## Implemented Scope

### Phase A: Foundation

- 開発トークン認証
- テナント付き BaseEntity
- インメモリストア
- 監査ログ保存

### Phase B: Knowledge Workspace

- ナレッジDB CRUD
- ナレッジ CRUD
- ヒアリング項目 CRUD
- 項目の固定生成と提案生成

### Phase C: Interview Workflow

- 記録 CRUD
- 記録メッセージ送信
- モック SSE ストリーム
- 提案一覧
- 個別承認、記録内全承認、一覧一括承認
- ナレッジ記録の要約ドラフト

### Phase D: Documents and Reference Chat

- 文書メタデータ登録
- 文書状態一覧
- 既読更新と確認済み更新
- ローカルチャットボット設定
- 参照チャット回答と citations

### Phase E: Development Runtime

- `Dockerfile.dev` ベースのフロント/API 開発起動
- Vite ホットリロード
- Uvicorn `--reload`

## Deferred or Partial Scope

- Cognito 本番JWT検証
- Elasticsearch 永続化
- SQS 実接続
- Worker と API の実ジョブ連携
- チャットボット設定のサーバー永続化
- 文書ファイル本体アップロード
- 承認済みフィールド値の正式ナレッジ反映モデル
