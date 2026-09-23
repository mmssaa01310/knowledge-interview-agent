# 音声インタビューE2Eテスト

KIKIORIの音声インタビューには、Playwrightを使った3段階のテストを用意しています。通常のテストは外部のGPT Live、Transcribe、Polly、Jev、LLM Judgeを呼びません。

## テストレベル

### Level 1: local / free

local-state.spec.mjs が、PlaywrightのAPI request fixtureとローカルAPIを使って次を確認します。

- transcript final が回答Stateへ保存されること
- candidate / confirmed と answerState
- stateVersion の増加
- GPT Liveの責務である次質問選択と、State Storeの責務である保存の分離
- ANSWER_NOT_PERSISTED などの決定論的な異常検出

### Level 2: browser + audio fixture

`browser-audio.spec.mjs` は、まずNORMALシナリオを対象にChromiumを起動し、テスト実行時に生成した固定PCM WAVを仮想マイクへ渡します。Live sessionとDataChannelはブラウザ内のfake transportで再現し、State updateだけはローカルAPIへ送ります。

このレベルでは、実GPT Live、Responses API、Transcribe、Pollyを呼びません。したがって、音声認識精度や実GPT Liveのレイテンシではなく、次の会話制御経路を低コストで確認します。

    WAV仮想マイク
      → fake Live transport の user transcript final
      → Interview State 保存
      → currentQuestion / stateVersion 更新
      → fake assistant speech started

Telemetryには、この境界を明示するため次の値を保存します。

- `audioInputMode`: `fake_microphone_wav`
- `transcriptSource`: `fake_live_transport`
- `liveApiUsed`: `false`
- `simulatedTurnLatencyMs`: `user_turn_completed` から `assistant_speech_started` までの擬似遅延

Level 2の`candidate`が自動確定処理によって`null`になる場合はあります。その場合でも、`confirmed`（`recordAnswer`）、`answerState`、`stateVersion`を検証します。

### Level 3: real voice API

`live/real-voice.spec.mjs` と `live/real-transcribe-polly.spec.mjs` は通常スキップされます。次の明示的なopt-inがある場合だけ実プロバイダを使います。

    VOICE_TEST_REAL_API_ENABLED=true \
    VOICE_TEST_START_LOCAL_STACK=true \
    pnpm voice-test -- --grep "real provider"

Transcribe + Pollyを実行する場合は、プロバイダも明示する。

    VOICE_TEST_REAL_API_ENABLED=true \
    VOICE_TEST_REAL_PROVIDER=transcribe_polly \
    VOICE_TEST_AUDIO_FIXTURE=/absolute/path/to/recorded.wav \
    VOICE_TEST_START_LOCAL_STACK=true \
    pnpm voice-test -- --grep "real Transcribe"

通常の pnpm test:e2e:voice では実APIテストは起動しません。

### Jev Supervisor

`TEST_AGENT_MODE=jev`を明示した場合だけ、`jev-supervisor.spec.mjs`を実行します。Jevは各ターン終了後のテスト進行だけを判断し、ユーザー発話本文は生成しません。発話には`scenarios.mjs`の固定fixtureを使用します。

各ターンは次の順で処理します。

    transcript final / State / transport
      → Hard Gate
      → Jev Supervisor（PASS時のみ、最大1回）
      → CONTINUE / STRESS_NEXT / RETRY_CURRENT / END_SUCCESS
      → 固定fixtureで次のターン

Hard Gateで異常が検出された場合は、Jevを呼ばずにFAILまたはSTOPします。対象はtranscript欠落、回答未保存、質問遷移不備、重複応答、HTTP 5xx、WebSocket/DataChannelエラー、応答タイムアウト、ターン数・Jev呼出数上限です。同一StateのJev結果はプロセス内でキャッシュします。

実行例:

    TEST_AGENT_MODE=jev \
    JEV_API_URL=https://jev.example/supervise \
    JEV_API_KEY=... \
    VOICE_TEST_START_LOCAL_STACK=true \
    VOICE_TEST_MAX_RETRIES=0 \
    pnpm exec playwright test tests/e2e/voice/jev-supervisor.spec.mjs --project=chromium

`JEV_API_KEY`はAuthorization Bearerとして送信し、TelemetryやEvidence Packageには保存しません。環境変数が未指定の場合はリポジトリルートの`.env`から`JEV_API_URL`と`JEV_API_KEY`を読み取ります。`TEST_AGENT_MODE=jev`を指定して`JEV_API_URL`が未設定の場合は、接続先が不明なため設定エラーとして停止します。通常のテストでは`TEST_AGENT_MODE`を指定しないため、Jev APIは呼ばれません。

## 実行方法

Playwright依存を取得したあと、APIとWebを別途起動して実行できます。

    pnpm install --frozen-lockfile
    pnpm test:e2e:voice

APIを含むローカルスタックをテスト側で起動する場合は次のようにします。

    uv sync --project app/api --locked
    VOICE_TEST_START_LOCAL_STACK=true pnpm test:e2e:voice

