# AIインタビュー / ナレッジ構造化アプリ仕様

## 1. アプリの目的

このアプリは、製造熟練者、業務担当者、システム開発関係者へのAIインタビューを行い、回答を構造化ナレッジとして蓄積・承認するWebアプリである。

単なる会話UIではなく、以下を目的とする。

* 熟練者へのAIインタビュー
* 会話内容からの構造化ナレッジ抽出
* AI提案内容の人による修正・承認
* ドキュメント取り込みによるナレッジ補強
* 将来的な外部DB連携

## 2. 主な機能

* AIインタビュー
* 目的別インタビュー（定型情報、業務フロー、システム要件）
* 質問項目設定
* テキストによるインタビュー
* リアルタイム音声によるインタビュー
* 会話内容の構造化
* AI提案カード生成
* 人による修正・承認
* 個別承認
* 記録単位の全承認
* 一覧からの一括承認
* ドキュメント追加
* ドキュメント取り込み状態管理
* ユーザーごとの既読・確認済み状態管理
* Cognitoログイン
* ログインユーザーに紐づいたデータ保存
* Elasticsearch中心の検索・保存
* 将来の外部DB送信

### 2.1 AI機能の分類

本アプリのAI機能は以下に分ける。

* 質問設計
* インタビュー実行

質問設計は、ユーザーから現場の答えを集める機能ではなく、熟練者に聞く質問項目を作成する機能である。

インタビュー実行は、熟練者との会話を進め、回答を構造化候補にする。

インタビューエージェントは不足情報、矛盾、Applicabilityを抽出し、Backendが決定した質問対象について質問文を生成する。次の質問対象、質問優先順位、完了判定はBackendが決定する。

AI機能の責務分離は、`docs/architecture/agents/agent-architecture.md`に従う。

個別エージェントの詳細仕様は、以下に従う。

* `docs/agents/agent-behavior-policy.md`
* `docs/agents/interview-agent-strands.md`
* `docs/agents/question-design-agent-strands.md`

### 2.2 インタビュー入出力経路

インタビュー実行は、以下の入出力経路に対応する。

* テキスト入力・テキスト出力
* 音声入力・音声出力

テキスト経路と音声経路は、同一のインタビュー状態、質問項目、回答評価、RAG、構造化提案処理を利用する。

音声経路専用の回答評価、質問進行、構造化処理を別実装してはいけない。

音声機能は、既存のインタビュー機能に対する別の入出力経路として扱う。

`system_requirement`の実行画面はテキストチャット専用とする。音声会話の開始操作、音声接続状態、音声再生操作を表示してはならない。これは音声経路とテキスト経路の状態処理を分けることを意味しない。将来このProfileへ音声入力を提供する場合も、同じ共通Interview Coordinatorを使用する。

回答処理はテキスト・音声共通の状態機械を使用する。生発話は監査用会話履歴として保持し、AI評価後の候補は明示確認まで正式回答へ保存しない。状態は`UNANSWERED`、`CANDIDATE_PENDING`、`AWAITING_CONFIRMATION`、`CONFIRMED`の順で管理し、`answerSummary`と`completedFieldIds`を更新できるのは`CONFIRMED`への遷移時だけとする。

`retrievalPolicy`は外部ナレッジ検索の実行可否だけを制御する。`never`でも発話意図、質問との関連性、十分性、正規化のAI評価を省略してはいけない。

質問設計機能は音声インタビューの対象外とし、音声経路から変更してはいけない。

`fixed_form`と`business_process`のインタビュー画面は、設定済み項目を「質問リスト」として表示する。`system_requirement`では通常の質問リストを表示せず、RequirementStateとProcessStateの確認状況を統合した「要件整理」パネルを表示する。

`system_requirement`の「要件整理」パネルは、システム要件の必須5項目、業務フローの有無、`process=present`確定後のProcess必須項目と追加確認項目を、Backendの状態順に表示する。各項目には`未確認`、`候補`、`確認中`、`確定`、`対象外`の状態を表示し、現在の質問対象を強調する。候補値は確認前の情報として表示し、パネルから正式回答へ直接確定してはならない。

`system_requirement`では、別の「システム要件ドラフト」一覧と質問リストを同時に表示してはならない。要件整理パネルを唯一の確認状況ナビゲーションとする。フローチャートとシーケンス図は、要件整理パネルとは分離した処理モデル表示として表示する。
深掘りのための追加質問は、別タブ、別カード、別回答欄へ表示しない。
聞き返しで得た回答内容は、対象の設定項目の回答要約へ統合する。
質問または項目へ紐付けられない会話履歴を「過去データ」や「分類不能な過去データ」として回答欄へ表示してはいけない。
ユーザーは確定済み回答をキーボードで編集し、対象項目へ明示的に保存できる。
編集操作によって未確認の回答を確定済みに変更してはいけない。

### 2.3 インタビュー実行画面

インタビュー実行画面は、利用者が「いま何を答えればよいか」「どこまで整理できたか」「図が表示されない理由」を画面内で確認できる構成とする。

画面幅が広い場合は、次の2列で表示する。

* 左列: インタビューの進捗
* 右列: 会話、その下に処理の流れ

画面幅が狭い場合は、左列、会話、処理の流れの順に縦へ並べる。画面幅が広い場合に進捗項目が画面高さを超えるときは、左列内でスクロールできるようにする。画面幅が狭い場合は、画面全体の通常の縦スクロールで操作できるようにする。

右列の会話には、次の質問対象を1件だけ「いま確認していること」として表示する。質問対象がない場合は、インタビュー開始前、回答整理中、完了後の状態に応じた案内を表示する。

`system_requirement`では、左列に「要件整理」パネルを1つだけ表示する。要件整理パネルには、システム要件、業務フローの有無、業務フローの詳細の確認状況を表示する。別の要件ドラフト一覧と質問リストを同時に表示してはならない。

