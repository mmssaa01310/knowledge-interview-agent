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

browser-audio.spec.mjs はChromiumを起動し、テスト実行時に生成した固定PCM WAVを仮想マイクへ渡します。Live sessionとDataChannelはブラウザ内driverで再現し、State updateだけはローカルAPIへ送ります。

このレベルでは、実GPT LiveやResponses APIを呼ばずに、マイク接続、transcript final、state保存、UI側のWebRTCイベント処理を確認できます。

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

特定シナリオ・seedの再実行:

    pnpm voice-test -- --scenario SELF_CORRECTION
    pnpm voice-test -- --replay 38291

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

超過した場合はテストを直ちに失敗させます。決定論的実行が既定で、Jevは TEST_AGENT_MODE=jev と JEV_API_URL の指定時だけ使います。

## TelemetryとEvidence Package

各テストの出力先に以下を保存します。

- telemetry.jsonl: turn、partial/final transcript、State version、first audio latency、error、anomaly
- interview-state.json: 失敗時点のInterview State
- network-errors.json
- timeline.json
- screenshot.png
- Playwrightのtrace（retain-on-failure）
- replay-command.txt

検出対象は ANSWER_NOT_PERSISTED、QUESTION_NOT_ADVANCED、REPEATED_QUESTION、DUPLICATE_RESPONSE、TRANSCRIPT_FINAL_MISSING、TURN_COMMITTED_TOO_EARLY、RESPONSE_TIMEOUT、WEBSOCKET_ERROR、API_5XX です。

LLM Judgeは初期実装では呼び出していません。決定論的検査で異常が発生した場合に追加できるcost-guard付きの境界だけを用意しています。

## 外部APIが呼ばれる条件

- Level 1 / Level 2: GPT Live、Responses、Transcribe、Polly、Jev、LLM Judgeを呼ばない
- Level 3: VOICE_TEST_REAL_API_ENABLED=true のときだけ、`VOICE_TEST_REAL_PROVIDER`で指定した実GPT LiveまたはTranscribe + Pollyを呼ぶ
- Jev: TEST_AGENT_MODE=jev かつ JEV_API_URL がある場合だけ呼ぶ
- LLM Judge: 初期テストでは呼ばない。追加実装時も異常時または明示指定時だけにする

1回の実APIテストで許可される上限は、既定で最大3セッション、1セッション8ターン、音声120秒、Jev 20回、LLM Judge 5回です。

## 現時点の残課題

- この環境ではnpm registryの名前解決ができず、@playwright/testの取得とPlaywright実ブラウザテスト実行は未検証です。CIではlockfileから取得して実行します。
- Level 2はブラウザの仮想マイクとLive DataChannelを決定論的に検証します。実Transcribe + PollyのAWSストリームはLevel 3の明示opt-in経路です。
- 実APIテストで意味的な会話品質を評価するLLM Judge adapterは、コストガードだけ実装し、Phase 1では呼び出していません。
