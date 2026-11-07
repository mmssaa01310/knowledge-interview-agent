# 外部連携

## 1. 連携一覧

| システム | 種別 | 用途 | 認証・設定 | 根拠 |
| --- | --- | --- | --- | --- |
| OpenAI Responses API | AI API | インタビュー解釈、質問設計、要件・処理モデル編集 | アプリ共通設定のOpenAI Project APIキー | `agents/**/provider.py` |
| OpenAI GPT Live | 音声API | WebRTC音声対話とBackendへの評価委譲 | Backendがセッションを作成 | `services/live_session.py` |
| PostgreSQL | DB | 構造化データとアプリ設定の保存 | `DATABASE_URL` | `repositories/store.py` |
| Elasticsearch | 検索 | 設定時の文書検索 | `.env`の接続情報 | `services/document_ingestion.py` |

## 2. 秘密情報

OpenAI Project APIキーはアプリケーション管理者だけが設定し、暗号化してPostgreSQLへ保存する。画面ではマスク表示し、生キーをログや監査本文へ出力しない。`OPENAI_SETTINGS_ENCRYPTION_KEY`はDB外で管理する。OpenAIキーは環境変数やFrontend bundleへ置かない。

## 3. 失敗時の扱い

OpenAI API呼び出しに失敗した場合、AI候補や状態を確定せず、APIエラーとして扱う。暗黙のProvider fallbackは行わない。PostgreSQLの接続確認はAPI起動時のStore準備処理で行う。

## 4. 観測性

APIは標準`logging`を使用する。OpenAIのリクエスト失敗ログにはモデル、HTTP状態、Request ID、例外型を記録し、キーや会話本文を含めない。
