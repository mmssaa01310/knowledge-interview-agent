# Feature Specification: AI Interview Knowledge Capture Current Baseline

**Feature Branch**: `001-ai-interview-knowledge-app`

**Created**: 2026-07-07

**Status**: Implemented Baseline

**Input**: Current implementation under `app/web`, `app/api`, `app/worker`

## User Scenarios & Testing

### User Story 1 - ナレッジDB配下でナレッジとヒアリング項目を管理する (Priority: P1)

ナレッジ管理者として、ナレッジDBを作成し、その配下にナレッジとヒアリング項目を定義したい。これにより、記録、文書、参照チャットの単位を整理できる。

**Independent Test**: `GET/POST/PATCH/DELETE /api/knowledge-dbs`、`/api/knowledge-dbs/{id}/knowledges`、`/api/knowledges/{id}/fields` が期待どおり動き、Web から一覧と編集ができることを確認する。

**Acceptance Scenarios**:

1. **Given** `knowledge_manager` ロールのユーザーがいる, **When** ナレッジDBを作成する, **Then** テナント配下の一覧に追加される。
2. **Given** ナレッジDBが存在する, **When** ナレッジを作成する, **Then** ナレッジ詳細、記録、文書、項目設定の導線から参照できる。
3. **Given** ナレッジが存在する, **When** ヒアリング項目を追加・更新・削除する, **Then** 項目一覧に反映される。
4. **Given** ナレッジ設定画面を開いている, **When** AI設定アシストを実行する, **Then** `generate-fields` または `field-suggestions` の結果を項目編集に利用できる。

### User Story 2 - 記録に対してモックAI提案を生成し承認する (Priority: P1)

インタビュアーとして、ナレッジに紐づく記録を作成し、メッセージ送信後にモックSSE応答と提案を受け取り、個別承認または全承認したい。

**Independent Test**: 記録作成、`POST /api/records/{id}/messages`、`GET /api/records/{id}/stream`、`POST /api/proposals/{id}/approve`、`POST /api/records/{id}/approve-all-proposals` を順に確認する。

**Acceptance Scenarios**:

1. **Given** ナレッジ配下に記録がある, **When** メッセージを送信する, **Then** 提案が1件保存される。
2. **Given** 記録に提案が存在する, **When** ストリームAPIを読む, **Then** `stream_start`, `delta`, `delta`, `stream_end`, `proposal_created` の順でイベントを受信できる。
3. **Given** `needs_review` の提案がある, **When** 個別承認する, **Then** 提案の `status` が `approved` になり監査ログが記録される。
4. **Given** 同一記録に複数提案がある, **When** 全承認する, **Then** 承認可能な提案だけが `record_bulk` で承認され、スキップ理由が返る。

### User Story 3 - 文書登録と既読状態を分けて管理する (Priority: P2)

ナレッジ管理者または閲覧者として、ナレッジに文書メタデータを登録し、取り込み状態とユーザー別既読状態を別々に扱いたい。

**Independent Test**: `POST /api/knowledges/{id}/documents`、`GET /api/knowledges/{id}/documents`、`POST /api/documents/{id}/read`、`POST /api/documents/{id}/acknowledge` を確認する。

**Acceptance Scenarios**:

1. **Given** ナレッジが存在する, **When** 文書を登録する, **Then** 文書は `uploaded` で作成され、キュー投入処理により `queued` へ更新される。
2. **Given** 文書が存在する, **When** 読了状態を更新する, **Then** 文書本体とは別に `document_read_status` が保存される。
3. **Given** 文書が存在する, **When** 確認済みにする, **Then** `readStatus=acknowledged` と `acknowledged=true` が保存される。

### User Story 4 - ローカルチャットボット設定で参照チャットを行う (Priority: P2)

ユーザーとして、ブラウザ内でチャットボット設定を持ち、参照対象のナレッジDB、ナレッジ、文書、除外文書、モデルID、件数上限を指定して質問したい。

**Independent Test**: Web でチャットボットを作成し、参照設定画面で対象を選び、`POST /api/chats/{chatId}/messages` の回答と citations を確認する。

**Acceptance Scenarios**:

1. **Given** ローカルチャットボットがある, **When** 参照設定を変更する, **Then** 設定はブラウザメモリ上の状態に反映される。
2. **Given** 参照対象が指定されている, **When** チャット送信する, **Then** `active` なナレッジDB・ナレッジと `completed` の文書だけが回答コンテキストに使われる。
3. **Given** 参照対象がない, **When** チャット送信する, **Then** 根拠不足を示すフォールバック回答が返る。

## Edge Cases

- `x-dev-token` または Bearer の開発トークンが無効な場合は 401 を返す。
- 別テナントの ID を直接指定しても scoped lookup で取得できない。
- すでに `approved` / `rejected` の提案は再承認時にスキップされる。
- 文書登録はファイル本体アップロードではなくメタデータ登録のみである。
- 参照チャットの citations は現状文字列配列であり、構造化根拠オブジェクトではない。

