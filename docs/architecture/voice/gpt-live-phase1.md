# GPT-Live Phase 1

GPT-Live Phase 1は、GPT-Live-1のWeb音声会話性能を確認するための接続検証経路である。既存のStructured Interview、VoiceSession/VoiceTurn保存、回答評価、RAG、質問生成とは分離する。

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

FastAPIはブラウザから受け取ったSDP offerでLiveセッションを作成し、`session.id`、`transport.type`、`transport.sdp`だけを返す。`OPENAI_API_KEY`はAPIコンテナの環境変数だけで読み込み、Frontendへ返さない。

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

## イベントとターン制御

最低限、`session.started`、`session.closed`、`session.input_transcript.delta`、`session.output_transcript.delta`、error系イベントをログへ記録する。Transcript deltaは断片であり、表示・観測用に扱うだけで、ターン完了、回答処理、次質問生成、応答待ちの起点にはしない。

DataChannel自体の切断はLiveセッションの正式終了を意味しないため、クライアント内部では`transport.closed`として切り分ける。サーバーから実際に届いた`session.closed`だけをLiveセッション終了イベントとして記録する。

GPT-Liveの全二重会話制御を利用するため、Phase 1では次を実装しない。

* `session.start`の送信
* `session.input_audio.append`による音声送信
* `session.output_audio.delta`のDataChannel再生
* 独自VAD、独自Turn Detector、無音起点のターン確定
* `speech_stopped`、`response.completed`、`response.done`起点の同期処理
* Transcribe、Polly、InterviewBridge、RAG、回答評価、質問生成
