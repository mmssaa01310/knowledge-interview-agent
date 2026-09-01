# Agent Behavior Policy

このドキュメントは、AIインタビューアーにおけるLLM / Structured Output実装の基本方針を定義する。

## 基本原則

本プロジェクトでは、エージェントの判断を rule-based な分岐で過剰に置き換えない。

原則は以下である。

**判断はAI、保証はbackend**

## AIに任せる判断

以下は原則としてStructured Interpreter / Question Generatorに判断させる。

* 質問項目を生成できるだけの材料があるか
* 追加情報を聞き返すべきか
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
* `utteranceCompleteness="INCOMPLETE"` の場合、field・requirement・processを更新しない
* `transcriptAssessment.correctionStatus="UNCERTAIN"` の場合、補正候補を確定しない
* `answerAssessment.sufficiency`に応じて、Coordinatorが候補・probe・完了を制御する
* schema 外の出力を安全に fallback する
* 次の質問対象、質問優先順位、完了判定をBackendで決定する
* DB保存しないものを保存しない
* LLMにRepositoryの読み書き権限を渡さない
* 通常pytestでは Bedrock / real LLM を呼ばない
* prompt全文や `user_message` 全文をログに出さない

## 避けるべき実装

以下のような rule-based precheck は原則として避ける。

* 「こんにちは」を文字列一致で `needs_info` にする
* hello / hi / よろしく のような挨拶辞書で判定する
* 「質問作って」を固定ルールで弾く
* 入力文字数だけで `ready` / `needs_info` や回答完了を決める
* 特定キーワードの有無だけで質問項目生成可否を決める
* 業務名や対象物を正規表現で推定して決めつける

これらは、エージェントの判断領域をアプリ側ルールで置き換えるため、原則禁止する。

ただし、Structured Interpreterの判定を補完する安全弁として、明らかな発話途中の語尾を検知した場合は、Backendが状態更新と次質問を止めてよい。これは完了判定の主経路ではなく、誤ったSTT finalによる早期遷移を防ぐための境界保証である。

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
* agent が `INCOMPLETE` / `UNCERTAIN` を返した場合、backendが状態更新と次質問を止める
* 補正候補がある場合、明示確認前に正式回答へ保存しない
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
* 発話が未完了なら `utteranceCompleteness="INCOMPLETE"` にする
* STT補正が必要なら `transcriptAssessment` に候補と確度を出す
* 回答の不足部分を `answerAssessment.sufficiency` と `probeType` で示す
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

### Structured Interview

AIに任せる:

* 回答が現在の質問への回答になっているか
* 聞き返すべきか
* 不足情報、矛盾、Applicabilityを抽出する
* field・requirement・processの候補
* Transcriptの軽微な正規化と、意味変更を伴う補正候補
* 回答充足度と、不足部分に対応するprobe種別

backend が保証する:

* `INCOMPLETE` / `UNCERTAIN` のとき候補を正式値へ進めない
* `INCOMPLETE` / `UNCERTAIN` のとき次の質問へ進めない
* 次の質問対象を固定優先順位で決定する
* `approved_fields` を勝手に更新しない

## まとめ

このプロジェクトでは、AIエージェントの判断をルールで先回りしすぎない。

* 判断はStructured Interpreter / Question Generator
* 境界保証は backend
* 保存判断は human approval / 明示的な承認フロー