## Requirements

### Functional Requirements

- **FR-001**: システムは `authorization: Bearer <dev-token>` または `x-dev-token` による開発用認証を提供しなければならない。
- **FR-002**: システムは `admin`, `knowledge_manager`, `interviewer`, `viewer` の4ロールを扱わなければならない。
- **FR-003**: システムは主要データに `tenantId`, `createdByUserId`, `updatedByUserId`, `createdAt`, `updatedAt` を保持しなければならない。
- **FR-004**: システムはナレッジDBの作成、一覧、詳細、更新、削除を提供しなければならない。
- **FR-005**: システムはナレッジDB配下でナレッジの作成、一覧、詳細、更新、削除を提供しなければならない。
- **FR-006**: システムはナレッジ配下でヒアリング項目の作成、一覧、更新、削除を提供しなければならない。
- **FR-007**: システムは `generate-fields` の固定候補生成と `field-suggestions` の提案生成を提供しなければならない。
- **FR-008**: システムはナレッジ配下で記録の作成、一覧、詳細、更新、削除を提供しなければならない。
- **FR-009**: システムは記録へのメッセージ送信時にモック提案を生成し保存しなければならない。
- **FR-010**: システムは記録単位の SSE モック配信を提供しなければならない。
- **FR-011**: システムは提案一覧、個別承認、記録内全承認、記録一覧一括承認を提供しなければならない。
- **FR-012**: システムは承認操作時に監査ログを保存しなければならない。
- **FR-013**: システムはナレッジ単位の要約ドラフト生成と、記録単位の要約提案生成を提供しなければならない。
- **FR-014**: システムはナレッジ配下で文書メタデータ登録と一覧取得を提供しなければならない。
- **FR-015**: システムは文書登録時に `queue_document` を呼び、取り込み状態を `queued` へ更新しなければならない。
- **FR-016**: システムは文書ごととは別に、ユーザー別既読状態として `read` と `acknowledge` 操作を提供しなければならない。
- **FR-017**: システムは `POST /api/chats` と `POST /api/chats/{chatId}/messages` による参照チャットを提供しなければならない。
- **FR-018**: システムは参照チャットで `active` なナレッジDB・ナレッジ、`completed` の文書だけをコンテキストに含めなければならない。
- **FR-019**: システムは Web でナレッジ系画面、チャットボット系画面、設定画面を提供しなければならない。
- **FR-020**: システムはチャットボット設定をブラウザ内のローカル状態として保持し、サーバー永続化しなくてよい。
- **FR-021**: システムは API 永続化にインメモリストアを使用し、テナント単位のフィルタを必ず適用しなければならない。
- **FR-022**: システムは Worker に文書取り込み状態のモック遷移関数を持たなければならない。

### Key Entities

- **KnowledgeDb**: ナレッジの入れ物。名前、説明、言語、デフォルトモデル、状態、件数集計を持つ。
- **Knowledge**: ナレッジDB配下の管理単位。説明、要約、systemPrompt、用途、対象設備、状態を持つ。
- **KnowledgeField**: ナレッジに属するヒアリング項目。入力型、必須、AI質問例、並び順を持つ。
- **InterviewRecord**: ナレッジに属する聞き取り記録。タイトル、対象設備、工程、承認件数を持つ。
- **AiProposal**: 記録に対するAI提案。`structuredData`, `status`, `confidence`, `approvalMethod` を持つ。
- **Document**: ナレッジに紐づく文書メタデータ。取り込み状態、進捗率、件数を持つ。
- **DocumentReadStatus**: 文書に対するユーザー別既読状態。
- **AuditLog**: 作成、更新、削除、承認の監査ログ。
- **LocalChatbot**: Web のローカル状態として保持する参照チャット設定。

## Success Criteria

### Measurable Outcomes

- **SC-001**: ログイン相当の開発トークンで `/api/me` がユーザー情報を返す。
- **SC-002**: ナレッジDB、ナレッジ、ヒアリング項目の CRUD が Web と API の両方で確認できる。
- **SC-003**: 記録メッセージ送信後に SSE モックイベント列と提案作成が確認できる。
- **SC-004**: 個別承認、記録内全承認、一覧一括承認で件数またはスキップ結果が返る。
- **SC-005**: 文書一覧とユーザー既読状態が別管理で更新できる。
- **SC-006**: 参照チャットが citations を返し、参照対象未指定時はフォールバック回答になる。

## Assumptions

- 現在の API 永続化はインメモリストアであり、Elasticsearch 永続化は未反映である。
- 現在の認証は Cognito 本番連携ではなく、開発トークン方式である。
- 現在のチャットボット設定はサーバー保存せず、ブラウザメモリにのみ保持する。
- 現在の Worker は段階遷移のモック実装であり、SQS 実接続や実ファイル処理は行わない。
