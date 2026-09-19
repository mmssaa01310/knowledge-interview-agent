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

記録付きセッションでは`POST /api/live/captures`へ未送信の字幕を送る。2秒の間隔は通信頻度の制限であり、新しい発話でリセットする無音検出ではない。`session.delegation.created`は送信を早めるだけで、届かなくても保存する。リクエストは認証・記録操作権限・Knowledgeアクセスを検証し、capture_idとrevisionで冪等化する。原文と受付ユーザーを既存の`messages`へ永続化してからHTTP 202を返す。これは受付完了であり、LLM整理完了ではない。受付失敗時はブラウザが同一バッチを再送する。

APIのlifespanで動作するconsumerが未処理の受付を読み取り、LLM整理と共通状態writerによる保存を行う。同一captureで届いている字幕は累積観測としてまとめて処理する。失敗時はDBに再試行時刻を保存し、最大60秒の間隔で再試行する。API再起動時も未処理受付を再取得する。PostgreSQL advisory lockでAPIプロセス間の重複処理を直列化し、字幕受付はLLM処理のロックを待たせない。新しいAWSサービス・DB・デプロイ単位は追加しない。

Frontendは認証付き`GET /api/live/captures/{record_id}?capture_id={capture_id}`で、現在の音声セッションに限定した整理状態とチェックリストを取得する。過去セッションの再試行待ちreceiptは現在セッションの`processing`表示に影響しない。音声と字幕は整理・状態取得の完了を待たず進む。

Backendは同一captureの両話者の履歴と全質問定義を共通Interpreterへ渡す。最新の固定質問への紐付けや次質問生成は行わない。部分回答・追加回答・訂正をitemId単位で統合し、共通Coordinatorが根拠とrequiredItemsを検証する。未回答を推測で埋めず、未確定候補は「補足・確認待ち」として表示する。この表示はLLM処理中を意味しない。完了済み項目の訂正でも他の既取得詳細を保持する。正式ナレッジの承認処理は呼ばない。

## 完了とページ離脱

必須項目を満たした後は、GPT-Liveが最後の自由な追加事項を一度尋ねる。Live観測用Structured Outputは、この質問と完結したユーザー回答の根拠IDを返す。Backendが話者・順序・根拠・発話の完全性を検証して`closingState`へ反映し、既存のProfile別`evaluate_completion`で完了を判定する。項目不足、未確認候補、矛盾、未確定Applicability、未整理受付が残る間は完了にしない。完了時は`interviewState.status=completed`を保存し、既存の記録ライフサイクルで`submitted`へ進める。人による正式承認とは別である。

Frontendは未送信字幕がなく、最新の取得結果で整理完了・インタビュー完了を確認してから音声接続を閉じ、「完了」を表示する。手動停止時に整理が残っている場合は「回答を整理中」の状態を表示する。遅れて届いた以前のセッションの結果では、新しい音声接続を終了しない。
完了通知がブラウザへ届く前の末尾字幕は、同じ受付ユーザー・同じcaptureに限り、最初の完了保存から30秒間だけ`submitted`後も受け付ける。この猶予は再送で延長しない。追加回答で再確認や整理が必要になった場合は監査を残して`in_progress`へ戻し、再び共通完了条件を評価する。`approved`の記録は変更しない。

整理済みの状態と不足詳細を`session.thinking.append`（delegation_id=null）で返す。同じ状態を繰り返し送らず、追加質問の参考情報として扱う。モデルには保存結果を待つことや新たな発話を要求しない。これは[Liveのバックグラウンド連携仕様](https://developers.openai.com/es-419/api/docs/guides/live-delegation)に基づく観測処理である。

DataChannel自体の切断はLiveセッションの正式終了を意味しないため、クライアント内部では`transport.closed`として切り分ける。サーバーから実際に届いた`session.closed`だけをLiveセッション終了イベントとして記録する。

会話は保存・検証完了を待たず継続する。保存中の追加字幕は次のバッチへ蓄積し、MediaTrackや字幕処理を止めない。切断時は未送信分を送信し、受付中の処理を取り消さない。字幕送信は容量を制限した`fetch keepalive`を使用し、ページ非表示・離脱時もflushする。未受付字幕が残る間はブラウザ離脱の警告対象とする。DB受付済みの整理・保存・再試行はページ移動やブラウザ終了後もAPI側で継続する。ネットワーク断、端末強制終了、ブラウザのkeepalive制限により、サーバー未到達の字幕までは保証できない。LLMの意味抽出精度と実音声の終了タイミングは、自動テストだけでは保証できない。

GPT-Liveの全二重会話制御を利用するため、次を実装しない。

* `session.start`の送信
* `session.input_audio.append`による音声送信
* `session.output_audio.delta`のDataChannel再生
* 独自VAD、独自Turn Detector、無音起点のターン確定
* `speech_stopped`、`response.completed`、`response.done`起点の同期処理
