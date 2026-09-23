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

### Level 2からLevel 3への昇格

Jev探索後は、Level 2の`exploration-summary.json`から、再現価値のある代表ケースだけをLevel 3へ昇格できます。対象はExpectation FAIL、`RETRY_CURRENT`、AMBIGUOUS / SELF_CORRECT / MULTI_ANSWER / ASK_CLARIFICATION / HESITATE、予期しない質問遷移、回答境界、非通常発話からのassistant recoveryです。正常なNORMALだけのPASSは昇格しません。同じbehaviorの候補は代表seedへまとめ、既定で最大5件に制限します。

Level 3では、Level 2と同じseed・候補情報を使い、Chromiumの仮想マイクへ指定した録音済みWAVを入力します。生成されるトーンWAVは音声認識用ではないため、Level 3では使用しません。WAVに複数ターンを含める場合も、1セッション6ターンを上限にします。

実行例:

    VOICE_TEST_REAL_API_ENABLED=true \
    VOICE_TEST_REAL_PROVIDER=gpt_live \
    VOICE_TEST_LEVEL2_SUMMARY=/absolute/path/to/exploration-summary.json \
    VOICE_TEST_AUDIO_FIXTURE=/absolute/path/to/recorded.wav \
    VOICE_TEST_MAX_LEVEL3_SESSIONS=5 \
    VOICE_TEST_MAX_LEVEL3_TURNS_PER_SESSION=6 \
    VOICE_TEST_MAX_LEVEL3_RETRIES=0 \
    pnpm exec playwright test tests/e2e/voice/live/level3-promotion.spec.mjs \
      --project=chromium --workers=1 --retries=0

Level 3のTelemetryは`audioInputMode=fake_microphone_wav`、`transcriptSource=gpt_live`、`liveApiUsed=true`としてLevel 2と区別します。speech start/end、transcript final、state commit、assistant speech start/endを実測し、speech endからtranscript final / assistant startまでのレイテンシを保存します。候補ごとの比較結果と失敗時のEvidence Packageは`level3/`以下に出力されます。この段階ではCodexの自動修正、LLM Judge、自動commitは行いません。

### Jev Supervisor（TypeSafe）

`TEST_AGENT_MODE=jev`を明示した場合だけ、`jev-supervisor.spec.mjs`を実行します。Jevは各ターン終了後のテスト進行だけを判断し、ユーザー発話本文は生成しません。発話には`scenarios.mjs`の固定fixtureを使用します。

各ターンは次の順で処理します。

    transcript final / State / transport / behavior expectation
      → Hard Gate
      → Jev Supervisor（PASS時のみ、最大1回）
      → CONTINUE / STRESS_NEXT / RETRY_CURRENT / END_SUCCESS
      → 固定fixtureで次のターン

常に成立すべきInvariant（HTTP 5xx、WebSocket/DataChannelエラー、重複応答、早すぎるcommit）と、behaviorごとに期待結果が変わるExpectation（transcript欠落、回答保存、質問遷移、応答タイムアウト）を分離します。たとえばSILENCEはfinal transcriptを要求せず、回答保存と質問遷移が発生しないことを検証します。Expectation違反、Invariant違反、内部エラー、または上限到達時だけJevを呼ばずにFAILまたはSTOPします。同一StateのJev結果はプロセス内でキャッシュします。

