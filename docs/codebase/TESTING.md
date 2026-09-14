# テスト

## 1. 実行コマンド

```bash
# Web
pnpm --dir app/web lint
pnpm --dir app/web check:i18n
pnpm --dir app/web build
node --test app/web/tests/*.test.mjs

# API / Voice / Worker
cd app/api && uv run pytest
cd app/voice && uv run pytest
cd app/worker && uv run pytest

# API test levels
cd app/api && uv run pytest tests/unit
cd app/api && uv run pytest tests/integration
cd app/api && uv run pytest tests/e2e

# Voice test levels (the cross-package voice/API E2E is under app/api)
cd app/voice && uv run pytest tests/unit
cd app/voice && uv run pytest tests/integration

# PostgreSQL Store integration (only with an explicit database)
cd app/api && TEST_DATABASE_URL=postgresql://... uv run pytest tests/integration/persistence/test_postgres_store.py

# Documentation
uv run --group dev mkdocs build --strict

# Voice interview critical conversation controls
cd app/api && uv run pytest tests/unit/services/test_structured_interview.py \
  tests/unit/services/test_interview_voice_case_catalog.py \
  tests/unit/services/test_interview_confirmation.py
```

## 2. 配置と分離

| レベル | 配置 | 主な確認 |
| --- | --- | --- |
| Unit | API: `app/api/tests/unit/{agents,services,state}`; Voice: `app/voice/tests/unit/runtimes` | 外部I/Oを使わない関数・クラス・サービス単位の検証 |
| Integration | API: `app/api/tests/integration/{api,persistence,voice}`; Voice: `app/voice/tests/integration/voice` | Runtime/API/State/Persistence間の接続と状態遷移 |
| E2E | `app/api/tests/e2e/voice` | 複数TurnのRuntime → API → 永続Stateのシナリオ |
| Worker | `app/worker/tests/` | 文書取り込み状態のサンプル処理 |
| Web | `app/web/tests/*.test.mjs` | 回答表示の状態分離 |

API通常テストはメモリStoreで動作する。PostgreSQL統合テストは明示した`TEST_DATABASE_URL`へ接続し、利用できない場合はskipする。

## 3. スコープ

| スコープ | 現在の有無 | 備考 |
| --- | --- | --- |
| Unit | あり | APIとVoiceの既存isolated suites |
| Integration | あり | API契約、Persistence、Voice Runtime/API連携 |
| E2E | 決定論的E2Eあり | Fake外部LLM/AWS境界で3Turn以上のAPI/Voice/State経路を確認 |
| PostgreSQL integration | 条件付き | `TEST_DATABASE_URL`が必要 |
| Browser / real-audio E2E | 未確認 | 実ブラウザ・実マイク・実AWS/ProviderのE2Eは別途必要 |
| 実AWS統合 | 手動確認が必要 | 認証情報、Bedrock、Transcribe / Polly、WebRTC環境に依存 |

音声インタビューのCriticalケースは、`app/api/tests/fixtures/interview_voice_critical_cases.json`を
機械可読な契約としてAPIの決定的回帰テストと同期する。`audio.file`が未設定のケースは実音声E2Eではなく、
Fake Providerを使う状態遷移テストとして扱う。実音声・実AWS・ブラウザ境界は別ランナーで実行する。

## 4. 品質シグナルとギャップ

* Webには型検査、翻訳整合性検査、本番Buildがある。
* Pythonではpytestが定義されている。
* 現行設定にカバレッジ閾値、ruff、mypy、Frontend E2E test runnerはない。
* 実AWS・実ブラウザの統合試験は別途環境を用意して実施する必要がある。

## 根拠

* `app/web/package.json`
* `app/web/tests/answerVisibility.test.mjs`
* `app/api/pyproject.toml`
* `app/api/tests/conftest.py`
* `app/api/tests/integration/persistence/test_postgres_store.py`
* `app/voice/tests/`
* `app/worker/tests/test_document_ingestion.py`