処理の流れは、会話から抽出したProcessStateを表示用に変換したビューである。LLMが生成したMermaidコード、React Flowの座標、または会話本文を直接表示してはならない。`process=unknown`では業務フローの確認中であることだけを表示し、`process=not_applicable`では処理フローなしで要件を整理することを表示する。`process=present`では情報の収集状況に応じてフローチャートとシーケンス図を表示する。

候補値や未確定の図要素は、確定値・確定要素と見た目で区別する。画面には`active`、`version`、`ProcessState`などの内部管理用語を表示してはならない。長い候補値は要約表示し、利用者の操作で全文を確認できるようにする。

## 3. AI提案・承認仕様

AI提案は必ず`draft`または`needs_review`として保存する。

人の操作なしに`approved`にしてはいけない。

承認方式は以下を区別する。

* `single`: 個別承認
* `record_bulk`: 記録内の全承認
* `list_bulk`: 記録一覧からの一括承認

全承認・一括承認では、以下を対象外にする。

* 必須項目未入力
* 権限なし
* 信頼度不足
* 差し戻し済み
* エラー状態
* すでに承認済み

監査ログには以下を保存する。

* 実行ユーザーID
* 実行日時
* 対象`record_id`
* 対象`proposal_id`または`field_value_id`
* 承認方式
* 成功件数
* 失敗件数
* 失敗理由
* AI提案値
* 人の修正値

テキスト経路と音声経路で、承認仕様を変更してはいけない。

音声インタビューから生成されたAI提案についても、人による承認を必須とする。

## 4. ドキュメント仕様

ドキュメントは、取り込み状態とユーザー既読状態を分けて管理する。

### 4.1 取り込み状態

* `uploaded`
* `queued`
* `processing`
* `text_extracted`
* `chunked`
* `embedding`
* `indexed`
* `completed`
* `failed`

### 4.2 ユーザー既読状態

* `unread`
* `opened`
* `reading`
* `read`
* `acknowledged`

`read`は、開いた・読了した状態を表す。

`acknowledged`は、ユーザーが明示的に確認ボタンを押した状態を表す。

取り込み状態と既読状態を、同じカラムや同じ状態値で管理してはいけない。

## 5. ストリーミング仕様

### 5.1 テキストチャット

テキストチャットのストリーミングにはSSEを使用する。

AI回答中はテキストをストリーム描画し、AI回答完了後に構造化提案カードを表示する。

推奨イベントは以下とする。

* `stream_start`
* `delta`
* `stream_end`
* `proposal_created`
* `error`

### 5.2 リアルタイム音声会話

リアルタイム音声会話の音声通信にはWebRTCを使用する。

WebRTC接続の確立や制御に必要なシグナリング通信には、WebSocketまたはHTTPSを使用できる。

音声データそのものを、通常のテキストSSEへ混在させてはいけない。

リアルタイム音声会話では、以下に対応する。

* マイク音声のリアルタイム送信
* ユーザー発話の文字起こし
* AI応答音声のストリーミング再生
* ユーザー発話によるAI音声の割り込み
* 現在の質問項目との回答の紐付け
* 音声セッション状態の管理
* インタビュー完了時の終了案内

音声経路で取得した確定文字起こしは、テキスト経路と同じインタビュー処理へ渡す。

音声RuntimeはNova Sonic方式とAmazon Transcribe Streaming + Amazon Polly方式を
共通契約の下で分離し、Voice Session作成時に選択できるものとする。接続中Sessionの
Provider自動fallbackは行わない。

Assistant音声への割り込みは音声出力の停止であり、コミット済みUser Turnの取消しを意味しない。
未コミットTurnだけを取消可能とし、明示的な訂正は新しいTurnとして状態更新する。

以下の処理の正本は、音声処理サービスではなく既存のバックエンドに置く。

* 質問進行
* 回答評価
* 回答不足の判定
* 深掘り質問の決定
* RAG
* インタビュー状態更新
* 構造化提案生成
* インタビュー完了判定

リアルタイム音声機能の詳細構成は、以下に従う。

* `docs/architecture/voice/realtime-voice.md`
* `docs/plans/realtime-voice-v1.md`

`docs/plans/realtime-voice-v1.md`は実装計画であり、確定仕様や業務ルールの正本として扱わない。

## 6. プロンプト仕様

このアプリでは、AI関連のシステムプロンプトを用途ごとに分離して管理する。

### 6.1 質問項目設計用プロンプト

質問項目を埋める、または設計するためのシステムプロンプトは、開発者管理の固定プロンプトとする。

* ユーザーは編集できない。
* 質問項目設計チャットや質問項目提案でのみ利用する。
* 熟練者へのAIインタビュー用プロンプトと混在させない。
* JSON出力制約、項目提案ガード、確認質問ルールはこの系統に含める。

### 6.2 AIインタビュー用プロンプト

熟練者にインタビューするためのプロンプトは、以下の2層に分ける。

* 開発者管理の固定ベースプロンプト
* ユーザーが追加で設定する特化型カスタマイズプロンプト

固定ベースプロンプトは、以下のような共通前提だけを持つ。

* AIインタビューアであること
* 端的かつ過不足なく質問すること
* 回答不足時は追加質問すること
* 推測で埋めず、必要なら確認すること

固定ベースプロンプトはユーザー編集不可とする。

テキスト経路と音声経路で、インタビューの目的や質問進行ルールを変えてはいけない。

音声出力に必要な話し方や発話制御は、インタビューの意味処理とは分離して管理する。

### 6.3 ユーザー追加カスタマイズ

ユーザーは、AIインタビューの聞き方を調整するための追加カスタマイズプロンプトを設定できるようにする。

