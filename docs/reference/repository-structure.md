# リポジトリ構成

## 1. 主な構成

| パス | 役割 |
| --- | --- |
| `app/web/` | React/Viteの画面、UIローカライズ、GPT Live WebRTCクライアント |
| `app/api/` | FastAPI、認証・認可、OpenAI APIアダプター、業務ロジック、Repository |
| `app/worker/` | 文書取り込み状態を返す最小Workerサンプル |
| `packages/shared-types/` | WebとBackendで共有する型 |
| `infra/docker-compose.yml` | ローカルWeb・API・PostgreSQLの起動構成 |
| `infra/postgres/` | PostgreSQL初期スキーマ |
| `docs/` | 仕様、設計、ガイド、実装リファレンス |
| `.github/skills/` | リポジトリ共有Agent Skills |
| `specs/` | 進行中の変更単位の作業仕様 |

## 2. Backend API

`app/api/src/ai_interviewer_api/`配下で責務を分ける。

- `routers/`: HTTP境界と認証・認可依存性
- `schemas/`: リクエスト・レスポンス型
- `services/`: 業務ロジック、状態遷移、検索
- `repositories/`: PostgreSQLとStore契約
- `models/`: ドメイン型
- `agents/`: OpenAI Responses APIを使う質問設計・構造化インタビュー

OpenAI SDK呼び出しはProviderに集約する。APIキーはシステム設定から取得し、AES-GCMで暗号化してPostgreSQLに保存する。

## 3. 音声経路

音声対話は`app/web/src/features/realtime-voice/`からGPT LiveへWebRTCで接続する。Backendは`/api/live`のセッション作成、委譲評価、認可、状態保存を担当する。独立した音声Gatewayや音声用Pythonサービスは置かない。

## 4. ローカル構成

Docker ComposeはWeb、API、PostgreSQLを起動する。WorkerはComposeに含まれない。Frontend依存はpnpm workspace、Python依存はuvで管理する。
