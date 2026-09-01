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
* `app/worker`: ドキュメント取り込み状態を返す最小Worker（サンプル。SQS接続は未実装）
* `packages/shared-types`: FrontendとBackendの共有型
* `infra/docker-compose.yml`: ローカル開発用Compose

### 2.1.1 操作ヘルプと操作ガイド

* `/help`は、サイドバーのユーザーメニューから別タブで開く操作マニュアルである。本文は`app/web/src/content/help/`のロケール別JSONで管理し、目次と本文をDesktopでは2列、狭い画面では1列で表示する。
* インタラクティブ操作ガイドは`driver.js`を使用し、`app/web/src/features/guides/`のGuide Registry、Selector、Engineで共通管理する。画面ごとのGuide Componentを複製せず、対象要素には`data-guide`属性を付ける。Selectorはロール、現在画面、選択中記録の状態からおすすめを先頭に表示する。ガイドは、初回案内、ナレッジ作成、インタビュー設定、質問設定、インタビュー実施、記録レビュー、差し戻し修正、閲覧、管理者の全体分析・優先対応・学習分析に分ける。
* Guideの進捗はユーザー単位のLocalStorageへ`guideId`、`version`、`status`、完了日時を保存する。ナレッジ作成後の設定ガイド案内を表示しない設定も同じ領域で管理し、Guideは完了後もユーザーメニューから再実行できる。操作待ちStepは、画面遷移または対象要素の表示を確認してから次のStepへ進む。
* インタビュー画面の確認対象は既存の`InterviewState.fieldStates`から画面が計算した状態を利用する。`CONFIRMED`の項目にはGuide対象属性を付けず、確認が必要な項目だけをGuide Engineが実行時に項目単位のStepへ展開してハイライトする。対象が存在しないOptional StepはGuide Engineが短時間でスキップする。設定画面の実行設定・質問設定、記録詳細、管理ダッシュボードのタブは、Guide Definitionの共通ステップから必要なタブを選択して対象を表示する。

### 2.2 Backendの責務分離

* `routers`: HTTP入出力と認証・認可依存性の接続
* `schemas`: リクエスト・レスポンスのPydantic定義
* `services`: 業務ロジック、AI呼び出し、状態更新
* `repositories`: 保存先へのアクセス
* `models`: ドメインモデル
* `agents`: 質問設計、構造化インタビューのAI処理

### 2.2.1 現行音声インタビュー経路とコード分類

Transcribe + Pollyを正式な音声入力経路、Structured Interviewを意味解釈と質問進行の正本とする。Nova Sonicは別Runtimeとして既存互換のため保持し、今回の品質改善の対象外である。

```text
Transcribe
  → app/voice/src/ai_interviewer_voice/runtimes/transcribe_polly/runtime.py::_on_transcribe_result
  → app/voice/src/ai_interviewer_voice/transports/webrtc/peer_connection.py::_finalize_user_turn
  → app/api/src/ai_interviewer_api/routers/internal_voice.py::process_internal_voice_turn
  → app/api/src/ai_interviewer_api/services/voice_interview.py::_process_structured_voice_turn
  → app/api/src/ai_interviewer_api/agents/interview_knowledge/service.py::generate_structured_interview_result
  → app/api/src/ai_interviewer_api/agents/interview_knowledge/coordinator.py::apply_structured_output
  → app/api/src/ai_interviewer_api/agents/interview_knowledge/provider.py::BedrockResponsesStructuredProvider
  → app/api/src/ai_interviewer_api/agents/interview_knowledge/service.py::_generate_question_text
  → provider.generate_question (Question Generator)
  → Voice API response → app/voice/src/ai_interviewer_voice/runtimes/transcribe_polly/runtime.py::_synthesize_chunks
  → app/voice/src/ai_interviewer_voice/runtimes/transcribe_polly/polly_synthesizer.py (Polly)
```

分類は次のとおりである。

