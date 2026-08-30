# 実装規約

## 1. 命名

| 対象 | 現行パターン | 例 | 根拠 |
| --- | --- | --- | --- |
| React Component | PascalCase `.tsx` | `KnowledgeRecordsPage.tsx` | `app/web/src/pages/` |
| Webの機能モジュール | camelCase `.ts` | `interviewLocale.ts` | `app/web/src/features/interviews/` |
| Pythonモジュール・関数 | snake_case | `voice_interview.py`、`create_voice_session` | `app/api/src/ai_interviewer_api/` |
| Pythonの型 | PascalCase | `PostgresStore`、`UserContext` | `repositories/store.py`、`auth/deps.py` |
| 環境変数 | UPPER_SNAKE_CASE | `DATABASE_URL` | `.env.example` |

## 2. 型検査・整形

* Webは`tsconfig.json`の`strict: true`を有効にし、`pnpm --dir app/web lint`で`tsc --noEmit`を実行する。
* Webの本番Buildは翻訳検査を前段で実行する。
* Pythonパッケージにはpytest設定がある。`ruff`、`mypy`、Prettier、ESLintの設定は現行リポジトリでは確認できないため、定義済みの必須手順として扱わない。

## 3. importと責務

* Webは相対importを使用する。path aliasは`app/web/tsconfig.json`に定義されていない。
* API RouterはService/Repositoryを呼び、PostgreSQL SQLを直接持たない。
* Voiceは`httpx`による内部HTTPでAPIと連携し、APIのPythonモジュールを直接importしない。
* 状態遷移・認可・正式保存はBackendで保証し、WebやVoiceに複製しない。

## 4. エラーとログ

* APIはFastAPIの`HTTPException`でHTTPエラーを返す。
* API起動時は`logging`でデータベース準備とBedrock設定を出力する。
* Voice Runtimeと外部AWS呼び出しの失敗は、各サービス層で処理する。外部サービスを使う通常テストでは実AWSを呼ばない。
* 秘密情報、認証情報、ユーザー入力全文をログやドキュメントへ出さない方針は`AGENTS.md`と`docs/agents/agent-behavior-policy.md`に従う。

## 5. テスト

* Pythonテストは各サービスの`tests/`に`test_*.py`として置く。
* APIの通常テストは`tests/conftest.py`で`DATABASE_URL=memory://test`を明示し、外部PostgreSQLから隔離する。
* PostgreSQL統合テストは`@pytest.mark.integration`で、`TEST_DATABASE_URL`がない場合はskipする。
* Webテストは`app/web/tests/*.test.mjs`をNode test runnerで実行する。

## 根拠

* `app/web/tsconfig.json`
* `app/web/package.json`
* `app/api/tests/conftest.py`
* `app/api/tests/repositories/test_postgres_store.py`
* `app/api/src/ai_interviewer_api/main.py`
* `app/api/src/ai_interviewer_api/auth/deps.py`
