# realtime-voice-v1.md

## 2026-07-27 Transcribe + Polly Runtime実装

共通`RealtimeVoiceRuntime`契約を維持したまま、`transcribe_polly`を実動作Providerとして追加した。
Transcribe + Pollyを既定Providerとし、Nova Sonicは
`VOICE_RUNTIME_PROVIDER` / `VITE_VOICE_RUNTIME_PROVIDER`で新規Voice Sessionの方式を
切り替えられるProviderとして維持する。

実装範囲:

* Transcribe Streamingへの16kHz mono PCM 100ms送信と最大2回再接続
* VAD、soft/normal/hard endpoint、安定部分・確定結果の分離
* 優先度付き単一OutputCoordinatorと20ms deadline再生
* Polly最大2件先行生成、先頭チャンクからの逐次再生、生成順序保証
* LISTEN_ACK、PROCESSING_ACK、長時間通知の固定音声キャッシュ
* 120ms連続音声によるbarge-inとgeneration単位の生成・再生キャンセル
* `clientTurnId` / `expectedStateVersion`によるAPI冪等性・競合拒否
* Barge-inの`OUTPUT_INTERRUPT`は音声出力だけを中断し、APIのUser Turnを変更しない
* API評価中の新規発話では、独立した`PENDING_TURN_CANCEL`として
  `RECEIVED/EVALUATING` Turnだけをcancel tombstoneで取消し
* `COMMITTED` Turnを維持し、明示訂正は新しいTurnと`SUPERSEDED`関連で表現
* `INTERVIEW_COMPLETED`、`INPUT_UNAVAILABLE`状態

自動検証と未確認事項は、この変更の完了報告および正本
`docs/architecture/voice/realtime-voice.md`を参照する。実AWS、実ブラウザ、負荷・クォータ試験は
別途実施する。

## 2026-07-20 async tool flow確定

最小実ストリーム試験の結果に基づき、通常回答のconfirmation prefaceを次へ変更した。

```text
audio inputはセッション中open
user transcript / toolUse
  -> 回答評価を非同期開始
  -> local fixed preface「確認します。」をWebRTCへenqueue
  -> Browser local preface playback drained
  -> 評価結果がready
  -> 元のtoolUseIdへ本来のtoolResultを1回送信
  -> 同一completion内の最終TEXT/AUDIO
  -> Browser evaluation reply playback drained
  -> input gate open
```

確定事項:

* ターンごとにaudio input contentを閉じる方式は採用しない
* cross-modal USER textによる「確認します。」生成は固定文にならなかったため採用しない
* local prefaceはPolly音声ではなく、本番と同じNova `matthew`で生成した24kHz mono s16固定PCMを使用する
* prefaceを仮toolResultとして送る方式を廃止した
* 評価後replyをinteractive USER textとして送る方式を廃止した
* toolResult後に新しいCompletionStartは発生せず、同一completion内で最終TEXT/AUDIOが続く
* local prefaceはNova completion外であり、そのplayback drainedでcompletionをfinalizeしない
* completion finalizeは評価後replyの最終ContentEndまたはCompletionEndで行う
* local prefaceと評価後replyには別responseId・generationを割り当てる
* 固定PCMは24kHz mono s16として同梱し、既存resamplerで48kHz / 20ms / 960 samplesへ変換する

自動検証:

* 評価完了先行、preface drain先行、重複通知、send retryを`EvaluationTurnCoordinator`単体テストで確認
* 元toolUseIdへのtoolResult 1回、interactive text 0回、同じcompletion内の評価TEXT/AUDIOをRuntime回帰テストで確認
* local preface drain時点でNova completionがfinalizeされないことを確認
* voice全テスト: 106件成功
* API voice contract: 22件成功
* Web TypeScript lint: 成功
* Web answer visibility: 5件成功

未確認:

* 実ブラウザで固定PCMが正常速度・正常音程で再生されること
* 「自己紹介→確認します→評価後reply→入力受付再開」の実環境完走
* 3項目連続会話

これらの実ブラウザ確認が終わるまでPhase 5CをGoとは扱わない。

## 完了済みPoC

### completion契約

以下はPoC完了済みとする。

* `CompletionStatus`
  * `GENERATING`
  * `OUTPUT_COMPLETE`
  * `PROTOCOL_COMPLETE`
