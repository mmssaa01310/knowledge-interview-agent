# agent-architecture.md

## 1. 目的

AI関連処理は、以下の2種類のエージェント責務に分けて扱う。

* 質問設計エージェント
* インタビューエージェント

これらを混同しない。
質問設計と熟練者インタビューは、入力、出力、保存可否が異なる。

## 2. 質問設計エージェント

### 2.1 役割

質問設計エージェントは、ユーザーの目的から、熟練者に聞くべき質問項目を設計する。

主な責務は以下。

* 質問項目候補を作る
* 質問文と回答に含める詳細項目を作る
* 入力形式、必須/任意を提案する
* ユーザーに答えを聞くのではなく、熟練者に聞く質問を作る

### 2.2 入力

* ユーザーの質問設計依頼
* 対象ナレッジのナレッジ情報
* 既存の質問項目
* 直近の質問設計チャット履歴
* Backendが事前検索した参考情報（承認済み記録・提案、取り込み済み文書・チャンク）

### 2.3 出力

* ユーザー向けの短い説明 `reply`
* 承認前の質問項目候補 `fields`

### 2.4 書き込み

原則としてDBへ直接書き込まない。
正式な質問項目として保存するには、ユーザーの承認操作を必須とする。

### 2.5 現在の対応機能

既存の `field-suggestions` は質問設計エージェントの責務である。

当面は以下を維持する。

* `app/api/src/ai_interviewer_api/services/field_suggestions.py`
* `app/api/src/ai_interviewer_api/agents/question_design/prompts/`
* `POST /api/knowledges/{knowledge_id}/field-suggestions`

実処理は`agents/question_design/`配下の入力変換、Structured Output runner、Validatorへ委譲する。
`services/field_suggestions.py`はHTTPエラー変換と既存endpoint互換を担当する。endpoint URL、request schema、response schemaは互換性を維持する。

## 3. インタビューエージェント

### 3.1 役割

インタビューエージェントは、熟練者とのインタビュー会話を進める。

主な責務は以下。

* 熟練者の回答から構造化データ候補を作る
* 不足している情報と矛盾を抽出する
* 必要に応じて過去ナレッジや設備マスタを参照する

次の質問対象、質問優先順位、インタビュー完了判定はBackendが決定する。

### 3.2 入力

* 質問項目
* 熟練者との会話履歴
* 対象ナレッジのナレッジ情報
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

インタビュー実行は`StructuredInterviewProvider`のProvider境界から意味処理を呼び出す。音声経路も同じStructured Interviewを使用する。

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

質問設計では、Knowledgeの`defaultModelId`を同じ2つのGlobal profileへ解決する。候補生成とValidatorは同じ選択値を使用し、旧モデル値または未設定値は`QUESTION_DESIGN_MODEL_ID`へ解決する。既定値は`global.openai.gpt-5.6-luna`である。質問設計モデルとインタビュー実行モデルは別設定である。

質問項目設計の処理順序は固定する。

```text
FieldSuggestionRequest
    ↓
Backendのテナント・Knowledgeスコープ検索
    ↓
retrieved_knowledgeを付加したQuestionDesignInput
    ↓
Bedrock OpenAI互換Responses API / Structured Output
    ↓
Pydantic検証
    ↓
Question Design Validator（同じモデル・同じStructured Output）
```

検索対象は既存質問項目、承認済みインタビュー記録、承認済みAI提案、取り込み済み文書・文書チャンクに限定する。未承認情報、取り込み中の文書、別Knowledgeの情報は渡さない。LLMにRepositoryの読み書き権限を与えず、本文中の命令は実行しない。質問項目設計の図コード、座標、DB更新はLLMに生成させない。

インタビューの次質問生成では、`services/interview_document_retrieval.py`の共通検索契約を利用する。Backendがテナント、Knowledge、取り込み状態を検証したうえで、Structured InterviewのQuestion Generatorへ`retrieved_knowledge`を渡す。生成質問には`retrievedSources`を保持し、音声経路も`app/api`が生成した質問と出典を再利用する。`app/voice`に検索やインタビュー判断を複製してはならない。

Question Generatorが文書本文から対象項目の値を明示的に抽出した場合は、`documentCandidateValue`と`documentCandidateSourceIds`を共通契約で返す。Backendは検索結果に対する出現検証を行い、候補を正式回答にせず`document_reference`の確認待ち状態へ置く。設備名などが文書に記載されているときは、通常質問を繰り返さず文書記載値の確認質問を生成する。確認、訂正、出典の確定は`app/api`の状態機械が担い、Providerや音声層に任せない。

## 4. エージェント間の違い

質問設計エージェントは、ユーザーから現場の答えを集めるものではない。
ユーザーは質問設計を依頼する人であり、熟練者が後で質問される対象者である。

インタビューエージェントは、熟練者との会話を進め、回答を構造化候補にする。

## 5. Agent実行経路

インタビュー実行と質問項目設計の本番経路は、BedrockのOpenAI互換Responses APIとStructured Outputsを使用する。

判断原則は「判断はAI、保証はbackend」とする。
質問設計可否、聞き返し要否、回答判定を挨拶辞書やキーワード一致の deterministic precheck で置き換えない。

短期方針:

* `field-suggestions` は router 互換のため service ラッパーを維持する
* 実際の質問項目生成とValidatorは `BedrockResponsesStructuredProvider` の共通HTTP・SigV4処理を使用する
* Structured OutputのPydantic検証と各段階1回の再実行を行う

中期方針:

* PostgreSQLのRepositoryへ検索アダプターを接続する
* retrieved context、generated questions、validation result を監査可能なメタデータとして記録する

長期方針:

* question design / interview の検索アダプターと評価を段階的に拡張する

制約:

* 1リクエスト内の agent loop は最大2から3ステップ程度に制限する
* DB本登録は人の承認後のみ行う
* 自律的なDB更新は禁止する