* A（現行）: 上記のTranscribe + Polly、Voice API、Structured Interpreter、Coordinator、Question Generator。
* B（共通）: 認証・Record認可、Store/Repository、VoiceSession/VoiceTurn、Interview Bridge、文書検索、メッセージ・イベントの冪等性。
* C（旧・削除済み）: 旧Strands Interview Agent、旧Voice回答評価、`dialogue_interpreter`、`interview_answer_processor`、Strands共通Tool、旧Feature Flagと旧専用設定。
* D（判断不能）: なし。Structured Interviewのみを正式経路とする方針に確定したため、旧経路分岐も削除した。

### 2.3 現在の保存方式

APIの保存先はPostgreSQLである。ローカル開発では`infra/docker-compose.yml`のPostgreSQLを起動し、`DATABASE_URL`でAPIから接続する。API起動時に`kikiori`スキーマと`kikiori.entity_store`を冪等に作成するため、再起動してもデータは保持される。

既存サービスの辞書ベースのRepository契約は`PostgresStore`が実装する。論理テーブル名、テナントID、エンティティIDをメタデータ列に保持し、ドメインの辞書全体はJSONB payloadとして保存する。テナント・Knowledge・Record単位の検索用インデックスを持つ。テストでは`DATABASE_URL=memory://test`を指定して`InMemoryStore`を使用する。

ナレッジ分析の集計結果、教育支援案、学習支援分析の下書きも同じPostgreSQLへ保存する。

## 3. 認証・認可

ローカルAPIは、次の開発トークンを`x-dev-token`または`Authorization: Bearer`で受け付ける。

| トークン | ユーザーID | ロール |
|---|---|---|
| `dev-admin` | `user-admin` | `admin` |
| `dev-manager` | `user-manager` | `knowledge_manager` |
| `dev-interviewer` | `user-interviewer` | `interviewer` |
| `dev-viewer` | `user-viewer` | `viewer` |

既定のリクエストヘッダーは`x-dev-token: dev-manager`である。無効なトークンはHTTP 401を返す。Vite開発画面では、左サイドバー下部のユーザーメニューにある「開発者設定」から4ロールを切り替えられる。切り替え後は画面を再読み込みする。ユーザーメニューには表示言語、表示テーマ、ヘルプ、操作ガイド、ログアウトもまとめている。表示テーマは`kikiori.color-theme`へライトまたはダークとして保存し、`data-theme`で配色トークンを切り替える。

`app/api/src/ai_interviewer_api/core/config.py`には旧来の`COGNITO_*`環境変数名が残っているが、現行の`auth/deps.py`は固定開発トークンだけを検証し、この設定を本番認証には使用していない。本番IdPは未決定であり、Microsoft Entra IDは候補として扱う。

保存する主要エンティティには、`tenantId`、`createdByUserId`、`updatedByUserId`、作成日時、更新日時を含める。取得時は認証ユーザーの`tenantId`でスコープする。