* これは「どう質問するか」を調整するためのものである。
* 質問項目設計用プロンプトとは絶対に混ぜない。
* 質問項目設計チャット、質問項目設計API、項目提案用のcontextへ流してはいけない。
* 質問項目設計では、この追加カスタマイズの文面を入力文脈として参照してはいけない。
* 固定ベースプロンプトを上書きするのではなく、追加で連結する。
* 現場固有の観点、業務特化の深掘り方、聞き方の重点を記述対象とする。

追加カスタマイズの適用先は、実インタビュー実行時のAI返答生成経路に限定する。

質問項目設計AIは、専用の固定プロンプトと質問項目設計用の文脈だけを使う。

追加カスタマイズは、テキスト経路と音声経路の両方に同じ条件で適用する。

### 6.4 将来拡張

将来的に、ユーザー追加カスタマイズは独立した再利用可能エンティティとして扱えるようにする。

想定する拡張は以下。

* 1つのナレッジDBに複数カスタマイズを登録できる
* 作成したカスタマイズを保存できる
* 別のナレッジDBや別ノウハウでも参照・再利用できる
* カスタマイズ作成を支援するAIチャット画面を用意する

この作成支援AIチャットは、質問項目設計AIとは別系統とし、混在させない。

この作成支援AIチャットの出力や下書きも、質問項目設計チャットへ自動連携してはいけない。

### 6.5 テンプレートの役割

テンプレートは、ユーザーが追加カスタマイズを書きやすくするための支援として扱う。

* 固定ベースプロンプトの代替にしない。
* 完成済みの全体プロンプトを保存する用途にしない。
* ユーザーが追加カスタマイズ欄へ流し込むためのたたき台とする。

## 7. 利用者、画面、認可

### 7.1 基本方針

インタビュー対象者が回答する「記録」と、ナレッジの定義・承認を行う「管理」を、画面と権限の両方で分離する。

同じ`InterviewRecord`を、対象者用と管理者用に複製してはいけない。インタビューの会話、回答状態、AI提案、承認履歴の正本は常に1件の`InterviewRecord`に置く。利用者ロールに応じて、表示範囲と許可する操作だけを変える。

Frontendでメニューを隠すことは操作補助であり、認可ではない。直接URL、API、音声接続を含むすべての入口でBackendが認可する。

### 7.2 ロール

ロールIDと画面上の名称は次のとおりとする。既存の`interviewer`は、質問を行う管理者ではなく、AIインタビューに回答するインタビュー対象者を表すロールIDとして扱う。

| ロールID | 画面上の名称 | 役割 |
|---|---|---|
| `admin` | システム管理者 | テナント内の全機能とシステム設定を管理する。 |
| `knowledge_manager` | ナレッジ管理者 | ナレッジ設定、記録のレビュー、提案の承認を行う。 |
| `interviewer` | インタビュー対象者 | 自分に割り当てられた記録へ回答し、レビューを依頼する。 |
| `viewer` | 閲覧者 | 閲覧を許可された確定済み記録だけを閲覧する。 |

### 7.3 ワークスペースとナビゲーション

アプリの論理ワークスペースは、以下の3つとする。論理ワークスペースは権限と初期遷移先を定義する。画面上では、複数のグローバルナビゲーション列を表示せず、1列の左サイドバーとメイン画面で構成する。

| ワークスペース | 用途 | 表示するロール |
|---|---|---|
| 記録 | インタビュー対象者が担当記録へ回答する。 | `interviewer`、`viewer` |
| ナレッジ管理 | ナレッジDB、ナレッジ、質問項目、インタビュープロファイル、文書、実行設定を扱う。 | `admin`、`knowledge_manager` |
| システム設定 | 利用者、ロール、テナント全体の設定を扱う。 | `admin` |

`interviewer`には「記録」だけを表示する。`viewer`には読み取り専用の「記録」だけを表示する。`knowledge_manager`にはナレッジ一覧を表示し、記録は選択したナレッジ内の「記録」から扱う。`admin`にはナレッジ一覧と「システム設定」を表示し、記録は選択したナレッジ内の「記録」から扱う。

左サイドバーは、ロールごとに次の内容を1列で表示する。

* `admin`、`knowledge_manager`: 見出し「ナレッジ」、ナレッジ作成ボタン、ナレッジ一覧。`admin`だけは下部に「システム設定」を表示する。
* `interviewer`、`viewer`: 見出し「記録」、利用可能な記録一覧。ナレッジ一覧、ナレッジ作成、ナレッジ設定を表示しない。

「ナレッジ管理」を独立したグローバル列として表示してはいけない。ナレッジ配下の「インタビュー」「記録」は、選択中ナレッジのメイン画面上部に表示する。ナレッジごとの操作を左サイドバー内にツリー表示してはいけない。設定はナレッジヘッダーの操作から開く。

サイドバーは開閉できる。閉じた状態では開閉ボタンだけを表示し、メイン画面を広げる。画面幅が狭い場合はサイドバーをメイン画面の上部へ移し、ナレッジまたは記録の一覧を横スクロールで表示する。

ナレッジ一覧の表示順は、`Knowledge.createdAt`の昇順で固定する。同一の作成日時の場合は`Knowledge.id`の昇順で固定する。ナレッジを選択しても、選択状態の変更や画面遷移によって一覧の表示順を変更してはいけない。

管理者・ナレッジ管理者がナレッジ管理の入口を開いた場合、メイン画面にナレッジ一覧を重複表示してはいけない。最後に開いたナレッジが存在する場合は、そのナレッジの「インタビュー」を開く。最後に開いたナレッジがない場合は、最初のナレッジの「インタビュー」を開く。ナレッジが1件もない場合だけ、メイン画面に作成案内を表示する。

全体の「記録」ワークスペースでは、同じ記録をロールに応じて表示する。

* `interviewer`: 担当中、差し戻し、提出済み、完了済みの自分の記録を表示する。
* `knowledge_manager`と`admin`: 担当者、進捗、承認待ちを含む管理対象の記録を表示する。
* `viewer`: 閲覧を許可された確定済み記録を表示する。

