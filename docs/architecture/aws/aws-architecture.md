# aws-architecture.md

## 1. 全体方針

このアプリは、AWS上で動作するAIインタビュー / ナレッジ構造化アプリである。

MVPでは以下を基本構成とする。

* Frontend: ECS Fargate + Nginx
* Backend API: ECS Fargate + FastAPI
* Worker: ECS Fargate Worker
* 認証: Amazon Cognito
* 検索・保存: Elasticsearch / Elastic Cloud on AWS
* AI処理: Amazon Bedrock
* 非同期処理: SQS + ECS Worker

## 2. 全体構成

```text
[Browser]
   |
   | HTTPS / SSE
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
   |       - Cognito JWT verification
   |
   +--> [ECS Fargate Worker Service]
           - Document ingestion
           - Text extraction
           - Chunking
           - Embedding
           - Elasticsearch indexing
           - Future export jobs

[Backend API / Worker]
   |
   +--> [Cognito]
   +--> [Elasticsearch / Elastic Cloud on AWS]
   +--> [Bedrock]
   +--> [SQS]
   +--> [Secrets Manager]
   +--> [CloudWatch Logs]
   +--> [KMS]
```

## 3. ALBルーティング

```text
/       -> Frontend Service
/api/*  -> Backend API Service
```

将来のリアルタイム音声対応時のみ、以下を追加する。

```text
/ws/*   -> Backend API Service
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
* Cognito JWT検証
* 認可チェック
* Elasticsearch検索・保存
* Bedrock呼び出し
* SQSへのジョブ投入

WebSocketは将来のリアルタイム音声用とする。

## 6. Worker Service

WorkerはSQSからジョブを受け取り、非同期処理を実行する。

主な対象は以下。

* ドキュメント取り込み
* テキスト抽出
* チャンク化
* embedding
* Elasticsearch index登録
* 将来の外部DB送信

重い処理はAPIリクエスト内で完結させず、SQS + Worker に逃がす。

## 7. Cognito

* Cognitoでログインする。
* FrontendはJWTを取得し、APIリクエスト時に送信する。
* Backend APIではCognito JWTを検証する。
* Cognitoの `sub` をアプリ上のユーザーIDとして扱う。
* 認証なしAPIを作らない。

## 8. 認証・認可

* 保存データにはCognitoユーザーIDを含める。
* 認可チェックを省略しない。
* ユーザーが参照権限を持つデータだけを検索・表示する。
* 権限フィルタなしでElasticsearch検索を行わない。

ロール別ワークスペース、Recordの担当者、承認操作の権限は、[利用者ワークスペースと認可アーキテクチャ](../access-control.md)に従う。

## 9. Elasticsearch / Elastic Cloud on AWS

Elasticsearchを中心に検索・保存を行う。

MVPでは以下への置き換えを行わない。

* PostgreSQL
* DynamoDB
* OpenSearch

検索クエリはRepository層に閉じ込め、UIやAPI routerに直接書かない。

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
* Elasticsearch index登録
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
* Aurora PostgreSQL
* DynamoDB
* OpenSearchへの置き換え

ただし、ファイル原本保存が明示された場合のみ、S3またはEFSの追加を検討してよい。
