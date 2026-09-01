# 外部連携

## 1. 連携一覧

| システム | 種別 | 用途 | 認証・設定 | 現在の状態 | 根拠 |
| --- | --- | --- | --- | --- | --- |
| PostgreSQL | DB | 構造化データの保存 | `DATABASE_URL` | APIで利用中 | `repositories/store.py` |
| Amazon Bedrock | AI API | インタビュー・質問設計のAI処理 | AWS profile / Region | APIで利用中 | `agents/**/provider.py`、`core/config.py` |
| Amazon Transcribe / Polly | 音声API | Voice Runtimeの音声認識・合成 | AWS profile / Voice設定 | Voice Runtimeに実装あり | `app/voice/src/ai_interviewer_voice/runtimes/transcribe_polly/` |
| Kinesis Video Streams WebRTC | 音声通信 | VoiceのICE/TURN設定取得 | Voice設定 | Voice serviceに実装あり。v1のシグナリングはHTTP SDP | `app/voice/src/ai_interviewer_voice/services/ice_server_service.py`、`routers/webrtc.py` |
| 開発用トークン | 認証 | ローカルのユーザー・ロール切替 | `Authorization` / `x-dev-token` | 開発用のみ | `auth/deps.py` |
| SQS | Queue | 文書取り込みなどの将来の非同期処理 | `SQS_DOCUMENT_QUEUE_URL` | URL設定のみ。Worker未接続 | `app/api/src/ai_interviewer_api/core/config.py`、`app/worker/` |

## 2. データストア

| Store | 役割 | アクセス層 | リスク | 根拠 |
| --- | --- | --- | --- | --- |
| `kikiori.entity_store` | 論理エンティティのJSONB保存 | `PostgresStore` | JSONB主体のため、検索・件数が増える場合はクエリとスキーマの見直しが必要 | `repositories/store.py`、`infra/postgres/init/001_schema.sql` |
| `InMemoryStore` | APIテスト用の明示的なテストダブル | `create_store()` | 本番保存先に使わない | `repositories/store.py`、`tests/conftest.py` |

## 3. 秘密情報

* `.env.example`は接続先と設定名だけを示す。実際の`.env`やAWS認証情報はコミットしない。
* Composeはホストの`~/.aws`をコンテナへ読み取り専用でマウントする。
* 内部Voice APIは`INTERNAL_API_TOKEN`を使用する。
* 本番の認証・秘密情報管理は未実装であり、目標構成はAWSアーキテクチャ文書に分離している。

## 4. 失敗時の扱い

* PostgreSQLの接続確認はAPI起動時の`store.ensure_schema()`と`store.health()`で行う。
* Structured Interviewまたは質問生成のBedrock呼び出しに失敗した場合は、状態を確定せずAPIエラーとして扱う。Voice Runtimeはそのエラーを共通イベントへ変換する。
* VoiceのProvider固有の再接続・音声出力制御はRuntimeに閉じ込める。
* 外部AWSを必要とする実接続とブラウザWebRTCのE2Eは通常の自動テスト範囲外である。

## 5. 観測性

* APIは標準`logging`を使用する。
* 構造化メトリクス、トレース、外部連携のSLOは現行コードでは確認できない。

## 根拠

* `app/api/src/ai_interviewer_api/core/config.py`
* `app/api/src/ai_interviewer_api/repositories/store.py`
* `app/api/src/ai_interviewer_api/auth/deps.py`
* `app/api/src/ai_interviewer_api/main.py`
* `app/voice/src/ai_interviewer_voice/runtimes/`
* `infra/docker-compose.yml`