`admin`と`knowledge_manager`が特定のナレッジを管理する画面は、次の2つを主ナビゲーションとして表示する。

| ナレッジ配下の画面 | 役割 |
|---|---|
| インタビュー | 新しい記録を作成し、途中の記録を再開する。設定状態の詳細は主画面に表示しない。 |
| 記録 | そのナレッジの記録一覧を表示し、公開、差し戻し、承認を行う。 |

記録詳細のインタビュー画面は、左側に会話、右側に会話から整理した情報を表示する。`system_requirement`では、右側の整理結果を「要件整理」と「処理の流れ」の2タブで切り替える。「処理の流れ」タブ内では「フローチャート」と「シーケンス図」を切り替える。`business_process`では右側に処理の流れを表示し、`fixed_form`では右側に質問リストを表示する。画面幅が狭い場合は会話を上、整理結果または質問リストを下に表示する。

設定は主ナビゲーションに表示せず、ナレッジヘッダーの主ボタン「インタビュー設定」から開く補助画面とする。ナレッジを新規作成した直後は、必ず設定画面の「実行設定」を開く。設定画面では、ナレッジ情報、質問項目、実行設定、事前知識を同じ画面内のタブで管理する。事前知識タブでは、質問項目設計やインタビュー内容の整理で参照する文書を追加・確認する。ドキュメント管理を主ナビゲーションの画面として表示してはいけない。インタビュー対象者と閲覧者には、ナレッジDB・ナレッジの設定画面を表示してはいけない。

`KnowledgeDb`は複数の`Knowledge`をまとめる内部の管理単位である。ナレッジ管理者が通常操作する一覧では`Knowledge`を主対象として表示し、「ナレッジDB」を作成・選択する操作を主導線にしてはいけない。`KnowledgeDb`が複数ある場合に限り、ナレッジ作成時の保存先を「業務領域」として選択できる。ナレッジ一覧には`KnowledgeDb`の技術名称やIDを表示してはいけない。

### 7.3.1 インタビュー設定の完了条件

インタビュー設定は、`Knowledge.interviewPlan`に次の値を保存した時点で完了とする。

* `profile`: `fixed_form`、`business_process`、`system_requirement`のいずれか
* `modelId`: `global.openai.gpt-5.6-terra`または`global.openai.gpt-5.6-luna`

設定が完了していないナレッジでは、管理者は記録を作成してはいけない。既存の`draft`記録も公開してはいけない。テキストインタビューの開始、回答送信、音声セッションの開始も許可してはいけない。

Frontendは設定未完了の状態を表示し、「設定を開始」を主操作として表示する。この状態では、記録作成・公開・開始操作を表示または有効化してはいけない。Backendは同じ条件を必ず検証し、満たさない場合は`409 interview_configuration_required`を返す。

### 7.3.2 設定画面の構成

設定画面に「基本設定」タブを置いてはいけない。ナレッジ名と説明は、設定画面上部の「ナレッジ情報」セクションに常に表示する。

設定画面のタブは次の3つとする。

| タブ | 設定内容 |
|---|---|
| 質問項目 | 質問項目の追加・編集・削除、詳細項目、必須/任意、質問項目設計チャット |
| 実行設定 | インタビュー用途、インタビュー実行モデル、質問項目設計モデル、追加カスタマイズプロンプト |
| 事前知識 | 参照文書の追加、取り込み状態、チャンク数、ユーザー既読状態、取り込みエラー |

「ナレッジ情報」は3つのタブのいずれにも含めず、タブの上に配置する。設定画面の保存操作は、ナレッジ情報、質問項目、実行設定をまとめて保存する。事前知識の文書追加・既読状態更新は、それぞれの操作時に保存する。タブごとに別の設定保存状態を持たせてはいけない。

### 7.4 記録の担当と状態

各`InterviewRecord`には、回答を担当するインタビュー対象者を1人だけ設定する。担当者は`ownerUserId`に保存する。`ownerUserId`が未設定の記録は、`interviewer`へ表示してはいけない。

記録のレビュー状態は、インタビュー内部の回答状態とは別に、次の値で管理する。

| 記録状態 | 意味 | 許可する遷移 |
|---|---|---|
| `draft` | 管理者が作成中で、対象者へ公開していない。 | `in_progress` |
| `in_progress` | 対象者が回答できる。 | `submitted` |
| `submitted` | 対象者がレビューを依頼済みで、管理者の確認待ち。 | `returned`、`approved` |
| `returned` | 管理者が修正依頼を付けて差し戻した。 | `in_progress` |
| `approved` | 管理者が正式な記録として承認した。 | 遷移なし |

`submitted`への遷移は、Profileで定義されたインタビュー完了条件を満たした場合だけ許可する。`returned`への遷移では、管理者は対象者に提示する差し戻し理由を必ず入力する。`approved`へ遷移した記録の回答、会話、AI提案を対象者が変更してはいけない。

管理者向けのナレッジ配下「記録」画面では、`draft`の公開、`submitted`の差し戻し、`submitted`の承認を一覧から実行できる。詳細画面でも同じ状態遷移を実行できる。状態遷移の正本はBackendとし、画面上のボタン表示だけで許可を判断してはいけない。

インタビュー内の`UNANSWERED`、`CANDIDATE_PENDING`、`AWAITING_CONFIRMATION`、`CONFIRMED`は、質問ごとの回答状態である。記録状態の`draft`、`in_progress`、`submitted`、`returned`、`approved`と混同してはいけない。

### 7.5 操作権限

