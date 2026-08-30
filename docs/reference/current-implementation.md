# KIKIORI 現行実装リファレンス

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
  * 画面ページは`React.lazy`によるルート単位の遅延読み込みを使用し、インタビュー画面のReact Flow・音声機能を初期バンドルへ含めない。
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

ナレッジ分析の集計結果は既存データから都度計算し、教育支援案と学習支援分析の下書きは現在`InMemoryStore`へ保存する。APIプロセスを再起動すると、集計対象の記録と同様に下書きも失われる。

## 3. 認証・認可

ローカルAPIは、次の開発トークンを`x-dev-token`または`Authorization: Bearer`で受け付ける。

| トークン | ユーザーID | ロール |
|---|---|---|
| `dev-admin` | `user-admin` | `admin` |
| `dev-manager` | `user-manager` | `knowledge_manager` |
| `dev-interviewer` | `user-interviewer` | `interviewer` |
| `dev-viewer` | `user-viewer` | `viewer` |

既定のリクエストヘッダーは`x-dev-token: dev-manager`である。無効なトークンはHTTP 401を返す。Vite開発画面では、左側の「開発用ユーザー」セレクトから4ロールを切り替えられる。切り替え後は画面を再読み込みする。

保存する主要エンティティには、`tenantId`、`createdByUserId`、`updatedByUserId`、作成日時、更新日時を含める。取得時は認証ユーザーの`tenantId`でスコープする。

ローカル実装では、全ロール共通のナレッジ一覧サイドバー、Recordの担当者・明示閲覧許可による一覧絞り込み、記録の確認待ち・差し戻し・承認状態を実装している。ナレッジ管理者は最後に開いたナレッジ、または最初のナレッジの「インタビュー」を開く。`interviewer`と`viewer`はアクセス可能な記録からナレッジを構成し、最初のアクセス可能なナレッジの「インタビュー」を開く。サイドバーのナレッジ一覧は作成日時順で固定し、ナレッジ選択後も並び順を変更しない。サイドバーでは、ナレッジをタグ別の折りたたみ可能なグループに分け、複数タグのナレッジは各グループに表示し、タグなしは「タグなし」グループへ表示する。グループはタグ名順で、「タグなし」を最後に表示する。ナレッジ配下では「インタビュー」「記録」をメイン画面上部の主タブとして表示し、管理者系ロールだけにヘッダーの主ボタン「インタビュー設定」を表示する。ナレッジ配下のページヘッダー左側にある「ナレッジ一覧」は、全ナレッジを表示する`/knowledge-dbs`へ遷移する。`/knowledge-dbs`では、最後に開いたナレッジのインタビューへ自動遷移せず、ナレッジ一覧を表示する。新規ナレッジは作成後に設定画面の「実行設定」を開き、設定が完了するまで記録作成を表示しない。質問項目は、通常時に順番・項目名・必須状態・詳細項目の要約を1行カードで表示し、選択した1件だけをアコーディオン形式で編集できる。項目追加直後は追加項目を展開し、AI提案は承認カードとして分離表示する。Frontendに全体の`/records`画面はなく、記録はナレッジ配下で確認する。記録詳細では`1200px`以上で会話を左、整理結果または質問リストを右に表示する。`901px`から`1199px`では会話と整理結果の2ペインを維持し、`900px`以下では整理結果または質問リストを右Drawerへ切り替える。業務フローとシステム要件では、整理結果を表示するサイドバーをデスクトップ時に全体の44%へ広げ、フローチャート領域を380pxへ拡大する。図の高さは狭い画面で段階的に縮小する。`system_requirement`の整理結果は「要件整理」「処理の流れ」タブで切り替え、「処理の流れ」内でフローチャートとシーケンス図を切り替える。「インタビュー」では管理者系ロールが新規記録を作成し、対象者が途中の記録を再開する。「記録」では権限範囲内の記録を確認し、管理者系ロールがインタビュー結果の編集、差し戻し、承認を行う。インタビュー完了時は記録状態を自動的に確認待ちへ変更する。記録削除は`admin`だけが実行できる。`KnowledgeDb`は内部の分類単位として保持し、ナレッジ一覧には表示しない。複数のDBがある場合の新規ナレッジ作成時だけ「業務領域」として選択できる。Record API、音声セッションAPI、記録に関連する提案APIで同じRecord認可判定を使用する。ユーザー一覧を永続管理する本番のユーザーディレクトリと、画面から担当者・閲覧者を選択する管理画面は未実装である。権限モデルは[利用者ワークスペースと認可アーキテクチャ](../architecture/access-control.md)を参照する。