ローカル実装では、全ロール共通のナレッジ一覧サイドバー、Recordの担当者・明示閲覧許可による一覧絞り込み、記録の確認待ち・差し戻し・承認状態を実装している。ナレッジ管理者は最後に開いたナレッジ、または最初のナレッジの「インタビュー」を開く。`interviewer`は同一テナントの有効なナレッジ一覧から選択し、設定済みのナレッジで自分の新規Recordを開始できる。`viewer`はアクセス可能な記録からナレッジを構成し、最初のアクセス可能なナレッジの「インタビュー」を開く。サイドバーのナレッジ一覧は表示設定がない場合は作成日時順で表示し、利用者がナレッジごとのピンアイコンをクリックしてピン留めを切り替え、ドラッグハンドルで同じタググループ内のナレッジを並べ替えできる。ピン留めしたナレッジはサイドバー上部の「ピン留め」グループへまとめ、元のタググループには重複表示しない。設定は認証ユーザーとテナント単位のlocalStorageへ保存し、同じピン留め状態のナレッジを指定順で表示する。サイドバーでは、ナレッジをタグ別の折りたたみ可能なグループに分け、複数タグのナレッジは各グループに表示し、タグなしは「タグなし」グループへ表示する。グループはタグ名順で、「タグなし」を最後に表示する。ナレッジ配下では「インタビュー」「記録」をメイン画面上部の主タブとして表示し、管理者系ロールだけにヘッダーの主ボタン「インタビュー設定」を表示する。ナレッジ配下のページヘッダー左側にある「ナレッジ一覧」は、全ナレッジを表示する`/knowledge-dbs`へ遷移する。`/knowledge-dbs`では、最後に開いたナレッジのインタビューへ自動遷移せず、ナレッジ一覧を表示する。新規ナレッジは作成後に設定画面の「実行設定」を開き、設定が完了するまで記録作成を表示しない。質問項目は、通常時に順番・項目名・必須状態・詳細項目の要約を1行カードで表示し、選択した1件だけをアコーディオン形式で編集できる。項目追加直後は追加項目を展開し、AI提案は承認カードとして分離表示する。Frontendに全体の`/records`画面はなく、記録はナレッジ配下で確認する。記録詳細では`1200px`以上で会話を左、整理結果または質問リストを右に表示する。`901px`から`1199px`では会話と整理結果の2ペインを維持し、`900px`以下では整理結果または質問リストを右Drawerへ切り替える。業務フローとシステム要件では、整理結果を表示するサイドバーをデスクトップ時に全体の44%へ広げ、フローチャート領域を380pxへ拡大する。図の高さは狭い画面で段階的に縮小する。`system_requirement`の整理結果は「要件整理」「処理の流れ」タブで切り替え、「処理の流れ」内でフローチャートとシーケンス図を切り替える。「インタビュー」では管理者系ロールと対象者が設定済みナレッジから記録を開始し、対象者は自分の途中の記録を再開する。「記録」では権限範囲内の記録を確認し、管理者系ロールがインタビュー結果の編集、差し戻し、承認を行う。インタビュー完了時は記録状態を自動的に確認待ちへ変更する。記録削除は`admin`だけが実行できる。`KnowledgeDb`は内部の分類単位として保持し、ナレッジ一覧には表示しない。複数のDBがある場合の新規ナレッジ作成時だけ「業務領域」として選択できる。Record API、音声セッションAPI、記録に関連する提案APIで同じRecord認可判定を使用する。ユーザー一覧を永続管理する本番のユーザーディレクトリと、画面から担当者・閲覧者を選択する管理画面は未実装である。権限モデルは[利用者ワークスペースと認可アーキテクチャ](../architecture/access-control.md)を参照する。

ナレッジ作成ダイアログとナレッジ設定の「ナレッジ情報」にはタグ編集欄を表示する。入力可能な検索付きComboboxで既存タグを選択し、候補にない値はタグマスターへ新規作成できる。タグは1ナレッジにつき1つを設定し、保存時のタグ正規化と制限はBackendが正本として検証する。設定画面ではナレッジ名を折りたたみ時も見出しとして表示し、展開時は名前とタグを横並び、説明をその下に表示する。

現行UIのタグ欄は、既存タグの選択と新規タグ作成を同じ検索可能なComboboxで行う。タグは1ナレッジにつき1つ選択でき、未設定を選んでも`KnowledgeTag`マスターからは削除しない。

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
| `KnowledgeTag` | テナント単位で再利用するタグマスター | `Knowledge.tags`から参照され、紐付け解除では削除しない |
| `Knowledge` | ナレッジとインタビュー設定 | `KnowledgeDb`に属し、タグ、Field、Record、Documentを保持 |
| `InterviewPromptProfile` | 実インタビューの追加カスタマイズ | テナントに属する |
| `KnowledgeField` | 固定フォームの質問項目 | `Knowledge`に属する |
| `InterviewRecord` | 1回のインタビュー記録 | `Knowledge`に属し、`ownerUserId`で回答担当者、`viewerUserIds`で明示閲覧者を管理する。 |
| `VoiceSession` | Recordの音声セッション | `InterviewRecord`に属する |
| `VoiceTurn` | 音声セッション内の発話 | `VoiceSession`とRecordに属する |
| `AiProposal` | AIが作成した未承認候補 | Recordに属する |
| `Document` | ナレッジに紐づく文書メタデータ | `Knowledge`に属する |
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

