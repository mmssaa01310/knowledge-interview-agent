# repository-structure.md

## 1. 基本方針

このリポジトリは、KIKIORI（AIインタビュー / ナレッジ構造化アプリ）のモノレポである。

主な構成は以下。

* `app/web`: フロントエンド
* `app/api`: Backend API
* `app/voice`: リアルタイム音声I/Oサービス
* `app/worker`: 文書取り込み状態の最小Worker（将来の非同期処理枠）
* `packages/shared-types`: 共有型
* `infra/cdk`: AWS CDK
* `infra/postgres`: ローカルPostgreSQLの初期スキーマ
* `docs`: 仕様・設計ドキュメント
* `.github/skills`: GitHub Copilot向けAgent Skills
* `specs`: 実装中の変更単位の作業仕様
* `.github`: GitHub Copilot / GitHub Actions 関連設定

## 2. 想定フォルダ構成

```text
.
├── AGENTS.md
├── mkdocs.yml
├── pyproject.toml          # MkDocsの開発依存
├── package.json
├── pnpm-workspace.yaml
├── docs/
│   ├── index.md
│   ├── spec.md
│   ├── architecture/
│   │   ├── agents/
│   │   ├── aws/
│   │   ├── access-control.md
│   │   └── voice/
│   ├── agents/
│   ├── guides/
│   ├── plans/
│   ├── codebase/
│   │   └── dashboard/
│   └── reference/
│       ├── current-implementation.md
│       ├── repository-structure.md
│       ├── spec-governance.md
│       ├── technology-stack.md
│       ├── response-format.md
│       └── ...
├── app/
│   ├── web/
│   │   ├── src/
│   │   │   ├── app/
│   │   │   ├── components/
│   │   │   ├── features/
│   │   │   │   ├── interviews/
│   │   │   │   │   └── interviewConfiguration.ts
│   │   │   ├── layouts/
│   │   │   ├── lib/
│   │   │   ├── pages/
│   │   │   │   ├── KnowledgeInterviewPage.tsx
│   │   │   │   └── KnowledgeRecordsPage.tsx
│   │   │   ├── providers/
│   │   │   ├── routes/
│   │   │   ├── types/
│   │   │   └── main.tsx
│   │   ├── Dockerfile
│   │   ├── Dockerfile.dev
│   │   └── nginx.conf
│   ├── api/
│   │   ├── src/
│   │   │   └── ai_interviewer_api/
│   │   │       ├── agents/
│   │   │       ├── auth/
│   │   │       ├── core/
│   │   │       │   └── interview_configuration.py
│   │   │       ├── models/
│   │   │       ├── repositories/
│   │   │       ├── routers/
│   │   │       ├── schemas/
│   │   │       ├── services/
│   │   │       └── main.py
│   │   ├── tests/
│   │   ├── pyproject.toml
│   │   ├── Dockerfile.dev
│   │   └── Dockerfile
│   └── worker/
│       ├── src/
│       │   └── ai_interviewer_worker/
│       ├── tests/
│       ├── pyproject.toml
│       └── Dockerfile
├── packages/
│   └── shared-types/
│       └── src/
├── infra/
│   ├── cdk/
│   ├── postgres/
│   │   └── init/
│   │       └── 001_schema.sql
│   └── docker-compose.yml
├── specs/
│   └── <active-feature>/
└── .github/
    ├── agents/
    ├── prompts/
    └── skills/
        └── <skill-name>/
            └── SKILL.md
```

## 3. Frontend

### 3.1 配置

Frontendは以下に配置する。

```text
app/web/
```

### 3.2 責務

FrontendはReact/Viteで実装する。

主な責務は以下。

* 画面表示
* ユーザー操作
* API呼び出し
* フォーム入力
* クライアント側バリデーション
* SSEストリーム表示
* AI提案カード表示
* 承認操作UI

### 3.3 ディレクトリ責務

```text
app/web/src/app/
```

アプリ初期化、グローバル設定、ルートアプリ定義を置く。

```text
app/web/src/pages/
```

画面単位のコンポーネントを置く。

```text
app/web/src/features/
```

機能単位のUI、hooks、API呼び出し、schema、typesを置く。

```text
app/web/src/components/
```

複数機能で使う汎用UIコンポーネントを置く。

```text
app/web/src/layouts/
```

アプリ共通レイアウトを置く。

```text
app/web/src/lib/
```

API client、共通関数、共通設定を置く。

```text
app/web/src/providers/
```

React provider定義を置く。

```text
app/web/src/routes/
```

画面遷移とルーティング定義を置く。

```text
app/web/src/types/
```

アプリ全体で共有するTypeScript型定義を置く。

### 3.4 Frontendで避けること

UIコンポーネントに以下を詰め込みすぎない。

* API呼び出し
* 複雑な状態管理
* バリデーション定義
* 業務ロジック
* 型定義

必要に応じて `features/` 配下に分離する。

## 4. Backend API

### 4.1 配置

Backend APIは以下に配置する。

```text
app/api/
```

Pythonパッケージは以下。

```text
app/api/src/ai_interviewer_api/
```

### 4.2 責務

Backend APIはFastAPIで実装する。

主な責務は以下。

* HTTP API
* 本番IdP JWT検証（候補: Microsoft Entra ID）
* 認可チェック
* リクエスト/レスポンスschema管理
* ユースケース実行
* PostgreSQL検索・保存
* Bedrock呼び出し
* SQSジョブ投入（目標。現行コードでは未接続）
* SSEストリーミング

プロンプト管理では、質問項目設計用とAIインタビュー用を分離して扱う。
ユーザーが編集する追加カスタマイズは、質問項目設計用プロンプトとは混在させない。
質問項目設計チャットの request/context には、実インタビュー用の追加カスタマイズを含めない。
AIエージェントの責務分離は `docs/architecture/agents/agent-architecture.md` に従う。