| 操作 | `admin` | `knowledge_manager` | `interviewer` | `viewer` |
|---|---:|---:|---:|---:|
| ナレッジDB・ナレッジ・項目・文書・実行設定の管理 | 可 | 可 | 不可 | 不可 |
| 記録の作成、担当者設定、削除 | 可 | 可 | 不可 | 不可 |
| 担当記録の会話・音声回答・確定済み回答の編集 | 可 | 可 | 自分の担当記録だけ可 | 不可 |
| 記録の提出 | 可 | 可 | 自分の担当記録だけ可 | 不可 |
| 提案・記録のレビュー、差し戻し、承認 | 可 | 可 | 不可 | 不可 |
| 確定済み記録の閲覧 | テナント内で可 | 管理対象で可 | 自分の担当記録だけ可 | 明示的に閲覧を許可された記録だけ可 |

`interviewer`はAI提案を正式承認してはいけない。対象者は回答を確定し、記録を`submitted`へ遷移させる。AI提案と記録の最終承認は、`admin`または`knowledge_manager`だけが実行する。

`viewer`は会話、音声接続、回答編集、提出、提案承認、文書既読状態の更新を実行してはいけない。

### 7.6 認可と保存の原則

* 保存データにはCognitoユーザーIDを含める。
* ユーザーに紐づかない保存をしてはいけない。
* 認証済みユーザーのみAPIを利用できる。
* ロール、テナント、記録の担当または明示的な閲覧許可をすべて確認してからデータを返す。
* 認可チェックを省略してはいけない。
* AI出力を自動で正式ナレッジ化してはいけない。
* 正式ナレッジ化には`admin`または`knowledge_manager`による承認を必須とする。
* 音声インタビューのセッションと発話は、対象記録および認証ユーザーに紐付ける。
* 音声経路の回答は、回答対象の質問IDと紐付ける。
* 確定前の部分文字起こしを、正式な回答として保存してはいけない。
* 音声原本を保存する場合は、文字起こしや構造化データとは分離して管理する。
* 音声原本の保存有無は、利用目的、同意、保持期間を明確にした上で決定する。

## 8. 将来拡張

将来的に以下を検討する。

* 外部DB送信
* ファイル原本保存
* ナレッジグラフ化
* RAG評価
* Agentic RAG
* リアルタイム音声インタビューの複数話者対応
* Nova SonicおよびTranscribe + Polly以外の音声Provider追加
* 音声セッションの複数インスタンス間引き継ぎ

## 9. インタビュー構造化拡張仕様

### 9.1 目的

インタビュー実行時に、1つのユーザー発話から次の情報を同時に抽出する。

* 設定済み質問項目の候補
* システム要求の候補
* 業務フローの候補
* 矛盾
* 分岐・例外・外部システム・エラー処理・引き渡し・入出力・業務フロー有無の適用可能性
* 未解決事項

インタビューの目的は、ユーザーが選択する用途で指定する。ユーザーに技術的な`fixed`、`process`、`hybrid`を選択させてはならない。

詳細なLLM契約、状態、Patch、描画処理は、[AIインタビュー構造化キャプチャ設計](architecture/agents/interview-knowledge-capture.md)に定義する。

### 9.2 利用者向け用途と内部Profile

| 利用者向け用途 | 内部Profile | 必須の正本情報 | 図の扱い |
|---|---|---|---|
| 定型情報を聞き取る | `fixed_form` | `FieldState` | 図を作成しない |
| 業務フローを整理する | `business_process` | `ProcessState` | フローチャートとシーケンス図を表示する |
| システム要件を整理する | `system_requirement` | `RequirementState`、`process=present`が確定した場合だけ`ProcessState` | 要求だけの場合は図を作成しない |

Profileの選択場所はKnowledge設定とする。RecordはKnowledgeのProfileを使用する。インタビュー開始後はKnowledgeのProfileを変更できない。Record単位のProfile上書きは提供しない。

### 9.3 共通Interpreter

Profileにかかわらず、インタビュー処理は共通Interpreterを使用する。

```text
ユーザー発話
  ↓
共通Interpreter
  ├── FieldState更新候補
  ├── RequirementState更新候補
  ├── ProcessState更新候補
  ├── 矛盾
  ├── Applicability
  └── 未解決事項
```

`fixed_form`では、共通Schemaを受け取った後もProcessStateをインタビューの有効な状態として適用しない。`business_process`ではProcessStateを使用する。`system_requirement`ではRequirementStateを必須とし、`process=present`が確定した場合だけProcessStateを使用する。

`FieldState`は全Profileで候補抽出対象にできる。完了条件に含める設定済みFieldは、`fixed_form`の`required=true`項目だけとする。`business_process`と`system_requirement`の設定済みFieldは補助情報として保存できるが、現在のProfile定義では必須質問対象にしない。

### 9.4 正本情報と派生ビュー

インタビュー中の正本は次の状態である。

```text
InterviewState
├── FieldState
├── RequirementState
├── ProcessState
└── ApplicabilityState
```

`ProcessModel`は`ProcessState`から生成する派生ビューである。フローチャートとシーケンス図は、`ProcessModel`から生成する。

ProcessStateの要素は、インタビュー確認状態`candidate`または`confirmed`と、履歴状態`active`または`superseded`を持つ。ProcessPatchで追加・変更した要素は`candidate`で保存し、Processの必須項目がすべて確認済みになった時だけBackendが`confirmed`へ更新する。`confirmed`は正式承認を意味しない。

`business_process`では、インタビュー画面に「フローチャート」と「シーケンス図」の表示を用意する。検証済みのProcessStateが更新されるたびに、両方の表示を更新する。候補状態の要素は確定済み要素と区別して表示し、正式承認前の図を正式ナレッジの図として扱わない。`fixed_form`では図を表示しない。`system_requirement`では`process=present`が確定した場合だけ図を表示する。

LLMに次の情報を生成させてはならない。

* Mermaidコード
* React Flowの座標
* ELKのレイアウト結果
* HTML、SVG、画像データ

### 9.5 Applicabilityの扱い

次の項目は、存在確認を完了条件に含める場合、初期状態を`unknown`とする。

* `branch`: 条件による分岐
* `exception`: 通常と異なるケース
* `external_system`: 外部システム連携
* `error_handling`: エラー処理
* `handoff`: 担当者・部門・システム間の引き渡し
* `input_output`: 入出力データ
* `process`: 業務フローの有無