* 通常ターンでは`OUTPUT_COMPLETE`を完了条件とする
* `completionEnd`は受信できた場合に`PROTOCOL_COMPLETE`として追跡する
* `userSpeechStart` / `userSpeechEnd`は正式な正常イベントとして扱う
* shutdown probeにより、セッション終了時に`completionEnd`を待機できることを確認した

### WebRTC Transport Phase 5A / 5B

Phase 5AとPhase 5Bは、実装と自動テストを完了した。

完了範囲は以下。

* 認証付きHTTP SDP offer / answer
* Voice Session認可
* Peer Connection Registry
* 1 Voice Sessionにつき同時Peer Connection 1つの制約
* Browser audio track受信
* WebRTC audioから16kHz mono PCMへの変換
* Runtime audioから48kHz WebRTC audio frameへの変換
* `voice-events` Data Channel
* Provider非依存イベント通知
* Playback Buffer
* unauthorized audio / 古いgeneration audioの破棄
* interruption時のPlayback Buffer破棄
* Fake Runtimeによる双方向音声Transportテスト
* close時のRuntime、Audio task、Data Channel、Peer Connection、Registry cleanup

自動テスト結果は以下。

```text
app/voice:
72 passed, 2 skipped
```

skipはsandbox環境におけるaiortc host candidate収集制限によるもの。
これは実装失敗ではなく、実行環境のsocket権限制限によるskipとして扱う。

### Phase 5C 実装状況

Phase 5Cは実装完了、実ブラウザ検証待ちである。

実装済み範囲は以下。

* WebRTC Transportから`NovaSonicRuntime`を生成・起動する
* Runtime起動条件を以下に限定する

```text
Voice Session認可済み
AND
Peer Connection connected
AND
Browser audio track受信済み
AND
Runtime未起動
```

* Browser音声を`NovaSonicRuntime.push_audio()`へ渡す
* Runtime共通イベントをData Channelへ送る
* Runtimeのauthorized audioだけをPlayback Bufferへ投入する
* `AssistantInterrupted`でPlayback Bufferを破棄する
* Assistant発話完了後にVoice Session状態を再取得し、`interview_state`を通知する
* Interview完了時はPlayback Buffer drain後に`interview_completed`を通知してcleanupする
* 開発用WebRTCページ`GET /voice/dev/webrtc`を用意する

Phase 5CはまだGoとは記載しない。
実ブラウザで以下を確認した後、Go / No-Goと検証結果を追記する。

* Peer Connectionが`connected`になる
* Browserマイク音声がNova Sonicへ届く
* User Transcript FINALを受信する
* Voice Turnを保存する
* 既存Interview Agentを実行する
* APIの`reply_text`をTool Resultへ設定する
* Tool Result前の音声がBrowserへ漏れない
* authorized audioだけをBrowserで再生できる
* Data Channelで共通イベントを受信できる
* Assistant Eventを保存できる
* explicit stream errorが発生しない
* close後にtaskとRegistryが残らない

### Phase 5C latency計測と改善

「回答を考えています…」が長く続く問題の切り分けとして、1ターンごとに`voice_turn_trace_id`を発行し、以下の区間をログで計測する。

```text
speech_end_to_transcript_final_ms
transcript_to_tool_use_ms
tool_use_to_tool_content_end_ms
turn_save_latency_ms
interview_process_latency_ms
transcript_to_interview_process_start_ms
interview_process_end_to_tool_result_ms
tool_result_to_assistant_text_ms
tool_result_to_first_audio_ms
speech_end_to_browser_audio_ms
```

今回の改善として、InterviewBridge処理は以下のタイミングで非同期に先行開始する。

```text
UserTranscriptFinal
AND process_interview_turn toolUse
```

Tool Resultの送信条件は従来どおり以下を維持する。

```text
InterviewBridge結果
AND TOOL contentEnd(stopReason=TOOL_USE)
```

これにより、`tool_use_to_tool_content_end_ms`の待機とTurn保存・Process API実行を並列化する。
Assistant Event保存は独立taskで行い、authorized audioのPlayback Buffer投入をブロックしない。

Frontend状態は、単一の「回答を考えています…」ではなく以下へ分ける。

