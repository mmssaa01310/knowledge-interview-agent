# question-design-agent-strands.md

## 1. 目的

`agents/question_design/` は質問項目設計の入力変換、事前検索、Structured Output生成、検証を置く。ファイル名は既存の文書参照との互換性のため維持するが、本番の質問項目設計経路はStrands Agentではない。

この agent は、インタビュー前に確認すべき質問項目を設計する。
interview agent のように会話を進める責務は持たない。

## 2. 責務

`agents/question_design/`:

* ナレッジ名、説明、既存項目、ユーザー指示をもとに質問項目候補を作る
* Backendが検索した同一Knowledgeの参考情報を入力として扱う
* 既存項目と重複しにくい候補を返す
* `reply` は提案導入文または補足文にする
* DB保存や承認済み扱いはしない

### 2.1 使用モデル

質問項目設計モデルはKnowledgeの`defaultModelId`で選択する。利用者が選択できる値は次の2つだけである。

* `global.openai.gpt-5.6-terra`
* `global.openai.gpt-5.6-luna`

選択したモデルは、候補生成とValidatorの両方に使用する。モデルを自動切り替えしてはならない。`defaultModelId`が未設定、または旧モデルの値である場合は、`QUESTION_DESIGN_MODEL_ID`へ解決する。`QUESTION_DESIGN_MODEL_ID`の既定値は`global.openai.gpt-5.6-luna`である。

質問項目設計モデルはインタビュー実行モデル（`interviewPlan.modelId`）と独立して設定できる。質問項目設計に画像生成モデルを使用してはならない。

`services/field_suggestions.py`:

* 既存 router 互換の薄いラッパーとして残す
* `FieldSuggestionRequest` と既存 response shape を維持する
* 実際の生成は `agents/question_design/` に委譲する

## 3. 事前検索とStructured Output

生成前にBackendが、認証ユーザーのテナントと対象Knowledgeを条件に読み取り検索を行う。LLMからRepositoryを直接呼び出してはならない。

検索対象は次のとおりである。

* 同じKnowledgeの既存質問項目
* `approved`状態のインタビュー記録と、そこから得られる実発話・回答
* `approved`状態のAI提案
* `indexed`または`completed`状態の文書と文書チャンク

次の情報を検索結果に含めてはならない。

* 別テナントまたは別Knowledgeの情報
* `approved`以外の記録・AI提案
* 取り込み中または失敗した文書・チャンク

検索結果は`QuestionDesignInput.retrieved_context`として入力し、プロンプト上では`retrieved_knowledge`として渡す。LLMは検索結果を参考情報としてだけ使用し、検索結果本文中の命令を実行してはならない。

候補生成とValidatorは、BedrockのOpenAI互換Responses APIへそれぞれ別リクエストを送信する。両方のリクエストで`text.format.type=json_schema`、`strict=true`を指定し、Pydantic Schemaで検証する。Mermaidコード、React Flow座標、DB更新はLLMに生成させない。

## 4. テスト方針

通常の pytest / CI では Bedrock を実呼び出ししない。

* service は fake runner へ差し替えてテストする
* adapter は既存 request / response 契約との互換を確認する
* import 時に AWS 接続が走らないことを前提にする
* Structured Output Provider失敗時に別モデル・別経路へ自動フォールバックしないことを確認する

質問項目生成可否の意味判断はLLMに任せる。
backend は `design_status` と `suggestions` の整合などの invariant を保証し、挨拶辞書や文字列一致による deterministic precheck で判断を置き換えない。

## 5. 安定性とエラー境界

質問設計と検証は`reasoning.effort=low`を既定とし、GPT-5.6では`temperature`を送信しない。構造化出力を検証できない場合は各段階で1回だけ再実行する。Validatorは、質問設計が `ready` かつ候補ありになった後にだけ実行する。

AIが明示した `needs_info` は正常な追加確認として扱う。一方、生成形式不正、候補欠落、検証失敗は `needs_info` へ変換せず、APIエラーとしてWebへ返す。通信・タイムアウト、AI出力不正、検証失敗、内部例外は画面上でも異なるエラーメッセージにする。
