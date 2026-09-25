# Transcribe + Polly 設定

このページは、Voice serviceで`transcribe_polly`を使用するときの設定をまとめたものです。
環境変数はリポジトリルートの`.env`に記載し、Docker Composeを再起動して反映します。

```bash
docker compose --env-file .env -f infra/docker-compose.yml up -d --build
```

## 1. どの経路で使われる設定か

`VOICE_RUNTIME_PROVIDER=transcribe_polly`のとき、Browserの音声入力をAmazon Transcribe Streamingで文字起こしし、Interview APIの正式な応答をAmazon Pollyで読み上げます。
`nova_sonic`や`openai_realtime`を選択している場合、以下のTranscribe + Polly設定は通常の応答経路には適用されません。

AWS profileはVoiceコンテナからホストの`~/.aws`を参照します。アクセスキーやシークレットキーを`.env`へ直接書かないでください。

## 2. 接続と経路の基本設定

| 環境変数 | 初期値 | 何を設定するか | 設定時の注意 |
| --- | --- | --- | --- |
| `VOICE_RUNTIME_PROVIDER` | `transcribe_polly` | Browserが開始する音声Runtimeの既定経路を選びます。 | `transcribe_polly`、`nova_sonic`、`openai_realtime`のいずれかを使用します。Transcribe設定を使う場合は`transcribe_polly`にします。 |
| `AWS_PROFILE` | `default` | Transcribe、Polly、WebRTC用AWS APIへ接続するprofile名です。 | Voiceコンテナにマウントされた`~/.aws`に同名profileが必要です。 |
| `AWS_REGION` | `ap-northeast-1` | VoiceがTranscribeとPollyを呼び出すAWSリージョンです。 | `AWS_DEFAULT_REGION`と異なる値にする場合は、意図したリージョンを確認してください。 |
| `AWS_DEFAULT_REGION` | `ap-northeast-1` | AWS SDKがリージョン未指定時に使う既定リージョンです。 | `AWS_REGION`と揃えるのが安全です。 |
| `VOICE_API_BASE_URL` | `http://127.0.0.1:8001` | VoiceがInterview APIへTranscriptと音声ターンを渡す接続先です。 | ComposeではAPIコンテナのポート設定に合わせます。 |
| `INTERNAL_API_TOKEN` | `dev-internal-token` | VoiceとInterview API間の内部API認証トークンです。 | 本番では開発用の既定値を使わず、秘密情報を安全に注入します。 |

## 3. Transcribe（音声認識）の設定

| 環境変数 | 初期値 | 何を設定するか | 調整するとどうなるか |
| --- | --- | --- | --- |
| `TRANSCRIBE_LANGUAGE_CODE` | `ja-JP` | Transcribeが聞き取る言語です。 | インタビュー言語と一致させます。言語が違うと認識精度が下がります。 |
| `TRANSCRIBE_PARTIAL_RESULTS_STABILITY` | `medium` | 部分Transcriptのうち、後から変わりにくい文字列をどの程度早く確定扱いにするかです。 | `low`は早く反映されやすい一方で変動が増え、`high`は安定する一方で反映が遅くなります。 |
| `TRANSCRIBE_AUDIO_CHUNK_MS` | `100` | Transcribeへ送るPCM音声を何ミリ秒単位に分割するかです。 | 小さくすると反応は細かくなりますが送信回数が増え、大きくすると遅延と再接続時の欠落単位が大きくなります。 |
| `TRANSCRIBE_RECONNECT_ATTEMPTS` | `2` | Transcribe Streaming接続が切れたときの再接続回数です。 | 増やすと一時的な切断に強くなりますが、失敗確定まで長くなります。 |
| `TRANSCRIBE_RECONNECT_AUDIO_BUFFER_MS` | `3000` | 再接続中に保持する未送信音声の最大時間です。 | 増やすと再接続時の音声欠落を抑えられますが、メモリ使用量と古い音声を送るリスクが増えます。 |
| `TRANSCRIBE_VAD_RMS_THRESHOLD` | `600` | ローカルVADが「発話あり」と判定する音声エネルギーのしきい値です。 | 小さくすると小声や環境音も拾いやすくなり、大きくすると雑音に反応しにくくなります。マイクごとに調整します。 |

## 4. 発話終了・Transcript確定の設定

これらはTranscribeの認識結果をいつ1ターンの回答としてInterview APIへ渡すかを決めます。