初期の探索実行では、ユーザー発話本文を生成させず、TypeSafeのJevに各ターンの
`session_action`と、必要な場合の`next_user_behavior`だけを選択させます。TypeSafeの
リクエストは公式の[SystemOne API](https://api.typesafe.ai/docs)へ送信し、本文は固定fixtureから
選びます。したがって外部APIはJevだけで、GPT Live、Transcribe、Polly、LLM Judgeは呼びません。

リポジトリルートの`.env`またはプロセス環境に、次のTypeSafe設定を用意します。

    TYPESAFE_API_KEY=...
    TYPESAFE_BASE_URL=https://api.typesafe.ai
    TYPESAFE_DEFAULT_MODEL=jev-latest

`TYPESAFE_BASE_URL`にはAPIのorigin（`https://api.typesafe.ai`）または
`/v1/systemone`まで含むURLを指定できます。モデルはTypeSafeで利用可能な
`jev-latest`または`jev-preview`を指定してください。

実行例（初期探索の上限は10セッション、1セッション8ターン、Jev合計80回）:

    TEST_AGENT_MODE=jev \
    VOICE_TEST_REAL_API_ENABLED=false \
    VOICE_TEST_START_LOCAL_STACK=true \
    VOICE_TEST_MAX_SESSIONS=10 \
    VOICE_TEST_MAX_TURNS_PER_SESSION=8 \
    VOICE_TEST_MAX_JEV_CALLS_TOTAL=80 \
    VOICE_TEST_MAX_RETRIES=0 \
    pnpm exec playwright test tests/e2e/voice/jev-supervisor.spec.mjs --project=chromium --workers=1

`TYPESAFE_API_KEY`はAuthorization Bearerとして送信し、TelemetryやEvidence Packageには保存しません。
`TEST_AGENT_MODE=jev`を指定し、TypeSafeキーが存在する場合だけTypeSafe APIを呼びます。
`VOICE_TEST_REAL_API_ENABLED`は`false`のままにしてください。これはGPT LiveやTranscribe + Pollyの
実APIを無効にしたまま、Jevだけを有効にするためです。旧`JEV_API_URL` / `JEV_API_KEY`も
互換経路として利用できますが、TypeSafe設定がない場合のフォールバックです。

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

決定論的モードの既定値は`tests/e2e/voice/voice-test.config.json`と`config.mjs`で管理し、
Jevモードの探索上限は`config.mjs`が別途適用します。環境変数で上書きできます。

| 環境変数 | 既定値 | 意味 |
| --- | ---: | --- |
| VOICE_TEST_REAL_API_ENABLED | false | 実APIテストの明示許可 |
| VOICE_TEST_REAL_PROVIDER | gpt_live | Level 3で使う実プロバイダ（`gpt_live` / `transcribe_polly`） |
| VOICE_TEST_AUDIO_FIXTURE | 未指定 | 仮想マイクへ入力する録音済みWAVの絶対パス |
| VOICE_TEST_MAX_SESSIONS | 3（決定論的） / 10（Jev） | 1実行で許可するセッション数 |
| VOICE_TEST_MAX_TURNS_PER_SESSION | 8 | セッションごとの最大ターン数 |
| VOICE_TEST_MAX_AUDIO_SECONDS_PER_SESSION | 120 | セッションごとの音声秒数 |
| VOICE_TEST_MAX_JEV_CALLS | 20（決定論的） / 80（Jev） | 後方互換用のJev最大呼出数 |
| VOICE_TEST_MAX_JEV_CALLS_TOTAL | `VOICE_TEST_MAX_JEV_CALLS` | 探索全体のJev最大呼出数 |
| VOICE_TEST_MAX_LLM_JUDGE_CALLS | 5 | LLM Judgeの最大呼出数 |
| VOICE_TEST_MAX_RETRIES | 1 | Playwright retryの上限 |
| VOICE_TEST_LEVEL2_SUMMARY | 未指定 | Level 3が読むLevel 2探索summaryの絶対パス（未指定時はoutputDir内を参照） |
| VOICE_TEST_MAX_LEVEL3_SESSIONS | 5 | Level 3で再検証する候補セッション数 |
| VOICE_TEST_MAX_LEVEL3_TURNS_PER_SESSION | 6 | Level 3のセッションごとの最大ターン数 |
| VOICE_TEST_MAX_LEVEL3_RETRIES | 0 | Level 3のretry上限 |

超過した場合はテストを直ちに停止または失敗させます。決定論的実行が既定です。
Jevモードでは未指定時の上限を10セッション、1セッション8ターン、合計80 Jev calls、
retryなしとして扱います。Hard Gateで機械的な異常を先に検出し、通過したターンだけJevを
最大1回呼びます。同一Stateはプロセス内でキャッシュします。

## TelemetryとEvidence Package

各テストの出力先に以下を保存します。

- `telemetry.jsonl`: turn、transcript final、candidate/confirmed、answerState、State version、current/next question、擬似レイテンシ、error、anomaly
- Jev Supervisor実行時は各turnに`hardGate`、behavior別の`expectations` / `observed` / `expectationResult`、`jev`（provider、model、sessionAction、nextUserBehavior、confidence、cacheHit、callCount、usage）を追加し、`exploration-summary.json`に全セッションの経路、行動・アクション集計、Expectation結果、Hard Gate異常数を保存
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
- Level 3: VOICE_TEST_REAL_API_ENABLED=true、実プロバイダ、Level 2 summary、録音済みWAVを明示したときだけ、Level 3 GPT Live昇格テストが実APIを呼ぶ。候補は最大5セッション、1セッション6ターン、retryなし
- Jev: TEST_AGENT_MODE=jev を明示し、TYPESAFE_API_KEY（または旧JEV_API_URL）が設定されている場合だけ呼ぶ。TYPESAFE_API_KEY / JEV_API_KEYはBearer認証に使う。通常の実行ではJevを呼ばない
- LLM Judge: 初期テストでは呼ばない。追加実装時も異常時または明示指定時だけにする

通常モードの既定上限は最大3セッション、1セッション8ターン、音声120秒、Jev 20回、LLM Judge 5回です。`TEST_AGENT_MODE=jev`では、初期探索の上限を最大10セッション、1セッション8ターン、Jev合計80回、retryなしとして扱います。

## 現時点の残課題

- Level 2はブラウザの仮想マイクとfake Live DataChannelを決定論的に検証します。実Transcribe + PollyのAWSストリームはLevel 3の明示opt-in経路です。
- Level 2のfake transportは実際の音声認識結果を生成しません。音声認識精度を検証する場合は、録音済みWAVを使うLevel 3を明示的に実行してください。
- 実APIテストで意味的な会話品質を評価するLLM Judge adapterは、コストガードだけ実装し、Phase 1では呼び出していません。
- Level 2候補を実GPT Liveで再検証するLevel 3の比較・実測は実装済みですが、実APIの安定性はネットワーク、APIキー、録音内容に依存します。失敗時は候補単位のEvidence Packageを次の原因調査へ渡します。