```text
user_speech_ended        → 発話を確認しています…
user_transcript_final    → 回答を考えています…
assistant_response_preparing → 音声を準備しています…
assistant_speech_started → インタビュアーが話しています
assistant_speech_ended   → 聞いています
```

現時点では実ブラウザでの2ターン計測値は未取得である。
実ブラウザ確認後、以下の形式で結果を追記する。

```text
turn_1:
speech_end_to_transcript_final_ms=
tool_use_to_tool_content_end_ms=
turn_save_latency_ms=
interview_process_latency_ms=
tool_result_to_first_audio_ms=
playback_enqueue_to_browser_frame_ms=
speech_end_to_browser_audio_ms=
frontend_final_to_speech_started_ms=
frontend_speech_started_to_audio_play_ms=

turn_2:
...

largest_latency_segment=
largest_latency_cause=
optimization_applied=
explicit_stream_error=
failed_stage=
```

### 初回質問の音声化PoC

初回質問の音声化はTool Result経路へ修正済み、実ブラウザ検証待ちである。

実装方針は以下。

* `app/api`がVoice Session作成時に`initialReplyText`を保存する
* `app/voice`はVoice Session取得時に`initialReplyText`を受け取る
* `initialReplyText`は「それではインタビューを開始します。」と現在質問を改行なしで連結する
* Session開始直後に、Voice Sessionの`initialReplyStatus=pending`をclaimして`sending`へ更新する
* 初回質問本文はUSER textとして送らず、既存の強制Tool Useを起動する制御テキストだけを送る
* `process_interview_turn` Tool UseとTOOL `contentEnd(stopReason=TOOL_USE)`を受信した後、`initialReplyText`をTool Resultの`reply_text`として送る
* 初回質問にもauthorized generationを適用する
* `initialQuestionId`と`currentQuestionId`の一致を確認する
* initial authorized output complete後に`initialReplyStatus=sent`と`initialReplySentAt`を保存し、初回質問の二重送信を防止する
* 途中失敗時は`failed_retryable`へ戻す

本画面では、Browserから`sendInitialReply`のような任意指定を送らない。
初回質問の送信可否はサーバ側のVoice Session状態で決める。

初回質問PoCもまだGoとは記載しない。
実ブラウザで以下を確認した後、Go / No-Goと検証結果を追記する。

* 初回Tool Useが開始する
* 初回Tool Result後にcompletionが開始する
* 初回質問のtext/audioが生成される
* authorized audioだけをBrowserへ転送できる
* 質問文が大きく変更されない
* その後のユーザー回答で強制Tool Use経路へ移行できる

### 2026-07-15 初回発話・重複表示・音声再生修正

実装済み:

* 初回挨拶を「それではインタビューを開始します。」へ変更
* 初回発話文を改行2つではなく、挨拶と現在質問の連結に変更
* `voice_session_id + response_id`相当のmessage idでAssistantメッセージをupsertし、同じ`responseId`の二重保存を防止
* FrontendのRealtime Voiceメッセージは`voiceResponseId` / `voiceTurnId`を使ってupsert
* `event.streams[0]`がないRemote audio trackでも`MediaStream([event.track])`で再生できるように変更
* autoplay失敗時は会話全体をerrorにせず、「音声を再生」操作へ誘導
* PlaybackBuffer、AudioOutputTrack、Frontend audio要素に再生経路ログを追加

検証:

```text
app/api tests/contract/test_voice_sessions.py: 7 passed
app/voice tests/unit/nova_sonic/test_runtime.py tests/unit/test_webrtc_components.py tests/integration/test_webrtc_transport.py: 36 passed, 1 skipped
app/web build: success
```

実ブラウザ確認は未完了。
初回発話、Assistant表示重複、Browser音声再生は実ブラウザで確認後にGo / No-Goを追記する。

### 2026-07-15 表示role・質問定義分離・検索ポリシー修正

実装済み:

