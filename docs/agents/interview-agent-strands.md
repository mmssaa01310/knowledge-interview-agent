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
* 質問設計エージェントや暗黙知回答エージェントの責務を混ぜない

## 3. テスト方針

通常の pytest / CI では Bedrock を実呼び出ししない。

* service は fake runner へ差し替えてテストする
* tool は未接続 stub を直接呼んで確認する
* import 時に AWS 接続が走らないことを前提にする

Bedrock を使う手動確認は `RUN_STRANDS_SMOKE=1` を付けた smoke script だけで行う。