ナレッジ作成ダイアログとナレッジ設定の「ナレッジ情報」にはタグ編集欄を表示する。入力したタグはEnter、カンマ、読点で追加でき、個別に削除できる。既存タグは候補リストから選択できる。保存時のタグ正規化と制限はBackendが正本として検証する。設定画面ではナレッジ名を折りたたみ時も見出しとして表示し、展開時は名前とタグを横並び、説明をその下に表示する。

ナレッジ一覧と業務領域内のナレッジ一覧では、タグを独立した列に表示する。タグ未設定は明示し、タグが多い場合はタグ列内でスクロールできる。

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
| `Knowledge` | ナレッジとインタビュー設定 | `KnowledgeDb`に属し、タグ、Field、Record、Documentを保持 |
| `InterviewPromptProfile` | 実インタビューの追加カスタマイズ | テナントに属する |
| `KnowledgeField` | 固定フォームの質問項目 | `Knowledge`に属する |
| `InterviewRecord` | 1回のインタビュー記録 | `Knowledge`に属し、`ownerUserId`で回答担当者、`viewerUserIds`で明示閲覧者を管理する。 |
| `VoiceSession` | Recordの音声セッション | `InterviewRecord`に属する |
| `VoiceTurn` | 音声セッション内の発話 | `VoiceSession`とRecordに属する |
| `AiProposal` | AIが作成した未承認候補 | Recordに属する |
| `Document` | ナレッジに紐づく文書メタデータ | `Knowledge`に属する |
| `DocumentReadStatus` | ユーザー別の文書既読状態 | Documentとユーザーに属する |
| `AuditLog` | 作成・更新・削除・承認、教育支援案の生成・公開の監査情報 | 操作対象を参照する |
| `GuidanceDraft` | 教育目標ごとの学習案内・指導案の下書きと公開状態 | `InterviewRecord`、`Knowledge`に属する |
| `LearningAnalysisDraft` | 同一ナレッジの複数記録を横断した学習支援分析、全体傾向、回答者別アドバイスの下書きと確認状態 | `Knowledge`に属し、対象記録IDをスコープへ保持する |

### 4.2 インタビュー設定

`Knowledge.interviewPlan`は次のProfileを持つ。

* `fixed_form`: 設定済み必須項目を順番に確認する
* `business_process`: ProcessStateを収集する
* `system_requirement`: RequirementStateを必須とし、業務フローが存在すると確定した場合だけProcessStateを収集する

構造化インタビューで選択できるモデルIDは次の2つだけである。

* `global.openai.gpt-5.6-terra`
* `global.openai.gpt-5.6-luna`

設定画面の初期選択はLunaとする。記録を作成・開始するには、選択したモデルを`interviewPlan.modelId`へ保存しなければならない。画像生成モデルは使用しない。

設定画面には「基本設定」タブを置かず、ナレッジ名と説明を「ナレッジ情報」としてタブの上に表示する。タブは「質問項目」「実行設定」「事前知識」の3つとし、事前知識タブでは参照文書の追加・取り込み状態・既読状態を管理する。設定保存操作はナレッジ情報、質問項目、実行設定をまとめて保存し、文書追加・既読状態更新は各操作時に反映する。

記録を作成・開始するには、`interviewPlan`へ有効な`profile`と`modelId`を保存済みであることが必要である。設定未完了の場合、Backendは`409 interview_configuration_required`を返す。

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

ナレッジの作成・更新APIは`tags: string[]`を受け付ける。保存時に前後空白の除去、空文字の除外、大文字・小文字を区別しない重複除去を行う。タグは1ナレッジあたり20件以下、1件あたり40文字以下とし、制限超過時はHTTP 422を返す。取得APIは、既存の認証・認可スコープ内でナレッジの`tags`を返す。

