# Tasks: AI Interview Knowledge Capture Current Baseline

**Input**: Current implementation in `/app/web`, `/app/api`, `/app/worker`

## Implemented Foundation

- [x] T001 開発トークン認証を実装する
- [x] T002 テナント付き `BaseEntity` を実装する
- [x] T003 API のインメモリストアを実装する
- [x] T004 監査ログ保存処理を実装する

## Implemented Knowledge Workspace

- [x] T005 ナレッジDB CRUD を実装する
- [x] T006 ナレッジ CRUD を実装する
- [x] T007 ヒアリング項目 CRUD を実装する
- [x] T008 固定候補の項目生成 API を実装する
- [x] T009 AI支援の項目提案 API を実装する
- [x] T010 Web のナレッジ一覧、概要、設定、記録、文書画面を実装する

## Implemented Interview Workflow

- [x] T011 記録 CRUD を実装する
- [x] T012 記録メッセージ送信でモック提案を作成する
- [x] T013 記録ストリーム SSE を実装する
- [x] T014 提案一覧 API を実装する
- [x] T015 個別承認 API を実装する
- [x] T016 記録内全承認 API を実装する
- [x] T017 記録一覧一括承認 API を実装する
- [x] T018 ナレッジ単位の要約ドラフト生成を実装する

## Implemented Documents and Chat

- [x] T019 文書メタデータ登録 API を実装する
- [x] T020 文書一覧 API を実装する
- [x] T021 文書既読更新 API を実装する
- [x] T022 文書確認済み API を実装する
- [x] T023 ローカルチャットボット状態管理を実装する
- [x] T024 参照チャット API を実装する
- [x] T025 Web のチャットボット概要、参照設定、チャット画面を実装する

## Implemented Development Runtime

- [x] T026 `Dockerfile.dev` を追加する
- [x] T027 `infra/docker-compose.yml` に Web/API 開発起動を追加する
- [x] T028 Vite ホットリロードと Uvicorn `--reload` を有効にする

## Remaining Gaps

- [ ] T029 Cognito 本番 JWT 検証へ置き換える
- [ ] T030 インメモリストアを Elasticsearch 永続化へ置き換える
- [ ] T031 文書本体アップロードと実ファイル処理を実装する
- [ ] T032 Worker と API を SQS 実接続で連携する
- [ ] T033 チャットボット設定のサーバー永続化を実装する
- [ ] T034 承認済み項目値の正式ナレッジ反映モデルを実装する