設定画面には「基本設定」タブを置かず、ナレッジ名と説明を「ナレッジ情報」としてタブの上に表示する。タブは「質問項目」「実行設定」「事前知識」の3つとし、事前知識タブでは管理者が登録した参照文書の本文表示・削除・取り込み状態を管理する。設定保存操作はナレッジ情報、質問項目、実行設定をまとめて保存し、文書追加・本文表示・削除は各操作時に反映する。

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

### 5.3 タグ

* `GET /api/knowledge-tags`
* `POST /api/knowledge-tags`
* `PATCH /api/knowledge-tags/{tag_id}`
* `DELETE /api/knowledge-tags/{tag_id}`

タグマスターはテナント単位で管理し、`admin`または`knowledge_manager`だけが取得・作成できる。既存ナレッジのタグは取得時にマスターへ同期される。ナレッジの`tags`から値を削除しても、タグマスターの値は削除しない。

### 5.4 ナレッジ

* `GET /api/knowledge-dbs/{knowledge_db_id}/knowledges`
* `POST /api/knowledge-dbs/{knowledge_db_id}/knowledges`
* `GET /api/knowledges/{knowledge_id}`
* `PATCH /api/knowledges/{knowledge_id}`
* `DELETE /api/knowledges/{knowledge_id}`

ナレッジの作成・更新APIは`tags: string[]`を受け付ける。保存時に前後空白の除去、空文字の除外、大文字・小文字を区別しない重複除去を行う。タグは1ナレッジあたり20件以下、1件あたり40文字以下とし、制限超過時はHTTP 422を返す。取得APIは、既存の認証・認可スコープ内でナレッジの`tags`を返す。

### 5.5 質問項目と質問設計

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

`field-suggestions`は、生成前に同じテナント・Knowledgeの既存質問項目、承認済み記録・AI提案、取り込み済み文書・チャンクをBackendで検索する。検索結果は`retrieved_knowledge`として質問設計のStructured Output入力へ渡す。生成とValidatorは選択されたGPT-5.6 LunaまたはTerraを使用する。検索結果が1件以上ある場合、APIレスポンスに`retrievedSources`を含める。検索結果が0件の場合、このキーを返さない。

通常の固定項目インタビューと構造化インタビューの次質問生成も、`interview_document_retrieval`の共通検索を利用する。`indexed`または既存Workerの取り込み完了状態にある同一テナント・同一Knowledgeの文書・チャンクだけを質問コンテキストへ渡し、質問には`retrievedSources`を記録する。音声インタビューは`app/api`が生成した質問と出典を再利用する。文書アップロードはBackendで本文抽出・チャンク化を行い、設定された文書Repositoryへ保存する。

質問対象の値が取り込み済み文書に明示されている場合、共通の`QuestionGenerationOutput`が`documentCandidateValue`と`documentCandidateSourceIds`を返す。Backendは検索結果への値の出現と出典IDを検証したうえで、`candidateSource=document_reference`、`answerState/status=AWAITING_CONFIRMATION`の仮候補として保存する。初回質問は「文書では○○となっています。この内容で合っていますか？」という確認事項になり、明示承認後だけ正式回答へ移る。値が文書にない場合、文書の取り込みが完了していない場合、または`retrievalPolicy=never`の場合は通常質問へ戻る。通常、構造化、音声のすべてでこの状態と出典を共有し、音声はAPIが生成した確認質問をそのまま再生する。

### 5.6 インタビュー記録

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