### 5.4 質問項目と質問設計

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

`field-suggestions`は、生成前に同じテナント・Knowledgeの既存質問項目、承認済み記録・AI提案、取り込み済み文書・チャンクをBackendで検索する。検索結果は`retrieved_knowledge`として質問設計のStructured Output入力へ渡す。生成とValidatorは選択されたGPT-5.6 LunaまたはTerraを使用し、質問項目設計の本番経路ではStrands Agentを使用しない。検索結果が1件以上ある場合、APIレスポンスに`retrievedSources`を含める。検索結果が0件の場合、このキーを返さない。

### 5.5 インタビュー記録

* `GET /api/records`
* `GET /api/knowledges/{knowledge_id}/records`
* `POST /api/knowledges/{knowledge_id}/records`
* `GET /api/records/{record_id}`
* `GET /api/records/{record_id}/interview-context`
* `PATCH /api/records/{record_id}`
* `DELETE /api/records/{record_id}`
* `POST /api/records/{record_id}/messages`
* `GET /api/records/{record_id}/interview-state`
* `PATCH /api/records/{record_id}/interview-answers/{field_id}`
* `GET /api/records/{record_id}/stream`

`GET /api/records`は、認証ユーザーのロールとRecordの状態・担当・明示閲覧許可で返却対象を絞り込む。FrontendではこのAPIをアクセス可能なナレッジ一覧の構成に使用し、記録一覧画面の入口には使用しない。`POST /api/knowledges/{knowledge_id}/records`と`PATCH /api/records/{record_id}`の担当者・閲覧者設定は管理者だけが実行できる。Recordの状態は`draft`、`in_progress`、`submitted`、`returned`、`approved`で管理する。新規Recordは`in_progress`で作成し、インタビュー状態が`completed`になったRecordは`submitted`へ自動変更する。`draft`は旧データ互換用であり、現在の画面には回答受付開始操作を表示しない。記録の削除は`admin`だけが実行できる。記録作成、テキスト開始・回答、音声開始ではインタビュー設定の完了を確認する。`viewer`の状態取得は表示用スナップショットとして処理し、状態を保存しない。

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

### 5.8 音声セッション

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

### 5.9 開発用データ操作

開発用Composeで有効になるAPIであり、本番APIとして利用しない。

* `POST /api/dev/voice-demo/reset`
* `POST /api/dev/system-requirement-demo/reset`

### 5.10 ナレッジ分析と教育支援

* `GET /api/admin/dashboard`
* `POST /api/admin/learning-analysis`
* `GET /api/admin/learning-analysis`
* `PATCH /api/admin/learning-analysis/{analysis_id}`
* `POST /api/admin/learning-analysis/{analysis_id}/review`
* `POST /api/admin/records/{record_id}/guidance`
* `GET /api/admin/records/{record_id}/guidance`
* `PATCH /api/admin/guidance/{draft_id}`
* `POST /api/admin/guidance/{draft_id}/publish`
* `POST /api/admin/guidance/{draft_id}/unpublish`
* `GET /api/records/{record_id}/guidance`

`GET /api/admin/dashboard`は`admin`または`knowledge_manager`が利用できる。画面上の名称は「ナレッジ分析」とし、内部APIのパスは互換性のため維持している。現行の権限モデルでは`knowledge_manager`にナレッジ単位の割当がないため、所属テナント内を集計する。期間、ナレッジ、インタビュー用途、記録状態で絞り込み、記録状態、時系列、回答者ごとの記録・回答・提出・教育目標状態の内訳、根拠付き確認優先度、教育目標の状態を返す。既存API互換のため教育支援案の公開状態もレスポンスへ含めるが、ナレッジ分析画面では表示しない。会話本文と音声原本は返さない。確認優先度は回答量や利用頻度だけでは上げず、必須項目の未確認、矛盾、未解決事項、差し戻しなどの保存済み状態から計算する。