startLocalStack.mjs はAPIを memory://test、Webを5173番ポートで起動し、dev用のvoice demo recordをseedします。
実際に発話をTranscribeするLevel 3では、`VOICE_TEST_AUDIO_FIXTURE=/absolute/path/to/recorded.wav`で録音済みWAVを指定してください。未指定時の短いWAVは、Level 2のマイク接続・イベント経路確認用です。
Transcribe + Pollyのローカルスタックを使う場合は、先に`uv sync --project app/voice --locked`も実行してください。

Level 1の決定論的シナリオ・seedの再実行:

    pnpm voice-test -- --scenario SELF_CORRECTION
    pnpm voice-test -- --replay 38291

Level 2のブラウザ音声テストは、現在はNORMALシナリオを固定実行します。

## コストガード

設定の既定値は tests/e2e/voice/voice-test.config.json に記載しています。環境変数で上書きできます。

| 環境変数 | 既定値 | 意味 |
| --- | ---: | --- |
| VOICE_TEST_REAL_API_ENABLED | false | 実APIテストの明示許可 |
| VOICE_TEST_REAL_PROVIDER | gpt_live | Level 3で使う実プロバイダ（`gpt_live` / `transcribe_polly`） |
| VOICE_TEST_AUDIO_FIXTURE | 未指定 | 仮想マイクへ入力する録音済みWAVの絶対パス |
| VOICE_TEST_MAX_SESSIONS | 3 | 1実行で許可するセッション数 |
| VOICE_TEST_MAX_TURNS_PER_SESSION | 8 | セッションごとの最大ターン数 |
| VOICE_TEST_MAX_AUDIO_SECONDS_PER_SESSION | 120 | セッションごとの音声秒数 |
| VOICE_TEST_MAX_JEV_CALLS | 20 | Jevの最大呼出数 |
| VOICE_TEST_MAX_LLM_JUDGE_CALLS | 5 | LLM Judgeの最大呼出数 |
| VOICE_TEST_MAX_RETRIES | 1 | Playwright retryの上限 |

超過した場合はテストを直ちに失敗させます。決定論的実行が既定です。Jevモードでは未指定時の上限を1セッション、5ターン、5 Jev callsとして扱います。`TEST_AGENT_MODE=jev`と`JEV_API_URL`が指定されている場合だけJevを呼びます。

## TelemetryとEvidence Package

各テストの出力先に以下を保存します。

- `telemetry.jsonl`: turn、transcript final、candidate/confirmed、answerState、State version、current/next question、擬似レイテンシ、error、anomaly
- Jev Supervisor実行時は各turnに`hardGate`と`jev`（sessionAction、nextUserBehavior、confidence、cacheHit、callCount）を追加
- 失敗時の`evidence/`: Interview State、transcript、timeline、console log、network log、DataChannel error、screenshot、Playwright traceの参照、replayコマンド

NORMALの各ターンでは、次の順序をTimelineに記録します。

    user_transcript_final
      → user_turn_completed
      → candidate_answer_saved / record_answer_saved
      → answer_state_updated
      → state_version_updated
      → current_question_updated
      → assistant_speech_started

検出対象は ANSWER_NOT_PERSISTED、QUESTION_NOT_ADVANCED、REPEATED_QUESTION、DUPLICATE_RESPONSE、TRANSCRIPT_FINAL_MISSING、TURN_COMMITTED_TOO_EARLY、RESPONSE_TIMEOUT、WEBSOCKET_ERROR、API_5XX です。

LLM Judgeは初期実装では呼び出していません。決定論的検査で異常が発生した場合に追加できるcost-guard付きの境界だけを用意しています。

## 外部APIが呼ばれる条件

- Level 1 / Level 2: GPT Live、Responses、Transcribe、Polly、Jev、LLM Judgeを呼ばない
- Level 3: VOICE_TEST_REAL_API_ENABLED=true のときだけ、`VOICE_TEST_REAL_PROVIDER`で指定した実GPT LiveまたはTranscribe + Pollyを呼ぶ
- Jev: TEST_AGENT_MODE=jev を明示し、JEV_API_URLが設定されている場合だけ呼ぶ。JEV_API_KEYはBearer認証に使う。JEV_API_URLが無い場合は設定エラーで停止する
- LLM Judge: 初期テストでは呼ばない。追加実装時も異常時または明示指定時だけにする

通常モードの既定上限は最大3セッション、1セッション8ターン、音声120秒、Jev 20回、LLM Judge 5回です。`TEST_AGENT_MODE=jev`では、初期Phaseの上限を最大1セッション、1セッション5ターン、Jev 5回として扱います。

## 現時点の残課題

- Level 2はブラウザの仮想マイクとfake Live DataChannelを決定論的に検証します。実Transcribe + PollyのAWSストリームはLevel 3の明示opt-in経路です。
- Level 2のfake transportは実際の音声認識結果を生成しません。音声認識精度を検証する場合は、録音済みWAVを使うLevel 3を明示的に実行してください。
- 実APIテストで意味的な会話品質を評価するLLM Judge adapterは、コストガードだけ実装し、Phase 1では呼び出していません。
