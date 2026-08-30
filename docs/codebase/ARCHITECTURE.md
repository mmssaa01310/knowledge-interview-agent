# アーキテクチャ

## 1. 方式

KIKIORIは、React/ViteのWeb、FastAPIのAPIとVoiceサービス、PostgreSQLからなるモノレポである。APIは`routers → services → repositories`で責務を分け、Webはページと機能単位で構成する。Voiceは内部HTTPを通じてAPIへ処理を委譲する。

主な制約は次のとおりである。

* APIが認可、インタビュー状態、AI処理、保存の正本である。
* Voiceは音声I/Oに限定し、APIのPythonモジュールを直接importしない。
* AIの候補は人による承認前に正式ナレッジへ確定しない。

## 2. 通常のテキストインタビュー

```text
Browser
  → React page / feature
  → Vite proxy (/api)
  → FastAPI router
  → service (認可・状態遷移・AI処理)
  → repository
  → PostgreSQL kikiori.entity_store (JSONB payload)
  → API response / SSE
```

API起動時に`store.ensure_schema()`が`kikiori.entity_store`とインデックスを冪等に作成する。テストでは`DATABASE_URL=memory://test`で`InMemoryStore`を明示選択する。

## 3. 音声インタビュー

```text
Browser WebRTC
  → app/voice (音声フレーム、Runtime、Transcript / 音声再生)
  → app/api internal HTTP
  → Interview service (Turn、回答評価、状態遷移)
  → PostgreSQL
```

Voice Runtimeは`nova_sonic`と`transcribe_polly`を共通契約の下で分ける。ComposeのWeb既定Providerは`transcribe_polly`である。

## 4. モジュール責務

| モジュール | 所有する責務 | 所有しない責務 | 根拠 |
| --- | --- | --- | --- |
| `app/web` | 画面、i18n、Guide、API/Voiceクライアント | 認可保証、保存 | `app/web/src/` |
| `app/api/src/ai_interviewer_api/routers` | HTTP境界 | SQL・AI Provider詳細 | `app/api/src/ai_interviewer_api/routers/` |
| `app/api/src/ai_interviewer_api/services` | インタビュー・承認・分析の業務処理 | WebRTC、codec | `app/api/src/ai_interviewer_api/services/` |
| `app/api/src/ai_interviewer_api/repositories` | Store契約とPostgreSQLアクセス | UI状態 | `app/api/src/ai_interviewer_api/repositories/` |
| `app/voice` | WebRTC、音声Runtime、API bridge | RAG、回答確定 | `app/voice/src/ai_interviewer_voice/` |
| `app/worker` | 現在は文書状態のサンプル | SQS受信・永続化（未実装） | `app/worker/src/` |

## 5. 再利用しているパターン

| パターン | 場所 | 目的 |
| --- | --- | --- |
| Repository Store | `repositories/store.py` | 辞書型ドメインデータをPostgreSQLへ永続化する |
| Router集約 | `routers/routes.py` | APIルートを単一アプリへ結合する |
| Provider / Runtime分離 | `app/voice/src/ai_interviewer_voice/runtimes/` | 音声Provider固有処理を隔離する |
| Stable guide target | Webの`data-guide`属性と`features/guides/` | UI構造変更に強い操作案内にする |

## 6. 既知の設計上の注意

* 本番用認証は未実装で、APIは開発用トークンを受け付ける。
* `entity_store`はJSONB互換Storeであり、業務データ量・検索要件に応じて正規化の検討が必要になる。
* WorkerとSQSの実接続は未実装である。

## 根拠

* `app/api/src/ai_interviewer_api/main.py`
* `app/api/src/ai_interviewer_api/repositories/store.py`
* `app/api/src/ai_interviewer_api/routers/routes.py`
* `app/voice/src/ai_interviewer_voice/main.py`
* `app/web/src/features/guides/`
* `infra/docker-compose.yml`