教育支援案の生成、編集、公開、非公開は`admin`または対象ナレッジを管理する`knowledge_manager`だけが実行できる。AI生成結果は`draft`として保存し、Backendが対象教育目標と根拠IDを検証する。`GET /api/records/{record_id}/guidance`は担当`interviewer`本人に対して公開済みの学習案内だけを返し、指導者向けメモを除外する。すべての生成・編集・公開・非公開操作を監査ログへ保存する。

`POST /api/admin/learning-analysis`は、選択した1つのナレッジに属する2件以上の記録を対象として、`dateFrom`、`dateTo`、`profile`、`recordStatus`で範囲を指定する。Backendが教育目標ごとの状態件数を計算し、選択された実行モデルへ全体分析を依頼した後、その結果と回答者ごとの自身の記録を同じモデルへ渡して個人アドバイスを生成する。AIの構造化出力から複数記録に共通するテーマ、全体向け学習案内、指導者向け支援案、回答者別アドバイスを作成する。AI結果は`draft`として保存し、テーマと個人重点確認の教育目標ID・記録IDをBackendが検証する。回答者IDは分析入力では一時的な`respondentKey`へ置き換え、保存時にBackendで復元する。`PATCH`は文章を編集して再び`draft`に戻し、`POST .../review`は管理者が確認済みであることを保存する。回答者が設定されていない記録は個人アドバイスの対象外とする。集計分析は対象者へ公開せず、対象者の比較・点数化・順位付け・理解度や能力の断定を行わない。

学習支援用システムプロンプトは、`app/api/src/ai_interviewer_api/agents/learning_support/prompts/overall_analysis.md`（全体分析）と`app/api/src/ai_interviewer_api/agents/learning_support/prompts/personal_advice.md`（回答者別アドバイス）で開発者が管理する。`prompt_loader.py`がUTF-8のMarkdownを読み込み、AI呼び出しへ渡す。IDの存在確認、対象者単位の記録分離、認可、下書き・確認済み状態はBackendが保証する。

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

ProcessModel、フローチャート、シーケンス図はProcessStateから生成する派生ビューである。フローチャートは端子・長方形・ひし形・平行四辺形などの標準記号へ変換し、シーケンス図は参加者ボックス・破線ライフライン・時系列メッセージ矢印で描画する。LLMにMermaidコードやReact Flowの座標を生成させない。フローチャートまたはシーケンス図の生成後は全画面で確認でき、管理者系ロールは全画面の編集・保存と要件・処理モデルへのコンパクトな編集指示入力を利用できる。編集指示は`RequirementState`とProcessStateを同時に対象にでき、手動修正と指示編集はBackendで検証する。要件だけの編集ではProcessStateのバージョンを更新せず、Process要素を変更した場合だけProcessStateのバージョンを更新する。承認済み記録は直接編集しない。

管理者は`/knowledge-dbs`のヘッダーから`/dashboard`を開く。ナレッジ分析はフィルター直下の「分析」「学習支援」タブで表示を分ける。「分析」には確認優先度の理由付き一覧、回答者ごとの状態集計、教育目標の状態を表示し、「学習支援」には複数記録を横断した学習支援分析のレビュー・編集操作を表示する。個別記録の教育支援案はナレッジ分析画面に表示しない。公開済みの個別学習案内は、担当`interviewer`が自分の記録詳細で確認できる。対象者には、指導者向けメモ、他者の記録、未公開の案、横断分析、対象者間の比較を表示しない。確認優先度一覧は折りたたみ可能な1行表示とスクロール領域を使用し、記録数が増えても画面全体が無制限に縦へ伸びない。

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

* 保全インタビュー
* 音声インタビュー
* システム要件インタビュー

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
* 承認済み項目値を正式ナレッジへ反映する永続モデル
* 本番向けの監視、負荷対策、マルチテナント運用

質問項目設計の検索データ源は現在`InMemoryStore`である。実Elasticsearch接続と文書ファイル本体の取り込みは未実装である。

これらを実装した場合は、恒久的な仕様を`docs/spec.md`または該当する`docs/architecture/`・`docs/reference/`へ反映し、この文書の実装状況も更新する。
