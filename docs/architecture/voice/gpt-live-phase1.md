# GPT-Live WebRTC

GPT-Live-1のWeb音声会話経路。会話音声と字幕をGPT-Liveに任せたまま、字幕の累積観測を既存Structured Interpreterで整理し、共通の状態writerで質問リストへ反映する。既存のVoiceSession/VoiceTurn保存、旧Realtimeのターン制御、独自VADは共有しない。

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

最低限、`session.started`、`session.closed`、`session.input_transcript.delta`、`session.output_transcript.delta`、error系イベントをログへ記録する。Transcript deltaは断片なので、順序どおりに字幕表示し、話者・時刻とともに蓄積する。ターン完了、次質問生成、応答待ちの起点にはしない。

記録付きセッションでは`POST /api/live/captures`へ未送信の字幕を送る。2秒の間隔は通信頻度の制限であり、新しい発話でリセットする無音検出ではない。`session.delegation.created`は送信を早めるだけで、届かなくても保存する。リクエストは認証・記録操作権限・Knowledgeアクセスを検証し、capture_idとrevisionで冪等化する。失敗時も原文を保持し、同一バッチを再試行してから後続を送る。

Backendは同一captureの両話者の履歴と全質問定義を共通Interpreterへ渡す。最新の固定質問への紐付けや次質問生成は行わない。部分回答・追加回答・訂正をitemId単位で統合し、共通Coordinatorが根拠とrequiredItemsを検証する。未回答を推測で埋めず、全必須詳細が揃うまでは整理中として表示する。完了済み項目の訂正でも他の既取得詳細を保持する。正式ナレッジの承認処理は呼ばない。

整理済みの状態と不足詳細を`session.thinking.append`（delegation_id=null）で返す。同じ状態を繰り返し送らず、追加質問の参考情報として扱う。モデルには保存結果を待つことや新たな発話を要求しない。これは[Liveのバックグラウンド連携仕様](https://developers.openai.com/es-419/api/docs/guides/live-delegation)に基づく観測処理である。

DataChannel自体の切断はLiveセッションの正式終了を意味しないため、クライアント内部では`transport.closed`として切り分ける。サーバーから実際に届いた`session.closed`だけをLiveセッション終了イベントとして記録する。

会話は保存・検証完了を待たず継続する。保存中の追加字幕は次のバッチへ蓄積し、MediaTrackや字幕処理を止めない。状態再取得も保存キューを待たせない。切断時は未送信分を送信し、保存中の処理を取り消さない。ただし古い結果を新しい音声接続へ送らない。ブラウザ終了・ページ再読み込みを跨ぐ送信保証はないため、保存エラー表示中はページを閉じず再試行を待つ。LLMの意味抽出精度と実音声の応答時間は、自動テストだけでは保証できない。

GPT-Liveの全二重会話制御を利用するため、次を実装しない。

* `session.start`の送信
* `session.input_audio.append`による音声送信
* `session.output_audio.delta`のDataChannel再生
* 独自VAD、独自Turn Detector、無音起点のターン確定
* `speech_stopped`、`response.completed`、`response.done`起点の同期処理