* Voice Session作成時の初回質問決定では、質問定義をチャットメッセージとして保存しない
* 音声経路のTurn Processでは、APIが決定した予定Assistant replyをチャットへ先行保存せず、Novaの`assistant_transcript_final`到着時だけ実発話として保存する
* Frontend snapshot取り込み時は、実発話ではないメッセージを表示対象から除外する
* Data Channel由来の`user_transcript_final`は`role=user`、`assistant_transcript_final`は`role=assistant`として明示変換する
* Realtime Voiceメッセージは`voice_session_id + turn_id`または`voice_session_id + response_id`相当でupsertする
* `RetrievalPolicy`を追加し、直接収集項目では検索を実行しない
* 暫定ルールとして、氏名、所属、担当、役割、日時、場所、数値、Yes/No、選択肢、自由記述収集に該当する項目は`retrieval_policy=never`として扱う
* `retrieval_policy=never`の単純項目では、Strands Interview Agentとread-only toolを起動せず、回答保存と次質問決定だけを行う
* UserTranscriptFinal後のTurn保存、検索判断、Process開始/完了、Tool Result、Assistant audio、Playback enqueueを追えるログを追加した

検証:

```text
app/api tests/contract/test_voice_sessions.py: 9 passed
app/voice tests/unit/nova_sonic/test_runtime.py tests/unit/test_interview_bridge.py tests/unit/test_interview_api_client.py tests/unit/test_webrtc_components.py tests/integration/test_webrtc_transport.py: 41 passed, 1 skipped
app/web build: success
```

未実施:

```text
実ブラウザでの2ターン会話確認
turn_1/turn_2の実測latency取得
Assistant音声再生の実機確認
```

実ブラウザ確認後、以下を追記する。

```text
phase=voice-chat-role-and-retrieval
initial_question_placeholder_visible=
initial_spoken_message_visible=
initial_audio_played=
user_answer_role=
assistant_reply_role=
assistant_reply_render_count=
turn_1_retrieval_policy=
turn_1_retrieval_executed=
turn_1_process_latency_ms=
turn_1_first_audio_latency_ms=
turn_2_process_completed=
explicit_stream_error=
failed_stage=
```

### 2026-07-20 Nova Sonic Runtime依存境界リファクタリング

この節は内部構造の進捗と検証状況を記録する。会話契約は変更していないため、`docs/spec.md`の更新対象ではない。

完了:

* `ProtocolEventDispatcher(self)`と`ToolTurnCoordinator(self)`を廃止した
* `runtime_ports.py`のProtocolと専用コンポーネントによる依存注入へ変更した
* 分割先からNovaSonicRuntimeのprivate属性・privateメソッド参照を除去した
* `PendingTurnStore`を抽出し、通常turn、評価待ちturn、tool taskの直接辞書共有を廃止した
* `ToolResultSender`を抽出し、tool result送信と承認対象登録をCoordinatorから分離した
* Runtimeの単純委譲ラッパーと他コンポーネントのprivate呼び出しを削除した
* completion lifecycleを`CompletionLifecycle`へ抽出し、generation再利用、preface/follow-up順序、send retryの回帰テストを維持した
* voiceテスト117件が成功した
* `python -m compileall`が成功した

未完了:

* `runtime.py`は1,049行、71メソッド・propertyであり、Facadeとしてまだ大きい
* initial reply lifecycleと関連task管理がRuntimeに残っている
* prompt/session end、stream close、receive task停止などshutdown / stream lifecycleがRuntimeに残っている
* audio入力計測、latency、failed stage、reply CompletionStart watchdogなどobservabilityの抽出状況を再確認する必要がある
* completion本体の状態遷移は抽出済みだが、initial reply完了記録とwatchdog callbackがRuntimeへ逆流していないか継続して確認する
* 実ブラウザで3項目以上の連続会話は未確認
* `ruff`は実行環境に存在せず未実行

行数だけを完了基準にしない。Runtimeを、依存組み立て、外部API、Novaストリーム開始・終了、音声入力、dispatcher委譲、Runtime event queue公開へ限定し、抽出先ごとの責務と単体テストが成立した段階を完了とする。

### 2026-07-20 「確認します。」停止問題の確認結果

> Historical: この節は旧「preface toolResult + interactive USER text」経路の診断記録である。現行方式は本書冒頭の「async tool flow確定」を正とする。

対象会話:

```text
AI: 自己紹介をお願いします。
User: 田中です
AI: 確認します。
期待: すみません。自己紹介をお願いします。
```

#### 実装上

preface completionからevaluation reply dispatchまでのレース対策は実装済みである。

