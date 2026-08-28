# agent-architecture.md

## 1. 目的

AI関連処理は、以下の3種類のエージェント責務に分けて扱う。

* 質問設計エージェント
* インタビューエージェント
* 暗黙知回答エージェント

これらを混同しない。
質問設計、熟練者インタビュー、承認済みナレッジ回答は、入力、出力、保存可否が異なる。

## 2. 質問設計エージェント

### 2.1 役割

質問設計エージェントは、ユーザーの目的から、熟練者に聞くべき質問項目を設計する。

主な責務は以下。

* ヒアリング項目候補を作る
* 質問例を作る
* 入力形式、必須/任意、AIが質問するかを提案する
* ユーザーに答えを聞くのではなく、熟練者に聞く質問を作る

### 2.2 入力

* ユーザーの質問設計依頼
* 対象ナレッジの基本設定
* 既存のヒアリング項目
* 直近の質問設計チャット履歴

### 2.3 出力

* ユーザー向けの短い説明 `reply`
* 承認前の質問項目候補 `fields`

### 2.4 書き込み

原則としてDBへ直接書き込まない。
正式なヒアリング項目として保存するには、ユーザーの承認操作を必須とする。

### 2.5 現在の対応機能

既存の `field-suggestions` は質問設計エージェントの責務である。

当面は以下を維持する。

* `app/api/src/ai_interviewer_api/services/field_suggestions.py`
* `app/api/src/ai_interviewer_api/services/prompts/field_fill/`
* `POST /api/knowledges/{knowledge_id}/field-suggestions`

将来的には、実処理を `agents/question_design/` 配下へ小さく移す。
ただし、endpoint URL、request schema、response schema は互換性を維持する。

## 3. インタビューエージェント

### 3.1 役割

インタビューエージェントは、熟練者とのヒアリング会話を進める。

主な責務は以下。

* 熟練者の回答から構造化データ候補を作る
* 不足している情報と矛盾を抽出する
* 必要に応じて過去ナレッジや設備マスタを参照する

次の質問対象、質問優先順位、インタビュー完了判定はBackendが決定する。

### 3.2 入力

* ヒアリング項目
* 熟練者との会話履歴
* 対象ナレッジの基本設定
* 参照可能な過去ナレッジ、設備マスタ、文書情報

### 3.3 出力

* Backendが決定した次の質問文
* 構造化データ候補
* AI提案カード
* 不足情報、矛盾、Applicabilityの抽出結果

### 3.4 書き込み

draft保存までを許可する。
承認なしに正式ナレッジへ登録してはいけない。

### 3.5 構造化インタビュー拡張

インタビュー実行で、固定項目、システム要求、業務フローを同じ発話から抽出する場合は、共通Interpreterを使用する。

共通Interpreterの出力先は次のとおりである。

```text
Interpreter
├── FieldState
├── RequirementState
├── ProcessState
├── ApplicabilityState
├── contradictions
└── openIssues
```

Profileごとに使用する状態をBackendが決定する。LLMが質問対象、完了状態、Patch適用可否を決定してはならない。

LLM出力、状態機械、ProcessPatch、ProcessModel、質問優先順位の詳細は、[AIインタビュー構造化キャプチャ設計](interview-knowledge-capture.md)に従う。

### 3.6 Provider境界

現在のInterview AgentはStrands/Bedrockを使用している。構造化インタビューの追加実装では、意味処理を`StructuredInterviewProvider`のProvider境界から呼び出す。

```text
Interview Coordinator
    ↓
StructuredInterviewProvider
    ↓
Bedrock Runtime OpenAI互換 Responses API adapter
    ↓
global.openai.gpt-5.6-terra または global.openai.gpt-5.6-luna
```

Providerの選択をRouterやState管理へ分散させてはならない。ナレッジの`interviewPlan.modelId`をCoordinatorがProviderへ渡し、許可されたGlobal profileだけを使用する。音声経路は確定transcriptを`app/api`へ渡し、`app/voice`からLLM Providerを直接呼び出してはならない。

質問設計では、Knowledgeの`defaultModelId`を同じ2つのGlobal profileへ解決する。候補生成とValidatorは同じ選択値を使用し、旧モデル値または未設定値は`QUESTION_DESIGN_MODEL_ID`へ解決する。既定値は`global.openai.gpt-5.6-terra`である。質問設計モデルとインタビュー実行モデルは別設定である。

## 4. 暗黙知回答エージェント

### 4.1 役割

暗黙知回答エージェントは、承認済みナレッジを検索して回答する。

主な責務は以下。

* 設備、症状、原因、対処、カンコツなどを使って検索する
* 承認済みナレッジから回答を作る
* 回答根拠を提示する

### 4.2 入力

* ユーザーの質問
* 参照対象のナレッジDB、ナレッジ、文書
* 検索結果

### 4.3 出力

* 回答本文
* 引用、根拠、参照元

### 4.4 書き込み

DB更新してはいけない。
回答専用であり、ナレッジ更新、項目作成、承認処理を行わない。

## 5. エージェント間の違い

質問設計エージェントは、ユーザーから現場の答えを集めるものではない。
ユーザーは質問設計を依頼する人であり、熟練者が後で質問される対象者である。

インタビューエージェントは、熟練者との会話を進め、回答を構造化候補にする。

暗黙知回答エージェントは、承認済みナレッジを使って回答する。
回答専用であり、DB更新してはいけない。

## 6. Strands Agent 方針

現在は interview agent と question design agent を Strands Agent ベースへ移行済みである。

判断原則は「判断はAI、保証はbackend」とする。
質問設計可否、聞き返し要否、回答判定を挨拶辞書やキーワード一致の deterministic precheck で置き換えない。

短期方針:

* `field-suggestions` は router 互換のため service ラッパーを維持する
* 実際の質問項目生成は Strands question design agent で行う
* validate-retry loop を検討する

中期方針:

* equipment master / past knowledge / existing fields などの tool interface を設計する
* read-only tool から開始する
* tool call、retrieved context、generated questions、validation result をログに残す

長期方針:

* tacit answer agent を必要に応じて agent runtime へ寄せる
* question design / interview は validate-retry や tool 拡張を段階的に進める

制約:

* 1リクエスト内の agent loop は最大2から3ステップ程度に制限する
* DB本登録は人の承認後のみ行う
* 自律的なDB更新は禁止する