状態値は次の3つだけとする。

```text
unknown
present
not_applicable
```

* 発話に対象が登場しないだけでは`not_applicable`にしない。
* `present`は、対象が存在すると発話から確認できた場合だけ設定する。
* `not_applicable`は、対象が存在しないと発話から確認できた場合だけ設定する。
* `present`と`not_applicable`には根拠メッセージIDを保存する。
* 根拠がない状態更新はBackendが拒否する。

`system_requirement`では、要求内容の確認後、次の質問で業務フローの有無を確認する。

> この要望には、利用者の操作やシステム間連携など、業務上の処理の流れがありますか？

回答が「ある」の場合は`process=present`、回答が「ない」の場合は`process=not_applicable`とする。どちらとも判断できない場合は`process=unknown`のままにする。`process=not_applicable`の場合はProcessStateの詳細を質問しない。

`business_process`では通常経路を確認した後、`system_requirement`で`process=present`が確定した場合は通常経路の確認後、未確認の存在確認が残っている場合は、次の確認を1回行う。

> 通常と異なるケース、条件によって処理が変わるケース、外部システムとの連携、エラー発生時の処理はありますか？

この回答から確認できなかった項目は`unknown`のまま保持する。`present`になった項目だけ詳細を質問し、`not_applicable`になった項目は詳細を質問しない。

### 9.6 Profile別の完了条件（Definition of Done）

完了判定はBackendが行う。LLMは完了状態を決定しない。

#### `fixed_form`

次のすべてを満たした場合に完了とする。

1. Profileの必須Fieldがすべて`CONFIRMED`である。
2. `AWAITING_CONFIRMATION`のFieldがない。
3. 未解決の矛盾がない。

#### `business_process`

次のすべてを満たした場合に完了とする。

1. `process.scope`が確定している。
2. `process.start`が確定している。
3. `process.end`が確定している。
4. `process.actors`が確定している。
5. `process.main_flow`が確定している。
6. Profileで定義されたApplicabilityがすべて`present`または`not_applicable`である。
7. `present`のApplicabilityの詳細が確定している。
8. `AWAITING_CONFIRMATION`の候補がない。
9. 未解決の矛盾がない。

#### `system_requirement`

次のすべてを満たした場合に完了とする。

1. `requirement.purpose_problem`が確定している。
2. `requirement.users`が確定している。
3. `requirement.request`が確定している。
4. `requirement.expected_result`が確定している。
5. `requirement.constraints`が確定している。
6. 業務フローの有無が確定している。
7. 業務フローがない場合、ProcessStateの詳細と条件付きApplicabilityの詳細を要求しない。
8. 業務フローがある場合、`process.trigger`、`process.actors`、`process.main_flow`、`process.end`、`process.interaction`を確定する。
9. `AWAITING_CONFIRMATION`の候補がない。
10. 未解決の矛盾がない。

### 9.7 次の質問対象

Backendは、次の優先順位で1件だけ質問対象を決定する。

1. 未解決の矛盾
2. `AWAITING_CONFIRMATION`中の候補
3. Profile必須項目の未確認
4. `ApplicabilityState=unknown`の項目
5. 任意項目の深掘り

同じ優先順位の候補が複数ある場合は、Profileの定義順、依存関係、項目の表示順で1件に絞る。

LLMは質問対象、優先順位、完了状態を返してはならない。LLMはBackendが指定した対象について自然な質問文だけを生成する。

### 9.8 モデル仕様

初期のインタビュー意味処理には、Amazon Bedrock上のOpenAI GPT-5.6 Lunaを既定モデルとして使用する。
呼び出しは`bedrock-runtime`のOpenAI互換Responses APIへ送信する。AWS SigV4で署名し、標準AWS認証情報チェーンから認証情報を取得する。OpenAI APIキーまたは画像生成モデルは使用しない。
Structured OutputはResponses APIの`text.format`で指定する。この実装では、同じターンの構造化処理にネイティブConverse APIの`outputConfig.textFormat`を使用しない。

モデルには、Global Inference Profileの`global.openai.gpt-5.6-luna`を既定値として指定する。Global profileは、対応する商用AWSリージョンの容量へルーティングされる。処理リージョンを単一リージョンまたは特定地域に限定する要件がある場合は、このGlobal profileを使用してはならず、別途リージョン要件を満たすprofileを定義する。

ナレッジ単位で、実行設定の「構造化インタビュー実行モデル」から次の2つを選択できる。選択したモデルはInterpreterとQuestion Generatorの両方に適用する。モデルの自動切り替えは行わない。設定がない既存ナレッジはBackend設定の`STRUCTURED_INTERVIEW_MODEL_ID`を使用し、その既定値はLunaとする。

質問項目設計では、実行設定の「質問項目の設計モデル」から同じ2つを選択できる。選択値はKnowledgeの`defaultModelId`に保存し、Question Design AgentとそのValidatorの両方に適用する。設定がない、または旧モデルが保存された既存ナレッジは、Backend設定の`QUESTION_DESIGN_MODEL_ID`を使用する。許可値はTerraとLunaだけであり、既定値はLunaとする。質問項目設計モデルとインタビュー実行モデルは別々に選択できる。

| 表示名 | `interviewPlan.modelId` | 用途 |
|---|---|---|
| OpenAI GPT-5.6 Luna（Global） | `global.openai.gpt-5.6-luna` | 既定。標準処理とコストを優先する |
| OpenAI GPT-5.6 Terra（Global） | `global.openai.gpt-5.6-terra` | 意味解釈と構造化抽出の品質を優先する |

質問項目設計モデルの選択値は次のとおりである。

