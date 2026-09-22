# GPT-Live WebRTC

GPT-Live-1のWeb音声会話経路。GPT Liveが唯一のインタビュアーとして会話、質問進行、Clarification、深掘りを担当し、Responses Delegationはユーザー回答の意味評価だけを行う。Application Backendは評価結果を検証してInterview Stateへ保存する。

既存の`openai_realtime`（旧Realtime API）とTranscribe + Pollyは後方互換のため残すが、GPT Liveの会話制御や状態評価を共有しない。

## 責務と接続構成

```text
User Voice
    ↓ WebRTC
GPT Live (唯一のInterviewer)
    │
    └─ Responses Delegation
          └─ Semantic Assessor
                └─ update_interview_state Function Tool
                      ↓
                Application Backend (State Store)
                      └─ validate / persist / state_version
```

Browserは`RTCPeerConnection`でマイクとリモート音声を接続し、DataChannelでJSONイベントを送受信する。FastAPIは認証済み`record_id`から質問定義・現在Stateを組み立て、OpenAI Python SDKの`client.live.create()`でLive sessionを作成する。`OPENAI_SECRET_KEY`はAPIだけで保持する。

記録付きsessionは次のDelegationを設定する。

```json
{
  "type": "responses",
  "responses": {
    "model": "${GPT_LIVE_RESPONSES_MODEL}",
    "instructions": "semantic assessment only",
    "tools": [{"type": "function", "name": "update_interview_state"}],
    "tool_choice": {"type": "function", "name": "update_interview_state"},
    "parallel_tool_calls": false
  }
}
```

`GPT_LIVE_RESPONSES_MODEL`の既定値は`gpt-5.6-terra`。GPT LiveにはJSONや特殊マーカーを発話させず、Liveの自然言語から正規表現でStateを抽出しない。

## Browserの接続順

1. `RTCPeerConnection`を作成する。
2. remote audioの`track` listenerを登録する。
3. `getUserMedia({ audio: true })`でマイクを取得する。
4. microphone trackを`addTrack()`する。
5. `createDataChannel("oai-events")`を作成し、message listenerを登録する。
6. offerを作成してICE gatheringを待つ。
7. `POST /api/live/sessions`へSDP offerを送る。
8. SDP answerを適用する。
9. `session.started`を受信して接続完了とする。

## DelegationとState保存

`session.delegation.created`はDelegation開始の監査イベントとして記録する。Responses側のネストされた`response.event`から、Function Toolが完了した`response.output_item.done`または`response.function_call_arguments.done`を読み取り、Browserは`POST /api/live/state-updates`へ転送する。`response.created`の`response_id`と外側の`delegation_id`も監査ログで関連付ける。その後、同じDataChannelへ`response.item.create`（`function_call_output`）を送り、`response.create`でGPT Liveの会話を再開する。

Function Toolの入力には、`turn_id`、`base_state_version`、`question_id`、`answer_status`、`covered_aspects`、`missing_aspects`、`evidence`を含める。`next_action`、次質問本文、ユーザー向け回答は含めない。Application Backendは質問・Aspectの存在、State version、冪等性、認証・認可を検証して保存する。古い`base_state_version`は`stale`として破棄し、古い結果で最新Stateを上書きしない。

`POST /api/live/captures`は字幕の原文receiptを保存するだけで、LLM評価を行わない。API lifespanのconsumerもreceiptをdurableにするだけであり、GPT Live経路でStructured Interpreterを再実行しない。意味評価の正本はResponses DelegationのFunction Tool入力である。

Responsesの結果は内部ContextとしてGPT Liveへ返す。State保存が成功した場合は、`persistedState`、最新の`checklist`、`current`もFunction Tool結果に含める。これらは次の会話判断に使う正本であり、初回session作成時の古いチェックリストを使い続けない。`covered_aspects`や`missing_aspects`をユーザーへ読み上げず、GPT Liveが既取得情報を繰り返さず、必要な不足部分だけを自然な質問へ変換する。Backend Coordinatorや`generate_interview_reply()`はGPT Live経路から呼び出さない。

Browserは`session.delegation.created`時点のユーザーTranscriptをDelegation ID単位で保持する。Responses処理中に次の発話が始まっても、Function Tool保存へ渡すTranscriptが後続発話で上書きされないようにする。新しい発話開始後は前DelegationのTranscriptを固定し、遅れて届く同一発話のTranscript断片だけを補完する。

## 遅延・停止時のUX

Frontendはユーザー発話終了からGPT Liveのresponse開始と最初の音声再生を測定する。通常の応答開始イベントまたは音声イベントを受信したらタイマーを解除する。

* 一定時間最初の音声が始まらない場合、`session.commentary.append`で「少々お待ちください。」を最大1回送る。
* 発話終了後もresponse開始がない場合、本文を生成せず`response.create`による内部continuation triggerを最大1回送る。
* Responses Delegationが処理中の場合、Watchdogのcontinuationは保留し、Function Tool結果の`response.create`だけで会話を再開する。
* 同一turnではwait noticeとcontinuationを重ねて発火させず、response開始時・終了時にタイマーを解除する。
* 待機案内はInterview State、captureのユーザー回答、Responses評価へ保存しない。

ログには`turn_id`、発話終了、Delegation作成、Function Tool call、State保存、Delegation完了、response開始、first audio、response完了、wait notice、continuation triggerを記録する。これによりBackend、Responses、Live音声のどこで遅延しているかを切り分ける。

## 既存経路との境界

GPT Liveでは質問本文をBackendから受け取らない。通常のStructured Interview、Transcribe + Polly、Nova Sonic、旧Realtimeの実装とState評価・質問生成を混ぜない。GPT Liveの音声はWebRTC MediaTrack、イベントとFunction Tool結果はDataChannelで扱い、独自VADや音声バイトのDataChannel送信は行わない。
