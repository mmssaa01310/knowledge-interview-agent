# 利用者ワークスペースと認可アーキテクチャ

## 1. 目的

インタビュー対象者が回答する画面と、ナレッジ管理者が設定・承認する画面を分離する。分離の対象は表示だけではなく、Frontendの画面遷移、Backend API、音声セッション、保存データの参照範囲を含む。

プロダクト上の振る舞いと権限は[プロダクト仕様](../spec.md)の「7. 利用者、画面、認可」を正本とする。この文書は、その仕様を実装する責務分離を定義する。

## 2. 正本データ

```text
InterviewRecord
├── ownerUserId                 # 回答を担当するインタビュー対象者
├── InterviewState              # 質問ごとの回答状態
├── Message / VoiceTurn         # 会話と確定発話
├── AiProposal                  # AI提案と承認状態
└── record status                # 記録のレビュー状態
```

対象者向け画面と管理者向け画面は、同じ`InterviewRecord`を参照する。対象者用の記録コピー、管理用の記録コピー、画面専用の承認状態を作成してはいけない。

質問ごとの回答状態と記録のレビュー状態は別の状態機械とする。

```text
質問ごとの回答: UNANSWERED → CANDIDATE_PENDING → AWAITING_CONFIRMATION → CONFIRMED
記録のレビュー: in_progress → submitted → returned → in_progress → submitted → approved
```

`draft`は旧データ互換用の状態であり、現在の画面から新規作成しない。Backendには旧`draft`を`in_progress`へ移行する処理を残すが、利用者向けの状態変更操作として表示してはいけない。

## 3. Frontendの責務

Frontendは`GET /api/me`で取得したロールを使用し、ワークスペースと操作を表示制御する。

画面上のナビゲーションは、全ロールで「左サイドバー1列＋メイン画面」とする。グローバルナビゲーション列とワークスペースナビゲーション列を併置してはいけない。全ロールで左サイドバーに権限範囲内のナレッジ一覧を直接表示する。

管理者系ロールがナレッジ管理の入口を開いた場合、最後に開いたナレッジの「インタビュー」を開く。最後に開いたナレッジがない場合は最初のナレッジを開き、ナレッジがない場合だけ作成案内を表示する。ナレッジ一覧をサイドバーとメイン画面へ重複表示してはいけない。

| ロール | 初期遷移先 | 使用する論理ワークスペース |
|---|---|---|
| `admin` | `/knowledge-dbs` | ナレッジ管理、システム設定。記録はナレッジ内で扱う |
| `knowledge_manager` | `/knowledge-dbs` | ナレッジ管理。記録はナレッジ内で扱う |
| `interviewer` | `/knowledge-dbs` | 同一テナントの有効なナレッジ。自分の記録はナレッジ内で開始・回答する |
| `viewer` | `/knowledge-dbs` | 閲覧許可済み記録を含むナレッジ。記録はナレッジ内で扱う |

画面ルートは次の役割で分ける。

| 画面ルート | 役割 |
|---|---|
| `/knowledge-dbs` | ナレッジ管理の一覧 |
| `/knowledge-dbs/{knowledge_db_id}/knowledges/{knowledge_id}/interview` | 設定状態を確認し、記録を作成するインタビュー画面 |
| `/knowledge-dbs/{knowledge_db_id}/knowledges/{knowledge_id}/records` | 全記録の一覧、インタビュー結果の確認・編集、差し戻し、承認 |
| `/knowledge-dbs/{knowledge_db_id}/knowledges/{knowledge_id}/settings` | 主タブ外のナレッジ情報、質問項目、実行設定、事前知識 |
| `/dashboard` | `admin`と`knowledge_manager`のテナント内集計、確認優先度、教育支援案レビュー |
| `/settings` | システム設定 |

全ロールは左サイドバーの権限範囲内のナレッジ一覧から`Knowledge`を選択し、ナレッジ配下の「インタビュー」「記録」をメイン画面上部の主導線として使用する。`interviewer`には同一テナントの有効なナレッジ、`viewer`には閲覧許可された記録が存在するナレッジだけを表示する。`interviewer`と`viewer`には設定画面、文書管理、ナレッジ作成を表示しない。Frontendの`/records`と`/records/{record_id}`ルートは提供しない。設定画面は主タブに表示せず、ナレッジヘッダーから開く補助画面とする。

`KnowledgeDb`は`Knowledge`をまとめる内部の管理単位であり、ナレッジ管理の主一覧には表示しない。複数の`KnowledgeDb`がある場合だけ、ナレッジ作成時に「業務領域」として保存先を選択できる。

インタビュー設定の完了条件は、保存済みの`Knowledge.interviewPlan.profile`と`Knowledge.interviewPlan.modelId`である。両方が有効な値でなければ、Frontendは管理者系ロールと対象者の記録作成・開始操作を表示または有効化してはいけない。

Frontendは権限のないナビゲーションやボタンを表示してはいけない。Frontendの表示制御だけを根拠に操作を許可してはいけない。

## 4. Backendの責務

Backendはすべてのユーザー向けAPIで、次の順に確認する。

1. 認証済みユーザーであること。
2. 対象データの`tenantId`がユーザーのテナントと一致すること。
3. 操作を許可するロールであること。
4. `InterviewRecord`操作では、ロールに応じた担当または閲覧許可の範囲に入ること。
5. 記録状態で許可された操作であること。

記録作成、旧`draft`から`in_progress`への互換移行、テキストの開始・回答、音声セッションの開始では、対象`Knowledge`のインタビュー設定完了も確認する。インタビュー状態が`completed`になった場合、Backendは記録状態を`submitted`へ変更する。設定未完了時は`409 interview_configuration_required`を返す。

