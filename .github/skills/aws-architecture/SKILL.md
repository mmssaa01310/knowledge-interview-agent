---
name: aws-architecture
description: AWS上でAIインタビューアプリの構成を設計・変更するときに使用する。
---

# Skill: aws-architecture

## 目的

AWS上でAIインタビューアプリを動かすための構成を維持する。

## MVP構成

- ALB
- ECS Fargate Frontend: Nginx + React/Vite
- ECS Fargate API: FastAPI
- ECS Fargate Worker: SQS非同期処理
- Cognito
- Elasticsearch / Elastic Cloud on AWS
- Bedrock
- SQS
- Secrets Manager
- CloudWatch Logs
- IAM
- KMS

## MVPで使わない

- CloudFront
- S3フロント配信
- EventBridge
- Aurora PostgreSQL
- DynamoDB
- Next.js

## 注意

- EventBridgeは定期同期や再処理が必要になった将来フェーズで追加する。
- ドキュメント原本保存が必要になった場合のみS3またはEFSを検討する。
- APIとWorkerはSecrets ManagerからElasticsearchやBedrock設定を取得する。
