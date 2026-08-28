# 現行実装リファレンス

## 1. 位置づけ

この文書は、現在のコードで実装されている範囲、API、データモデル、ローカル実行条件を管理する。

次の役割を混同しない。

* `docs/spec.md`: プロダクトの正式な振る舞いと業務ルール
* `docs/architecture/`: システム構成と責務分離
* `docs/reference/current-implementation.md`: 現在のコードで実装済みの範囲
* `specs/<id>/`: 実装中の変更単位の作業資料

プロダクト仕様に対して未実装の内容は、この文書の「未実装・制限」に記載する。実装済みの変更を新しい`specs/`として残さない。

## 2. 実装済みの構成

### 2.1 アプリケーション

* `app/web`: React + ViteのWebアプリ
* `app/api`: FastAPI API
* `app/voice`: 音声セッション用FastAPIサービス
* `app/worker`: ドキュメント取り込み用Worker
* `packages/shared-types`: FrontendとBackendの共有型
* `infra/docker-compose.yml`: ローカル開発用Compose

### 2.2 Backendの責務分離

* `routers`: HTTP入出力と認証・認可依存性の接続
* `schemas`: リクエスト・レスポンスのPydantic定義
* `services`: 業務ロジック、AI呼び出し、状態更新
* `repositories`: 保存先へのアクセス
* `models`: ドメインモデル
* `agents`: 質問設計、インタビュー、構造化インタビューのAI処理

### 2.3 現在の保存方式

APIの現在のローカル実装は`InMemoryStore`を使用する。APIプロセスを再起動すると保存内容は失われる。

`ELASTICSEARCH_URL`、SQS、Cognitoの設定項目は存在するが、現在のローカル実装ではそれぞれメモリ保存、メモリキュー、開発トークン認証を使用する。

## 3. 認証・認可

ローカルAPIは、次の開発トークンを`x-dev-token`または`Authorization: Bearer`で受け付ける。

| トークン | ユーザーID | ロール |
|---|---|---|
| `dev-admin` | `user-admin` | `admin` |
| `dev-manager` | `user-manager` | `knowledge_manager` |
| `dev-interviewer` | `user-interviewer` | `interviewer` |
| `dev-viewer` | `user-viewer` | `viewer` |

既定のリクエストヘッダーは`x-dev-token: dev-manager`である。無効なトークンはHTTP 401を返す。

保存する主要エンティティには、`tenantId`、`createdByUserId`、`updatedByUserId`、作成日時、更新日時を含める。取得時は認証ユーザーの`tenantId`でスコープする。

音声サービスからAPI内部エンドポイントを呼び出す場合は、`x-internal-api-token`を使用する。内部エンドポイントを外部ユーザー向けAPIとして扱ってはならない。

## 4. データモデル

すべての保存エンティティは、原則として次の共通項目を持つ。

```text
id
tenantId
createdByUserId
updatedByUserId
ownerUserId?
createdAt
updatedAt
deletedAt?
```

### 4.1 エンティティ

| エンティティ | 主な役割 | 主な関連 |
|---|---|---|
| `KnowledgeDb` | ナレッジをまとめる単位 | `Knowledge`を複数保持 |
| `Knowledge` | ナレッジとインタビュー設定 | `KnowledgeDb`に属し、Field、Record、Documentを保持 |
| `InterviewPromptProfile` | 実インタビューの追加カスタマイズ | テナントに属する |
| `KnowledgeField` | 固定フォームの質問項目 | `Knowledge`に属する |
| `InterviewRecord` | 1回のインタビュー記録 | `Knowledge`に属する |
| `VoiceSession` | Recordの音声セッション | `InterviewRecord`に属する |
| `VoiceTurn` | 音声セッション内の発話 | `VoiceSession`とRecordに属する |
| `AiProposal` | AIが作成した未承認候補 | Recordに属する |
| `Document` | ナレッジに紐づく文書メタデータ | `Knowledge`に属する |
| `DocumentReadStatus` | ユーザー別の文書既読状態 | Documentとユーザーに属する |
| `AuditLog` | 作成・更新・削除・承認の監査情報 | 操作対象を参照する |

