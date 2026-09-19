# 音声起動・ターン切替の計測

## 時刻の意味

`voice_startup_latency`はBrowserの`performance.now()`による同一起動内の経過時間と、突き合わせ用の`timestamp_ms`を出力する。
`start_clicked`、`voice_session_request_started/ready`、`get_user_media_started/ready`、
`frontend_initial_question_visible`、`provider_connect_started/connected`をSession IDで追う。
表示イベントはメッセージ更新要求時点であり、DOM paintや実音声開始を保証しない。

OpenAIのSDP応答はsideband transport接続後に返す。Browserがanswerを適用する前に
`session.created`や初回質問dispatchを待たない。Conversation Readyは別のゲートであり、
初回dispatch前のUser Turnは引き続き保留する。起動watchdog失敗時はCallを終了する。

初回Assistantの正本は`initialReplyText`。Realtime metadataのIDを使って統合し、
metadataがないtranscriptは一時保持する。`response.done`からもmetadataを回収する。
初回の全文表示を音声transcriptの断片で置き換えない。

## Transcribe/Polly

以下は異なる時刻であり、混同しない。

1. `assistant_speech_ended`: PCM生成完了（ブラウザ再生完了ではない）。
2. サーバーPlaybackBuffer排出: RTP送出側の完了。
3. ブラウザの実再生完了: jitter buffer・音声デバイス遅延を含む。
4. `assistant_playback_drained`: 現行Browser trackerが送る推定完了通知。
5. `voice_handoff event=listening_resume_requested/listening_ready`: runtime入力ゲート再開。
6. `voice_handoff event=first_user_audio_chunk`: 再開後最初の100ms PCM送信。無音も含むため、ユーザーが実際に発声した証拠ではない。

AudioOutputTrackのprerollは再生開始前だけに適用する。開始後の末尾を閾値未満という
理由で残さない。生成完了した短いsegmentも`finish_segment()`で排出する。

**未解決の計測上の制約:** 現行trackerは既知durationに1秒のguardを加える。
duration不明時は3秒fallback＋1秒guard、開始通知欠落時は最低4秒の回復待ちがある。
この通知は物理的な再生完了イベントではない。継続するWebRTC MediaStreamでは、
HTMLAudioElementの`ended`を各Assistant Turnの終了通知として使えない。
ガードを短くするだけではエコーバック防止の保証にならない。

実マイク受入確認ではBrowserとvoiceログを同一Session IDで取得し、実音声末尾と
drain通知を区別する。音声終了後0/100/300/500msに発話し、先頭語も確認する。
マイク機器・ネットワーク・出力デバイス条件を添え、未計測値を0msと記載しない。

## 初回音声の区間計測

初回音声の調査では、Browser Consoleの`voice_startup_latency`とvoice/APIログの
`voice_startup_stage`を同じ`voice_session_id`（GPT-LiveはLive session ID）で突き合わせる。
`monotonic_ms`は各プロセス内の区間計算用、`timestamp_ms`はプロセスをまたぐ相関用であり、
SDP、音声本文、認証情報はログに出さない。

主な対応は次のとおり。

| 区間 | Browser | API / voice |
| --- | --- | --- |
| T0 | `start_clicked` | - |
| T1 | `get_user_media_ready` | - |
| T2〜T3 | `voice_session_request_started` / `voice_session_ready` | `backend_voice_session_request_started` / `backend_voice_session_ready` |
| WebRTC準備 | `browser_offer_ready`、`backend_offer_request_started`、`backend_answer_received`、`remote_description_set` | `backend_offer_received`、`backend_answer_ready`、`webrtc_connected` |
| T4〜T7 | `runtime_ready_received`（legacy）/ `live_session_started`（GPT-Live） | `external_service_connect_started`、`external_service_ready`、`runtime_ready` |
| T8〜T10 | `initial_instructions_sent`、`initial_response_request_sent` | `initial_response_request_dispatched`、`first_response_event`、`first_audio_chunk_received`、`first_tts_chunk_ready` |
| T11〜T13 | `remote_audio_track_received`、`audio_playing_started` | `first_audio_frame_sent_to_browser` |

Transcribe + PollyはPollyのレスポンスを1チャンク分読み切ってからPCMを出力するため、
`first_tts_chunk_ready`は「Pollyの最初のチャンクが全量取得できた時刻」であり、
音声生成開始時刻とは異なる。Nova SonicはBedrockの音声イベントを受信した時点を
`first_audio_chunk_received`で記録する。GPT-Liveは音声データをDataChannelへ流さず、
Remote MediaTrackの`onplaying`をT13として記録する。

実測時は以下のようにログを保存し、`voice_session_id`ごとに隣接イベントの差分を計算する。

```bash
docker compose -f infra/docker-compose.yml logs --since=10m api voice
```

ブラウザ側のT0〜T13はDevTools Consoleから取得する。セッションを開始していない
ログや、一部区間しか揃っていないログから平均値・合計値を補完してはならない。

## エラー

`interview_snapshot_failed`はrecord ID、HTTP status、例外型、経過msのみ記録する。
レスポンス本文、認証情報、SDPは記録しない。`realtime_voice_start_failed`のstageと
同時刻のAPIアクセスログを照合する。GET失敗が再現していなければ原因確定とは扱わない。

## ローカル検証

```bash
cd app/web
node --test tests/*.test.mjs
```

`assistantPlaybackTiming.test.mjs`は現状の固定待ちを観測するcharacterization test。
これが通っても500msの実音声受入基準達成ではない。
Voiceの`test_formal_reply_ignores_audio_until_playback_drained`はdrain通知**後**の
0/100/300/500ms入力を検証する。外部AWS/OpenAIや物理デバイスはテストダブルである。