* preface完了前に評価が終わる順序と、評価前にprefaceが終わる順序の両方を扱う
* AssistantSpeechEndedがなくても、audio/final text ContentEndまたはCompletionEndでauthorized completionをfinalizeする
* `CompletionState.finalized`により複数完了イベントからのfollow-up送信を1回に制限する
* authorize失敗時はuser textを送信しない
* send sequence失敗時はqueued replyを保持し、0.2秒後に1回だけ再試行する
* follow-upのBrowser再生完了前は入力ゲートを開かない

#### テスト上

以下のcharacterization / regression testが存在し、voice全117件が成功している。

* preface完了が評価完了より先
* 評価完了がpreface完了より先
* AssistantSpeechEndedなしでCompletionEndのみ受信
* CompletionEndなしでaudio/final text ContentEndを受信
* ContentEndとCompletionEndの両方を受信してもfollow-up送信は1回
* authorize失敗時にuser textを送信しない
* send失敗後にqueued replyを保持して1回だけretryする
* 同じcompletionIdを別response/generationで再利用すると状態を初期化する
* 古いgenerationの終了イベントで新しいfollow-upを終了しない
* preface再生完了だけでは入力ゲートを開かず、follow-up再生完了後のdrained通知とguardで開く

#### 実ブラウザ上

判定は**コード修正済み・実環境では未解決**である。2026-07-20の実ブラウザログでは、preface completionレースは通過したが、後続音声の再生には到達していない。

観測した実イベント列:

```text
confirmation_preface_enqueued                    304820253 ms
confirmation_preface_output_complete             304820868 ms
authorized_completion_finalized                  304820868 ms
preface_completion_finished                      304820868 ms
evaluation_reply_blocked reason=reply_not_ready  304820868 ms
assistant_playback_drained                       304825043 ms
PeerConnection closed                            304844274 ms前後
evaluation_reply_ready                           304850250 ms
evaluation_completion_started                    304850251 ms
evaluation_reply_send_started                    304850251 ms
evaluation_reply_send_failed                     Runtime未開始
retry evaluation_reply_send_started              304850487 ms
retry evaluation_reply_send_failed               Runtime未開始
```

API側では`answer_evaluation_started=304820251 ms`の後、Bedrock RuntimeのDNS名前解決に失敗した。エラーfallbackがVoiceへ返るまで約30秒かかり、その約6秒前にBrowserのPeerConnectionとRuntimeが閉じられていた。送信失敗の例外は`RuntimeError: NovaSonicRuntime has not been started`である。

この実行では以下へ到達していない。

```text
assistant_reply_authorize_succeeded
assistant_reply_sequence_send_completed
evaluation_reply_send_completed
follow-upのnova_completion_started
evaluation_audio_first_chunk_received
follow-upのassistant_speech_started / assistant_speech_ended
follow-upのassistant_playback_drained
follow-up後のvoice_input_gate_opened
```

したがって現在の正確な停止箇所は、**評価reply ready後の`evaluation_reply_send_started`からauthorizeへ入る前**である。ただし直接原因はdispatch条件ではなく、評価遅延中にPeerConnectionが閉じてRuntimeが停止したことである。

次の修正候補は以下。

* `app/api/.../services/voice_interview.py`: Bedrock評価失敗のtimeoutとfallback完了時間を短縮し、`answer_evaluation_ms`を分離計測する
* `app/voice/.../nova_sonic/reply_sequence.py`: Runtime終了後のretryを無駄に繰り返さず、session closedとして明示終了する
* WebRTC session管理箇所: 評価処理中にPeerConnectionが閉じた契機を特定し、`ANSWER_PROCESSING`中の不要なcleanupでないことを確認する

preface completion修正の回帰テストだけではWebRTC・Nova実ストリーム上の後続発話を保証できない。API評価が正常に2秒以内で完了する環境で、次の列を再度実ブラウザ確認する。

```text
preface_completion_finished
evaluation_reply_ready
evaluation_reply_send_started
assistant_reply_authorize_succeeded
assistant_reply_sequence_send_completed
evaluation_reply_send_completed
nova_completion_started
evaluation_audio_first_chunk_received
assistant_playback_drained
voice_input_gate_opened
```