| 表示名 | `Knowledge.defaultModelId` | 用途 |
|---|---|---|
| OpenAI GPT-5.6 Luna（Global） | `global.openai.gpt-5.6-luna` | 既定。標準的な質問項目設計とコストを優先する |
| OpenAI GPT-5.6 Terra（Global） | `global.openai.gpt-5.6-terra` | 複雑な意味解釈と検証品質を優先する |

インタビュー実行モデル（`interviewPlan.modelId`）は、インタビュー開始後も変更できる。保存後の次回処理から、選択したモデルをInterpreterとQuestion Generatorに適用する。既に保存済みの質問、回答、構造化状態は変更しない。質問項目設計モデル（`Knowledge.defaultModelId`）は、保存後に実行する質問項目設計から適用し、既に作成済みの候補には適用しない。

Responses APIのStructured Outputは、`text.format.type=json_schema`、`text.format.name`、`text.format.strict=true`、`text.format.schema`で指定する。Structured Outputは返却形式を固定する仕組みであり、BackendがLLM呼び出し前に検索を実行することを禁止しない。LLMの返却JSONはBackendがPydantic Schemaで検証し、検証成功後だけ状態へ適用する。

次の処理表は、モデル未選択時の既定動作を示す。Terraを選択した場合は、同じ処理、同じSchema、同じ推論強度でモデルIDだけをTerraへ置き換える。

| 処理 | 初期モデル | `reasoning.effort` |
|---|---|---|
| Dialogue Act判定 | `global.openai.gpt-5.6-luna` | `low` |
| Field抽出 | `global.openai.gpt-5.6-luna` | `low` |
| Requirement抽出 | `global.openai.gpt-5.6-luna` | `low` |
| Process抽出 | `global.openai.gpt-5.6-luna` | `low` |
| 矛盾・曖昧性判定 | `global.openai.gpt-5.6-luna` | `low` |
| 次の質問文生成 | `global.openai.gpt-5.6-luna` | `low` |

質問項目設計では、通常時も検証時もKnowledgeで選択された`defaultModelId`を使用する。未設定または旧モデル値の場合は`QUESTION_DESIGN_MODEL_ID`へ解決する。質問項目設計においてTerraとLunaを自動切り替えしてはならない。

質問項目設計は、Strands Agentを本番経路に使用せず、BedrockのOpenAI互換Responses APIへ直接送信する。GPT-5.6 Terra／Lunaは`temperature`を使用せず、Structured OutputのJSON Schemaを指定する。GPT-5.6以外のモデルを追加する場合は、モデルが対応する場合に限り温度設定を別仕様で定義する。

質問項目設計の生成前に、Backendは同じテナントかつ同じKnowledgeに属する情報だけを読み取る。検索対象は、既存質問項目、承認済みインタビュー記録、承認済みAI提案、取り込み済みの文書・文書チャンクである。未承認の記録・提案、取り込み中の文書、別Knowledgeの情報をLLMへ渡してはならない。検索結果は`retrieved_knowledge`として入力へ埋め込み、LLMにDBアクセスやDB更新を許可してはならない。

Question Design AgentとValidatorは、同じ選択モデルへそれぞれStructured Outputリクエストを送る。生成結果と検証結果はBackendがPydantic Schemaで検証し、検証失敗時は各段階で1回だけ再実行する。Provider障害時にStrands、別モデル、別の自動フォールバックへ切り替えてはならない。

既存状態との矛盾、複数フロー、大量更新、大量要求、既存ProcessStateの大幅変更をBackendが検知した場合だけ、ナレッジで選択されたTerraまたはLunaを`medium`で再実行する。

初期実装ではTerraとLunaの自動ルーティングを行わない。Solは選択肢に含めず、利用しない。Solを追加する場合、または自動ルーティングを追加する場合は、Terraとの比較評価、ルーティング条件、失敗時の挙動を別仕様で定義する。

画像生成モデルはインタビュー処理、図生成、図表示のいずれにも使用しない。

標準の開発・Compose実行では、Backendの`STRUCTURED_INTERVIEW_ENABLED=true`を設定し、構造化インタビューを使用する。`STRUCTURED_INTERVIEW_ENABLED=false`を明示した場合だけ、既存のStrands/Bedrock経路を使用する。環境変数を設定しない場合のコード既定値は、既存利用者との互換性を保つため`false`である。同じターンを構造化経路と既存経路で重複処理してはならない。AWS認証情報またはIAM権限が不足している場合は状態を更新せず、エラーとして扱う。

構造化インタビューの環境変数は次のとおりである。

| 環境変数 | 必須 | 既定値 | 用途 |
|---|---:|---|---|
| `STRUCTURED_INTERVIEW_ENABLED` | いいえ | 標準設定は`true`。未設定時のコード既定値は`false` | 構造化インタビューの有効化 |
| `BEDROCK_AWS_REGION` | いいえ | `ap-northeast-1` | Bedrock Runtimeへ接続する呼び出し元リージョン |
| `STRUCTURED_INTERVIEW_MODEL_ID` | いいえ | `global.openai.gpt-5.6-luna` | Bedrock inference profile IDまたはARN |
| `STRUCTURED_INTERVIEW_REASONING_EFFORT` | いいえ | `low` | 通常時の推論強度 |
| `STRUCTURED_INTERVIEW_MEDIUM_REASONING_EFFORT` | いいえ | `medium` | 複雑条件検知時の推論強度 |
| `STRUCTURED_INTERVIEW_MAX_OUTPUT_TOKENS` | いいえ | `6000` | Interpreterの1回のLLM出力上限。長文回答でJSONが上限に達した場合、最大10000トークンまで増やして1回だけ再試行する |
| `STRUCTURED_INTERVIEW_QUESTION_MAX_OUTPUT_TOKENS` | いいえ | `600` | Question Generatorの1回のLLM出力上限。短い質問文のStructured Outputに適用する |
| `QUESTION_DESIGN_MODEL_ID` | いいえ | `global.openai.gpt-5.6-luna` | 質問項目の設計モデル。TerraまたはLunaを指定する |
| `QUESTION_DESIGN_REASONING_EFFORT` | いいえ | `low` | 質問項目設計の推論強度 |
| `QUESTION_DESIGN_MAX_OUTPUT_TOKENS` | いいえ | `6000` | 質問項目設計の生成・検証出力上限 |
| `STRUCTURED_INTERVIEW_CONNECT_TIMEOUT_SECONDS` | いいえ | `5` | HTTP接続タイムアウト |
| `STRUCTURED_INTERVIEW_READ_TIMEOUT_SECONDS` | いいえ | `120` | HTTP応答読み取りタイムアウト |

