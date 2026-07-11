# question-design-agent-strands.md

## 1. 目的

`agents/question_design/` は Strands Agent を使った question design agent を置く。

この agent は、ヒアリング前に確認すべき質問項目を設計する。
interview agent のように会話を進める責務は持たない。

## 2. 責務

`agents/question_design/`:

* ナレッジ名、説明、既存項目、ユーザー指示をもとに質問項目候補を作る
* 既存項目と重複しにくい候補を返す
* `reply` は提案導入文または補足文にする
* DB保存や承認済み扱いはしない

`services/field_suggestions.py`:

* 既存 router 互換の薄いラッパーとして残す
* `FieldSuggestionRequest` と既存 response shape を維持する
* 実際の生成は `agents/question_design/` に委譲する

## 3. tool 方針

question design agent では、必要最小限の read-only tool だけを使う。

* `search_existing_fields`
* `search_past_knowledge`

`search_equipment_master` は固定領域への寄りを強めやすいため、現時点では登録しない。

## 4. テスト方針

通常の pytest / CI では Bedrock を実呼び出ししない。

* service は fake runner へ差し替えてテストする
* adapter は既存 request / response 契約との互換を確認する
* import 時に AWS 接続が走らないことを前提にする
* Strands 失敗時に legacy fallback しないことを確認する