### 4.3 ディレクトリ責務

```text
agents/
```

AI関連処理の責務別パッケージを置く。
現時点では将来移行の受け皿であり、既存APIの外部仕様は変えない。

```text
agents/question_design/
```

質問項目設計を置く。
既存の `field-suggestions` と `agents/question_design/prompts/` はこの責務に属する。
`services/field_suggestions.py` は router 互換の薄いラッパーとして残し、事前検索は`services/question_design_retrieval.py`、生成と検証はBedrock OpenAI互換Responses APIのStructured Output runnerに置く。

```text
agents/interview/
```

熟練者インタビューの進行、次質問判断、構造化候補生成、draft保存の責務を置く。
正式ナレッジ化は人の承認後に限定する。

```text
agents/common/
```

Bedrock client、prompt loader、JSON parser、contract retry、observabilityなど、エージェント共通基盤の候補を置く。
tool は read-only から開始し、自律的なDB更新を行わない。

```text
auth/
```

認証・認可関連の依存処理を置く。

```text
core/
```

設定、ロガー、共通基盤、アプリ初期化処理を置く。

```text
models/
```

内部モデルを置く。

```text
repositories/
```

PostgreSQLへのアクセス処理を置く。現在の保存契約は`store.py`の`PostgresStore`へ集約する。

```text
routers/
```

FastAPI routerを置く。

```text
schemas/
```

Pydantic request / response schemaを置く。

```text
services/
```

ユースケース、業務ロジック、Bedrock呼び出しを置く。

プロンプトはエージェントごとに分離して管理する。

```text
agents/question_design/prompts/
  base.md
  validation.md
agents/interview/prompts/
  base.md
agents/learning_support/prompts/
  overall_analysis.md
  personal_advice.md
```

* `question_design/prompts/`: 質問項目設計の生成・検証用プロンプト
* `interview/prompts/`: AIインタビュー用の固定ベースプロンプト
* `learning_support/prompts/`: 管理者向け学習分析・助言用プロンプト
* ユーザー追加カスタマイズは実インタビュー実行時だけに適用し、質問項目設計用プロンプトとは分離する

### 4.4 Backendで避けること

FastAPI routerに業務ロジックを詰め込まない。

PostgreSQLクエリはRepository層に閉じ込める。

Bedrock呼び出しはService層に閉じ込める。

認証ユーザーIDの保存を省略しない。

認可チェックを省略しない。

## 5. Worker

### 5.1 配置

Workerは以下に配置する。

```text
app/worker/
```

Pythonパッケージは以下。

```text
app/worker/src/ai_interviewer_worker/
```

### 5.2 責務

Workerは将来の非同期処理を担当する。現行コードは文書取り込み状態を返す最小サンプルであり、SQSからの受信やPostgreSQLへの保存はまだ行わない。

主な対象は以下。

* ドキュメント取り込み（目標）
* テキスト抽出（目標）
* チャンク化（目標）
* embedding（目標）
* PostgreSQLへの取り込み状態保存（目標）
* 将来の外部DB送信（目標）

APIリクエスト内で重い処理を完結させずSQS + Workerで処理する構成は目標であり、現行の文書取り込みはまだその構成へ接続されていない。

## 6. packages/shared-types

FrontendとBackendで共有する型・schemaを置く。

主な対象は以下。

* API contract
* 共通enum
* 承認状態
* ドキュメント状態
* SSEイベント型

ただし、FrontendとBackendの密結合が強くなりすぎる場合は、OpenAPIやJSON Schemaを正とする。

## 7. infra

```text
infra/cdk/
```

AWS CDKコードを置く。

```text
infra/docker-compose.yml
```

ローカル開発用のDocker Composeを置く。

```text
infra/postgres/init/001_schema.sql
```

Compose起動時にPostgreSQLへ適用する初期スキーマを置く。現行APIの起動時にも同じ保存契約のスキーマ確認を行う。

## 8. docs

仕様・設計ドキュメントを置く。

| パス | 内容 |
|---|---|
| `docs/spec.md` | アプリ仕様・業務ルール |
| `docs/architecture/` | システム構成、AI責務、音声構成 |
| `docs/agents/` | 個別AIエージェント仕様 |
| `docs/guides/` | 開発手順、検証手順、パッケージ運用 |
| `docs/plans/` | 未実装機能の実装計画 |
| `docs/reference/current-implementation.md` | 現行コードの実装範囲、API、データモデル |
| `docs/reference/` | 共通ルール・技術情報 |

## 9. .github/skills

GitHub Copilotが必要なタスクだけ読み込むAgent Skillsを置く。各スキルは独立したディレクトリを持ち、入口ファイルを`SKILL.md`とする。

主な対象は以下。

* 実装品質
* Frontend実装
* Backend実装
* PostgreSQL
* AI / RAG
* AWS Architecture
* Testing
* Refactoring

スキルの説明と指示は`.github/skills/<skill-name>/SKILL.md`に集約する。全タスクに適用するリポジトリ共通ルールは`AGENTS.md`に置く。

`.agents/skills`はローカルエージェント互換用のリンクであり、編集対象にしない。スキルの正本は`.github/skills`だけとする。

## 10. specs

実装中の変更単位の作業仕様を置く。実装完了後は恒久情報を`docs/`へ反映し、対象の`specs/<id>/`を削除する。Spec Kitのエージェント定義とテンプレートは`.github/agents/`、`.github/prompts/`、`.specify/`に置く。

## 11. .github

GitHub関連の設定を置く。

```text
.github/agents/
```

AIエージェント向けの追加指示を置く。

```text
.github/prompts/
```

GitHub Copilotや初期実装向けのプロンプトを置く。