ユーザーが提示したprofile ARNは、Terraが`arn:aws:bedrock:us-east-1:755974828484:inference-profile/global.openai.gpt-5.6-terra`、Lunaが`arn:aws:bedrock:us-east-1:755974828484:inference-profile/global.openai.gpt-5.6-luna`である。コードの既定値は、呼び出し元リージョンを変更しても同じ指定を使えるprofile IDの`global.openai.gpt-5.6-luna`とする。ARNを設定する場合は、ARNの呼び出し元リージョンと`BEDROCK_AWS_REGION`を一致させる。

Global profileのIAM許可には、少なくとも対象inference profileへの`bedrock:InvokeModel`、アカウントの`project/default`、呼び出し元リージョンのfoundation model、Global profileが参照するGlobal foundation modelへの許可が必要である。組織のSCPでリージョン制限を行っている場合は、Global profileの宛先リージョンおよび`aws:RequestedRegion=unspecified`を考慮する。

### 9.9 LLMとBackendの責務

LLMの責務は次のとおりである。

* 発話の意味解釈
* Field、Requirement、Processの候補抽出
* 矛盾の検出
* Applicabilityの候補抽出
* 未解決事項の抽出
* Backendが指定した対象についての質問文生成

Backendの責務は次のとおりである。

* Structured OutputのSchema検証
* 根拠メッセージIDの検証
* FieldState、RequirementState、ProcessStateの更新
* Applicabilityの確定
* ProcessPatchの適用可否判定
* 完了判定
* 次の質問対象の決定
* 質問の重複防止
* 候補、確認済み、正式承認済みの境界保証

### 9.10 テキストと音声

テキスト経路と音声経路は、確定したユーザー発話を同じInterpreter、同じ状態、同じ完了条件、同じ質問優先順位へ渡す。

音声経路では次のルールを適用する。

* partial transcriptを正式回答として処理しない。
* 確定transcriptだけを`app/api`へ渡す。
* `app/voice`にInterviewの意味判断を実装しない。
* `app/voice`から`app/api`のPythonモジュールを直接importしない。
* Terraへ音声データを直接送信しない。
* 音声経路専用の完了条件、質問優先順位、回答評価を作らない。

### 9.11 提案と正式承認

Interpreterの出力は、Backendの検証後も候補またはAI提案として扱う。

* インタビュー対象者による候補確認と、レビュー担当者による正式承認を分ける。
* `ProcessState`の候補を正式ProcessModelとして公開しない。
* `RequirementState`の候補を正式要求として公開しない。
* AI提案は`draft`または`needs_review`で保存する。
* 人の操作なしに`approved`へ変更しない。

### 9.12 会話進行、提案、図表示

確認質問には、発行した`questionId`、質問対象の`targetType`、`targetId`を必ず紐付ける。確認への回答は、その`questionId`が示す現在の対象だけに適用する。確認待ち一覧の先頭項目や、LLMが再抽出した別候補へ適用してはならない。

利用者が候補を明示的に肯定した場合、Backendは対象を`CONFIRMED`へ遷移させ、候補値を確定値へ移し、次の質問対象を再評価する。肯定の直後に、同じ`targetType`と`targetId`の確認質問を新規発行してはならない。訂正、否定、不明確な回答、または技術エラーからの明示再試行だけは同じ対象を再質問できる。

利用者が「提案して」「例を出して」など、値の提案を求めた場合、AIが示す値は`assistant_proposal`由来の候補として扱う。利用者の発話から抽出した事実として保存してはならない。提案には「AIの案」であることを表示し、利用者の明示的な採用または修正を受けてから確定候補として扱う。提案を採用しても正式承認にはならない。

`system_requirement`で`process=unknown`の間は、業務フローがないと表示してはならない。`process=present`になった時点で、要件整理パネルに処理モデルの収集中であることと、現在不足している情報を表示する。`process=not_applicable`になった場合だけ、業務フローを作成しないことを表示する。

フローチャートは、activeな処理ノードが2件以上、かつ遷移が1件以上ある場合に有効化する。シーケンス図は、activeな参加者が2件以上、かつ根拠付きの相互作用が1件以上ある場合に有効化する。条件を満たさない場合は空の図を表示せず、次に必要な情報を表示する。条件を満たした図は、ProcessStateの検証済み更新ごとに更新する。

テキスト経路の同一回答の再送、SSE再接続、音声経路の再送によって、同じ回答を二重に処理したり、同じ質問を二重発行したりしてはならない。

テキスト送信はクライアント生成の`clientMessageId`を付与する。同じ`clientMessageId`、回答対象、内容の再送は、保存済みメッセージを返して再処理しない。インタビュー状態に`stateVersion`を保持し、回答送信時に画面が参照したバージョンと一致しない場合は状態競合として拒否する。保存済みメッセージの再送は、状態バージョンの検証より先に冪等応答として処理する。

### 9.13 関連設計

以下の文書を本仕様と合わせて参照する。

* [AIインタビュー構造化キャプチャ設計](architecture/agents/interview-knowledge-capture.md)
* [エージェントアーキテクチャ](architecture/agents/agent-architecture.md)
* [Interview Agent仕様](agents/interview-agent-strands.md)
* [リアルタイム音声仕様](architecture/voice/realtime-voice.md)