| 環境変数 | 初期値 | 何を設定するか | 調整するとどうなるか |
| --- | --- | --- | --- |
| `VOICE_ENDPOINT_SILENCE_MS` | `600` | 発話後の無音を検出して、回答の終了候補（soft endpoint）にするまでの時間です。 | 小さくすると次の処理が早くなりますが、文の途中で確定しやすくなります。大きくすると話し終わりを待てますが応答開始が遅くなります。 |
| `VOICE_TRANSCRIPT_SETTLE_MS` | `300` | soft endpoint後にTranscriptの更新を観測する互換設定です。これだけでユーザー発話を確定することはありません。 | 小さくしてもhard endpointより前にはInterview APIへ送られません。 |
| `VOICE_FINAL_RESULT_SETTLE_MS` | `150` | `VOICE_TRANSCRIPT_SETTLE_MS`を指定しない旧呼び出し元向けの確定待ち時間です。 | 通常のCompose経路では`VOICE_TRANSCRIPT_SETTLE_MS`を優先します。互換設定として残しています。 |
| `VOICE_HARD_TIMEOUT_MS` | `1200` | soft endpoint後も入力を同じユーザーturnとして待つhard endpointまでの時間です。初期値ではsoft 600msと合わせて最大約1800msです。 | 小さくすると応答は早くなりますが、長い間を置いた続きが次turnへ分かれる可能性があります。 |
| `VOICE_HARD_ENDPOINT_MS` | `1000` | 旧endpoint判定を使う呼び出し元向けの互換設定です。 | 現行の本番Compose経路は`VOICE_ENDPOINT_SILENCE_MS`、`VOICE_TRANSCRIPT_SETTLE_MS`、`VOICE_HARD_TIMEOUT_MS`を使います。 |

## 5. Polly（読み上げ）の設定

| 環境変数 | 初期値 | 何を設定するか | 調整するとどうなるか |
| --- | --- | --- | --- |
| `POLLY_VOICE_ID` | `Kazuha` | 日本語の読み上げに使うPolly音声です。 | 選ぶ音声によって声質と速度感が変わります。インタビュー言語が日本語以外の場合は言語ごとの既定音声が優先されます。 |
| `POLLY_ENGINE` | `neural` | Pollyの音声合成エンジンです。 | 選択した音声が対応しているエンジンを指定します。通常は`neural`を使います。 |
| `POLLY_SPEECH_RATE` | `115%` | Pollyの実際の読み上げ速度です。 | `100%`が通常速度です。`110%`〜`120%`程度から試し、速すぎる場合は下げます。`fast`などのプリセットも指定できます。 |
| `POLLY_MAX_PARALLEL_REQUESTS` | `2` | 応答文の先頭チャンクなどをPollyへ先行要求する同時数です。 | 増やすと音声の待ち時間を短くできる場合がありますが、AWS API呼び出し数と同時実行負荷が増えます。読み上げ速度そのものは変わりません。 |

`POLLY_SPEECH_RATE`は、次の形式を使用できます。

```env
# 数値指定（20%〜200%）
POLLY_SPEECH_RATE=115%

# Pollyのプリセット
# POLLY_SPEECH_RATE=fast
```

実装では文章をSSMLの`<prosody>`へ入れてからPollyへ渡しています。文章中の`<`、`>`、`&`はSSMLとして解釈されないようにエスケープします。速度を変更した音声は別キャッシュとして扱います。
Pollyの速度指定の詳細は[AWS公式ドキュメント](https://docs.aws.amazon.com/polly/latest/dg/voice-speed-change-vip.html)を参照してください。

## 6. 相槌・応答処理・再生終了の関連設定

| 環境変数 | 初期値 | 何を設定するか | 調整するとどうなるか |
| --- | --- | --- | --- |
| `VOICE_ENABLE_BACKCHANNELS` | `false` | 発話中の「なるほど。」などの相槌と、処理中の案内を有効にするかです。 | `true`にするとPollyの先行生成が行われます。通常の短い間では再生せず、発話途中の長い無音が続いた場合だけ相槌を出します。 |
| `VOICE_PROCESSING_ACK_DELAY_MS` | `900` | ユーザーturnを確定してから、Interview APIの処理が続いている場合に固定音声の相槌を開始するまでの時間です。 | `700`〜`1200`ms程度で調整します。応答が速い場合は正式応答開始時にキャンセルされます。 |
| `VOICE_TURN_SAVE_TIMEOUT_SECONDS` | `5` | 音声ターンの保存処理を待つ上限秒数です。 | 小さくしすぎると保存完了前にタイムアウトしやすくなります。 |
| `VOICE_TURN_PROCESS_TIMEOUT_SECONDS` | `30` | Interview APIの回答解釈・次質問生成を待つ上限秒数です。 | 小さくすると失敗応答へ早く切り替わりますが、通常のLLM処理も失敗扱いになりやすくなります。 |
| `VOICE_WEBRTC_PLAYBACK_DRAIN_TIMEOUT_SECONDS` | `5` | 読み上げ音声の再生終了を待つ上限秒数です。 | 小さくすると入力再開が早くなりますが、末尾再生中に次ターンへ進むリスクがあります。 |

## 7. 調整例

読み上げだけを少し速くする場合は、次の変更で足ります。

```env
POLLY_SPEECH_RATE=115%
```

応答開始までの待ち時間も短くしたい場合は、読み上げ速度とは別に次を検討します。

```env
POLLY_SPEECH_RATE=115%
POLLY_MAX_PARALLEL_REQUESTS=2
VOICE_ENDPOINT_SILENCE_MS=500
VOICE_TRANSCRIPT_SETTLE_MS=250
```

無音判定と確定待ちを同時に下げると、話の途中でターンが確定する可能性があります。まず`POLLY_SPEECH_RATE`だけを変更し、次にログの`polly_first_chunk_ms`と`total_turn_latency_ms`を確認しながら、認識・確定設定を個別に調整してください。
