# GPT-Live WebRTC

GPT-Live-1のWeb音声会話経路。接続確認後は、会話音声と字幕をGPT-Liveに任せたまま、モデルが明示的に委譲した回答だけを既存のStructured Interview状態へ反映する。既存のVoiceSession/VoiceTurn保存、旧Realtimeのターン制御、独自VADは共有しない。

既存の`openai_realtime`（旧Realtime API）経路は後方互換のため残すが、`gpt_live`経路からは実装・イベント処理・ターン制御を共有しない。

## 接続構成

```text
Browser
  ├─ microphone MediaTrack
  ├─ remote audio MediaTrack
  └─ RTCPeerConnection
        │
        │ WebRTC
        ▼
     GPT-Live-1

FastAPI app/api
  └─ POST /api/live/sessions
       └─ OpenAI Python SDK client.live.create()
```

FastAPIはブラウザから受け取ったSDP offerでLiveセッションを作成し、`session.id`、`transport.type`、`transport.sdp`だけを返す。旧Realtime経路と共通の`OPENAI_SECRET_KEY`をAPIコンテナの環境変数だけで読み込み、Frontendへ返さない。

記録付きのセッション作成では、FastAPIが認証済みの`record_id`から質問項目、`questionPlan.requiredItems`、現在の状態を読み取り、Live instructionsへチェックリストとして渡す。ブラウザから質問項目や権限情報を受け取って信頼しない。

APIはLive API対応の`openai-python`（`openai>=3.12.0,<4.0.0`、現在のlock解決版は`3.16.2`）を使用し、セッション作成は公式の`client.live.create()`だけで行う。`/live/sessions`への互換HTTP層や旧Realtime APIへのフォールバックは実装しない。

## Browserの接続順

Frontendの`gpt_live`経路は次の順序を固定する。

1. `RTCPeerConnection`を作成する。
2. remote audioの`track` listenerを登録する。
3. `getUserMedia({ audio: true })`でマイクを取得する。
4. microphone trackを`addTrack()`する。
5. offer作成前に`createDataChannel("oai-events")`を作成する。
6. DataChannelの`message` listenerを登録する。
7. offerを作成し、local descriptionへ設定する。
8. ICE gathering完了を待つ。
9. local SDPを`POST /api/live/sessions`へ送る。
10. 返却されたSDP answerをremote descriptionへ設定する。
11. DataChannelで`session.started`を受信して接続完了とする。

音声はWebRTC MediaTrackだけで送受信する。DataChannelはJSONイベントの監視専用であり、音声データを送受信しない。

## イベント、字幕、状態委譲

最低限、`session.started`、`session.closed`、`session.input_transcript.delta`、`session.output_transcript.delta`、error系イベントをログへ記録する。Transcript deltaは断片なので、順序どおりに連結して字幕へ表示するだけであり、ターン完了、回答処理、次質問生成、応答待ちの起点にはしない。

記録付きセッションでは`session.delegation.created`だけをアプリケーション状態更新の境界として扱う。ブラウザはそれまでに蓄積したユーザー字幕を`POST /api/live/delegations`へ送り、FastAPIが既存のStructured Interview状態 writerで評価・確定し、結果を`session.thinking.append`でLiveへ返す。これはモデルの委譲結果を返す処理であり、無音時間や字幕deltaの終端を検出する独自ターン管理ではない。

DataChannel自体の切断はLiveセッションの正式終了を意味しないため、クライアント内部では`transport.closed`として切り分ける。サーバーから実際に届いた`session.closed`だけをLiveセッション終了イベントとして記録する。

会話は委譲した回答の保存・検証完了を待たず継続する。Liveには会話中に得た回答とチェックリストに基づいて質問を進めるよう指示し、保存済み・完了の断定だけはbackendの結果を根拠とする。保存処理は記録の整合性のため順序を保持するが、MediaTrackや字幕の処理を止めない。委譲時に字幕を予約して後続委譲との重複を防ぎ、切断後の古い結果は新しい接続へ返さない。状態再取得の完了をLiveへの結果通知の条件にしない。`gpt_live_delegation_applied.elapsed_ms`は保存APIの待ち時間であり、発話遅延の実測値ではない。

GPT-Liveの全二重会話制御を利用するため、次を実装しない。

* `session.start`の送信
* `session.input_audio.append`による音声送信
* `session.output_audio.delta`のDataChannel再生
* 独自VAD、独自Turn Detector、無音起点のターン確定
* `speech_stopped`、`response.completed`、`response.done`起点の同期処理
