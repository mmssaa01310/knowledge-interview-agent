# interview-agent-strands.md

## 1. 目的

`agents/interview/` は Strands Agent を使った interview agent の walking skeleton を置く。

今回は以下だけを実装対象とする。

* `agents/common/strands_runtime.py`
* `agents/common/tools/`
* `agents/interview/agent.py`
* `agents/interview/schemas.py`
* `agents/interview/service.py`
* `agents/interview/prompts/base.md`
* `tests/agents/test_interview_agent.py`
* `scripts/smoke_interview_agent.py`

## 2. 責務

`strands_runtime.py`:

* `BedrockModel` と `Agent` の生成
* model ID / region / temperature の受け渡し
* stdout への streaming を無効化するため `callback_handler=None` を使う

`agents/common/tools/`:

* read-only tool だけを置く
* write系処理、DB更新、外部API更新を入れない
* 未接続時は明示メッセージを返す

`agents/interview/`:

* 熟練者との会話を進める interview agent 固有処理を置く
* 質問設計エージェントの責務を混ぜない
* 発話から構造化候補、不足情報、矛盾、Applicabilityを抽出する
* Backendが指定した質問対象について質問文を生成する

次の質問対象、質問優先順位、完了判定、ProcessPatchの適用可否はBackendが決定する。

## 3. テスト方針

通常の pytest / CI では Bedrock を実呼び出ししない。

* service は fake runner へ差し替えてテストする
* tool は未接続 stub を直接呼んで確認する
* import 時に AWS 接続が走らないことを前提にする

Bedrock を使う手動確認は `RUN_STRANDS_SMOKE=1` を付けた smoke script だけで行う。

回答の意味解釈と聞き返し候補の生成はinterview agentに任せる。
backendは`answer_status`と`draft_updates`の整合、次の質問対象、質問優先順位、完了判定を保証する。会話内容の意味をrule-basedに置き換えてはならない。

`retrievalPolicy=never`はread-only検索toolを無効にするが、interview agent自体の回答評価は実行する。agentは発話意図、関連性、十分性、正規化済み候補、`answerResolution`（`AUTO_CONFIRM`、`TENTATIVE`、`RETRY`、`CONFIRM_REQUIRED`）、追加質問、例外時の確認質問を構造化出力し、backendは候補と確定回答の保存境界を保証する。通常のユーザー回答に確認質問を付けるかどうかは、候補の有無ではなく、会話を止める必要性で判定する。

確認質問の自然な表現はagentが現在質問と項目定義から生成する。backendのfallbackは項目名と候補を使うドメイン非依存の形式に限定し、特定の業務・項目名・回答語尾を条件分岐で列挙しない。

## 4. 構造化インタビュー拡張との関係

この文書で定義するStrands Agentは現行実装の互換レイヤーである。新しい構造化インタビューの業務契約は、[AIインタビュー構造化キャプチャ設計](../architecture/agents/interview-knowledge-capture.md)の共通Interpreter契約を正本とする。

追加実装では、次の責務を分離する。

* Interpreter: 発話からField、Requirement、Process、矛盾、Applicability、未解決事項をStructured Outputで抽出する。
* Backend Coordinator: Schema検証、状態更新、ProcessPatch検証、完了判定、次の質問対象決定を行う。
* Question Generator: Backendが指定した質問対象について、自然な質問文を1件生成する。

Strands/Bedrockを互換経路として残す場合も、同じターンを構造化Providerと既存Strands経路の両方で処理してはならない。Bedrockへの接続はProviderアダプターへ限定し、Router、Repository、`app/voice`から直接呼び出してはならない。

初期の構造化インタビューでは、InterpreterとQuestion Generatorに、ナレッジ設定で選択された`global.openai.gpt-5.6-terra`または`global.openai.gpt-5.6-luna`を使用する。選択未設定時の既定はLunaである。通常の`reasoning.effort=low`から、構造条件をBackendが検知した場合だけ選択済みモデルの`medium`で再実行する。TerraとLunaの自動ルーティングおよびSolの利用は行わない。
