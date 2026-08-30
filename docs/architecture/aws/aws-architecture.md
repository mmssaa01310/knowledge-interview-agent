# KIKIORI AWSアーキテクチャ

## 1. 全体方針と実装状況

この文書はKIKIORIの目標AWS構成を定義する。現行コードで確認できるローカル実装範囲は[現行実装](../../reference/current-implementation.md)を正本とする。

| 領域 | 現行コードで確認できる状態 | 目標構成 |
| --- | --- | --- |
| Web / API / Voice | Docker Composeで起動 | ECS Fargate各サービス + ALB |
| 保存 | PostgreSQL `entity_store` | マネージドPostgreSQL |
| 認証 | 開発用トークン | 企業IdP（Entra ID候補）のJWT |
| AI | Bedrock呼び出しの実装あり | Bedrock |
| Worker / Queue | Workerはサンプル処理。Composeで未起動 | SQS + ECS Worker |

以降のECS、ALB、企業IdP（Entra ID候補）、音声関連AWSサービス、SQS、Secrets Manager、CloudWatch Logs、KMSに関する記述は、実装済みのローカル構成ではなく目標アーキテクチャである。IdPはまだ確定していない。

MVPでは以下を基本構成とする。

* Frontend: ECS Fargate + Nginx
* Backend API: ECS Fargate + FastAPI
* Voice: ECS Fargate + WebRTC / 音声I/Oサービス
* Worker: ECS Fargate Worker
* 認証: 企業IdP（Entra ID候補。未確定）
* 検索・保存: PostgreSQL
* AI処理: Amazon Bedrock
* 非同期処理: SQS + ECS Worker

## 2. 全体構成

```text
[Browser]
   |
   | HTTPS / SSE / WebRTC
   v
[ALB]
   |
   +--> [ECS Fargate Frontend Service]
   |       - Nginx
   |       - React/Vite dist
   |
   +--> [ECS Fargate Backend API Service]
   |       - FastAPI
   |       - REST API
   |       - SSE streaming
   |       - Enterprise IdP JWT verification (candidate: Entra ID)
   |
   +--> [ECS Fargate Voice Service]
   |       - HTTP SDP signaling
   |       - WebRTC gateway
   |       - Voice Runtime
   |
   +--> [ECS Fargate Worker Service]
           - Document ingestion
           - Text extraction
           - Chunking
           - Embedding
           - PostgreSQL persistence
           - Future export jobs

[Backend API / Voice / Worker]
   |
   +--> [Enterprise IdP (candidate: Entra ID)]
   +--> [PostgreSQL]
   +--> [Bedrock]
   +--> [SQS]
   +--> [Transcribe / Polly / Nova Sonic / KVS TURN]
   +--> [Secrets Manager]
   +--> [CloudWatch Logs]
   +--> [KMS]
```

## 3. ALBルーティング

```text
/       -> Frontend Service
/api/*   -> Backend API Service
/voice/* -> Voice Service
```

将来、Trickle ICEなどでWebSocketを採用する場合のみ、Voice Serviceへ以下を追加する。

```text
/ws/*   -> Voice Service
```

## 4. Frontend Service

FrontendはECS Fargate上でNginxを起動し、React/Viteのビルド成果物を配信する。

```text
Browser
  ↓
ALB
  ↓
ECS Fargate Frontend Service
  - Nginx
  - React/Vite dist
```

MVPでは以下を使わない。

* Next.js
* CloudFront
* S3フロント配信

## 5. Backend API Service

Backend APIはFastAPIで実装する。

主な責務は以下。

* REST API
* SSEストリーミング
* 本番IdP JWT検証（候補: Microsoft Entra ID）
* 認可チェック
* PostgreSQL検索・保存
* Bedrock呼び出し
* SQSへのジョブ投入
* 音声業務処理は行わず、音声サービスとの内部契約を提供する

## 6. Worker Service

WorkerはSQSからジョブを受け取り、非同期処理を実行する。

主な対象は以下。

* ドキュメント取り込み
* テキスト抽出
* チャンク化
* embedding
* PostgreSQLへの取り込み状態保存
* 将来の外部DB送信

重い処理はAPIリクエスト内で完結させず、SQS + Worker に逃がす。

## 7. 本番認証（IdP未確定）

* 企業IdPでログインする。現時点の候補はMicrosoft Entra IDである。
* FrontendはJWTを取得し、APIリクエスト時に送信する。
* Backend APIでは採用したIdPのJWTを検証する。
* IdPのsubjectをアプリ上のユーザーIDとして扱う。
* 認証なしAPIを作らない。

## 8. 認証・認可

* 保存データには認証プロバイダーが発行したユーザーIDを含める。
* 認可チェックを省略しない。
* ユーザーが参照権限を持つデータだけを検索・表示する。
* 権限フィルタなしでPostgreSQL検索を行わない。

ロール別ワークスペース、Recordの担当者、承認操作の権限は、[利用者ワークスペースと認可アーキテクチャ](../access-control.md)に従う。

## 9. PostgreSQL

構造化データの保存と検索はPostgreSQLを正本とする。ローカル開発ではDocker ComposeのPostgreSQLを使用し、現行APIは`DATABASE_URL`で接続する。Workerの`DATABASE_URL`接続は目標構成であり、現行Workerには未実装である。

現在のRepository互換層は、`kikiori.entity_store`へ論理エンティティをJSONB payloadとして保存する。テナントID、論理エンティティ種別、関連IDには検索用の列・インデックスを持たせ、権限スコープを適用したRepository操作だけを許可する。

本番では可用性、バックアップ、接続プール、秘密情報管理を満たすマネージドPostgreSQLへ配置する。検索クエリはRepository層に閉じ込め、UIやAPI routerに直接書かない。

## 10. Bedrock

Bedrockは以下に利用する。

* AIインタビュー
* 構造化抽出
* RAG回答生成
* embeddings

Bedrock呼び出しはService層に閉じ込める。

## 11. SQS

SQSは非同期ジョブの受け渡しに利用する。

主なジョブは以下。

* ドキュメント取り込み
* embedding生成
* PostgreSQLへの取り込み状態保存
* 将来の外部DB送信

MVPでは非同期処理にEventBridgeを使わない。

## 12. Secrets Manager / KMS

* APIキー、接続情報、秘密情報はSecrets Managerで管理する。
* 必要に応じてKMSで暗号化する。
* `.env`、秘密情報、認証情報をコミットしない。

## 13. CloudWatch Logs

CloudWatch Logsにはアプリケーションログを出力する。

ただし、以下をログに出さない。

* JWT
* APIキー
* パスワード
* 個人情報
* 機密情報
* Bedrockへの機密プロンプト全文

## 14. MVPで使わないAWSサービス

MVPでは以下を使わない。

* CloudFront
* S3フロント配信
* EventBridge

ただし、ファイル原本保存が明示された場合のみ、S3またはEFSの追加を検討してよい。