`interviewer`が記録へアクセスする場合、`ownerUserId`が認証ユーザーIDと一致しなければならない。`interviewer`は有効なナレッジから記録を新規作成できるが、Backendは`ownerUserId`を認証ユーザーへ固定し、他ユーザーの担当者・閲覧者を指定する要求を拒否する。`admin`と`knowledge_manager`はテナント内の管理対象記録にアクセスできる。`viewer`は明示的に閲覧を許可された`approved`状態の記録だけを取得できる。

記録一覧APIは、Frontendで一覧を取得してから絞り込んではいけない。Backendがロールごとの対象記録だけを返す。

ProcessModelの手動保存と編集指示は、`admin`と`knowledge_manager`だけに許可する。Backendは通常のRecord参照に加えて`manage`認可を確認し、`approved`状態のRecordを編集対象から除外する。`interviewer`と`viewer`はProcessModelを全画面で閲覧できるが、編集APIを実行できない。編集APIは`baseProcessVersion`を検証し、変更した要素、実行ユーザー、使用モデル、指示内容を監査ログへ保存する。

音声セッションの作成・取得・停止・WebRTC接続では、Record APIと同じ記録アクセス判定を再利用する。`VoiceSession.ownerUserId`だけでRecordへのアクセスを許可してはいけない。

## 5. 記録状態の遷移責務

| 遷移 | 実行ロール | Backendが確認する条件 |
|---|---|---|
| `draft` → `in_progress` | `admin`、`knowledge_manager` | 旧データ互換処理。現在の画面から利用者向け操作として実行しない。 |
| `in_progress` → `submitted` | 担当`interviewer`、`admin`、`knowledge_manager` | Profileの完了条件を満たしている。 |
| `submitted` → `returned` | `admin`、`knowledge_manager` | 差し戻し理由が空でない。 |
| `submitted` → `approved` | `admin`、`knowledge_manager` | 承認対象の必須情報と承認条件を満たしている。 |
| `returned` → `in_progress` | 担当`interviewer`、`admin`、`knowledge_manager` | 差し戻し後の再開操作である。 |

`approved`は終端状態である。再編集が必要な場合は、承認済み記録を直接書き換えず、管理者が新しい記録を作成する。

インタビュー状態が`completed`になった場合の`in_progress` → `submitted`は、Backendが自動実行する。Frontendに提出操作を残してはいけない。記録の削除は`admin`だけに許可する。

## 6. 実装上の分離

* `app/web`: ロール別ナビゲーション、画面上の操作制御、権限不足時の案内を担当する。
* `app/api/auth`と`app/api/core`: 認証済みユーザー、ロール、テナント、記録アクセスの共通判定を担当する。
* `app/api/routers`: 共通認可判定を呼び出し、個別ルートで独自のロール判定を複製しない。
* `app/api/services`: 記録状態遷移と承認条件を検証する。UIから渡された状態値だけを信頼してはいけない。
* `app/voice`: APIが許可したRecordとVoiceSessionだけを扱う。インタビュー評価や認可ロジックを複製しない。

`viewer`が記録の状態を表示する場合、Backendは表示用の一時スナップショットだけを返す。状態の初期化、移行、更新を保存してはいけない。

`GET /api/admin/dashboard`は`admin`または`knowledge_manager`に許可し、所属テナント内の記録を集計する。画面上の名称は「ナレッジ分析」とし、内部APIのパスは互換性のため維持する。現行の権限モデルでは`knowledge_manager`にナレッジ単位の割当がないため、集計範囲はテナント内とする。`POST /api/admin/learning-analysis`、`GET /api/admin/learning-analysis`、学習支援分析の編集・確認済み化は`admin`または`knowledge_manager`だけに許可する。学習支援分析は選択した1ナレッジの2件以上の記録に限定し、対象者の比較・点数化・順位付け・理解度や能力の断定を行わない。`POST /api/admin/records/{record_id}/guidance`、`GET /api/admin/records/{record_id}/guidance`、教育支援案の編集・公開・非公開は、`admin`または対象ナレッジを管理する`knowledge_manager`だけに許可する。`GET /api/records/{record_id}/guidance`は担当`interviewer`本人に対し、公開済みの学習案内だけを返す。`viewer`には教育支援案を返さない。教育支援案の公開レスポンスからは指導者向けメモを除外する。

## 7. テスト要件

ロール分離の実装では、各ユーザー向けに少なくとも次を確認する。

* `interviewer`が自分以外の記録を一覧・取得・回答・音声接続できない。
* `interviewer`が同一テナントの有効なナレッジを読み取り、新規記録を自分の所有者として作成できる。
* `interviewer`が無効またはアーカイブ済みのナレッジから記録を作成できない。
* `interviewer`が自分で作成する記録に他ユーザーの担当者・閲覧者を設定できない。
* `interviewer`がナレッジ設定、文書管理、実行設定、提案承認を実行できない。
* `knowledge_manager`が管理対象の記録をレビュー、差し戻し、承認できる。
* `viewer`が許可されていない記録と、`approved`以外の記録を取得できない。
* 権限のない画面ルートを直接開いた場合も、操作可能なデータを取得できない。
* `approved`状態の記録を対象者が変更できない。
* `interviewer`と`viewer`がProcessModelの手動保存・編集指示を実行できない。
* 管理者系ロールがProcessModelを保存でき、古いバージョンは409で拒否される。
* `interviewer`と`viewer`がナレッジ分析画面（集計API）を取得できない。
* `interviewer`と`viewer`がナレッジ分析または学習支援分析を取得・生成できない。
* 学習支援分析が異なるナレッジの記録を混在させず、2件未満の記録では生成できない。
* `knowledge_manager`が管理対象外ナレッジの教育支援案を取得・公開できない。
* 公開前の教育支援案と指導者向けメモが`interviewer`へ返らない。
