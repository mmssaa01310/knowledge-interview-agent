# technology-stack.md

## 1. Frontend

* React
* Vite
* TypeScript
* i18next
* react-i18next
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

## 7. 構造化インタビューのLLM

### 7.1 適用範囲

この節は、`docs/spec.md`の「インタビュー構造化拡張仕様」に対応する追加設計である。現行コードの全AI処理を直ちに構造化インタビューProviderへ移行することを意味しない。質問項目設計は、本仕様のResponses API・Structured Output経路を使用する。

### 7.2 初期構成

構造化インタビューのInterpreterとQuestion Generatorには、Amazon Bedrock RuntimeのOpenAI互換Responses APIとStructured Outputsを使用する。AWS SigV4で認証し、OpenAI APIキーを使用しない。

| 設定 | 値 |
|---|---|
| 既定モデルID | `global.openai.gpt-5.6-luna` |
| 選択可能モデルID | `global.openai.gpt-5.6-terra`、`global.openai.gpt-5.6-luna` |
| 質問項目設計モデル設定 | Knowledgeの`defaultModelId`。未設定または旧モデル値は`QUESTION_DESIGN_MODEL_ID`へ解決 |
| 質問項目設計モデル既定値 | `global.openai.gpt-5.6-luna` |
| 初期`reasoning.effort` | `low` |
| Question Generator出力上限 | `600` tokens |
| 高難度時 | 選択済みモデルを`medium`で再実行 |
| 画像生成モデル | 使用しない |
| LLMによる図コード・座標生成 | 使用しない |

初期実装では、TerraとLunaの自動ルーティングを行わない。未選択時はLunaを使用し、Terraを選択した場合だけTerraを使用する。Solは選択肢に含めない。Solを追加する場合、または自動ルーティングを追加する場合は、Terraとの比較評価、ルーティング条件、Provider障害時の挙動を別仕様で定義する。

質問項目設計は、生成前にBackendが同じテナント・Knowledgeの承認済み情報と取り込み済み文書・チャンクを検索し、検索結果をStructured Outputリクエストの入力へ渡す。LLMへDBアクセスを与えず、MermaidコードやReact Flow座標も生成させない。

### 7.3 Provider境界

Bedrock API呼び出しは、Backendの`StructuredInterviewProvider`アダプターへ限定する。

* `app/api`のRouter、Repository、状態機械からBedrock APIを直接呼び出さない。
* `app/voice`からBedrock APIを呼び出さない。
* 音声データをGPT-5.6モデルへ直接送信しない。音声経路は確定transcriptを構造化インタビューへ渡す。
* 既存のStrands/Bedrock経路を残す場合も、同じターンを構造化Providerと既存経路の両方で処理しない。