### 4.2 インタビュー設定

`Knowledge.interviewPlan`は次のProfileを持つ。

* `fixed_form`: 設定済み必須項目を順番に確認する
* `business_process`: ProcessStateを収集する
* `system_requirement`: RequirementStateを必須とし、業務フローが存在すると確定した場合だけProcessStateを収集する

構造化インタビューで選択できるモデルIDは次の2つだけである。

* `global.openai.gpt-5.6-terra`
* `global.openai.gpt-5.6-luna`

未選択時の構造化インタビューと質問項目設計の既定モデルはTerraである。画像生成モデルは使用しない。

## 5. API

APIのルートプレフィックスは`/api`である。`/api/health`を除くユーザー向けAPIは認証を必要とする。

### 5.1 ヘルスチェック

* `GET /api/health`
* `GET /api/me`

### 5.2 ナレッジDB

* `GET /api/knowledge-dbs`
* `POST /api/knowledge-dbs`
* `GET /api/knowledge-dbs/{knowledge_db_id}`
* `PATCH /api/knowledge-dbs/{knowledge_db_id}`
* `DELETE /api/knowledge-dbs/{knowledge_db_id}`

### 5.3 ナレッジ

* `GET /api/knowledge-dbs/{knowledge_db_id}/knowledges`
* `POST /api/knowledge-dbs/{knowledge_db_id}/knowledges`
* `GET /api/knowledges/{knowledge_id}`
* `PATCH /api/knowledges/{knowledge_id}`
* `DELETE /api/knowledges/{knowledge_id}`
* `POST /api/knowledges/{knowledge_id}/record-summary-draft`

### 5.4 ヒアリング項目と質問設計

* `GET /api/knowledges/{knowledge_id}/fields`
* `POST /api/knowledges/{knowledge_id}/fields`
* `PATCH /api/knowledge-fields/{field_id}`
* `DELETE /api/knowledge-fields/{field_id}`
* `POST /api/knowledges/{knowledge_id}/generate-fields`
* `POST /api/knowledges/{knowledge_id}/field-suggestions`
* `GET /api/interview-prompt-profiles`
* `POST /api/interview-prompt-profiles`
* `PATCH /api/interview-prompt-profiles/{profile_id}`
* `DELETE /api/interview-prompt-profiles/{profile_id}`

### 5.5 インタビュー記録

* `GET /api/knowledges/{knowledge_id}/records`
* `POST /api/knowledges/{knowledge_id}/records`
* `GET /api/records/{record_id}`
* `PATCH /api/records/{record_id}`
* `DELETE /api/records/{record_id}`
* `POST /api/records/{record_id}/messages`
* `GET /api/records/{record_id}/interview-state`
* `PATCH /api/records/{record_id}/interview-answers/{field_id}`
* `POST /api/records/{record_id}/summary-proposals`
* `GET /api/records/{record_id}/stream`

テキストメッセージ送信は`clientMessageId`による重複排除と`stateVersion`による状態競合検出を行う。

SSEの主なイベントは次のとおりである。

```text
stream_start
delta
stream_end
proposal_created
```

### 5.6 提案と承認

* `GET /api/records/{record_id}/proposals`
* `POST /api/proposals/{proposal_id}/approve`
* `POST /api/records/{record_id}/approve-all-proposals`
* `POST /api/records/bulk-approve`

AI提案は`draft`または`needs_review`で保存し、人の操作なしに`approved`へ変更しない。承認方式は`single`、`record_bulk`、`list_bulk`を区別する。

### 5.7 文書

* `GET /api/knowledges/{knowledge_id}/documents`
* `POST /api/knowledges/{knowledge_id}/documents`
* `POST /api/documents/{document_id}/read`
* `POST /api/documents/{document_id}/acknowledge`

現在の文書登録はメタデータ登録であり、登録後にメモリ上の取り込みキューへ投入する。文書本体のアップロードは未実装である。

