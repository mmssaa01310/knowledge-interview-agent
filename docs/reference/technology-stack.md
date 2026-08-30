# technology-stack.md

## 1. Frontend

現行のWebアプリは`app/web`のReact/Viteアプリである。

* React 18 + TypeScript
* Vite 5
* i18next / react-i18next
* Driver.js（操作ガイド）
* React Flow（`@xyflow/react`、処理フローの表示）
* CSS Modulesではなく、`styles.css`と`kikiori.css`を中心としたCSS

現行の`app/web/package.json`にはTailwind CSS、shadcn/ui、Radix UI、lucide-react、TanStack Query、React Hook Form、Zod、Zustand、Jotaiは含まれない。導入済みとして扱わない。

## 2. Frontend Hosting（目標構成）

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
* psycopg（PostgreSQL接続）
* boto3
* 本番IdP JWT検証（候補: Microsoft Entra ID。未実装）
* SSE

リアルタイム音声は現行の`app/voice`によるWebRTC + HTTP SDP signalingを使用する。WebSocketは現行の公開経路ではなく、Trickle ICEなどを採用する場合の将来検討である。

## 4. Worker

現行の`app/worker`はPython / uvの最小実装で、文書取り込み状態のサンプル処理を持つ。SQS受信、PostgreSQL接続、Bedrock呼び出しはまだ実装されていない。

ECS Worker、SQS、PostgreSQL接続、boto3、Bedrockは、Workerを本実装する際の目標構成であり、現行Workerの実行依存としては記載しない。

## 5. インフラとAWS

ローカル開発ではDocker ComposeによりWeb、API、Voice、PostgreSQLを起動する。PostgreSQLは`postgres:16-alpine`で、初期スキーマは`infra/postgres/init/001_schema.sql`に置く。

AWS CDKの雛形は`infra/cdk`にある。ECS Fargate上のFrontend、API、Voice、Worker、ALB、企業IdP（Entra ID候補）、SQS、Secrets Manager、CloudWatch Logs、IAM、KMSは目標アーキテクチャとして`docs/architecture/aws/aws-architecture.md`に記載する。導入済みの実行環境として断定しない。

## 6. データベース方針

アプリケーションの構造化データはPostgreSQLを正本とする。ローカル開発ではDocker Composeの`postgres:16-alpine`を使用し、APIは`DATABASE_URL`で接続する。

検索と保存は同じPostgreSQLのRepository境界へ集約する。ドメインの拡張に追従できるよう、現在の互換Storeは`kikiori.entity_store`のJSONB payloadへ保存し、テナント・論理エンティティ・関連IDの検索インデックスを持つ。

本番環境のPostgreSQL提供方式、バックアップ、冗長化はデプロイ環境ごとに決定する。ローカルと本番でアプリケーションの保存契約を分けない。

ただし、ファイル原本保存が明示された場合のみ、S3またはEFSの追加を検討してよい。

## 開発・ドキュメント

* Node.js依存管理: pnpm workspace（pnpm 11）
* Python依存管理: uv
* API / Voice / Workerテスト: pytest
* Webの型検査: TypeScript (`pnpm --dir app/web lint`)
* Web翻訳検査: `pnpm --dir app/web check:i18n`
* ドキュメント: MkDocs Material (`uv run --group dev mkdocs build --strict`)

`ruff`、`mypy`、カバレッジ閾値、FrontendのE2Eテスト基盤は現行設定では定義されていない。検証コマンドは[検証ルール](../guides/verification.md)を参照する。

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
