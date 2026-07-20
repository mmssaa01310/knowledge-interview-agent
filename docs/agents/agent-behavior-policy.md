# Agent Behavior Policy

このドキュメントは、AIインタビューアーにおける Strands Agent 実装の基本方針を定義する。

## 基本原則

本プロジェクトでは、エージェントの判断を rule-based な分岐で過剰に置き換えない。

原則は以下である。

**判断はAI、保証はbackend**

## AIに任せる判断

以下は原則として Strands Agent / LLM に判断させる。

* 質問項目を生成できるだけの材料があるか
* 追加情報を聞き返すべきか
* どの観点を次に確認すべきか
* ユーザー回答が現在の質問への回答になっているか
* 回答からどの構造化候補を作るか
* 会話の自然な聞き返し方
* 質問設計における `ready` / `needs_info`
* インタビュー進行における `answered` / `not_answered`

## backend が保証すること

backend は LLM の判断を無条件に信頼しない。

ただし、backend の役割は「判断を置き換えること」ではなく、「破綻しない境界を保証すること」である。

backend が保証する invariant の例:

* `design_status="needs_info"` の場合、`suggestions=[]` にする
* `answer_status="not_answered"` の場合、`draft_updates={}` にする
* schema 外の出力を安全に fallback する
* DB保存しないものを保存しない
* read-only tool 以外を agent に渡さない
* `used_tools` は許可された tool 名だけに正規化する
* 通常pytestでは Bedrock / real LLM を呼ばない
* prompt全文や `user_message` 全文をログに出さない

## 避けるべき実装

以下のような rule-based precheck は原則として避ける。

* 「こんにちは」を文字列一致で `needs_info` にする
* hello / hi / よろしく のような挨拶辞書で判定する
* 「質問作って」を固定ルールで弾く
* 入力文字数だけで `ready` / `needs_info` を決める
* 特定キーワードの有無だけで質問項目生成可否を決める
* 業務名や対象物を正規表現で推定して決めつける

これらは、エージェントの判断領域をアプリ側ルールで置き換えるため、原則禁止する。

## 例外的に許容するルール

以下のような、LLMに渡す意味がない入力の最低限のガードは許容する。

* `None`
* 空文字
* 空白のみ
* 型不正
* 明らかに schema validation に失敗する入力

この場合も、業務判断ではなく入力検証として扱う。

## 実装パターン

推奨パターン:

```python
output = run_agent(input)

if output.design_status == "needs_info":
    output.suggestions = []
    output.reply = output.clarification_question or output.reply or DEFAULT_CLARIFICATION
```

非推奨パターン:

```python
if user_input in ["こんにちは", "hello", "hi"]:
    return needs_info_response()
```

## テスト方針

通常テストでは real LLM を呼ばない。

その代わり、fake runner を使って以下を検証する。

* agent が `needs_info` を返した場合、backend が `suggestions=[]` にする
* agent が `ready` を返した場合、backend が `suggestions` を維持する
* agent が `not_answered` を返した場合、backend が `draft_updates` を破棄する
* schema 不正時に安全 fallback する
* DB保存が増えない
* API response shape が変わらない

テストで確認すべきではないこと:

* アプリ側が「こんにちは」を直接判定していること
* 挨拶辞書が存在すること
* 入力文字数で材料不足を判定していること

## prompt の責務

prompt は、エージェントに判断基準を与える。

例:

* 材料不足なら `design_status="needs_info"` にする
* 回答になっていなければ `answer_status="not_answered"` にする
* 入力にない業種・業務・対象物を推測しない
* 汎用テンプレ項目を穴埋めで作らない

ただし prompt だけに依存せず、backend invariant で最終的な安全性を保証する。

## agent 別の適用

### question_design agent

AIに任せる:

* 質問項目を生成できる材料があるか
* 追加で何を聞くべきか
* どの質問項目を提案するか

backend が保証する:

* `needs_info` のとき fields を返さない
* `needs_info` のとき `clarification_question` を `reply` に優先する
* suggestions をDB保存しない

### interview agent

AIに任せる:

* 回答が現在の質問への回答になっているか
* 聞き返すべきか
* 次に何を聞くべきか
* `draft_updates` の候補

backend が保証する:

* `not_answered` のとき `draft_updates` を破棄する
* `not_answered` のとき `next_questions` を進めない
* `draft_updates` をDB保存しない
* `approved_fields` を勝手に更新しない

## まとめ

このプロジェクトでは、AIエージェントの判断をルールで先回りしすぎない。

* 判断は Strands Agent / LLM
* 境界保証は backend
* 保存判断は human approval / 明示的な承認フロー