### 5.8 参照チャット

* `POST /api/chats`
* `POST /api/chats/{chat_id}/messages`

参照チャットの回答は`answer`と`citations`を返す。

### 5.9 音声セッション

ユーザー向け音声セッションAPI:

* `POST /api/records/{record_id}/voice-sessions`
* `GET /api/voice-sessions/{voice_session_id}`
* `POST /api/voice-sessions/{voice_session_id}/stop`

音声サービス専用の内部API:

* `POST /internal/voice-sessions/{voice_session_id}/turns`
* `POST /internal/voice-sessions/{voice_session_id}/turn-intent`
* `POST /internal/voice-sessions/{voice_session_id}/turns/cancel`
* `GET /internal/voice-sessions/{voice_session_id}`
* `POST /internal/voice-sessions/{voice_session_id}/initial-reply-sent`
* `POST /internal/voice-sessions/{voice_session_id}/initial-reply-failed`
* `POST /internal/voice-sessions/{voice_session_id}/initial-reply/claim`
* `POST /internal/voice-sessions/{voice_session_id}/turns/{turn_id}/process`
* `POST /internal/voice-sessions/{voice_session_id}/assistant-events`
* `POST /internal/voice-sessions/{voice_session_id}/connection-events`

### 5.10 開発用データ操作

開発用Composeで有効になるAPIであり、本番APIとして利用しない。

* `POST /api/dev/voice-demo/reset`
* `POST /api/dev/system-requirement-demo/reset`

## 6. 構造化インタビューの責務

確定したユーザー発話は、テキスト経路と音声経路で共通のインタビュー状態へ渡す。

```text
ユーザー発話
  ↓
Interpreter
  ├── FieldState候補
  ├── RequirementState候補
  ├── ProcessState候補
  ├── 矛盾
  ├── Applicability
  └── 未解決事項
  ↓
Backendの検証・状態更新
  ↓
次の質問対象を1件決定
  ↓
Question Generator
```

LLMは意味解釈、候補抽出、矛盾・Applicabilityの判定、質問文生成を担当する。BackendはStructured Output検証、状態遷移、完了判定、質問対象の優先順位、重複防止、承認境界を担当する。

ProcessModel、フローチャート、シーケンス図はProcessStateから生成する派生ビューである。LLMにMermaidコードやReact Flowの座標を生成させない。

## 7. ローカル動作確認

### 7.1 Compose

```bash
docker compose -f infra/docker-compose.yml up --build
```

既定ポートは次のとおりである。

| サービス | URL |
|---|---|
| Web | `http://localhost:5173` |
| API | `http://localhost:8001` |
| Voice | `http://localhost:8010` |

ソースコードはbind mountされ、Web、API、Voiceはホットリロードする。通常はWebのURLだけをブラウザで開く。

### 7.2 開発用データ

Composeでは次の開発用データを起動時に冪等作成する。

* 保全ヒアリング
* 音声インタビュー
* システム要件ヒアリング

音声デモとシステム要件デモは、次のAPIで会話状態をリセットできる。

```text
POST /api/dev/voice-demo/reset
POST /api/dev/system-requirement-demo/reset
```

### 7.3 直接起動

Composeを使用しない場合の詳細は[README.md](../../README.md)を参照する。

## 8. 未実装・制限

次の項目はプロダクト仕様上の将来または本番対応であり、現在のローカル実装には含まれない。

* Cognitoの本番JWT検証
* Elasticsearchへの実データ永続化
* SQSの実接続とAPI・Worker間の実ジョブ連携
* 文書ファイル本体のアップロードと実ファイル処理
* チャットボット設定のサーバー永続化
* 承認済み項目値を正式ナレッジへ反映する永続モデル
* 本番向けの監視、負荷対策、マルチテナント運用

これらを実装した場合は、恒久的な仕様を`docs/spec.md`または該当する`docs/architecture/`・`docs/reference/`へ反映し、この文書の実装状況も更新する。
