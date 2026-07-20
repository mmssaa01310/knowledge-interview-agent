# technology-stack.md

## 1. Frontend

* React
* Vite
* TypeScript
* Tailwind CSS
* shadcn/ui
* Radix UI
* lucide-react
* TanStack Query
* React Hook Form
* Zod
* Zustand または Jotai

## 2. Frontend Hosting

* ECS Fargate
* Nginx
* React/Vite の `dist` を配信

MVPでは以下を使わない。

* Next.js
* CloudFront
* S3フロント配信

## 3. Backend API

* Python
* uv
* FastAPI
* Pydantic
* Elasticsearch Python client
* boto3
* Cognito JWT検証
* SSE

WebSocketは将来のリアルタイム音声用とする。

## 4. Worker

* Python
* uv
* ECS Worker
* SQS
* Elasticsearch Python client
* boto3
* Bedrock
* Pydantic

Workerは、ドキュメント取り込みや将来の外部DB送信など、APIリクエスト内で完結させない非同期処理を担当する。

## 5. AWS

* ECS Fargate
* ALB
* Cognito
* Elasticsearch / Elastic Cloud on AWS
* Bedrock
* SQS
* ECS Worker
* Secrets Manager
* CloudWatch Logs
* IAM
* KMS

## 6. MVPで使わないもの

MVPでは以下を追加しない。

* EventBridge
* Aurora PostgreSQL
* DynamoDB
* OpenSearchへの置き換え

ただし、ファイル原本保存が明示された場合のみ、S3またはEFSの追加を検討してよい。