`GET /api/records`は、認証ユーザーのロールとRecordの状態・担当・明示閲覧許可で返却対象を絞り込む。FrontendではこのAPIをアクセス可能なナレッジ一覧の構成に使用し、記録一覧画面の入口には使用しない。`GET /api/knowledge-dbs`、`GET /api/knowledge-dbs/{knowledge_db_id}/knowledges`、`GET /api/knowledges/{knowledge_id}/fields`は、`interviewer`にも同一テナントの有効なナレッジを読み取り専用で返す。`POST /api/knowledges/{knowledge_id}/records`は管理者系ロールと`interviewer`が実行できるが、対象者の場合はBackendが`ownerUserId`を認証ユーザーへ固定し、他ユーザーの担当者・閲覧者設定を拒否する。`PATCH /api/records/{record_id}`の担当者・閲覧者設定は管理者系ロールだけが実行できる。Recordの状態は`draft`、`in_progress`、`submitted`、`returned`、`approved`で管理する。新規Recordは`in_progress`で作成し、インタビュー状態が`completed`になったRecordは`submitted`へ自動変更する。`draft`は旧データ互換用であり、現在の画面には回答受付開始操作を表示しない。記録の削除は`admin`だけが実行できる。記録作成、テキスト開始・回答、音声開始ではインタビュー設定の完了を確認する。`viewer`の状態取得は表示用スナップショットとして処理し、状態を保存しない。

テキストメッセージ送信は`clientMessageId`による重複排除と`stateVersion`による状態競合検出を行う。

SSEの主なイベントは次のとおりである。

```text
stream_start
delta
stream_end
proposal_created
```

### 5.7 提案と承認

* `GET /api/records/{record_id}/proposals`
* `POST /api/proposals/{proposal_id}/approve`
* `POST /api/records/{record_id}/approve-all-proposals`
* `POST /api/records/bulk-approve`

AI提案は`draft`または`needs_review`で保存し、人の操作なしに`approved`へ変更しない。承認方式は`single`、`record_bulk`、`list_bulk`を区別する。

### 5.8 文書

* `GET /api/knowledges/{knowledge_id}/documents`
* `POST /api/knowledges/{knowledge_id}/documents`
* `POST /api/knowledges/{knowledge_id}/documents/upload`
* `GET /api/documents/{document_id}/content`
* `DELETE /api/documents/{document_id}`

JSON形式の文書登録は後続Worker向けのメタデータ登録として残し、ファイル本体を取り込む場合は`documents/upload`を利用する。アップロード経路は同期的に本文抽出・チャンク化・検索Repositoryへの保存まで行い、取り込み状態を`indexed`または`failed`へ更新する。本文表示と削除は同じテナントの管理者だけが実行でき、削除時は検索Repositoryの本文・チャンクも併せて削除する。

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

### 5.11 ナレッジ分析と教育支援

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
| PostgreSQL | `localhost:5432`（DB: `kikiori`） |

ソースコードはbind mountされ、Web、API、Voiceはホットリロードする。通常はWebのURLだけをブラウザで開く。

PostgreSQLのデータは`postgres-data`ボリュームへ保存される。既定の接続先は`postgresql://kikiori:kikiori@localhost:5432/kikiori`で、Compose内のAPIからはホスト名`postgres`を使用する。

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

Composeを使用しない場合の詳細は、リポジトリ直下の`README.md`を参照する。

## 8. 未実装・制限

次の項目はプロダクト仕様上の将来または本番対応であり、現在のローカル実装には含まれない。

* 本番IdP（Entra ID候補）のJWT検証
* SQSの実接続とAPI・Worker間の実ジョブ連携
* 承認済み項目値を正式ナレッジへ反映する永続モデル
* 本番向けの監視、負荷対策、マルチテナント運用

文書ファイルは`POST /api/knowledges/{knowledge_id}/documents/upload`で受信し、PDF、DOCX、XLSX、PPTX、CSV、Markdown、TXTから本文を抽出してチャンク化する。文書メタデータと取り込み状態はPostgreSQLに保存し、本文・チャンクは`DOCUMENT_KNOWLEDGE_BACKEND`に応じてPostgreSQLまたはElastic Cloudへ保存する。Elastic Cloudでは起動時に設定済みインデックスの存在と接続を確認する。既存文書の切替先への移行は、別途再インデックス操作が必要である。

未実装項目を実装した場合は、恒久的な仕様を`docs/spec.md`または該当する`docs/architecture/`・`docs/reference/`へ反映し、この文書の実装状況も更新する。
